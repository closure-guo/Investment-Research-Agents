import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * delta add-event-driven-lane T8：选股页事件观察区块 E2E（默认套件，8000/5173；
 * 隔离端口对 8001/5174 的 playwright.isolated.config.ts 亦可运行——API 基址取自
 * config 的 baseURL，不硬编码端口）。
 *
 * 红线：不 mock /api/*——预告/分诊/清单经 TESTING=1 的 /api/test/seed 真实写入事件库
 * （webServer env EVENTS_DB_PATH=data/test-e2e-events.db，与开发库 data/events.db
 * 物理隔离），前端经真实 GET /api/events/watchlist 读取渲染。
 *
 * 用例串行（serial）：共享同一事件库，seed/reset 互相覆盖，并行会互踩；每例前后均
 * reset（`events_reset` 清事件线四表），避免遗留条目让其他用例的空态前提失效
 * （共享库泄漏）。本 spec 只动事件库，screening 池/温度计的并行造数复位互不影响。
 *
 * 三例覆盖（spec frontend「选股页事件观察区块」）：
 * 1. 造数（outperform/in_line/risk 三条，覆盖三态徽章）→ `/screening`：区块渲染
 *    全要素——来源标识「业绩预告」/ 标的与名称 / 公告日 / 三态徽章文案与口径注释 /
 *    报告期·预告类型·净利润变动幅度三要素卡 / 理由 / 证据引用（field=value 逐条）/
 *    「发起深度分析」按钮可用；
 * 2. 空态：`events_reset` 后（beforeEach 已复位）→ `event-watchlist-empty` 固定文案，
 *    且无行/无徽章/无按钮/不冒充加载中；
 * 3. 发起分析 → `POST /api/events/watchlist/{id}/analyze` 真实入队（复用既有队列
 *    单例，非新通道）→ 队列徽标即刻出现（断言方式沿 screening-page.spec 先例；
 *    徽标文案随队列推进换态，故正则覆盖四态）。
 *
 * 证伪自检（T8 交付要求）：临时改错一处期望值（如徽章期望文案）必须变红，改回变绿；
 * 两段输出进 task-8-report.md。
 *
 * 条目 id 取数（而非硬编码 1）：`event_watchlist.id` 为 AUTOINCREMENT，`events_reset`
 * 走 DELETE 清行、sqlite_sequence 不回退 → 同一库多次造数的 id 逐轮递增。故造数后经
 * 只读端点读回真实 id 再拼 testid，跨轮次稳定（读回即是「seed 真落库」的核验）。
 *
 * 首屏取数守卫：`event-watchlist-card` 是无条件外层容器（goto 后即渲染，数据未到先出
 * `event-watchlist-loading`），把 15s 挂它等于空转 → 现挂在各例**第一条数据依赖断言**上
 * （造数=首行 / 空态=空态文案 / 发起分析=按钮），其后断言一律默认 5s。
 */
test.describe.configure({ mode: 'serial', timeout: 60_000 })

// API 造数显式走 IPv4：Windows 上 localhost 可能先解析到 ::1，而 vite dev server
// 绑 0.0.0.0（IPv4），APIRequestContext 只试 ::1 时会 ECONNREFUSED（见
// screening-page.spec.ts 注）。基址取自 config baseURL → 默认/隔离套件通用。
function apiBase(baseURL: string | undefined): string {
  expect(baseURL, 'config use.baseURL 未配置').toBeTruthy()
  return (baseURL as string).replace('//localhost', '//127.0.0.1')
}

type WatchlistItem = {
  id: number
  source: string | null
  status: string | null
  session_id: string | null
  code: string | null
  name: string | null
  report_period: string | null
  forecast_type: string | null
  change_low: number | null
  change_high: number | null
  announce_date: string | null
  verdict: string | null
  reason: string | null
  evidence: Array<{ field: string; value: unknown }> | null
  model: string | null
  prompt_fingerprint: string | null
  triaged_at: string | null
}

const FORECAST_A = {
  code: '600519',
  name: '贵州茅台',
  report_period: '2025-03-31',
  source_record_id: '归属于上市公司股东的净利润|2025-04-15',
  forecast_type: '预增',
  change_low: 60.0,
  change_high: 90.0,
  announce_date: '2025-04-15',
}
const FORECAST_B = {
  code: '000001',
  name: '平安银行',
  report_period: '2025-03-31',
  source_record_id: '扣除非经常性损益后的净利润|2025-04-16',
  forecast_type: '略增',
  change_low: 5.0,
  change_high: 15.0,
  announce_date: '2025-04-16',
}
const FORECAST_C = {
  code: '300750',
  name: '宁德时代',
  report_period: '2025-03-31',
  source_record_id: '归属于上市公司股东的净利润|2025-04-17',
  forecast_type: '预减',
  change_low: -40.0,
  change_high: -20.0,
  announce_date: '2025-04-17',
}

const keyOf = (forecast: { code: string; report_period: string; source_record_id: string }) =>
  `${forecast.code}|${forecast.report_period}|${forecast.source_record_id}`

/** 三条预告（三态分诊结论各一）+ 对应清单行（earnings 来源） */
const EVENTS_SEED = {
  forecasts: [FORECAST_A, FORECAST_B, FORECAST_C],
  triage: [
    {
      forecast_key: keyOf(FORECAST_A),
      verdict: 'outperform',
      reason: '预增 60% ~ 90%，净利润大幅增长',
      // 两条证据引用：断言 field=value 逐条渲染 + 全角分号连接
      evidence: [
        { field: 'change_low', value: 60 },
        { field: 'forecast_type', value: '预增' },
      ],
      model: 'seed',
      prompt_fingerprint: 'event_triage@1',
    },
    {
      forecast_key: keyOf(FORECAST_B),
      verdict: 'in_line',
      reason: '预增 5% ~ 15%，符合历史季节规律',
      evidence: [{ field: 'change_low', value: 5 }],
      model: 'seed',
      prompt_fingerprint: 'event_triage@1',
    },
    {
      forecast_key: keyOf(FORECAST_C),
      verdict: 'risk',
      reason: '预减 20% ~ 40%，毛利率承压',
      evidence: [{ field: 'change_high', value: -20 }],
      model: 'seed',
      prompt_fingerprint: 'event_triage@1',
    },
  ],
  watchlist: [
    { forecast_key: keyOf(FORECAST_A), source: 'earnings' },
    { forecast_key: keyOf(FORECAST_B), source: 'earnings' },
    { forecast_key: keyOf(FORECAST_C), source: 'earnings' },
  ],
}

async function resetEvents(request: APIRequestContext, baseURL: string | undefined) {
  const resp = await request.post(`${apiBase(baseURL)}/api/test/seed`, {
    data: { events_reset: true },
  })
  expect(resp.ok()).toBeTruthy()
}

async function seedEvents(
  request: APIRequestContext,
  baseURL: string | undefined,
  events: Record<string, unknown>
) {
  const resp = await request.post(`${apiBase(baseURL)}/api/test/seed`, { data: { events } })
  expect(resp.ok()).toBeTruthy()
  // 守卫链核验：seed 请求若被「非造数早退」吞掉仍是 200 ok 占位响应，故断言 mode 之外
  // 再经只读端点读回真实 id（见文件头「条目 id 取数」）——读回为空即 fail loud。
  const listResp = await request.get(`${apiBase(baseURL)}/api/events/watchlist`)
  expect(listResp.ok()).toBeTruthy()
  const items = ((await listResp.json()) as { items: WatchlistItem[] }).items
  expect(items.length, 'seed 后事件库应至少有一条清单行').toBeGreaterThan(0)
  return items
}

/** 按代码取条目（清单按 id 升序，与造数序一致） */
function itemByCode(items: WatchlistItem[], code: string): WatchlistItem {
  const item = items.find((row) => row.code === code)
  expect(item, `事件库中未找到代码 ${code} 的清单条目`).toBeTruthy()
  return item as WatchlistItem
}

test.beforeEach(async ({ request, baseURL }) => {
  await resetEvents(request, baseURL)
})

test.afterEach(async ({ request, baseURL }) => {
  // 每例后复位（含最后一例）：不留行给同套件其他 spec 的事件区块空态前提添乱
  await resetEvents(request, baseURL)
})

test('造数三条（三态）→ 区块全要素渲染，空态不冒充', async ({ page, request, baseURL }) => {
  const items = await seedEvents(request, baseURL, EVENTS_SEED)
  const a = itemByCode(items, FORECAST_A.code)
  const b = itemByCode(items, FORECAST_B.code)
  const c = itemByCode(items, FORECAST_C.code)

  await page.goto('/screening')

  // 首屏取数守卫挂在**第一条数据依赖断言**上：`event-watchlist-card` 是无条件外层
  // 容器（数据未到也照渲染 loading 态），挂它等于空转。首屏取数在默认套件 8 并发
  // worker 下偶见 >5s（与 screening-page.spec 同款处置）→ 显式 15s 只此一处。
  const firstRow = page.getByTestId(`event-watchlist-row-${a.id}`)
  await expect(firstRow).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('event-watchlist-card')).toBeVisible()

  // 三条行全渲染（三态各一），空态/加载态不出现
  await expect(page.getByTestId(/^event-watchlist-row-/)).toHaveCount(3)
  await expect(page.getByTestId('event-watchlist-empty')).toBeHidden()
  await expect(page.getByTestId('event-watchlist-loading')).toBeHidden()

  // 口径注释（分诊结论 ≠ 投资建议；徽章 title 同源）
  await expect(page.getByTestId('event-verdict-caliber-note')).toHaveText(
    '研究标记，非投资建议'
  )

  // 三态徽章：文案 + data-verdict（前后端枚举口径一致）
  const badgeA = page.getByTestId(`event-verdict-badge-${a.id}`)
  await expect(badgeA).toHaveText('超预期')
  await expect(badgeA).toHaveAttribute('data-verdict', 'outperform')
  await expect(badgeA).toHaveAttribute('title', '研究标记，非投资建议')
  await expect(page.getByTestId(`event-verdict-badge-${b.id}`)).toHaveText('符合预期')
  await expect(page.getByTestId(`event-verdict-badge-${b.id}`)).toHaveAttribute(
    'data-verdict',
    'in_line'
  )
  await expect(page.getByTestId(`event-verdict-badge-${c.id}`)).toHaveText('风险')
  await expect(page.getByTestId(`event-verdict-badge-${c.id}`)).toHaveAttribute(
    'data-verdict',
    'risk'
  )

  // 来源标识 / 标的与名称 / 公告日 / 理由
  await expect(page.getByTestId(`event-source-badge-${a.id}`)).toHaveText('业绩预告')
  await expect(firstRow).toContainText('600519 贵州茅台')
  await expect(firstRow).toContainText('公告日 2025-04-15')
  await expect(firstRow).toContainText('理由：预增 60% ~ 90%，净利润大幅增长')

  // 来源相关要素卡（业绩预告三要素：报告期 / 预告类型 / 净利润变动幅度区间）
  await expect(page.getByTestId(`event-element-${a.id}-report_period`)).toContainText('2025-03-31')
  await expect(page.getByTestId(`event-element-${a.id}-forecast_type`)).toContainText('预增')
  await expect(page.getByTestId(`event-element-${a.id}-change`)).toContainText('60.0% ~ 90.0%')
  // 负区间如实带符号（不折算成正数）
  await expect(page.getByTestId(`event-element-${c.id}-change`)).toContainText('-40.0% ~ -20.0%')

  // 证据引用：field=value 逐条、全角分号连接（多引用）
  await expect(page.getByTestId(`event-evidence-${a.id}`)).toHaveText(
    '证据：change_low=60；forecast_type=预增'
  )
  await expect(page.getByTestId(`event-evidence-${c.id}`)).toHaveText('证据：change_high=-20')

  // 未发起分析：按钮可用且无队列徽标/会话行
  await expect(page.getByTestId(`event-analyze-btn-${a.id}`)).toBeVisible()
  await expect(page.getByTestId(`event-analyze-btn-${a.id}`)).toBeEnabled()
  await expect(page.getByTestId(`event-queue-badge-${a.id}`)).toBeHidden()
  await expect(page.getByTestId(`event-session-${a.id}`)).toBeHidden()
})

test('空态：events_reset 后 → 固定空态文案，不冒充数据', async ({ page }) => {
  // beforeEach 已 reset；本用例不 seed → 空清单
  await page.goto('/screening')
  // 首屏取数守卫落在数据依赖的空态文案上（容器是无条件外层 div，挂它等于空转）
  const empty = page.getByTestId('event-watchlist-empty')
  await expect(empty).toBeVisible({ timeout: 15_000 })
  await expect(empty).toHaveText('事件观察清单为空：暂无通过分诊的事件（等待日批抓取与分诊）')

  // 不冒充：无行、无徽章、无按钮、无加载中/错误态
  await expect(page.getByTestId(/^event-watchlist-row-/)).toHaveCount(0)
  await expect(page.getByTestId(/^event-verdict-badge-/)).toHaveCount(0)
  await expect(page.getByTestId(/^event-analyze-btn-/)).toHaveCount(0)
  await expect(page.getByTestId('event-watchlist-loading')).toBeHidden()
  await expect(page.getByTestId('event-watchlist-error')).toBeHidden()
})

test('发起深度分析 → 真实入队并即刻渲染队列徽标（复用既有队列）', async ({
  page,
  request,
  baseURL,
}) => {
  const items = await seedEvents(request, baseURL, {
    forecasts: [FORECAST_A],
    triage: [EVENTS_SEED.triage[0]],
    watchlist: [EVENTS_SEED.watchlist[0]],
  })
  const a = itemByCode(items, FORECAST_A.code)
  expect(a.status).toBe('pending')
  expect(a.session_id).toBeNull()

  await page.goto('/screening')
  const btn = page.getByTestId(`event-analyze-btn-${a.id}`)
  await expect(btn).toBeVisible({ timeout: 15_000 })
  await expect(btn).toBeEnabled()

  const analyzeResponse = page.waitForResponse(
    (resp) =>
      resp.url().includes(`/api/events/watchlist/${a.id}/analyze`) &&
      resp.request().method() === 'POST'
  )
  await btn.click()
  const analyzeResp = await analyzeResponse
  expect(analyzeResp.ok()).toBeTruthy()
  const payload = (await analyzeResp.json()) as {
    id: number
    session_id: string | null
    status: string | null
    queue_state: string | null
    already_started: boolean
  }
  // 首次发起：真建会话段并入队（非幂等短路路径）
  expect(payload.id).toBe(a.id)
  expect(payload.already_started).toBe(false)
  expect(payload.session_id).toBeTruthy()
  expect(payload.status).toBe('analyzing')
  expect(['queued', 'running']).toContain(payload.queue_state)

  // 入队即渲染徽标（并发上限 2、队列空 → 必有队列态）；文案随队列推进换态，
  // 故正则覆盖四态（断言方式沿 screening-page.spec「全量入队显示徽标」先例）
  const badge = page.getByTestId(`event-queue-badge-${a.id}`)
  await expect(badge).toBeVisible()
  await expect(badge).toContainText(/排队中|分析中|已完成|失败/)
  // 会话关联如实透出（后端标记 session_id）+ 已发起条目不重复提交
  await expect(page.getByTestId(`event-session-${a.id}`)).toContainText(
    payload.session_id as string
  )
  await expect(btn).toBeDisabled()
})
