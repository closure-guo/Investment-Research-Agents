import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * add-screening-pipeline T5：选股页 E2E 门禁（默认 config，8000/5173）。
 *
 * 红线：不 mock 业务接口——批次/池经 TESTING=1 的 /api/test/seed 真实写入
 * screening 库（playwright.config.ts webServer env
 * SCREENING_DB_PATH=data/test-e2e-screening.db，与开发库隔离），前端经真实
 * GET /api/screening/pool 读取渲染。
 *
 * 用例串行（serial）：共享同一 screening 库，seed/reset 互相覆盖，并行会互踩；
 * 每例前后均 reset，避免遗留池行让其他用例的空态前提失效（共享库泄漏）。
 *
 * 分析点击用例的确定性前提：E2E 快照库（data/test-e2e-snapshot.db）无
 * market_snapshot 行（无任何 spec 造快照行情行）→ analyze 端点的时效性校验
 * 以「无最新可得状态」跳过 → 池内标的全部入队（徽标可见）。该前提**已在本用例内
 * 显式断言**：捕获真实 POST /api/screening/analyze 响应并断言 removed === []。
 * 若未来 spec/脚本写入池日期快照行（校验会按最新快照移除不在行内/命中新事件的
 * 标的），本用例以「前提失效」显式失败，而不是徽标不可见的超时误读。
 * （未选「seed 干净快照行」方案：那会让校验真正运行并触发 fetch_key_events 的
 * 真实 WebSearch/litellm 网络调用——前提更脆、更慢，故取显式断言方案。）
 */
test.describe.configure({ mode: 'serial', timeout: 60_000 })

const BATCH_DATE = '2026-10-13'
const FAILED_DATE = '2026-10-14'

// API 造数显式走 IPv4：Windows 上 localhost 可能先解析到 ::1，而 vite dev server
// 绑 0.0.0.0（IPv4），APIRequestContext 只试 ::1 时会 ECONNREFUSED（首轮实测偶发）
const API_BASE = 'http://127.0.0.1:5173'

// 池行口径：consecutive_days 正整数、l3_verdict ∈ keep/drop、l3_reason 非空、
// factor_contribs 可 JSON 序列化（后端 _pool_row_payload fail loud 校验）
const POOL = [
  {
    symbol: '600519',
    composite_score: 1.823,
    rank: 1,
    consecutive_days: 5,
    l3_verdict: 'keep',
    l3_reason: '无重大风险事件',
    factor_contribs: [
      { factor_id: 7, zscore: 0.4, weight: 0.5, contribution: 0.11 },
      { factor_id: 3, zscore: -1.2, weight: 0.6, contribution: -0.83 },
      { factor_id: 11, zscore: 0.9, weight: 0.4, contribution: 0.42 },
    ],
  },
  {
    symbol: '000001',
    composite_score: 1.204,
    rank: 2,
    consecutive_days: 3,
    l3_verdict: 'keep',
    l3_reason: '无风险事件',
    factor_contribs: [{ factor_id: 5, zscore: 0.2, weight: 0.3, contribution: 0.2 }],
  },
]

async function resetScreening(request: APIRequestContext) {
  const resp = await request.post(`${API_BASE}/api/test/seed`, { data: { screening_reset: true } })
  expect(resp.ok()).toBeTruthy()
}

async function seedPool(request: APIRequestContext) {
  const resp = await request.post(`${API_BASE}/api/test/seed`, {
    data: {
      screening: {
        batch: {
          trade_date: BATCH_DATE,
          status: 'ok',
          started_at: `${BATCH_DATE}T15:30:00`,
          finished_at: `${BATCH_DATE}T15:33:00`,
          l1_passed: 20,
          l2_top: 9,
          l3_keep: 2,
        },
        pool: POOL,
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

test.afterAll(async ({ request }) => {
  await resetScreening(request)
})

test('种子池 → 表格可见（含 L3 理由、连续在榜天数、批次日期）', async ({ page, request }) => {
  await seedPool(request)

  await page.goto('/screening')
  // 首屏取数在 8 并发 worker 的默认套件下偶见 >5s（单进程后端 + 全量并行）：
  // 显式放宽首屏断言超时（与 snapshot-banner.spec 同款处置），不掩盖真实故障
  await expect(page.getByTestId('screening-pool-table')).toBeVisible({ timeout: 15_000 })

  const row = page.getByTestId('screening-row-600519')
  await expect(row).toContainText('600519')
  await expect(row).toContainText('1.823')
  await expect(row).toContainText('无重大风险事件')
  await expect(row).toContainText('5') // 连续在榜天数
  await expect(row).toContainText(BATCH_DATE) // 批次日期列

  await expect(page.getByTestId('screening-row-000001')).toContainText('无风险事件')
})

test('hover 综合分 → 浮层展示因子贡献（|contribution| 降序）', async ({ page, request }) => {
  await seedPool(request)

  await page.goto('/screening')
  // 首屏取数在 8 并发 worker 的默认套件下偶见 >5s（单进程后端 + 全量并行）：
  // 显式放宽首屏断言超时（与 snapshot-banner.spec 同款处置），不掩盖真实故障
  await expect(page.getByTestId('screening-pool-table')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('screening-contribs-tooltip')).toBeHidden()

  await page.getByTestId('screening-score-600519').hover()
  const tooltip = page.getByTestId('screening-contribs-tooltip')
  await expect(tooltip).toBeVisible()
  await expect(tooltip).toContainText('-0.8300')
  await expect(tooltip).toContainText('0.4200')

  // 降序：|−0.83| > |0.42| > |0.11| → 3 → 11 → 7（nth 断言为 web-first 自动重试）
  const items = tooltip.getByTestId(/^screening-contrib-/)
  await expect(items).toHaveCount(3)
  await expect(items.nth(0)).toHaveAttribute('data-testid', 'screening-contrib-3')
  await expect(items.nth(1)).toHaveAttribute('data-testid', 'screening-contrib-11')
  await expect(items.nth(2)).toHaveAttribute('data-testid', 'screening-contrib-7')
})

test('批次失败 → 横幅可见且标注最近成功批次日期（池仍展示）', async ({ page, request }) => {
  await seedPool(request)
  const failed = await request.post(`${API_BASE}/api/test/seed`, {
    data: {
      screening: {
        batch: {
          trade_date: FAILED_DATE,
          status: 'failed',
          error: 'SnapshotNotFreshError: 快照最新数据日期为 2026-10-09',
          started_at: `${FAILED_DATE}T15:30:00`,
          finished_at: `${FAILED_DATE}T15:30:01`,
        },
      },
    },
  })
  expect(failed.ok()).toBeTruthy()

  await page.goto('/screening')
  const banner = page.getByTestId('screening-batch-stale-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('选股批次失败')
  await expect(banner).toContainText(BATCH_DATE) // 最近成功日期（非失败日）
  // 首屏取数在 8 并发 worker 的默认套件下偶见 >5s（单进程后端 + 全量并行）：
  // 显式放宽首屏断言超时（与 snapshot-banner.spec 同款处置），不掩盖真实故障
  await expect(page.getByTestId('screening-pool-table')).toBeVisible({ timeout: 15_000 })
})

test('无成功批次 → 空态 + 分析按钮禁用', async ({ page }) => {
  await page.goto('/screening')

  await expect(page.getByTestId('screening-empty')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('screening-pool-table')).toBeHidden()
  await expect(page.getByTestId('screening-analyze-btn')).toBeDisabled()
})

test('点击批量分析 → 池内标的全量入队，显示排队/运行徽标', async ({ page, request }) => {
  await seedPool(request)

  await page.goto('/screening')
  // 首屏取数在 8 并发 worker 的默认套件下偶见 >5s（单进程后端 + 全量并行）：
  // 显式放宽首屏断言超时（与 snapshot-banner.spec 同款处置），不掩盖真实故障
  await expect(page.getByTestId('screening-pool-table')).toBeVisible({ timeout: 15_000 })

  await expect(page.getByTestId('screening-analyze-btn')).toBeEnabled()
  // 显式断言前提（见文件头「分析点击用例的确定性前提」）：校验若运行并移除标的，
  // removed 非空 → 此处以「前提失效」显式失败，而非徽标不可见的超时误读
  const analyzeResponse = page.waitForResponse(
    (resp) => resp.url().includes('/api/screening/analyze') && resp.request().method() === 'POST',
  )
  await page.getByTestId('screening-analyze-btn').click()
  const analyzeResp = await analyzeResponse
  expect(analyzeResp.ok()).toBeTruthy()
  const payload = (await analyzeResp.json()) as {
    started: { symbol: string }[]
    queued: { symbol: string }[]
    removed: unknown[]
  }
  expect(payload.removed).toEqual([])
  expect([...payload.started, ...payload.queued].map((item) => item.symbol).sort()).toEqual([
    '000001',
    '600519',
  ])

  // 入队即渲染徽标（并发上限 2：2 只标的均为 running/queued，必有徽标）
  await expect(page.getByTestId('screening-queue-badge-600519')).toBeVisible()
  await expect(page.getByTestId('screening-queue-badge-600519')).toContainText(/分析中|排队中/)
  await expect(page.getByTestId('screening-queue-badge-000001')).toBeVisible()
})
