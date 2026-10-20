import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * delta add-daily-ops-page T2.3：日批运维页 E2E 门禁（PageShellD 当日时间线 + 选股批次历史）。
 *
 * 红线：不 mock 业务接口——批次历史经 TESTING=1 的 /api/test/seed 真实写入 screening
 * 库，前端经真实 GET /api/screening/batches、GET /api/v1/ops/jobs 读取渲染。
 *
 * 用例串行（serial）：共享同一 screening 库，seed/screening_reset 互相覆盖，并行会
 * 互踩；每例前后均 reset（同 screening-page.spec / broker-console.spec 隔离纪律）。
 *
 * 时间线断言不依赖运行历史状态（共享 ops DB 的 job_runs 可能有历史行）：
 * 注册表 10 项与排程时刻来自后端 SCHEDULES（数据驱动载荷），运行态只断言
 * 「徽章 ∈ 五态」这一不变量；「补跑」用例允许 ok/failed 两个终态（integrity_check
 * 依赖 sessions 库表存在与否，沿 eval-ops-console.spec 手动补跑先例）。
 *
 * 运行命令（默认端口 8000/5173 被 Docker 生产栈占用时改跑 isolated config——
 * 该 config 已按 add-broker-console 先例注入 SCREENING_DB_PATH，
 * 否则 screening_reset 会清开发库 data/screening.db）：
 *   npx playwright test -c playwright.isolated.config.ts tests/daily-ops.spec.ts
 */
test.describe.configure({ mode: 'serial', timeout: 60_000 })

const OK_DATE = '2026-10-13'
const FAILED_DATE = '2026-10-14'

/**
 * 造数 API 基址：取 config ``use.baseURL`` 的 origin 并把 localhost 换成 127.0.0.1
 * （Windows 上 localhost 可能先解析到 ::1，而 vite 绑 IPv4；同 broker/screening spec 处置）。
 */
function apiBase(): string {
  const base = test.info().project.use.baseURL ?? 'http://localhost:5173'
  return base.replace('//localhost', '//127.0.0.1')
}

// 池行口径：consecutive_days 正整数、l3_verdict ∈ keep/drop、l3_reason 非空、
// factor_contribs 可 JSON 序列化（后端 _pool_row_payload fail loud 校验）
const OK_POOL = [
  {
    symbol: '600519',
    composite_score: 1.823,
    rank: 1,
    consecutive_days: 5,
    l3_verdict: 'keep',
    l3_reason: '无重大风险事件',
    factor_contribs: [],
  },
  {
    symbol: '000001',
    composite_score: 1.204,
    rank: 2,
    consecutive_days: 3,
    l3_verdict: 'keep',
    l3_reason: '无风险事件',
    factor_contribs: [],
  },
]

async function resetScreening(request: APIRequestContext) {
  const resp = await request.post(`${apiBase()}/api/test/seed`, { data: { screening_reset: true } })
  expect(resp.ok()).toBeTruthy()
}

/** 历史两态种子：一成功（含池行 → pool_size=2）一失败（错误逐字） */
async function seedBatchHistory(request: APIRequestContext) {
  const resp = await request.post(`${apiBase()}/api/test/seed`, {
    data: {
      screening: {
        batches: [
          {
            trade_date: OK_DATE,
            status: 'ok',
            started_at: `${OK_DATE}T15:40:00`,
            finished_at: `${OK_DATE}T15:43:00`,
            l1_passed: 20,
            l2_top: 9,
            l3_keep: 2,
            pool: OK_POOL,
          },
          {
            trade_date: FAILED_DATE,
            status: 'failed',
            error: 'RuntimeError: LLM 网关整体不可用',
            started_at: `${FAILED_DATE}T15:40:00`,
            finished_at: `${FAILED_DATE}T15:40:05`,
          },
        ],
      },
    },
  })
  expect(resp.ok()).toBeTruthy()
}

test.beforeEach(async ({ request }) => {
  await resetScreening(request)
})

test.afterEach(async ({ request }) => {
  await resetScreening(request)
})

test('当日时间线：注册表全部任务按排程时刻升序渲染，每项含五态徽章', async ({ page }) => {
  await page.goto('/ops')
  // 首屏取数在共享测试库/并发套件下偶见 >5s：显式放宽首屏断言超时（同 screening spec 处置）
  await expect(page.getByTestId('daily-ops-page')).toBeVisible({ timeout: 15_000 })

  // 批次卡 = 当日各批（任务载荷数据驱动）；数量 = 当前注册表规模
  // （spec 场景 GIVEN 10 个日批任务 + add-monthly-batch-scheduling 的 2 个月批任务
  //  + wire-builtin-factor-registration 的内置因子注册 + add-factor-harvest-bridge
  //  的因子收割 + add-crowding-regime-positioning 的拥挤度日批
  //  + add-event-driven-lane 的事件线三件（16:05/16:15/16:25）
  //  + add-microcap-lane-guardrails 的小票通道（16:45）
  //  + add-convertible-bond-coverage 的转债日批（16:50）
  //  + add-research-report-dataplane 的研报数据面四 job（16:52/16:54/16:56/16:58）
  //  + add-auto-pool-analysis-daily-brief 的自动选股分析（16:22）与每日汇报（20:30）= 26）
  const items = page.locator('[data-testid^="page-shell-batch-"]')
  await expect(items).toHaveCount(26)

  // 排程升序（数据驱动）：09:00/10:00 月批 → 15:30 行情快照 → 15:35 拥挤度 →
  // 15:40 选股 → 15:45 因子衰减监控 → 15:50 内置因子注册 → 15:55 因子收割 →
  // 16:00 判定 → 16:05 预告抓取 → 16:10 仿真执行 → 16:15 预告分诊 → 16:20 paper 结算
  // → 16:22 自动选股分析 → 16:25 事件结算 → … → 16:45 小票通道 → 16:50 转债日批 →
  // 16:52 研报元数据 → 16:54 评级变动 → 16:56 一致预期快照 → 16:58 研报投喂 → 18:00 cohort
  // → 20:30 每日汇报
  await expect(items.nth(0)).toContainText('每月 1 日 09:00')
  await expect(items.nth(0)).toContainText('记忆提炼')
  await expect(items.nth(1)).toContainText('每月 1 日 10:00')
  await expect(items.nth(1)).toContainText('因子提案')
  await expect(items.nth(2)).toContainText('15:30')
  await expect(items.nth(2)).toContainText('行情快照')
  await expect(items.nth(3)).toContainText('15:35')
  await expect(items.nth(3)).toContainText('市场拥挤度')
  await expect(items.nth(4)).toContainText('15:40')
  await expect(items.nth(4)).toContainText('选股')
  await expect(items.nth(5)).toContainText('15:45')
  await expect(items.nth(5)).toContainText('因子衰减监控')
  await expect(items.nth(6)).toContainText('15:50')
  await expect(items.nth(6)).toContainText('内置因子注册')
  await expect(items.nth(7)).toContainText('15:55')
  await expect(items.nth(7)).toContainText('因子收割')
  await expect(items.nth(9)).toContainText('16:05')
  await expect(items.nth(9)).toContainText('业绩预告抓取')
  await expect(items.nth(11)).toContainText('16:15')
  await expect(items.nth(11)).toContainText('预告分诊')
  await expect(items.nth(13)).toContainText('16:22')
  await expect(items.nth(13)).toContainText('自动选股分析')
  await expect(items.nth(14)).toContainText('16:25')
  await expect(items.nth(14)).toContainText('事件结算')
  await expect(items.nth(18)).toContainText('16:45')
  await expect(items.nth(18)).toContainText('小票通道')
  await expect(items.nth(19)).toContainText('16:50')
  await expect(items.nth(19)).toContainText('转债日批')
  await expect(items.nth(20)).toContainText('16:52')
  await expect(items.nth(20)).toContainText('研报元数据')
  await expect(items.nth(21)).toContainText('16:54')
  await expect(items.nth(21)).toContainText('评级变动')
  await expect(items.nth(22)).toContainText('16:56')
  await expect(items.nth(22)).toContainText('一致预期快照')
  await expect(items.nth(23)).toContainText('16:58')
  await expect(items.nth(23)).toContainText('研报投喂')
  await expect(items.nth(24)).toContainText('18:00')
  await expect(items.nth(24)).toContainText('cohort 跑批')
  await expect(items.nth(25)).toContainText('20:30')
  await expect(items.nth(25)).toContainText('每日汇报')

  // 每项状态徽章 ∈ 五态（成功/失败/未跑/运行中/跳过；运行历史不由本 spec 控制，只断言枚举不变量）
  for (let index = 0; index < 26; index += 1) {
    const badge = items.nth(index).getByTestId('status-badge')
    await expect(badge).toHaveAttribute('data-status', /^(active|failed|retired|running|degraded)$/)
    await expect(badge).toHaveText(/^(成功|失败|未跑|运行中|跳过)$/)
  }

  // 调度器未运行（TESTING=1 恒真）显式披露，不冒充「一切正常」
  await expect(page.getByTestId('ops-scheduler-note')).toContainText('调度器未运行')
})

test('选股批次历史：种子两态 → 两行日期倒序（成功含计数组，失败含原因逐字）', async ({ page, request }) => {
  await seedBatchHistory(request)

  await page.goto('/ops')
  await expect(page.getByTestId('ops-batches-table')).toBeVisible({ timeout: 15_000 })

  const rows = page.getByTestId(/^ops-batch-row-/)
  await expect(rows).toHaveCount(2)

  // 日期倒序：2026-10-14（失败）在前，2026-10-13（成功）在后
  const failedRow = page.getByTestId(`ops-batch-row-${FAILED_DATE}`)
  const okRow = page.getByTestId(`ops-batch-row-${OK_DATE}`)
  await expect(rows.nth(0)).toHaveAttribute('data-testid', `ops-batch-row-${FAILED_DATE}`)
  await expect(rows.nth(1)).toHaveAttribute('data-testid', `ops-batch-row-${OK_DATE}`)

  // 失败行：failed 徽章 + 错误原因逐字
  await expect(failedRow.getByTestId('status-badge')).toHaveAttribute('data-status', 'failed')
  await expect(failedRow.getByTestId(`ops-batch-error-${FAILED_DATE}`)).toHaveText(
    'RuntimeError: LLM 网关整体不可用'
  )

  // 成功行：active 徽章 + 各层计数与池数（l1=20 / l2=9 / l3=2 / pool_size=2）
  await expect(okRow.getByTestId('status-badge')).toHaveAttribute('data-status', 'active')
  await expect(okRow).toContainText('20')
  await expect(okRow).toContainText('9')
  await expect(okRow).toContainText('2')
})

test('无批次历史 → 区块空态如实（不以空表冒充）', async ({ page }) => {
  // before 钩子已 reset：screening 五表为空
  await page.goto('/ops')
  await expect(page.getByTestId('daily-ops-page')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('ops-batches-empty')).toContainText('暂无选股批次历史')
  await expect(page.getByTestId('ops-batches-table')).toHaveCount(0)
})

test('「评估运维」跳转进入设置中心评估运维分区（全量控制台）', async ({ page }) => {
  await page.goto('/ops')
  await expect(page.getByTestId('ops-open-eval-ops')).toBeVisible({ timeout: 15_000 })

  await page.getByTestId('ops-open-eval-ops').click()

  await expect(page).toHaveURL(/\/settings$/)
  await expect(page.getByTestId('eval-ops-pane')).toBeVisible({ timeout: 30_000 })
})

test('一键补跑：走既有补跑通道并按端点返回如实收敛（终态徽章或拒绝原因）', async ({ page }) => {
  await page.goto('/ops')
  const runButton = page.getByTestId('ops-job-run-integrity_check')
  await expect(runButton).toBeVisible({ timeout: 15_000 })

  const responsePromise = page.waitForResponse(
    (resp) => resp.url().includes('/api/v1/ops/jobs/integrity_check/run') && resp.request().method() === 'POST'
  )
  await runButton.click()
  const resp = await responsePromise

  // 两态都合法（integrity_check 依赖 sessions 库表存在；本 spec 不造 track_record 表），
  // 但界面必须如实：202 → 该任务今日已有终态运行行（成功/失败徽章）；非 2xx → 原因逐字可见。
  if (resp.status() === 202) {
    const badge = page.getByTestId('page-shell-batch-integrity_check').getByTestId('status-badge')
    await expect(badge).toHaveAttribute('data-status', /^(active|failed)$/, { timeout: 15_000 })
    await expect(badge).toHaveText(/^(成功|失败)$/)
  } else {
    expect(resp.status()).toBeGreaterThanOrEqual(400)
    await expect(page.getByTestId('ops-job-notice-integrity_check')).toBeVisible({ timeout: 15_000 })
  }
})

test('每日汇报：种子后展示日期/模型/Markdown（add-auto-pool-analysis-daily-brief）', async ({ page, request }) => {
  const seed = await request.post(`${apiBase()}/api/test/seed`, {
    data: {
      daily_brief: {
        date: '2026-10-20',
        status: 'ok',
        markdown: '## 今日结论\n精选池 0 只，今日 0 笔买入。',
        model: 'openai/deepseek-v4.1-flash',
      },
    },
  })
  expect(seed.status()).toBe(200)

  await page.goto('/ops')
  await expect(page.getByTestId('daily-brief')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('daily-brief-date')).toHaveText('2026-10-20')
  await expect(page.getByTestId('daily-brief-model')).toContainText('deepseek-v4.1-flash')
  await expect(page.getByTestId('daily-brief-markdown')).toContainText('今日结论')
})
