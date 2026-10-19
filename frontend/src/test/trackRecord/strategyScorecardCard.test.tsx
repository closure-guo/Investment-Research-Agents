import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { StrategyScorecardCard } from '../../pages/trackRecord/StrategyScorecardCard'

// 策略族成绩单契约（delta add-strategy-family-scorecards）。
// 载荷键名权威源 src/finance_agent/outcome/scorecards.py::build_family_payload：
// 槽位缺数据 = null + *_reason 原因码，MUST NOT 折算 0 或留白。
const PREDICTIONS_FAMILY = {
  family_id: 'predictions',
  name: '观点线',
  as_of: '2026-10-18',
  expectancy: { gross: 0.012, net: 0.009, n: 34, insufficient: false },
  max_drawdown: { value: -0.18, n_days: 41 },
  capacity: { suggested_capital: 1200000, min_adv20: 120000000, excluded: 2 },
  extreme_samples: [
    { ticker: '600519', date: '2026-09-30', ret: -0.22 },
    { ticker: '000001', date: '2026-10-08', ret: -0.16 },
  ],
  win_rate: 0.56,
  caliber_note: '毛=已判定观点 raw_return 均值；净=毛−往返成本估算率；极端样本阈值 -15%',
}

// 执行线：样本刚够但历史结算日不足、快照无成交额 → 槽位原因码与读数混排
const EXECUTION_FAMILY = {
  family_id: 'execution',
  name: '执行线',
  as_of: '2026-10-17',
  expectancy: { gross: 0.002, net: 0.002, n: 12, insufficient: false },
  max_drawdown: null,
  max_drawdown_reason: 'insufficient_history',
  capacity: null,
  capacity_reason: 'no_liquidity_data',
  extreme_samples: [],
  win_rate: null,
  caliber_note: '毛=净=组合级日收益均值（paper_equity 净值派生，已含撮合费，不二次扣减）',
}

// 族空数据（no_samples）：无任何样本 → 全槽 null + as_of null
const EMPTY_FAMILY = {
  family_id: 'execution',
  name: '执行线',
  as_of: null,
  expectancy: null,
  expectancy_reason: 'no_samples',
  max_drawdown: null,
  max_drawdown_reason: 'insufficient_history',
  capacity: null,
  capacity_reason: 'no_liquidity_data',
  extreme_samples: [],
  win_rate: null,
  caliber_note: '毛=净=组合级日收益均值（paper_equity 净值派生，已含撮合费，不二次扣减）',
}

// 样本不足（insufficient_sample）：有样本但 < 门槛 → 槽位 null，MUST NOT 折算 0
const INSUFFICIENT_FAMILY = {
  family_id: 'execution',
  name: '执行线',
  as_of: '2026-10-17',
  expectancy: null,
  expectancy_reason: 'insufficient_sample',
  max_drawdown: null,
  max_drawdown_reason: 'insufficient_history',
  capacity: null,
  capacity_reason: 'no_liquidity_data',
  extreme_samples: [],
  win_rate: null,
  caliber_note: '毛=净=组合级日收益均值（paper_equity 净值派生，已含撮合费，不二次扣减）',
}

function stubPayload(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('StrategyScorecardCard', () => {
  it('两族切换渲染四槽：期望值毛/净+样本量、回撤、容量、极端样本数与其明细表', async () => {
    const fetchMock = stubPayload({ families: [PREDICTIONS_FAMILY, EXECUTION_FAMILY] })
    const user = userEvent.setup()
    render(<StrategyScorecardCard />)
    await screen.findByTestId('scorecard-family-tabs')
    // 端点契约：GET /api/scorecards（相对路径，经 vite /api 代理到后端）
    expect(fetchMock).toHaveBeenCalledWith('/api/scorecards')
    // 族切换控件：注册表序（首族默认选中）
    const tabs = screen.getByTestId('scorecard-family-tabs')
    expect(within(tabs).getByTestId('scorecard-family-tab-predictions').textContent).toContain('观点线')
    expect(within(tabs).getByTestId('scorecard-family-tab-execution').textContent).toContain('执行线')

    // 槽① 期望值：毛/净双值 + 样本量
    const expectancy = screen.getByTestId('scorecard-expectancy')
    expect(expectancy.textContent).toContain('毛 +1.20%')
    expect(expectancy.textContent).toContain('净 +0.90%')
    expect(expectancy.textContent).toContain('样本 34')
    // 槽② 最大回撤：数值 + 结算日数
    expect(screen.getByTestId('scorecard-drawdown').textContent).toContain('-18.00%')
    expect(screen.getByTestId('scorecard-drawdown').textContent).toContain('41 个结算日')
    // 槽③ 容量：建议资金上限（最小日均成交额 × 参与率）+ 缺数据剔除数
    const capacity = screen.getByTestId('scorecard-capacity')
    expect(capacity.textContent).toContain('建议资金上限 ¥1,200,000')
    expect(capacity.textContent).toContain('¥120,000,000')
    expect(capacity.textContent).toContain('2 个标的缺成交额数据已剔除')
    // 槽④ 极端样本数 + 明细表（标的 / 结算日 / 收益）
    expect(screen.getByTestId('scorecard-extremes').textContent).toContain('2 条')
    const row = screen.getByTestId('scorecard-extreme-row-600519-2026-09-30')
    expect(row.textContent).toContain('600519')
    expect(row.textContent).toContain('2026-09-30')
    expect(row.textContent).toContain('-22.00%')
    // 口径注释随行展示
    expect(screen.getByTestId('scorecard-caliber-note').textContent).toContain('净=毛−往返成本估算率')
    // spec：胜率类字段 MUST NOT 占据区块主指标位置（四槽内不出现胜率读数）
    expect(expectancy.textContent).not.toContain('56')

    // 切换族：四槽整体换为新族读数，旧族明细不残留
    await user.click(screen.getByTestId('scorecard-family-tab-execution'))
    expect(screen.getByTestId('scorecard-expectancy').textContent).toContain('毛 +0.20%')
    expect(screen.getByTestId('scorecard-expectancy').textContent).toContain('样本 12')
    expect(screen.getByTestId('scorecard-drawdown').textContent).toContain('结算日不足')
    expect(screen.getByTestId('scorecard-capacity').textContent).toContain('成交额数据缺失')
    expect(screen.getByTestId('scorecard-extremes').textContent).toContain('无')
    expect(screen.queryByTestId('scorecard-extreme-row-600519-2026-09-30')).not.toBeInTheDocument()
    expect(screen.getByTestId('scorecard-caliber-note').textContent).toContain('已含撮合费')
  })

  it('insufficient_sample 槽位展示「样本不足」，无 0 值/空白冒充读数', async () => {
    stubPayload({ families: [INSUFFICIENT_FAMILY] })
    render(<StrategyScorecardCard />)
    expect((await screen.findByTestId('scorecard-insufficient')).textContent).toContain('样本不足')
    const expectancy = screen.getByTestId('scorecard-expectancy')
    // 槽内零数字：既不折算 0，也不留白（文案即读数替代）
    expect(expectancy.textContent).not.toMatch(/[0-9]/)
    expect(expectancy.textContent).not.toContain('%')
    // 其余缺数据槽位同样以原因码文案呈现，不显示读数
    const drawdown = screen.getByTestId('scorecard-drawdown')
    expect(drawdown.textContent).toContain('结算日不足')
    expect(drawdown.textContent).not.toContain('%')
    const capacity = screen.getByTestId('scorecard-capacity')
    expect(capacity.textContent).toContain('成交额数据缺失')
    expect(capacity.textContent).not.toContain('¥')
    // 极端样本槽：无条目如实「无」，不显示 0 条
    const extremes = screen.getByTestId('scorecard-extremes')
    expect(extremes.textContent).toContain('无')
    expect(extremes.textContent).not.toContain('0 条')
    // 三态互斥：样本不足既不冒充族空态，也不冒充错误态
    expect(screen.queryByTestId('scorecard-empty')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-error')).not.toBeInTheDocument()
  })

  it('族无样本（no_samples）切换至该族展示空态文案，不渲染四槽、不冒充错误态', async () => {
    stubPayload({ families: [PREDICTIONS_FAMILY, EMPTY_FAMILY] })
    const user = userEvent.setup()
    render(<StrategyScorecardCard />)
    await screen.findByTestId('scorecard-expectancy')
    await user.click(screen.getByTestId('scorecard-family-tab-execution'))
    const empty = await screen.findByTestId('scorecard-empty')
    expect(empty.textContent).toContain('暂无样本')
    // 空态替代四槽与明细表（无数据可渲染，不留 0/空白槽位冒充读数）
    expect(screen.queryByTestId('scorecard-expectancy')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-drawdown')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-capacity')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-extremes')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-extremes-table')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-error')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-insufficient')).not.toBeInTheDocument()
  })

  it('请求失败展示错误态，不渲染四槽/空态/样本不足态', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('boom')))
    render(<StrategyScorecardCard />)
    const error = await screen.findByTestId('scorecard-error')
    expect(error.textContent).toContain('成绩单加载失败')
    expect(screen.queryByTestId('scorecard-family-tabs')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-expectancy')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-empty')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-insufficient')).not.toBeInTheDocument()
  })

  it('HTTP 200 但载荷畸形（缺 families）走错误态，不崩溃、不渲染四槽', async () => {
    // 代理错误页等畸形 200 体：ok=true 但缺 families → 在 fetch 边界拦下，
    // 直通 setData 会在渲染期对 undefined 取 length 抛错炸整页
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ foo: 'bar' }) }))
    render(<StrategyScorecardCard />)
    expect((await screen.findByTestId('scorecard-error')).textContent).toContain('成绩单加载失败')
    expect(screen.queryByTestId('scorecard-expectancy')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scorecard-empty')).not.toBeInTheDocument()
  })
})
