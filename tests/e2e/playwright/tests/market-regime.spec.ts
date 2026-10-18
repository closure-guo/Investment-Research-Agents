import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * add-crowding-regime-positioning T8：选股页市场温度计区块 E2E（默认套件，8000/5173；
 * 隔离端口对 8001/5174 的 playwright.isolated.config.ts 亦可运行——API 基址取自
 * config 的 baseURL，不硬编码端口）。
 *
 * 红线：不 mock /api/*——温度计行经 TESTING=1 的 /api/test/seed 真实写入 regime 库
 * （webServer env REGIME_DB_PATH=data/test-e2e-regime.db，与开发库 data/regime.db
 * 物理隔离），前端经真实 GET /api/regime 读取渲染。
 *
 * 用例串行（serial）：共享同一 regime 库，seed/reset 互相覆盖，并行会互踩；每例前后
 * 均 reset（`regime_reset`），避免遗留行让其他用例的空态前提失效（共享库泄漏）。
 * 本 spec 只动 regime 库，screening 池的并行造数/复位与温度计互不影响。
 *
 * 三例覆盖（spec frontend「选股页市场温度计区块」）：
 * 1. elevated：新鲜日期 + small_cap_share_pctile=0.85（≥0.8 阈值）→ 徽章「偏热」/
 *    总仓位系数 0.5 / 四指标卡（当日值 + 分位）/ 理由一行 / 无陈旧提示；
 * 2. stale：旧日期（age_days > 7）→ `regime-stale-notice` 可见且内容照常渲染
 *    （陈旧 ≠ 无数据，MUST NOT 隐藏读数）；
 * 3. 空态：`regime_reset` 后 → `regime-empty`「温度计未接入」文案，且徽章/系数/
 *    指标/理由 MUST NOT 出现（不以骨架屏或伪数据冒充）。
 *
 * 证伪自检（T8 交付要求）：临时改错一处期望值（如徽章期望文案）必须变红，改回变绿；
 * 两段输出进 task-8-report.md。
 */
test.describe.configure({ mode: 'serial', timeout: 60_000 })

// API 造数显式走 IPv4：Windows 上 localhost 可能先解析到 ::1，而 vite dev server
// 绑 0.0.0.0（IPv4），APIRequestContext 只试 ::1 时会 ECONNREFUSED（见
// screening-page.spec.ts 注）。基址取自 config baseURL → 默认/隔离套件通用。
function apiBase(baseURL: string | undefined): string {
  expect(baseURL, 'config use.baseURL 未配置').toBeTruthy()
  return (baseURL as string).replace('//localhost', '//127.0.0.1')
}

/** 本机本地日期（与后端 staleness 的 date.today() 同源），offset 天偏移 */
function localDate(offsetDays = 0): string {
  const d = new Date()
  d.setDate(d.getDate() + offsetDays)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// 行字段与 regime_daily DDL 列名逐字一致（含 cap_source 新列；缺省字段如实落 NULL）
function regimeRow(tradeDate: string, overrides: Record<string, unknown> = {}) {
  return {
    trade_date: tradeDate,
    small_cap_share: 0.078,
    up_count: 3120,
    down_count: 1800,
    flat_count: 96,
    limit_up_count: 45,
    limit_down_count: 8,
    total_amount: 8.0e11,
    small_cap_share_pctile: 0.85,
    total_amount_pctile: 0.55,
    computed_at: `${tradeDate}T15:35:00`,
    cap_source: 'spot',
    ...overrides,
  }
}

async function resetRegime(request: APIRequestContext, baseURL: string | undefined) {
  const resp = await request.post(`${apiBase(baseURL)}/api/test/seed`, {
    data: { regime_reset: true },
  })
  expect(resp.ok()).toBeTruthy()
}

async function seedRegime(
  request: APIRequestContext,
  baseURL: string | undefined,
  rows: Array<Record<string, unknown>>
) {
  const resp = await request.post(`${apiBase(baseURL)}/api/test/seed`, {
    data: { regime: rows },
  })
  expect(resp.ok()).toBeTruthy()
}

test.beforeEach(async ({ request, baseURL }) => {
  await resetRegime(request, baseURL)
})

test.afterEach(async ({ request, baseURL }) => {
  // 每例后复位（含最后一例）：不留行给同套件其他 spec 的温度计空态前提添乱
  await resetRegime(request, baseURL)
})

test('elevated：徽章/系数/指标卡/理由/曲线可见，无陈旧提示', async ({
  page,
  request,
  baseURL,
}) => {
  const today = localDate()
  // 三行历史（两条旧 + 今日），最新行触发 elevated（0.85 ∈ [0.8, 0.9)）
  await seedRegime(request, baseURL, [
    regimeRow(localDate(-2), { small_cap_share_pctile: 0.5, total_amount_pctile: 0.4 }),
    regimeRow(localDate(-1), { small_cap_share_pctile: 0.62, total_amount_pctile: 0.48 }),
    regimeRow(today),
  ])

  await page.goto('/screening')
  // 首屏取数在默认套件 8 并发 worker 下偶见 >5s（与 screening-page.spec 同款处置）
  await expect(page.getByTestId('market-regime-card')).toBeVisible({ timeout: 15_000 })

  // 三态徽章：elevated →「偏热」（语义色不在 E2E 断言范围，组件单测已钉）
  const badge = page.getByTestId('regime-state-badge')
  await expect(badge).toBeVisible()
  await expect(badge).toHaveAttribute('data-state', 'elevated')
  await expect(badge).toHaveText('偏热')

  // 总仓位系数（默认配置 elevated=0.5）+ 数据日期
  const multiplier = page.getByTestId('regime-multiplier')
  await expect(multiplier).toContainText('总仓位系数 0.5')
  await expect(page.getByTestId('market-regime-card')).toContainText(`as_of ${today}`)

  // 四指标卡：当日值 + 历史分位（触发指标取落库分位，如实展示不折算）
  const metrics = page.getByTestId('regime-metrics')
  await expect(metrics.getByTestId('regime-metric-small_cap_share')).toContainText('7.8%')
  await expect(metrics.getByTestId('regime-metric-small_cap_share')).toContainText('分位 85%')
  await expect(metrics.getByTestId('regime-metric-breadth')).toContainText('↑ 3120 / ↓ 1800')
  await expect(metrics.getByTestId('regime-metric-limit')).toContainText('涨停 45 / 跌停 8')
  await expect(metrics.getByTestId('regime-metric-amount')).toContainText('8000.00 亿元')
  await expect(metrics.getByTestId('regime-metric-amount')).toContainText('分位 55%')
  // Breadth 分位样本 < min_history（60）→ 如实置「—」，不编造
  await expect(metrics.getByTestId('regime-metric-breadth')).toContainText('涨分位 —')

  // 理由一行：触发读数 + Breadth 读数（judgment.reasons 原样透出）
  await expect(page.getByTestId('regime-reasons')).toContainText(
    'small_cap_share_pctile=0.85>=0.8'
  )
  await expect(page.getByTestId('regime-reasons')).toContainText('up_count=3120')

  // 新鲜日期（age_days=0）→ 无陈旧提示；history 有分位 → 曲线渲染（非积累期提示）
  await expect(page.getByTestId('regime-stale-notice')).toBeHidden()
  await expect(page.getByTestId('regime-chart')).toBeVisible()
  await expect(page.getByTestId('regime-chart-pending')).toBeHidden()
})

test('stale：旧日期 → 陈旧提示可见且内容照常渲染', async ({ page, request, baseURL }) => {
  // 固定旧日期（远早于任何运行日）→ age_days > 7 必陈旧；陈旧 ≠ 无数据
  const oldDate = '2020-01-03'
  await seedRegime(request, baseURL, [regimeRow(oldDate)])

  await page.goto('/screening')
  await expect(page.getByTestId('market-regime-card')).toBeVisible({ timeout: 15_000 })

  const notice = page.getByTestId('regime-stale-notice')
  await expect(notice).toBeVisible()
  await expect(notice).toContainText('数据已陈旧')
  await expect(notice).toContainText(oldDate)

  // 内容仍渲染（不因陈旧隐藏读数）：徽章 + 系数 + 指标 + 理由
  await expect(page.getByTestId('regime-state-badge')).toHaveText('偏热')
  await expect(page.getByTestId('regime-multiplier')).toContainText('总仓位系数 0.5')
  await expect(page.getByTestId('regime-metrics')).toBeVisible()
  await expect(page.getByTestId('regime-reasons')).toContainText('small_cap_share_pctile')
})

test('空态：regime_reset 后 → 未接入文案，不冒充数据', async ({ page }) => {
  // beforeEach 已 reset；本用例不 seed → missing 空态
  await page.goto('/screening')
  await expect(page.getByTestId('market-regime-card')).toBeVisible({ timeout: 15_000 })

  const empty = page.getByTestId('regime-empty')
  await expect(empty).toBeVisible()
  await expect(empty).toHaveText('温度计未接入：暂无市场状态数据（等待日批采集）')

  // 不冒充：徽章/系数/指标/理由/曲线/加载中/错误态均不出现
  await expect(page.getByTestId('regime-state-badge')).toBeHidden()
  await expect(page.getByTestId('regime-multiplier')).toBeHidden()
  await expect(page.getByTestId('regime-metrics')).toBeHidden()
  await expect(page.getByTestId('regime-reasons')).toBeHidden()
  await expect(page.getByTestId('regime-chart')).toBeHidden()
  await expect(page.getByTestId('regime-loading')).toBeHidden()
  await expect(page.getByTestId('regime-error')).toBeHidden()
})
