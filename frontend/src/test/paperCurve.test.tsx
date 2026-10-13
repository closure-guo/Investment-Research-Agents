import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { TrackRecordPage } from '../pages/trackRecord/TrackRecordPage'

// add-paper-broker Task 3（B4）：战绩页「虚拟结算 vs 仿真成交」双曲线 + 执行损耗标注。
// fetch 全量 mock（页面并行拉 overview/equity-curve/segments/predictions/current/
// index-compare）；ECharts 需 canvas，jsdom 不支持 → stub 并捕获 option（既有先例）。

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
  Toaster: () => null,
}))

const capturedOptions: unknown[] = []
vi.mock('echarts-for-react', () => ({
  default: (props: { option: unknown }) => {
    capturedOptions.push(props.option)
    return <div data-testid="mock-chart" />
  },
}))

// 后端 overview.paper 块形状（api.py _paper_overview_block；spec 场景数值：
// 虚拟 1.043 / 仿真 1.031 → 执行损耗 -1.2%）
const PAPER = {
  series: [
    { date: '2026-09-01', virtual_equity: 1.0, paper_equity: 1.0 },
    { date: '2026-09-30', virtual_equity: 1.043, paper_equity: 1.031 },
  ],
  loss_pct: -1.2,
  base_date: '2026-09-01',
  note: '两条曲线同一批决策同一基准期；差值为执行损耗',
}

const CURVE = [
  { date: '2026-09-01', agent_nav: 1.0, benchmark_nav: 1.0 },
  { date: '2026-09-30', agent_nav: 1.043, benchmark_nav: 1.01 },
]

function overview(paper: unknown) {
  return {
    total: 0, open: 0, settled: 0, win_rate: null, avg_excess: null,
    status_counts: {}, source_type: 'live', insufficient_sample: true,
    as_of: '2026-10-13', disclaimer: '历史业绩不代表未来表现',
    portfolio: { available: false, annual_return: null, volatility: null,
      sharpe: null, max_drawdown: null, risk_score: null, risk_label: null, as_of: null },
    ...(paper === undefined ? {} : { paper }),
  }
}

function mockFetch(paper: unknown, equity: unknown = CURVE) {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
    if (url.includes('/api/v1/track-record/overview')) return json(overview(paper))
    if (url.includes('/api/v1/track-record/equity-curve')) {
      return json({ points: equity, as_of: '2026-10-13', disclaimer: 'x' })
    }
    if (url.includes('/api/v1/track-record/segments')) return json({ dimensions: [], as_of: 'x', disclaimer: 'x' })
    if (url.includes('/api/v1/track-record/current')) return json({ current: [], total: 0, as_of: 'x', disclaimer: 'x' })
    if (url.includes('/api/v1/track-record/predictions')) {
      return json({ predictions: [], page: 1, page_size: 50, total: 0, as_of: 'x', disclaimer: 'x' })
    }
    if (url.includes('/api/v1/track-record/index-compare')) {
      return json({ span: 'all', window: { start: 'x', end: 'x' }, agent_return: null, indices: [], as_of: 'x', disclaimer: 'x' })
    }
    return Promise.resolve(new Response('', { status: 404 }))
  }))
}

function lastChartOption() {
  return capturedOptions[capturedOptions.length - 1] as {
    xAxis: { data: string[] }
    series: Array<{ name: string; data?: Array<number | null> }>
  }
}

describe('战绩页双曲线与执行损耗（add-paper-broker Task 3）', () => {
  beforeEach(() => {
    capturedOptions.length = 0
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('paper 块存在：两条序列 + 图例「虚拟结算」「仿真成交」+ 损耗标注「执行损耗: -1.2%」', async () => {
    mockFetch(PAPER)
    render(<TrackRecordPage onBack={vi.fn()} />)
    await screen.findByTestId('track-record-curve')

    // 曲线数据：两序列按共同日期对齐（起点归一 1.0，基期两线重合）
    const opt = lastChartOption()
    expect(opt.series.map(s => s.name)).toEqual(['虚拟结算', '仿真成交'])
    expect(opt.xAxis.data).toEqual(['2026-09-01', '2026-09-30'])
    expect(opt.series[0].data).toEqual([1.0, 1.043])
    expect(opt.series[1].data).toEqual([1.0, 1.031])

    // DOM 图例（ECharts 图例画在 canvas 内，此处为可断言的 DOM 图例区）
    const legend = screen.getByTestId('curve-legend')
    expect(within(legend).getByTestId('legend-virtual')).toHaveTextContent('虚拟结算')
    expect(within(legend).getByTestId('legend-paper')).toHaveTextContent('仿真成交')
    // spec 场景：1.043/1.031 → 执行损耗 -1.2%（1 位小数）
    expect(within(legend).getByTestId('exec-loss')).toHaveTextContent('执行损耗: -1.2%')
    // 口径说明文本（接口 note 透传）
    expect(within(legend).getByTestId('paper-note')).toHaveTextContent('两条曲线同一批决策同一基准期')
  })

  it('paper: null → 单曲线渲染不报错，无损耗标注', async () => {
    mockFetch(null)
    render(<TrackRecordPage onBack={vi.fn()} />)
    await screen.findByTestId('track-record-curve')

    const opt = lastChartOption()
    expect(opt.series.map(s => s.name)).toEqual(['组合净值'])
    expect(opt.xAxis.data).toEqual(['2026-09-01', '2026-09-30'])
    expect(screen.queryByTestId('exec-loss')).not.toBeInTheDocument()
    expect(screen.queryByTestId('curve-legend')).not.toBeInTheDocument()
  })

  it('paper 字段缺省（旧后端）→ 同 null，单曲线渲染', async () => {
    mockFetch(undefined)
    render(<TrackRecordPage onBack={vi.fn()} />)
    await screen.findByTestId('track-record-curve')

    expect(lastChartOption().series.map(s => s.name)).toEqual(['组合净值'])
    expect(screen.queryByTestId('exec-loss')).not.toBeInTheDocument()
  })
})
