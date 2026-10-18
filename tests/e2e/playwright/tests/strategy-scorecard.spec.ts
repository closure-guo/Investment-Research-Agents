import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * 策略族成绩单（delta add-strategy-family-scorecards）Task 6：track-record 专属套件 E2E 门禁。
 *
 * 红线：不 mock 业务接口——读数全部来自真实后端 GET /api/scorecards（前端区块
 * StrategyScorecardCard），造数经 TESTING=1 的 /api/test/seed 的 track_record
 * .predictions / .paper_equity 通道写入独立测试库（与生产落库/状态变更同源路径）。
 *
 * 套件序（本套件串行共享 data/test-e2e-track-record.db，无清库端点）：本文件名
 * 排在 track-record-* 之前，而 track-record-pred-tabs.spec.ts 依赖「观点日志为空」的
 * 空态前提（paper-broker.spec.ts 亦靠每例复位维持该前提）——故本 spec 每例
 * beforeEach/afterEach 均经 track_record_reset 全量复位，种子不跨例、不跨 spec 残留。
 *
 * 容量槽（断言 ③/⑥）前提如实：本 config 注入的 SNAPSHOT_DB_PATH
 * （data/test-e2e-snapshot.db）内无行情快照行（套件内无任何 bar 写入者），执行库亦无
 * 持仓——两族容量输入全缺 → 后端槽位 null + no_liquidity_data → 卡片按口径渲染
 * 「成交额数据缺失」。此处**不伪造成交额**，断言的就是「缺数据如实标注」这条语义。
 *
 * 事实核对（写断言前先跑真实后端探针取数，两族原始载荷见 task-6-report.md）：
 * 观点线：24 条已判定样本（23×+1.20%、1×−18.00%）→ 毛 = (23×0.012 − 0.18)/24 =
 *   +0.004 = +0.40%；净 = 毛 − 往返成本估算率 0.003（同源 config/matching_engine.json：
 *   佣金双边 0.0005 + 印花税 0.0005 + 中档滑点双边 0.002）= +0.10%；结算日 24 天 →
 *   回撤读数 −18.00%（极值样本落在末日，此前单边上涨 → 峰值后回撤即该样本）；
 *   极端样本表 1 行。种子里另有 1 条 open 观点：不计入样本（样本量 24 而非 25）。
 * 执行线：24 天净值 → 23 个相邻日收益样本（首点无前值不成样本）；22×+0.2%、1×−1%
 *   → 均值 = (22×0.002 − 0.01)/23 = +0.0015 = +0.15%；净值已含撮合费 → 净 = 毛（不二次
 *   扣减）；回撤 −1.00%，结算日 23 天；组合级阈值 −5% → 无极端样本。
 * 样本不足/空态：5 条已判定样本 < 门槛 10（期望值槽「样本不足」）+ 5 个结算日 < 门槛 20
 *   （回撤槽「结算日不足」）；全量复位后无样本 → 族空态「暂无样本」，四槽不渲染。
 */
test.describe.configure({ mode: 'serial' })

// 观点线样本：24 个已判定样本各占一个结算日（同日多样本会被日聚合平均，此处不需要）
const SAMPLE_DAYS = 24
const GAIN = 0.012 // 常规样本 +1.20%
const EXTREME_RET = -0.18 // 极端样本 −18.00%（≤ 注册表阈值 −15%）
const EXTREME_SYMBOL = '600030.SH'
const SYMBOLS = ['601398.SH', '600036.SH', '000858.SZ', '002594.SZ', '601288.SH'] as const

// 执行线样本：24 天净值曲线（首点不成样本 → 23 个日收益）
const PAPER_DAYS = 24
const PAPER_BASE = 100000
const PAPER_GAIN = 1.002 // 常规日 +0.2%
const PAPER_DROP = 0.99 // 第 13 天 −1%
const PAPER_DROP_INDEX = 12

const FIRST_DAY = Date.UTC(2026, 8, 1) // 2026-09-01 起连排 24 个自然日
const AS_OF = '2026-09-24' // 两族 as_of（最大结算日/净值日）

function isoDay(index: number): string {
  return new Date(FIRST_DAY + index * 86_400_000).toISOString().slice(0, 10)
}

function scorecardSeed(sampleDays: number = SAMPLE_DAYS) {
  const predictions: Record<string, unknown>[] = []
  for (let i = 0; i < sampleDays; i += 1) {
    const day = isoDay(i)
    const extreme = i === sampleDays - 1
    predictions.push({
      symbol: extreme ? EXTREME_SYMBOL : SYMBOLS[i % SYMBOLS.length],
      symbol_name: `种子股${i}`,
      direction: extreme ? 'short' : 'long',
      entry_price: 10 + i * 0.1,
      created_at: `${day}T09:30:00`,
      status: extreme ? 'resolved_loss' : 'resolved_win',
      resolution_rule: 'horizon',
      resolved_at: `${day}T15:00:00`,
      raw_return: extreme ? EXTREME_RET : GAIN,
      exit_price: extreme ? 8.2 : 10.12,
    })
  }
  // 1 条 open 观点：open/avoidance 等不进样本（样本量断言守护该过滤）
  predictions.push({
    symbol: '600000.SH',
    symbol_name: '种子股开放',
    direction: 'long',
    entry_price: 12.0,
    created_at: '2026-09-25T09:30:00',
  })

  const paperEquity: Record<string, unknown>[] = []
  let equity = PAPER_BASE
  for (let i = 0; i < PAPER_DAYS; i += 1) {
    if (i > 0) equity *= i === PAPER_DROP_INDEX ? PAPER_DROP : PAPER_GAIN
    // cash + positions_value = equity（自洽：本用例只消费 equity，其余列保持一致性）
    paperEquity.push({ trade_date: isoDay(i), cash: equity, positions_value: 0, equity })
  }
  return { predictions, paper_equity: paperEquity }
}

/** 全量复位战绩造数面（五张表 + paper_equity）；见 /api/test/seed 的 track_record_reset。 */
async function resetTrackRecord(request: APIRequestContext): Promise<void> {
  const resp = await request.post('/api/test/seed', { data: { track_record_reset: true } })
  expect(resp.ok()).toBeTruthy()
}

async function seedScorecards(
  request: APIRequestContext,
  sampleDays: number = SAMPLE_DAYS,
): Promise<void> {
  const resp = await request.post('/api/test/seed', {
    data: { track_record: scorecardSeed(sampleDays) },
  })
  expect(resp.ok()).toBeTruthy()
}

test.beforeEach(async ({ request }) => {
  await resetTrackRecord(request)
})

// 复位收尾：本文件排在 track-record-* 之前，否则种子会让 pred-tabs 的空态前提误红
test.afterEach(async ({ request }) => {
  await resetTrackRecord(request)
})

test.describe('战绩页:策略族成绩单（观点线/执行线四槽）', () => {
  test('观点线四槽渲染：毛/净与样本量、回撤、容量缺数据原因、极端样本明细', async ({
    page,
    request,
  }) => {
    await seedScorecards(request)
    await page.goto('/track-record')

    // ① 卡片与族切换控件（注册表序：观点线默认选中）
    const card = page.getByTestId('strategy-scorecard-card')
    await expect(card).toBeVisible()
    await expect(page.getByTestId('scorecard-family-tabs')).toBeVisible()
    await expect(page.getByTestId('scorecard-family-tab-predictions')).toHaveAttribute(
      'aria-selected',
      'true',
    )
    await expect(page.getByTestId('scorecard-family-tab-execution')).toHaveAttribute(
      'aria-selected',
      'false',
    )
    await expect(page.getByTestId('scorecard-as-of')).toHaveText(`样本截至 ${AS_OF}`)

    // ② 期望值：毛/净双读 + 样本量（种子里的 open 观点不计入 → 24 而非 25）
    const expectancy = page.getByTestId('scorecard-expectancy')
    await expect(expectancy).toContainText('毛 +0.40%')
    await expect(expectancy).toContainText('净 +0.10%')
    await expect(expectancy).toContainText('样本 24 条')

    // ③ 最大回撤：24 个结算日 → 出数值（−18.00%），非原因码
    const drawdown = page.getByTestId('scorecard-drawdown')
    await expect(drawdown).toContainText('-18.00%')
    await expect(drawdown).toContainText('24 个结算日')

    // ④ 容量：快照无成交额证据 → 原因文案（MUST NOT 折算 0 或留白）
    await expect(page.getByTestId('scorecard-capacity')).toContainText('成交额数据缺失')

    // ⑤ 极端样本：阈值 −15% 命中 1 条 + 明细表行（标的/结算日/收益）
    await expect(page.getByTestId('scorecard-extremes')).toContainText('1 条')
    const row = page.getByTestId(`scorecard-extreme-row-${EXTREME_SYMBOL}-${AS_OF}`)
    await expect(row).toBeVisible()
    await expect(row).toContainText(EXTREME_SYMBOL)
    await expect(row).toContainText(AS_OF)
    await expect(row).toContainText('-18.00%')

    // 口径注释随行展示（快照参与率/极端阈值口径可见）
    await expect(page.getByTestId('scorecard-caliber-note')).toContainText('极端样本阈值 -15%')
  })

  test('切换执行线 tab：净值派生读数渲染，毛=净不二次扣费，容量同样按原因文案', async ({
    page,
    request,
  }) => {
    await seedScorecards(request)
    await page.goto('/track-record')
    await expect(page.getByTestId('strategy-scorecard-card')).toBeVisible()

    await page.getByTestId('scorecard-family-tab-execution').click()
    await expect(page.getByTestId('scorecard-family-tab-execution')).toHaveAttribute(
      'aria-selected',
      'true',
    )
    await expect(page.getByTestId('scorecard-family-tab-predictions')).toHaveAttribute(
      'aria-selected',
      'false',
    )

    // 24 天净值 → 23 个日收益样本；均值 (22×0.002 − 0.01)/23 = +0.15%，净值已含费 → 净 = 毛
    const expectancy = page.getByTestId('scorecard-expectancy')
    await expect(expectancy).toContainText('样本 23 条')
    const parts = (await expectancy.innerText()).match(
      /毛 ([+-]\d+\.\d{2}%) · 净 ([+-]\d+\.\d{2}%)/,
    )
    expect(parts?.[1]).toBe('+0.15%')
    expect(parts?.[2]).toBe(parts?.[1])

    // 回撤：−1% 单日回撤 → 数值读数 + 23 个结算日
    const drawdown = page.getByTestId('scorecard-drawdown')
    await expect(drawdown).toContainText('-1.00%')
    await expect(drawdown).toContainText('23 个结算日')

    // 容量：执行库无持仓 + 快照无成交额 → 同为原因文案
    await expect(page.getByTestId('scorecard-capacity')).toContainText('成交额数据缺失')

    // 组合级阈值 −5%：−1% 日不构成极端样本；切换后观点线明细不残留
    await expect(page.getByTestId('scorecard-extremes')).toContainText('无')
    await expect(page.getByTestId(`scorecard-extreme-row-${EXTREME_SYMBOL}-${AS_OF}`)).toHaveCount(0)
    await expect(page.getByTestId('scorecard-extremes-table')).toHaveCount(0)
    await expect(page.getByTestId('scorecard-caliber-note')).toContainText('不二次扣减')
  })

  test('样本不足族展示「样本不足」原因文案，不折算 0 或留白', async ({ page, request }) => {
    // 5 条已判定样本 < 期望值门槛 10 → 期望值槽 insufficient_sample；
    // 5 个结算日 < 回撤门槛 20 → 回撤槽 insufficient_history（非空态、非错误态）
    await seedScorecards(request, 5)
    await page.goto('/track-record')

    await expect(page.getByTestId('strategy-scorecard-card')).toBeVisible()
    await expect(page.getByTestId('scorecard-insufficient')).toHaveText('样本不足')
    // 槽内不得出现折算后的 0 / 百分比读数（文案即读数替代）
    const expectancy = page.getByTestId('scorecard-expectancy')
    await expect(expectancy).not.toContainText('%')
    expect((await expectancy.innerText()).match(/\d/)).toBeNull()
    await expect(page.getByTestId('scorecard-drawdown')).toContainText('结算日不足')
    await expect(page.getByTestId('scorecard-capacity')).toContainText('成交额数据缺失')
    // 三态互斥：样本不足既不冒充族空态，也不冒充错误态
    await expect(page.getByTestId('scorecard-empty')).toHaveCount(0)
    await expect(page.getByTestId('scorecard-error')).toHaveCount(0)
  })

  test('无样本族展示空态文案，不渲染四槽冒充读数（非错误态）', async ({ page }) => {
    // beforeEach 已复位：观点线/执行线均无样本 → 默认选中族走空态
    await page.goto('/track-record')

    const card = page.getByTestId('strategy-scorecard-card')
    await expect(card).toBeVisible()
    const empty = page.getByTestId('scorecard-empty')
    await expect(empty).toBeVisible()
    await expect(empty).toHaveText('暂无样本')
    // 空态替代四槽（无数据即无读数，不留 0/空白槽位），也不冒充错误态
    await expect(page.getByTestId('scorecard-expectancy')).toHaveCount(0)
    await expect(page.getByTestId('scorecard-drawdown')).toHaveCount(0)
    await expect(page.getByTestId('scorecard-capacity')).toHaveCount(0)
    await expect(page.getByTestId('scorecard-extremes')).toHaveCount(0)
    await expect(page.getByTestId('scorecard-error')).toHaveCount(0)
    // 族切换控件仍在（注册表全族照列，空的是数据不是族）
    await expect(page.getByTestId('scorecard-family-tab-predictions')).toBeVisible()
    await expect(page.getByTestId('scorecard-family-tab-execution')).toBeVisible()
  })
})
