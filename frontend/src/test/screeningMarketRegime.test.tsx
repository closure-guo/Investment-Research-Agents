import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MarketRegimeCard } from '../pages/screening/MarketRegimeCard'
import { ScreeningPage } from '../pages/screening/ScreeningPage'
import type {
  RegimeAvailablePayload,
  RegimeHistoryRow,
  RegimeIndicators,
  RegimePayload,
  ScreeningPoolResponse,
} from '../types'

/**
 * 选股页市场温度计区块单测（delta add-crowding-regime-positioning T5）。
 *
 * 契约（testid 见任务简报）：
 * - 三态徽章（normal/elevated/danger 语义色）+ 总仓位系数 + 四指标（当日值/分位）+ 理由；
 * - history 非空 → 近一年曲线（reuse ReactECharts + getChartTheme）；空 → 不渲染图表；
 * - stale=true → 「数据已陈旧」+ as_of，内容仍可见；
 * - available=false → 「温度计未接入」空态，按 missing/corrupt 区分文案，
 *   MUST NOT 用骨架屏/错误提示冒充（无 badge/metrics/chart/reasons）；
 * - 只读：区块内无任何按钮/开关/输入控件（不可切换状态或覆盖系数）；
 * - 挂载：ScreeningPage 滚动容器首子节点（在 stale 横幅之前）。
 *
 * ECharts 以 option 捕获替身断言（jsdom 不渲染 canvas，同 brokerConsolePage 先例）。
 */

const capturedOptions: unknown[] = []
vi.mock('echarts-for-react', () => ({
  default: ({ option }: { option: unknown }) => {
    capturedOptions.push(option)
    return null
  },
}))

const BASE_INDICATORS: RegimeIndicators = {
  small_cap_share: { value: 0.315, pctile: 0.93 },
  up_count: { value: 3120, pctile: 0.62 },
  down_count: { value: 1800, pctile: 0.31 },
  limit_up_count: { value: 45, pctile: 0.78 },
  limit_down_count: { value: 8, pctile: 0.12 },
  total_amount: { value: 125_000_000, pctile: 0.95 },
}

function historyRow(
  trade_date: string,
  smallCapPctile: number | null,
  amountPctile: number | null
): RegimeHistoryRow {
  return {
    trade_date,
    small_cap_share: 0.3,
    up_count: 3000,
    down_count: 2000,
    flat_count: 100,
    limit_up_count: 40,
    limit_down_count: 10,
    total_amount: 120_000_000,
    small_cap_share_pctile: smallCapPctile,
    total_amount_pctile: amountPctile,
    computed_at: `${trade_date}T15:30:00`,
  }
}

const HISTORY: RegimeHistoryRow[] = [
  historyRow('2026-10-14', 0.55, 0.48),
  historyRow('2026-10-15', 0.71, 0.66),
  historyRow('2026-10-16', 0.93, 0.95),
]

function availablePayload(
  overrides: Partial<RegimeAvailablePayload> = {}
): RegimeAvailablePayload {
  return {
    available: true,
    as_of: '2026-10-16',
    state: 'normal',
    multiplier: 1,
    reasons: ['small_cap_share_pctile=0.93>=0.9'],
    indicators: BASE_INDICATORS,
    history: HISTORY,
    age_days: 1,
    stale: false,
    ...overrides,
  }
}

function poolBody(overrides: Partial<ScreeningPoolResponse> = {}): ScreeningPoolResponse {
  return {
    batch_date: '2026-10-16',
    batch_status: 'ok',
    stale: false,
    selection_mode: 'factor',
    notice: null,
    factor_signal: {
      selection_mode: 'factor',
      reason: '全部 active 因子 IC 样本达标',
      min_samples: 10,
      expected_factor_mode_date: null,
      factors: [],
    },
    pool: [
      {
        symbol: '600519',
        composite_score: 1.823,
        rank: 1,
        consecutive_days: 5,
        l3_verdict: 'keep',
        l3_reason: '无重大风险事件',
        factor_contribs: [],
      },
    ],
    queue: [],
    ...overrides,
  }
}

function mockFetch(regime: RegimePayload, pool: ScreeningPoolResponse = poolBody()) {
  const respond = (body: unknown, status = 200) =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })
    )
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/api/regime') return respond(regime)
    if (url === '/api/screening/pool') return respond(pool)
    if (url === '/api/sessions') return respond({ sessions: [] })
    if (url === '/api/snapshot/status') {
      return respond({
        latest_date: null,
        stale: false,
        message: null,
        last_error: null,
        last_error_date: null,
      })
    }
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => {
  capturedOptions.length = 0
  localStorage.clear()
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('MarketRegimeCard 三态与内容', () => {
  it.each([
    ['normal', 1, '正常', '--status-success-default'],
    ['elevated', 0.5, '偏热', '--status-warning-default'],
    ['danger', 0, '过热', '--status-error-default'],
  ] as const)(
    'state=%s → 徽章语义色 + 系数 %s + 四指标 + 理由',
    async (state, multiplier, label, colorVar) => {
      mockFetch(availablePayload({ state, multiplier }))
      render(<MarketRegimeCard />)

      const badge = await screen.findByTestId('regime-state-badge')
      expect(badge.getAttribute('data-state')).toBe(state)
      expect(badge.textContent).toContain(label)
      expect(badge.getAttribute('style')).toContain(colorVar)

      const multiplierNode = screen.getByTestId('regime-multiplier')
      expect(multiplierNode.textContent).toContain(String(multiplier))
      expect(multiplierNode.textContent).toContain('总仓位系数')

      const metrics = screen.getByTestId('regime-metrics')
      expect(within(metrics).getByText('小市值组成交额占比')).toBeTruthy()
      expect(within(metrics).getByText('涨跌家数')).toBeTruthy()
      expect(within(metrics).getByText('涨停跌停家数')).toBeTruthy()
      expect(within(metrics).getByText('全市场成交额')).toBeTruthy()
      // 当日值 + 历史分位都要出现（不折算、不省略）
      expect(metrics.textContent).toContain('31.5%')
      expect(metrics.textContent).toContain('分位 93%')
      expect(metrics.textContent).toContain('↑ 3120 / ↓ 1800')
      expect(metrics.textContent).toContain('涨分位 62% / 跌分位 31%')
      expect(metrics.textContent).toContain('涨停 45 / 跌停 8')
      expect(metrics.textContent).toContain('涨停分位 78% / 跌停分位 12%')
      expect(metrics.textContent).toContain('1.25 亿元')
      expect(metrics.textContent).toContain('分位 95%')

      const reasons = screen.getByTestId('regime-reasons')
      expect(reasons.textContent).toContain('small_cap_share_pctile=0.93>=0.9')
    }
  )
})

describe('MarketRegimeCard 曲线', () => {
  it('history 非空 → 渲染近一年曲线（升序日期 + 两触发指标分位序列）', async () => {
    mockFetch(availablePayload())
    render(<MarketRegimeCard />)

    expect(await screen.findByTestId('regime-chart')).toBeTruthy()
    expect(capturedOptions).toHaveLength(1)
    const option = capturedOptions[0] as {
      xAxis: { data: string[] }
      series: Array<{ name: string; data: Array<number | null> }>
    }
    expect(option.xAxis.data).toEqual(['2026-10-14', '2026-10-15', '2026-10-16'])
    expect(option.series.map(series => series.name)).toEqual(['小市值占比分位', '成交额分位'])
    expect(option.series[0].data).toEqual([0.55, 0.71, 0.93])
    expect(option.series[1].data).toEqual([0.48, 0.66, 0.95])
  })

  it('history 为空 → 不渲染图表（空态），指标仍可见', async () => {
    mockFetch(availablePayload({ history: [] }))
    render(<MarketRegimeCard />)

    expect(await screen.findByTestId('regime-metrics')).toBeTruthy()
    expect(screen.queryByTestId('regime-chart')).toBeNull()
    expect(capturedOptions).toHaveLength(0)
  })

  it('history 全 null（分位未积累期）→ 不画空坐标系，渲染积累中提示', async () => {
    mockFetch(
      availablePayload({
        history: [
          historyRow('2026-10-14', null, null),
          historyRow('2026-10-15', null, null),
          historyRow('2026-10-16', null, null),
        ],
      })
    )
    render(<MarketRegimeCard />)

    const pending = await screen.findByTestId('regime-chart-pending')
    expect(pending.textContent).toContain('分位样本积累中')
    expect(pending.textContent).toContain('60')
    expect(screen.queryByTestId('regime-chart')).toBeNull()
    expect(capturedOptions).toHaveLength(0)
    // 未积累 ≠ 无数据：指标/理由照常可见
    expect(screen.getByTestId('regime-metrics')).toBeTruthy()
    expect(screen.getByTestId('regime-reasons')).toBeTruthy()
  })

  it('部分分位可用（一条序列全 null）→ 照常渲染曲线，无误导性积累提示', async () => {
    mockFetch(
      availablePayload({
        history: [historyRow('2026-10-15', null, 0.66), historyRow('2026-10-16', null, 0.95)],
      })
    )
    render(<MarketRegimeCard />)

    expect(await screen.findByTestId('regime-chart')).toBeTruthy()
    expect(screen.queryByTestId('regime-chart-pending')).toBeNull()
    expect(capturedOptions).toHaveLength(1)
    const option = capturedOptions[0] as {
      series: Array<{ name: string; data: Array<number | null> }>
    }
    expect(option.series[0].data).toEqual([null, null])
    expect(option.series[1].data).toEqual([0.66, 0.95])
  })
})

describe('MarketRegimeCard 陈旧披露', () => {
  it('stale=true → 明示数据已陈旧 + as_of，内容仍可见', async () => {
    mockFetch(
      availablePayload({ stale: true, as_of: '2026-10-06', age_days: 10 })
    )
    render(<MarketRegimeCard />)

    const notice = await screen.findByTestId('regime-stale-notice')
    expect(notice.textContent).toContain('数据已陈旧')
    expect(notice.textContent).toContain('2026-10-06')
    expect(notice.textContent).toContain('10 天')

    // 陈旧 ≠ 隐藏：徽章/指标/曲线照常展示
    expect(screen.getByTestId('regime-state-badge')).toBeTruthy()
    expect(screen.getByTestId('regime-metrics')).toBeTruthy()
    expect(screen.getByTestId('regime-chart')).toBeTruthy()
  })

  it('stale=false → 无陈旧提示（不无端告警）', async () => {
    mockFetch(availablePayload({ stale: false }))
    render(<MarketRegimeCard />)

    expect(await screen.findByTestId('regime-multiplier')).toBeTruthy()
    expect(screen.queryByTestId('regime-stale-notice')).toBeNull()
  })
})

describe('MarketRegimeCard 空态（available=false）', () => {
  it('reason=missing → 「温度计未接入」空态，无骨架屏/错误提示冒充', async () => {
    mockFetch({ available: false, reason: 'missing' })
    render(<MarketRegimeCard />)

    const empty = await screen.findByTestId('regime-empty')
    expect(empty.textContent).toContain('温度计未接入')
    expect(empty.textContent).not.toContain('损坏')
    expect(screen.queryByTestId('regime-state-badge')).toBeNull()
    expect(screen.queryByTestId('regime-multiplier')).toBeNull()
    expect(screen.queryByTestId('regime-metrics')).toBeNull()
    expect(screen.queryByTestId('regime-chart')).toBeNull()
    expect(screen.queryByTestId('regime-reasons')).toBeNull()
    expect(screen.queryByTestId('regime-loading')).toBeNull()
    expect(screen.queryByTestId('regime-error')).toBeNull()
  })

  it('reason=corrupt → 文案与 missing 区分（明示损坏），仍属「未接入」空态', async () => {
    mockFetch({ available: false, reason: 'corrupt' })
    render(<MarketRegimeCard />)

    const empty = await screen.findByTestId('regime-empty')
    expect(empty.textContent).toContain('温度计未接入')
    expect(empty.textContent).toContain('损坏')
    expect(screen.queryByTestId('regime-loading')).toBeNull()
    expect(screen.queryByTestId('regime-error')).toBeNull()
  })

  it('畸形 200 体（有记录但指标结构缺失）→ 失败态，不炸整页', async () => {
    mockFetch({
      ...availablePayload(),
      indicators: {},
    } as unknown as RegimePayload)
    render(<MarketRegimeCard />)

    expect(await screen.findByTestId('regime-error')).toBeTruthy()
    expect(screen.queryByTestId('regime-metrics')).toBeNull()
  })
})

describe('MarketRegimeCard 只读', () => {
  it('渲染完成后区块内无按钮/输入/开关等控制入口', async () => {
    mockFetch(availablePayload({ state: 'danger', multiplier: 0 }))
    render(<MarketRegimeCard />)

    const card = await screen.findByTestId('market-regime-card')
    // 徽章 + 内容已就绪再检查，避免把 loading 态当成终态
    await screen.findByTestId('regime-multiplier')
    expect(
      card.querySelectorAll(
        'button, input, select, textarea, [role="switch"], [role="slider"], [role="checkbox"], [contenteditable="true"]'
      )
    ).toHaveLength(0)
  })
})

describe('选股页挂载', () => {
  it('温度计区块位于滚动容器最顶部（stale 横幅之前）', async () => {
    mockFetch(availablePayload(), poolBody({ stale: true, batch_status: 'failed' }))
    render(<ScreeningPage onBack={() => {}} />)

    const card = await screen.findByTestId('market-regime-card')
    const banner = await screen.findByTestId('screening-batch-stale-banner')
    expect(screen.getByTestId('screening-page').contains(card)).toBe(true)
    const scroll = card.parentElement
    expect(scroll).not.toBeNull()
    expect(scroll?.firstElementChild).toBe(card)
    expect(scroll?.contains(banner)).toBe(true)
  })
})
