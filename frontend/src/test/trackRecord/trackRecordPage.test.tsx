import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { TrackRecordPage } from '../../pages/trackRecord/TrackRecordPage'
import App from '../../App'
import { DEFAULT_TRACK_PREFS } from '../../lib/trackPrefs'

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
  Toaster: () => null,
}))

// ECharts 需要 canvas，jsdom 不支持：stub 组件并捕获 option（chartsMarkLine.test 同款方案），
// 便于断言基准线开关 / 净值图形态等消费行为。
const capturedOptions: unknown[] = []
vi.mock('echarts-for-react', () => ({
  default: (props: { option: unknown }) => {
    capturedOptions.push(props.option)
    return <div data-testid="mock-chart" />
  },
}))

const OVERVIEW = {
  total: 2, open: 1, settled: 1, win_rate: 1, avg_excess: 0.1,
  status_counts: { open: 1, resolved_win: 1 }, source_type: 'live',
  insufficient_sample: true, as_of: '2026-09-03', disclaimer: '历史业绩不代表未来表现',
  portfolio: {
    available: true, annual_return: 0.12, volatility: 0.2, sharpe: 0.5,
    max_drawdown: 0.05, risk_score: 4, risk_label: '中', as_of: '2026-09-04',
  },
}

const PREDICTIONS: Record<string, unknown>[] = [
  {
    prediction_id: 'p1', source_type: 'live', symbol: '600519.SH', symbol_name: '贵州茅台',
    direction: 'long', entry_price: 100, target_price: 120, horizon_days: 252,
    confidence: 0.8, benchmark: '000300.SH', langfuse_trace_id: null,
    status: 'resolved_win', created_at: '2026-09-01T10:00:00', resolved_at: '2026-09-02',
    exit_price: 115, raw_return: 0.15, excess_return: 0.1, resolution_rule: 'expiry',
  },
  {
    prediction_id: 'p2', source_type: 'live', symbol: '300308.SZ', symbol_name: '中际旭创',
    direction: 'neutral', entry_price: 100, target_price: null, horizon_days: 252,
    confidence: 0.5, benchmark: '000300.SH', langfuse_trace_id: null,
    status: 'open', created_at: '2026-09-02T10:00:00', resolved_at: null,
    exit_price: null, raw_return: null, excess_return: null, resolution_rule: null,
  },
]

function mockFetch(opts: { overview?: unknown; predictions?: unknown; equity?: unknown; segments?: unknown } = {}) {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.includes('/api/v1/track-record/overview')) {
      return Promise.resolve(new Response(JSON.stringify(opts.overview ?? {
        total: 0, open: 0, settled: 0, win_rate: null, avg_excess: null,
        status_counts: {}, source_type: null, insufficient_sample: true,
        as_of: '2026-09-03', disclaimer: '历史业绩不代表未来表现',
        portfolio: { available: false, annual_return: null, volatility: null,
          sharpe: null, max_drawdown: null, risk_score: null, risk_label: null, as_of: null },
      }), { status: 200 }))
    }
    if (url.includes('/api/v1/track-record/equity-curve')) {
      return Promise.resolve(new Response(JSON.stringify({
        points: opts.equity ?? [], as_of: '2026-09-03', disclaimer: '历史业绩不代表未来表现',
      }), { status: 200 }))
    }
    if (url.includes('/api/v1/track-record/segments')) {
      return Promise.resolve(new Response(JSON.stringify({
        dimensions: opts.segments ?? [], as_of: '2026-09-03', disclaimer: '历史业绩不代表未来表现',
      }), { status: 200 }))
    }
    if (url.includes('/api/v1/track-record/predictions')) {
      return Promise.resolve(new Response(JSON.stringify({
        predictions: opts.predictions ?? [], page: 1, page_size: 50, total: 0,
        as_of: '2026-09-03', disclaimer: '历史业绩不代表未来表现',
      }), { status: 200 }))
    }
    return Promise.resolve(new Response('', { status: 404 }))
  }))
}

function renderPage() {
  return render(<TrackRecordPage onBack={vi.fn()} />)
}

describe('track-record 战绩页（add-track-record）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('空态显示「样本积累中」且不显示 0 冒充数据', async () => {
    mockFetch()
    renderPage()
    expect(await screen.findByTestId('track-record')).toBeInTheDocument()
    expect(screen.getByText(/样本积累中/)).toBeInTheDocument()
    // 风险提示常驻可见
    expect(screen.getByTestId('track-record-disclaimer')).toBeInTheDocument()
  })

  it('总览与观点日志渲染，默认含 loss，风险提示可见', async () => {
    mockFetch({
      overview: OVERVIEW,
      predictions: PREDICTIONS,
    })
    renderPage()
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument()
    expect(screen.getByText('中际旭创')).toBeInTheDocument()
    // 状态标签：命中(绿)、进行中(蓝/持有中)
    expect(screen.getByText('命中')).toBeInTheDocument()
    // 进行中观点展示浮动收益并标「未结算」
    expect(screen.getAllByText(/未结算/).length).toBeGreaterThan(0)
    expect(screen.getByTestId('track-record-disclaimer')).toBeInTheDocument()
    // 样本不足标注
    expect(screen.getByText(/样本较少|样本积累中/)).toBeInTheDocument()
  })

  it('null 胜率与超额占位「—」', async () => {
    mockFetch({
      overview: { ...OVERVIEW, win_rate: null, avg_excess: null, insufficient_sample: true },
      predictions: PREDICTIONS,
    })
    renderPage()
    await screen.findByText('贵州茅台')
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })
})

describe('战绩页全页视图下的导航（bug 复现：新建会话应回聊天首页）', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('fa_api_key', 'test-key')
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  function appMock() {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.includes('/api/v1/track-record/overview')) {
        return Promise.resolve(new Response(JSON.stringify({
          total: 0, open: 0, settled: 0, win_rate: null, avg_excess: null,
          status_counts: {}, source_type: null, insufficient_sample: true,
          as_of: '2026-09-03', disclaimer: '历史业绩不代表未来表现',
          portfolio: { available: false, annual_return: null, volatility: null,
            sharpe: null, max_drawdown: null, risk_score: null, risk_label: null, as_of: null },
        }), { status: 200 }))
      }
      if (url.includes('/api/v1/track-record/equity-curve')) {
        return Promise.resolve(new Response(JSON.stringify({ points: [], as_of: '2026-09-03', disclaimer: 'x' }), { status: 200 }))
      }
      if (url.includes('/api/v1/track-record/segments')) {
        return Promise.resolve(new Response(JSON.stringify({ dimensions: [], as_of: '2026-09-03', disclaimer: 'x' }), { status: 200 }))
      }
      if (url.includes('/api/v1/track-record/predictions')) {
        return Promise.resolve(new Response(JSON.stringify({ predictions: [], page: 1, page_size: 50, total: 0, as_of: '2026-09-03', disclaimer: 'x' }), { status: 200 }))
      }
      if (url === '/api/sessions') {
        return Promise.resolve(new Response(JSON.stringify({ sessions: [] }), { status: 200 }))
      }
      return Promise.resolve(new Response('', { status: 404 }))
    }))
  }

  it('战绩页点击「新建分析」回到聊天首页', async () => {
    appMock()
    window.history.pushState({}, '', '/track-record')
    render(<App />)
    await screen.findByTestId('track-record')
    fireEvent.click(screen.getByTestId('sidebar-new'))
    await waitFor(() => expect(window.location.pathname).toBe('/'))
    expect(await screen.findByText('今天想研究什么？')).toBeTruthy()
  })
})

describe('组合风险指标与净值曲线（add-track-record-stage-b）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('风险卡渲染真实指标与风险分', async () => {
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('贵州茅台')
    const risk = screen.getByTestId('track-record-risk')
    expect(risk.textContent).toContain('12.00%') // 年化收益(Delta 两位小数)
    expect(risk.textContent).toContain('20.0%') // 波动率
    expect(risk.textContent).toContain('0.50') // 夏普
    expect(risk.textContent).toContain('5.0%') // 最大回撤
    expect(risk.textContent).toContain('4') // 风险分
    expect(risk.textContent).toContain('中')
  })

  it('无快照时显示「暂无净值快照」空态，不渲染曲线', async () => {
    mockFetch({ overview: undefined, predictions: [] })
    renderPage()
    await screen.findByTestId('track-record')
    expect(screen.getByTestId('track-record-risk-empty')).toBeInTheDocument()
    expect(screen.queryByTestId('track-record-curve')).not.toBeInTheDocument()
  })

  it('净值点 ≥2 时渲染净值曲线（agent vs 基准）', async () => {
    mockFetch({
      overview: OVERVIEW,
      predictions: PREDICTIONS,
      equity: [
        { date: '2026-09-01', agent_nav: 1.0, benchmark_nav: 1.0 },
        { date: '2026-09-02', agent_nav: 1.01, benchmark_nav: 1.005 },
        { date: '2026-09-03', agent_nav: 1.02, benchmark_nav: 1.01 },
      ],
    })
    renderPage()
    await screen.findByText('贵州茅台')
    expect(screen.getByTestId('track-record-curve')).toBeInTheDocument()
  })

  it('风险分 ≥8 高亮红色', async () => {
    const highRiskOverview = {
      ...OVERVIEW,
      portfolio: { available: true, annual_return: -0.2, volatility: 0.8,
        sharpe: -0.3, max_drawdown: 0.41, risk_score: 10, risk_label: '极高', as_of: '2026-09-04' },
    }
    mockFetch({ overview: highRiskOverview, predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('贵州茅台')
    const score = screen.getByText('10')
    expect(score.className).toContain('status-error-default')
  })
})

describe('切片面板/版本切换/详情跳转（add-track-record-stage-c）', () => {
  beforeEach(() => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('切片面板渲染四维桶表并标注样本不足', async () => {
    mockFetch({
      overview: OVERVIEW,
      predictions: PREDICTIONS,
      segments: [
        {
          dimension: '行业', total: 1, settled: 1,
          buckets: [{ name: '白酒', sample_size: 1, win_rate: 1, avg_excess: 0.05, insufficient: true }],
        },
        {
          dimension: '持有期', total: 1, settled: 1,
          buckets: [{ name: '6-20天', sample_size: 1, win_rate: 1, avg_excess: 0.05, insufficient: true }],
        },
        {
          dimension: '市值', total: 0, settled: 0,
          buckets: [{ name: '未知', sample_size: 0, win_rate: null, avg_excess: null, insufficient: true }],
        },
        {
          dimension: '市场环境', total: 0, settled: 0,
          buckets: [{ name: '未知', sample_size: 0, win_rate: null, avg_excess: null, insufficient: true }],
        },
      ],
    })
    renderPage()
    await screen.findByText('贵州茅台')
    const panel = screen.getByTestId('track-record-segments')
    expect(panel.textContent).toContain('白酒')
    expect(panel.textContent).toContain('样本不足')
    expect(panel.textContent).toContain('持有期')
  })

  it('多版本时渲染版本选择器，切换后带 version 参数请求', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push(url)
      if (url.includes('/overview')) {
        return Promise.resolve(new Response(JSON.stringify({
          ...OVERVIEW,
          version_seq: 2,
          versions: [
            { agent_id: 'a1', model_version: 'v1', strategy_version: null, version_seq: 1, retired_at: '2026-09-01', created_at: 'x', note: null },
            { agent_id: 'a2', model_version: 'v2', strategy_version: null, version_seq: 2, retired_at: null, created_at: 'x', note: null },
          ],
        }), { status: 200 }))
      }
      if (url.includes('/predictions')) {
        return Promise.resolve(new Response(JSON.stringify({ predictions: [], page: 1, page_size: 50, total: 0, as_of: 'x', disclaimer: 'x' }), { status: 200 }))
      }
      if (url.includes('/equity-curve') || url.includes('/segments')) {
        return Promise.resolve(new Response(JSON.stringify({ points: [], dimensions: [], as_of: 'x', disclaimer: 'x' }), { status: 200 }))
      }
      return Promise.resolve(new Response('', { status: 404 }))
    }))
    renderPage()
    const selector = await screen.findByTestId('track-record-version-select')
    expect(selector).toBeInTheDocument()
    fireEvent.change(selector, { target: { value: '1' } })
    await waitFor(() => expect(calls.some(u => u.includes('/overview?version=1'))).toBe(true))
    expect(screen.getByText(/已封存/)).toBeInTheDocument()
  })

  it('点击观点行跳转详情页', async () => {
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('贵州茅台')
    fireEvent.click(screen.getByTestId('prediction-row-p1'))
    await waitFor(() => expect(window.location.pathname).toBe('/track-record/predictions/p1'))
  })
})

describe('观点日志排序/过滤/日期列/分页（add-track-record-sort-filter）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('渲染建立日期列（created_at 日期部分）', async () => {
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('贵州茅台')
    expect(screen.getByText('2026-09-01')).toBeInTheDocument()
    expect(screen.getByText('2026-09-02')).toBeInTheDocument()
  })

  it('点击「区间收益」表头请求 sort_by=raw_return 并显示排序指示', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push(url)
      if (url.includes('/predictions')) {
        return Promise.resolve(new Response(JSON.stringify({
          predictions: PREDICTIONS, page: 1, page_size: 50, total: 2,
          as_of: 'x', disclaimer: 'x',
        }), { status: 200 }))
      }
      if (url.includes('/overview')) {
        return Promise.resolve(new Response(JSON.stringify(OVERVIEW), { status: 200 }))
      }
      return Promise.resolve(new Response(JSON.stringify({ points: [], dimensions: [] }), { status: 200 }))
    }))
    renderPage()
    await screen.findByText('贵州茅台')
    fireEvent.click(screen.getByTestId('sort-raw_return'))
    await waitFor(() => expect(calls.some(u => u.includes('sort_by=raw_return'))).toBe(true))
    // 再次点击切换为 desc（服务端默认，URL 省略 sort_dir）
    fireEvent.click(screen.getByTestId('sort-raw_return'))
    await waitFor(() => expect(calls.some(u => u.includes('sort_by=raw_return') && !u.includes('sort_dir=asc'))).toBe(true))
  })

  it('关键字 + 查询按钮请求 keyword 参数', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push(url)
      if (url.includes('/predictions')) {
        return Promise.resolve(new Response(JSON.stringify({
          predictions: [PREDICTIONS[0]], page: 1, page_size: 50, total: 1, as_of: 'x', disclaimer: 'x',
        }), { status: 200 }))
      }
      if (url.includes('/overview')) {
        return Promise.resolve(new Response(JSON.stringify(OVERVIEW), { status: 200 }))
      }
      return Promise.resolve(new Response(JSON.stringify({ points: [], dimensions: [] }), { status: 200 }))
    }))
    renderPage()
    await screen.findByText('贵州茅台')
    fireEvent.change(screen.getByTestId('track-record-keyword'), { target: { value: '茅台' } })
    fireEvent.click(screen.getByTestId('track-record-apply'))
    await waitFor(() => expect(calls.some(u => u.includes('keyword=%E8%8C%85%E5%8F%B0'))).toBe(true))
  })

  it('起止日期 + 查询请求 date_from/date_to', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push(url)
      if (url.includes('/predictions')) {
        return Promise.resolve(new Response(JSON.stringify({ predictions: [], page: 1, page_size: 50, total: 0, as_of: 'x', disclaimer: 'x' }), { status: 200 }))
      }
      if (url.includes('/overview')) {
        return Promise.resolve(new Response(JSON.stringify(OVERVIEW), { status: 200 }))
      }
      return Promise.resolve(new Response(JSON.stringify({ points: [], dimensions: [] }), { status: 200 }))
    }))
    renderPage()
    await screen.findByTestId('track-record')
    fireEvent.change(screen.getByTestId('track-record-date-from'), { target: { value: '2026-09-01' } })
    fireEvent.change(screen.getByTestId('track-record-date-to'), { target: { value: '2026-09-30' } })
    fireEvent.click(screen.getByTestId('track-record-apply'))
    await waitFor(() => expect(calls.some(u => u.includes('date_from=2026-09-01') && u.includes('date_to=2026-09-30'))).toBe(true))
  })

  it('total 超过单页时渲染分页，下一页请求 page=2', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push(url)
      if (url.includes('/predictions')) {
        return Promise.resolve(new Response(JSON.stringify({ predictions: PREDICTIONS, page: 1, page_size: 50, total: 120, as_of: 'x', disclaimer: 'x' }), { status: 200 }))
      }
      if (url.includes('/overview')) {
        return Promise.resolve(new Response(JSON.stringify(OVERVIEW), { status: 200 }))
      }
      return Promise.resolve(new Response(JSON.stringify({ points: [], dimensions: [] }), { status: 200 }))
    }))
    renderPage()
    await screen.findByText('贵州茅台')
    const next = screen.getByTestId('track-record-next')
    expect(next).toBeInTheDocument()
    fireEvent.click(next)
    await waitFor(() => expect(calls.some(u => u.includes('page=2'))).toBe(true))
  })
})

describe('回避终态标签（update-decision-settlement-contract：status=avoidance）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  // 已结算 neutral 行：后端终态 status='avoidance'，细粒度结果在 avoidance_status 列
  const AVOIDANCE_ROW = {
    prediction_id: 'p3', source_type: 'live', symbol: '300308.SZ', symbol_name: '中际旭创',
    direction: 'neutral', entry_price: 100, target_price: null, horizon_days: 20,
    confidence: 0.5, benchmark: '000300.SH', langfuse_trace_id: null,
    status: 'avoidance', created_at: '2026-09-02T10:00:00', resolved_at: '2026-09-30',
    exit_price: 92, raw_return: -0.08, excess_return: -0.06, resolution_rule: 'expiry',
  }

  it('avoidance 行渲染「回避」标签（不空白）且为中性同族灰、无不可判定删除线', async () => {
    mockFetch({ overview: OVERVIEW, predictions: [AVOIDANCE_ROW] })
    renderPage()
    const row = await screen.findByTestId('prediction-row-p3')
    const label = within(row).getByText('回避')
    expect(label).toBeInTheDocument()
    // 颜色与「中性」同族（--text-secondary），且不沿用 unresolvable 的删除线
    expect(label.className).toContain('var(--text-secondary)')
    expect(label.className).not.toContain('line-through')
  })
})

describe('战绩展示偏好消费（add-agent-settings-center Task 12）', () => {
  // 净值点 ≥2 才渲染曲线（TrackRecordPage showCurve 条件）
  const CURVE = [
    { date: '2026-08-03', agent_nav: 1.0, benchmark_nav: 1.0 },
    { date: '2026-09-03', agent_nav: 1.02, benchmark_nav: 1.01 },
  ]

  // 时间跨度用例：5 个点横跨 8 个月（2026-01 ~ 2026-09），超出所有选项窗口
  const LONG_CURVE = [
    { date: '2026-01-05', agent_nav: 0.95, benchmark_nav: 0.96 },
    { date: '2026-03-05', agent_nav: 0.98, benchmark_nav: 0.97 },
    { date: '2026-06-05', agent_nav: 1.0, benchmark_nav: 0.99 },
    { date: '2026-08-05', agent_nav: 1.02, benchmark_nav: 1.0 },
    { date: '2026-09-03', agent_nav: 1.04, benchmark_nav: 1.02 },
  ]

  // 最近一次捕获的图表 option（含 xAxis 日期序列，用于断言时间跨度窗口裁剪）
  function lastChartOption() {
    return capturedOptions[capturedOptions.length - 1] as {
      xAxis: { data: string[] }
      series: Array<{ name: string; data?: number[]; areaStyle?: unknown }>
    }
  }

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

  it('回撤阈值默认 0.2：max_drawdown=0.12 时不触发警示高亮', async () => {
    // 未设置偏好 → 使用默认阈值 0.2；0.12 < 0.2 应为正常色
    mockFetch({
      overview: { ...OVERVIEW, portfolio: { ...OVERVIEW.portfolio, max_drawdown: 0.12 } },
      predictions: PREDICTIONS,
    })
    renderPage()
    await screen.findByText('贵州茅台')
    const md = within(screen.getByTestId('track-record-risk')).getByText('12.0%')
    expect(md.getAttribute('style')).not.toContain('status-error-default')
  })

  it('回撤阈值偏好 0.1 生效：max_drawdown=0.12 时风险卡警示高亮', async () => {
    localStorage.setItem('fa_track_prefs', JSON.stringify({ ...DEFAULT_TRACK_PREFS, drawdownThreshold: 0.1 }))
    mockFetch({
      overview: { ...OVERVIEW, portfolio: { ...OVERVIEW.portfolio, max_drawdown: 0.12 } },
      predictions: PREDICTIONS,
    })
    renderPage()
    await screen.findByText('贵州茅台')
    const md = within(screen.getByTestId('track-record-risk')).getByText('12.0%')
    expect(md.getAttribute('style')).toContain('status-error-default')
  })

  it('基准偏好 none：净值图只渲染组合净值系列', async () => {
    localStorage.setItem('fa_track_prefs', JSON.stringify({ ...DEFAULT_TRACK_PREFS, benchmark: 'none' }))
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS, equity: CURVE })
    renderPage()
    await screen.findByTestId('track-record-curve')
    expect(lastChartOption().series.map(s => s.name)).toEqual(['组合净值'])
  })

  it('基准偏好 hs300：净值图叠加沪深300基准线（无 areaStyle）', async () => {
    localStorage.setItem('fa_track_prefs', JSON.stringify({ ...DEFAULT_TRACK_PREFS, benchmark: 'hs300' }))
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS, equity: CURVE })
    renderPage()
    await screen.findByTestId('track-record-curve')
    const series = lastChartOption().series
    expect(series.map(s => s.name)).toEqual(['组合净值', '沪深300'])
    // 默认累计净值形态：折线不带面积填充
    expect(series[0].areaStyle).toBeUndefined()
  })

  it('净值形态偏好 interval：组合净值 series 带 areaStyle 面积填充', async () => {
    localStorage.setItem('fa_track_prefs', JSON.stringify({ ...DEFAULT_TRACK_PREFS, navChartForm: 'interval' }))
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS, equity: CURVE })
    renderPage()
    await screen.findByTestId('track-record-curve')
    const series = lastChartOption().series
    expect(series[0].name).toBe('组合净值')
    expect(series[0].areaStyle).toBeTruthy()
  })

  it('基准 none + 形态 interval 组合生效：单系列且带面积', async () => {
    localStorage.setItem('fa_track_prefs', JSON.stringify({
      ...DEFAULT_TRACK_PREFS, benchmark: 'none', navChartForm: 'interval',
    }))
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS, equity: CURVE })
    renderPage()
    await screen.findByTestId('track-record-curve')
    const series = lastChartOption().series
    expect(series.map(s => s.name)).toEqual(['组合净值'])
    expect(series[0].areaStyle).toBeTruthy()
  })

  it('时间跨度 6m：净值曲线点按最近 6 个月裁剪（总览指标仍全期）', async () => {
    localStorage.setItem('fa_track_prefs', JSON.stringify({ ...DEFAULT_TRACK_PREFS, timeSpan: '6m' }))
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS, equity: LONG_CURVE })
    renderPage()
    await screen.findByTestId('track-record-curve')
    const opt = lastChartOption()
    // 最新点 2026-09-03 往前 6 个月 → 保留日期 ≥2026-03-03，剔除 2026-01-05
    expect(opt.xAxis.data).toEqual(['2026-03-05', '2026-06-05', '2026-08-05', '2026-09-03'])
    expect(opt.series[0].data).toEqual([0.98, 1.0, 1.02, 1.04])
  })

  it('时间跨度 all：净值曲线点不过滤，全量展示', async () => {
    localStorage.setItem('fa_track_prefs', JSON.stringify({ ...DEFAULT_TRACK_PREFS, timeSpan: 'all' }))
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS, equity: LONG_CURVE })
    renderPage()
    await screen.findByTestId('track-record-curve')
    expect(lastChartOption().xAxis.data).toEqual(LONG_CURVE.map(p => p.date))
  })
})
