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
    beta: 0.85, jensen_alpha: 0.031,
  },
}

// 跑赢指数对比响应（add-index-performance-compare）：与 indexCompareCard.test 的 RESP 同构，
// 一只指数 beat=null → 摘要分母 = beat 非 null 数 → 「跑赢 3/4 个指数」
const INDEX_COMPARE = {
  span: 'all',
  window: { start: '2026-09-28', end: '2026-10-09' },
  agent_return: 0.05,
  indices: [
    { code: '000001', name: '上证指数', return: 0.03, effective_start_date: '2026-09-28', beat: true },
    { code: '000300', name: '沪深300', return: 0.01, effective_start_date: '2026-09-26', beat: true },
    { code: '000905', name: '中证500', return: 0.1, effective_start_date: '2026-09-28', beat: false },
    { code: '000852', name: '中证1000', return: null, effective_start_date: null, beat: null },
    { code: '399006', name: '创业板指', return: 0.02, effective_start_date: '2026-09-28', beat: true },
  ],
  as_of: '2026-10-02',
  disclaimer: '历史业绩不代表未来表现',
}

const PREDICTIONS: Record<string, unknown>[] = [
  {
    prediction_id: 'p1', source_type: 'live', symbol: '600519.SH', symbol_name: '贵州茅台',
    direction: 'long', entry_price: 100, target_price: 120, horizon_days: 252,
    confidence: 0.8, benchmark: '000300.SH', langfuse_trace_id: null,
    status: 'resolved_win', created_at: '2026-09-01T10:00:00', resolved_at: '2026-09-02',
    settle_entry_price: 112.5,
    exit_price: 115, raw_return: 0.15, excess_return: 0.1, resolution_rule: 'expiry',
  },
  {
    prediction_id: 'p2', source_type: 'live', symbol: '300308.SZ', symbol_name: '中际旭创',
    direction: 'neutral', entry_price: 100, target_price: null, horizon_days: 252,
    confidence: 0.5, benchmark: '000300.SH', langfuse_trace_id: null,
    status: 'open', created_at: '2026-09-02T10:00:00', resolved_at: null,
    settle_entry_price: null,
    exit_price: null, raw_return: null, excess_return: null, resolution_rule: null,
  },
]

const CURRENT = [
  {
    prediction_id: 'p2', source_type: 'live', symbol: '300308.SZ', symbol_name: '中际旭创',
    direction: 'neutral', entry_price: 100, target_price: null, horizon_days: 20,
    confidence: 0.5, benchmark: '000300.SH', langfuse_trace_id: null,
    status: 'open', created_at: '2026-09-02T10:00:00', resolved_at: null,
    exit_price: null, raw_return: null, excess_return: null, resolution_rule: null,
  },
]

function mockFetch(opts: { overview?: unknown; predictions?: unknown; predictionsTotal?: number; equity?: unknown; segments?: unknown; current?: unknown; indexCompare?: unknown } = {}) {
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
    if (url.includes('/api/v1/track-record/current')) {
      return Promise.resolve(new Response(JSON.stringify({
        current: opts.current ?? [], total: (opts.current as unknown[])?.length ?? 0,
        as_of: '2026-09-03', disclaimer: '历史业绩不代表未来表现',
      }), { status: 200 }))
    }
    if (url.includes('/api/v1/track-record/index-compare')) {
      return Promise.resolve(new Response(JSON.stringify(opts.indexCompare ?? INDEX_COMPARE), { status: 200 }))
    }
    if (url.includes('/api/v1/track-record/predictions')) {
      return Promise.resolve(new Response(JSON.stringify({
        predictions: opts.predictions ?? [], page: 1, page_size: 50, total: opts.predictionsTotal ?? 0,
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

  it('连续超额 + 同窗随机基准 + 带宽读数条（update-outcome-band-volatility-scaled）', async () => {
    mockFetch({
      overview: {
        ...OVERVIEW,
        continuous: {
          n: 12,
          mean_excess: 0.021,
          median_excess: 0.008,
          std: 0.062,
          t_stat: 1.17,
          excess_win_rate: 0.583,
          distinct_symbols: 9,
          population: 'long/short 日主已判定（resolved_win/loss/neutral）',
        },
        baseline: {
          available: true,
          windows: 5,
          skipped_windows: 0,
          stocks_per_window: 5480,
          mean_excess: -0.035,
          median_excess: -0.03,
          caliber: '同窗全市场等权（close_raw，基准 000300.SH）',
        },
        band: {
          mode: 'vol_scaled',
          floor: 0.015,
          vol_multiplier: 0.5,
          vol_window_days: 20,
          fallback_band: 0.02,
          settled_with_band: 12,
          cutover: '2026-11-28',
        },
      },
      predictions: PREDICTIONS,
    })
    renderPage()
    const strip = await screen.findByTestId('track-record-continuous')
    expect(within(strip).getByTestId('continuous-label')).toHaveTextContent('n=12')
    expect(within(strip).getByTestId('continuous-label')).toHaveTextContent('去重标的 9 只')
    expect(within(strip).getByText('超额胜率 58.3%')).toBeInTheDocument()
    expect(within(strip).getByText('t=1.17')).toBeInTheDocument()
    const baseline = within(strip).getByTestId('track-record-baseline')
    expect(baseline).toHaveTextContent('同窗随机股票')
    expect(baseline).toHaveTextContent('5 个窗口 × 约 5480 只')
    expect(within(strip).getByTestId('track-record-band')).toHaveTextContent('新口径已结算 12 条')
  })

  it('同窗随机基准不可用时如实披露原因，不折算 0', async () => {
    mockFetch({
      overview: {
        ...OVERVIEW,
        continuous: {
          n: 12, mean_excess: 0.021, median_excess: 0.008, std: 0.062, t_stat: 1.17,
          excess_win_rate: 0.583, distinct_symbols: 12, population: 'p',
        },
        baseline: {
          available: false, reason: '快照库或指数收盘数据缺失', windows: 0,
          skipped_windows: 3, stocks_per_window: null, mean_excess: null,
          median_excess: null, caliber: 'c',
        },
      },
      predictions: PREDICTIONS,
    })
    renderPage()
    const strip = await screen.findByTestId('track-record-continuous')
    expect(within(strip).getByTestId('track-record-baseline-unavailable')).toHaveTextContent(
      '快照库或指数收盘数据缺失'
    )
  })
})

describe('结算价格同口径展示（update-track-record-settle-price-display）', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('fa_api_key', 'test-key')
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  const renderPage = (awaited = '贵州茅台') => {
    render(<TrackRecordPage onBack={vi.fn()} />)
    return screen.findByText(awaited)
  }

  it('列头口径标注：参考价（盘面）/ 结算入场价（后复权）/ 结算价（后复权）', async () => {
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS })
    await renderPage()
    expect(screen.getByText('参考价（盘面）')).toBeInTheDocument()
    expect(screen.getByText('结算入场价（后复权）')).toBeInTheDocument()
    expect(screen.getByText('结算价（后复权）')).toBeInTheDocument()
  })

  it('已结算行展示 settle_entry_price（与 exit_price 同口径可比）', async () => {
    mockFetch({
      overview: OVERVIEW,
      predictions: [
        { ...PREDICTIONS[0], settle_entry_price: 112.5 },
      ],
    })
    await renderPage()
    const row = screen.getByTestId('prediction-row-p1')
    expect(within(row).getByText('112.50')).toBeInTheDocument()
    expect(within(row).getByText('115.00')).toBeInTheDocument()
  })

  it('进行中行结算入场价占位「—」且新列头不带排序按钮', async () => {
    mockFetch({
      overview: OVERVIEW,
      predictions: [{ ...PREDICTIONS[1], settle_entry_price: null }],
    })
    await renderPage('中际旭创')
    const row = screen.getByTestId('prediction-row-p2')
    expect(within(row).getAllByText('—').length).toBeGreaterThanOrEqual(2)
    // 新列不参与排序：后端 _SORT_WHITELIST 不含该键，前端不应渲染 sort 按钮
    expect(screen.queryByTestId('sort-settle_entry_price')).not.toBeInTheDocument()
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
      // 跑赢指数对比卡片端点：本组用例不覆盖该数据 → 404 走卡片失败态分支，
      // 避免兜底 200 的 {points,dimensions} 形状喂给卡片（无 indices 字段会炸渲染）
      if (url.includes('/index-compare')) {
        return Promise.resolve(new Response('', { status: 404 }))
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
      // 跑赢指数对比卡片端点：本组用例不覆盖该数据 → 404 走卡片失败态分支，
      // 避免兜底 200 的 {points,dimensions} 形状喂给卡片（无 indices 字段会炸渲染）
      if (url.includes('/index-compare')) {
        return Promise.resolve(new Response('', { status: 404 }))
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
      // 跑赢指数对比卡片端点：本组用例不覆盖该数据 → 404 走卡片失败态分支，
      // 避免兜底 200 的 {points,dimensions} 形状喂给卡片（无 indices 字段会炸渲染）
      if (url.includes('/index-compare')) {
        return Promise.resolve(new Response('', { status: 404 }))
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
      // 跑赢指数对比卡片端点：本组用例不覆盖该数据 → 404 走卡片失败态分支，
      // 避免兜底 200 的 {points,dimensions} 形状喂给卡片（无 indices 字段会炸渲染）
      if (url.includes('/index-compare')) {
        return Promise.resolve(new Response('', { status: 404 }))
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

describe('观点日志标题与状态 tab（update-prediction-log-tabs）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  function predictionsCalls(calls: string[]) {
    return calls.filter(u => u.includes('/predictions'))
  }

  it('缺省「当前持有」:请求带 status=open,tab 有 active 标识,标题与副标题渲染', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push(url)
      if (url.includes('/predictions')) {
        return Promise.resolve(new Response(JSON.stringify({
          predictions: PREDICTIONS, page: 1, page_size: 50, total: 57, as_of: 'x', disclaimer: 'x',
        }), { status: 200 }))
      }
      if (url.includes('/index-compare')) return Promise.resolve(new Response('', { status: 404 }))
      if (url.includes('/overview')) return Promise.resolve(new Response(JSON.stringify(OVERVIEW), { status: 200 }))
      return Promise.resolve(new Response(JSON.stringify({ points: [], dimensions: [] }), { status: 200 }))
    }))
    renderPage()
    await screen.findByText('贵州茅台')
    expect(screen.getByTestId('prediction-log-header')).toBeVisible()
    expect(screen.getByText(/每条 = 一次分析结论/)).toBeVisible()
    const tab = screen.getByTestId('prediction-tab-open')
    expect(tab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('prediction-tab-resolved')).toHaveAttribute('aria-selected', 'false')
    expect(predictionsCalls(calls).every(u => u.includes('status=open'))).toBe(true)
  })

  it('切换「已判定」:请求带 status=resolved 且重置分页', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push(url)
      if (url.includes('/predictions')) {
        return Promise.resolve(new Response(JSON.stringify({
          predictions: [], page: 1, page_size: 50, total: 51, as_of: 'x', disclaimer: 'x',
        }), { status: 200 }))
      }
      if (url.includes('/index-compare')) return Promise.resolve(new Response('', { status: 404 }))
      if (url.includes('/overview')) return Promise.resolve(new Response(JSON.stringify(OVERVIEW), { status: 200 }))
      return Promise.resolve(new Response(JSON.stringify({ points: [], dimensions: [] }), { status: 200 }))
    }))
    renderPage()
    await screen.findByText('观点日志')
    fireEvent.click(screen.getByTestId('prediction-tab-resolved'))
    await waitFor(() => expect(screen.getByTestId('prediction-tab-resolved')).toHaveAttribute('aria-selected', 'true'))
    const predUrls = predictionsCalls(calls)
    expect(predUrls.some(u => u.includes('status=resolved'))).toBe(true)
    expect(predUrls[predUrls.length - 1]).not.toContain('page=2')  // 分页重置
  })

  it('切换「全部」:请求不带 status 参数', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push(url)
      if (url.includes('/predictions')) {
        return Promise.resolve(new Response(JSON.stringify({
          predictions: PREDICTIONS, page: 1, page_size: 50, total: 108, as_of: 'x', disclaimer: 'x',
        }), { status: 200 }))
      }
      if (url.includes('/index-compare')) return Promise.resolve(new Response('', { status: 404 }))
      if (url.includes('/overview')) return Promise.resolve(new Response(JSON.stringify(OVERVIEW), { status: 200 }))
      return Promise.resolve(new Response(JSON.stringify({ points: [], dimensions: [] }), { status: 200 }))
    }))
    renderPage()
    await screen.findByText('观点日志')
    fireEvent.click(screen.getByTestId('prediction-tab-all'))
    await waitFor(() => expect(screen.getByTestId('prediction-tab-all')).toHaveAttribute('aria-selected', 'true'))
    const last = predictionsCalls(calls)[predictionsCalls(calls).length - 1]
    expect(last).not.toContain('status=')
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
    settle_entry_price: 100,
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

// Δ2 口径三披露（update-decision-settlement-contract：后端 overview 已返回但前端未读）
// + add-eval-ops-console Task 7：回避正确率（与胜率同门槛）/ caliber_horizon 常驻 / legacy_settled 常驻。
describe('总览三披露：回避正确率 / 判定口径 / 存量旧口径（add-eval-ops-console Task 7）', () => {
  beforeEach(() => { vi.spyOn(window, 'scrollTo').mockImplementation(() => {}) })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  // 后端 avoidance 形状：avoidance_stats()（win/(win+loss) 口径，<10 不展示率）
  const AVOIDANCE = { avoidance_win: 7, avoidance_loss: 5, avoidance_neutral: 2, settled: 12, avoidance_rate: 0.5833 }

  const overviewWith = (extra: Record<string, unknown>) => ({
    ...OVERVIEW, avoidance: AVOIDANCE, caliber_horizon: 20, legacy_settled: 0, ...extra,
  })

  it('回避样本充足时展示回避正确率与样本数', async () => {
    mockFetch({ overview: overviewWith({}), predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('贵州茅台')
    const card = screen.getByTestId('track-record-avoidance')
    expect(card).toHaveTextContent('58.3%')
    expect(card).toHaveTextContent('12')
  })

  it('回避样本不足（后端已置 null）时展示「样本积累中」而非 0 值', async () => {
    // 门槛真源在后端：settled 不足时后端把 avoidance_rate 置 null（见 api.py），
    // 前端只认 null，不自己比 settled 与 10。
    mockFetch({
      overview: overviewWith({ avoidance: { ...AVOIDANCE, settled: 3, avoidance_win: 2, avoidance_loss: 1, avoidance_rate: null } }),
      predictions: PREDICTIONS,
    })
    renderPage()
    await screen.findByText('贵州茅台')
    const card = screen.getByTestId('track-record-avoidance')
    expect(card).toHaveTextContent('样本积累中')
    // update-track-record-display-clarity:术语「已判定」→「已结算」
    expect(card).toHaveTextContent('已结算 3 条')
    expect(card.textContent ?? '').not.toContain('0%')
  })

  it('后端给了率就照实展示（前端不再自行套 settled<10 门槛）', async () => {
    mockFetch({
      overview: overviewWith({ avoidance: { ...AVOIDANCE, settled: 3, avoidance_win: 2, avoidance_loss: 1, avoidance_rate: 0.6667 } }),
      predictions: PREDICTIONS,
    })
    renderPage()
    await screen.findByText('贵州茅台')
    const card = screen.getByTestId('track-record-avoidance')
    expect(card).toHaveTextContent('66.7%')
    expect(card).not.toHaveTextContent('样本积累中')
  })

  it('判定口径与存量计数常驻；存量为 0 时明示「无存量」', async () => {
    mockFetch({ overview: overviewWith({ caliber_horizon: 20, legacy_settled: 0 }), predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('贵州茅台')
    expect(screen.getByTestId('track-record-caliber')).toHaveTextContent('T+20 交易日')
    expect(screen.getByTestId('track-record-legacy')).toHaveTextContent('无存量')
  })

  it('存量旧口径 >0 时展示「另有 n 条旧口径已结算」（术语对齐 delta spec）', async () => {
    mockFetch({ overview: overviewWith({ caliber_horizon: 20, legacy_settled: 7, legacy_open: 0 }), predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('贵州茅台')
    const legacy = screen.getByTestId('track-record-legacy')
    expect(legacy).toHaveTextContent('另有 7 条旧口径已结算')
  })

  // 口径披露行双计数（update-track-record-display-clarity delta spec「口径披露行双计数」Scenario）：
  // legacy_settled（已结算）与 legacy_open（进行中，不计入头条口径）分列披露
  it('口径披露行双计数:legacy_settled 与 legacy_open 两分句同时渲染', async () => {
    mockFetch({ overview: overviewWith({ caliber_horizon: 20, legacy_settled: 5, legacy_open: 7 }), predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('贵州茅台')
    const open = screen.getByTestId('track-record-legacy-open')
    expect(open).toBeVisible()
    expect(open).toHaveTextContent('另有 7 条旧口径进行中，不计入头条口径')
    expect(screen.getByTestId('track-record-legacy')).toHaveTextContent('另有 5 条旧口径已结算')
  })

  it('legacy_open=0 或缺省时不渲染进行中分句;legacy_settled=0 仍明示「无存量」', async () => {
    // overviewWith 基线 legacy_settled: 0 且无 legacy_open 键（容忍旧后端）
    mockFetch({ overview: overviewWith({}), predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('贵州茅台')
    expect(screen.queryByTestId('track-record-legacy-open')).not.toBeInTheDocument()
    expect(screen.getByTestId('track-record-legacy')).toHaveTextContent('无存量')
  })

  it('回避读数缺失（老会话/缺字段）时不展示 0%，如实占位', async () => {
    mockFetch({ overview: { ...OVERVIEW }, predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('贵州茅台')
    const card = screen.getByTestId('track-record-avoidance')
    expect(card.textContent ?? '').not.toContain('0%')
    expect(card).toHaveTextContent('—')
  })
})

describe('战绩页：跑赢指数对比卡片（add-index-performance-compare）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('净值图区块之后渲染卡片与摘要（span 随页面偏好）', async () => {
    mockFetch({
      overview: OVERVIEW,
      predictions: PREDICTIONS,
      equity: [
        { date: '2026-09-01', agent_nav: 1.0, benchmark_nav: 1.0 },
        { date: '2026-09-02', agent_nav: 1.01, benchmark_nav: 1.005 },
      ],
    })
    renderPage()
    const card = await screen.findByTestId('index-compare-card')
    expect(card).toBeVisible()
    // 摘要 N/M 口径：分母 = beat 非 null 指数数（000852 beat=null 不计入）→ 3/4
    expect(await screen.findByTestId('index-compare-summary')).toHaveTextContent(/跑赢 \d+\/\d+ 个指数/)
    // 挂载位置：净值图区块之后、观点日志过滤工具栏之前（span 与净值图窗口同源）
    const curve = screen.getByTestId('track-record-curve')
    expect(curve.compareDocumentPosition(card)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    expect(card.compareDocumentPosition(screen.getByTestId('track-record-filters'))).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })
})

// β/α 指标位（add-portfolio-beta-alpha）：overview portfolio.beta/jensen_alpha 两格，
// 有值渲染（β 两位小数 / α 带符号百分比），null/缺失如实占位「—」
describe('战绩页：β/α 指标位（add-portfolio-beta-alpha）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('渲染 β 与 α 两格（两位小数/带符号百分比）与副标题', async () => {
    mockFetch({ overview: OVERVIEW, predictions: PREDICTIONS })
    renderPage()
    await screen.findByText('β 市场敞口')
    expect(screen.getByTestId('portfolio-beta')).toHaveTextContent('0.85')
    expect(screen.getByText('α 年化超额')).toBeVisible()
    expect(screen.getByTestId('portfolio-alpha')).toHaveTextContent('+3.10%')
    // 副标题同步披露新指标
    expect(screen.getByText('年化/波动/夏普/最大回撤/风险分/β/α')).toBeInTheDocument()
  })

  it('null 时显示 —', async () => {
    mockFetch({
      overview: { ...OVERVIEW, portfolio: { ...OVERVIEW.portfolio, beta: null, jensen_alpha: null } },
      predictions: PREDICTIONS,
    })
    renderPage()
    await screen.findByText('β 市场敞口')
    expect(screen.getByTestId('portfolio-beta')).toHaveTextContent('—')
    expect(screen.getByTestId('portfolio-alpha')).toHaveTextContent('—')
  })
})

// 观点日志窗口列 + 副标题（update-track-record-display-clarity）：T+N 逐行展示、
// 窗口为展示列不参与排序（无 sort-horizon_days）；副标题去 20 日硬编码
describe('观点日志窗口列与副标题（update-track-record-display-clarity）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('观点日志窗口列:T+N 逐行展示,表头无排序按钮', async () => {
    mockFetch({ current: [], predictions: PREDICTIONS })
    renderPage()
    const log = await screen.findByTestId('prediction-log')
    // p2 (open, h=252) 显示 T+252;p1 (resolved_win, h=252) 同
    const p2row = within(log).getByTestId('prediction-row-p2')
    expect(p2row).toHaveTextContent('T+252')
    // 窗口表头不可排序:无 sort-horizon_days 按钮
    expect(screen.queryByTestId('sort-horizon_days')).not.toBeInTheDocument()
    expect(screen.getByText('窗口')).toBeInTheDocument()
  })
})

// 观点日志 open 行浮动收益（update-track-record-display-clarity）：区间收益/基准超额列
// 消费 latest_mark（最新盯市），附盯市日期 title；无盯市 open 行如实占位「—」
describe('观点日志 open 行浮动收益（update-track-record-display-clarity）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('open 行浮动收益:有盯市显示浮动车并带盯市日期 title,无盯市显示 —', async () => {
    const withMark = {
      ...PREDICTIONS[1],
      prediction_id: 'p3', symbol: '600016.SH', symbol_name: '民生银行',
      latest_mark: { mark_date: '2026-10-08', cum_return: -0.012, cum_excess: -0.008 },
    }
    mockFetch({ current: [], predictions: [PREDICTIONS[0], PREDICTIONS[1], withMark] })
    renderPage()
    const p3row = await screen.findByTestId('prediction-row-p3')
    expect(p3row).toHaveTextContent('-1.20%')
    expect(p3row).toHaveTextContent('-0.80%')
    // findByTestId 返回 Element，其上无查询方法（简报片段 API 缺陷，同 Task 4 修正）：within(row) 模式
    const markCell = within(p3row).getAllByTitle('盯市 2026-10-08（未结算浮动）')
    expect(markCell.length).toBeGreaterThanOrEqual(1)
    const p2row = screen.getByTestId('prediction-row-p2')
    expect(p2row).toHaveTextContent('—')
    expect(within(p2row).queryByTitle(/盯市/)).toBeNull()
  })
})

// 当前观点区（add-current-stance-view）：GET /api/v1/track-record/current，
// 每股最新一条 open 的立场视图；独立加载，失败显式文案不冒充空态
describe('当前观点区（add-current-stance-view）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('当前观点区:每股最新一条 open,含判定窗口与状态', async () => {
    mockFetch({ current: CURRENT, predictions: [] })
    renderPage()
    const stance = await screen.findByTestId('current-stance')
    expect(stance).toBeInTheDocument()
    expect(screen.getByText(/每股仅显示最新一条进行中观点/)).toBeInTheDocument()
    // jest-dom v7 无 toContainText（简报笔误），等价 matcher：toHaveTextContent 默认子串匹配
    expect(screen.getByTestId('current-stance-row-p2')).toHaveTextContent('中际旭创')
    expect(screen.getByTestId('current-stance-row-p2')).toHaveTextContent('T+20')
    expect(screen.getByTestId('current-stance-row-p2')).toHaveTextContent('进行中')
  })

  it('当前观点区空态:显示空态文案而非隐藏区块', async () => {
    mockFetch({ current: [] })
    renderPage()
    expect(await screen.findByTestId('current-stance-empty')).toHaveTextContent('暂无进行中观点')
  })

  it('当前观点区加载失败:显式失败文案,不冒充空态', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.includes('/api/v1/track-record/current')) {
        return Promise.resolve(new Response('', { status: 500 }))
      }
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
        return Promise.resolve(new Response(JSON.stringify({ points: [] }), { status: 200 }))
      }
      if (url.includes('/api/v1/track-record/segments')) {
        return Promise.resolve(new Response(JSON.stringify({ dimensions: [] }), { status: 200 }))
      }
      if (url.includes('/api/v1/track-record/predictions')) {
        return Promise.resolve(new Response(JSON.stringify({
          predictions: [], page: 1, page_size: 50, total: 0,
        }), { status: 200 }))
      }
      if (url.includes('/api/v1/track-record/index-compare')) {
        return Promise.resolve(new Response(JSON.stringify(INDEX_COMPARE), { status: 200 }))
      }
      return Promise.resolve(new Response('', { status: 404 }))
    }))
    renderPage()
    expect(await screen.findByText('当前观点加载失败')).toBeInTheDocument()
    expect(screen.queryByTestId('current-stance-empty')).not.toBeInTheDocument()
  })
})

// 观点日志同日重复行折叠（update-track-record-display-clarity）：当前页内 consecutive
// (symbol, 建立日期) 的 duplicate_of_day 行默认合并为「同日重复 ×n」汇总行，点击展开/收起；
// 折叠仅影响展示行数，分页 total 不变
describe('观点日志同日重复行折叠（update-track-record-display-clarity）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('同日重复行默认折叠为汇总,点击展开明细,分页 total 不变', async () => {
    const dupBase = {
      ...PREDICTIONS[0], symbol: '000858.SH', symbol_name: '五粮液',
      status: 'duplicate_of_day' as const, resolution_rule: 'duplicate_of_day',
      exit_price: null, raw_return: null, excess_return: null,
    }
    const dupRows = [
      { ...dupBase, prediction_id: 'd1', created_at: '2026-10-05T11:00:00' },
      { ...dupBase, prediction_id: 'd2', created_at: '2026-10-05T12:00:00' },
      { ...dupBase, prediction_id: 'd3', created_at: '2026-10-05T13:00:00' },
    ]
    mockFetch({ current: [], predictions: dupRows, predictionsTotal: 7 })
    renderPage()
    // 默认折叠:1 行汇总,3 条明细不渲染
    // testid 含首行 prediction_id（key = symbol|date|首行 pid，防同股同日多段重复撞 key）
    // jest-dom v7 无 toContainText（简报笔误），等价 matcher：toHaveTextContent 默认子串匹配
    expect(await screen.findByTestId('dup-group-000858.SH-2026-10-05-d1')).toHaveTextContent('同日重复 ×3')
    expect(screen.queryByTestId('prediction-row-d1')).not.toBeInTheDocument()
    // 分页 total 不受折叠影响
    expect(screen.getByText(/共 7 条/)).toBeInTheDocument()
    // 点击展开
    fireEvent.click(screen.getByTestId('dup-group-000858.SH-2026-10-05-d1'))
    expect(screen.getByTestId('prediction-row-d1')).toBeInTheDocument()
    expect(screen.getByTestId('prediction-row-d3')).toBeInTheDocument()
  })

  // 防回归（生产 688072 2026-10-02 实证）：created_at DESC 下同股同日 dup 可能被日主行
  // 切成多段 consecutive 区。旧实现 key=symbol|date → 两段共享 key/testid：DOM 出现重复
  // testid（getByTestId 直接抛多元素错误）且 expandedDups 串扰——点一组两组同开同收。
  // 修复：key 加首行 prediction_id，两段各自独立 toggle。
  it('同股同日两段重复被 open 行隔开:两个汇总行 key 唯一,展开互不联动', async () => {
    const dupBase = {
      ...PREDICTIONS[0], symbol: '000858.SH', symbol_name: '五粮液',
      status: 'duplicate_of_day' as const, resolution_rule: 'duplicate_of_day',
      exit_price: null, raw_return: null, excess_return: null,
    }
    const openBreaker = {
      ...PREDICTIONS[0], symbol: '000858.SH', symbol_name: '五粮液',
      status: 'open' as const, resolved_at: null, resolution_rule: null,
      exit_price: null, raw_return: null, excess_return: null,
    }
    const rows = [
      { ...dupBase, prediction_id: 'd1', created_at: '2026-10-05T11:00:00' },
      { ...dupBase, prediction_id: 'd2', created_at: '2026-10-05T12:00:00' },
      { ...dupBase, prediction_id: 'd3', created_at: '2026-10-05T13:00:00' },
      { ...openBreaker, prediction_id: 'o1', created_at: '2026-10-05T14:00:00' },
      { ...dupBase, prediction_id: 'd4', created_at: '2026-10-05T15:00:00' },
      { ...dupBase, prediction_id: 'd5', created_at: '2026-10-05T16:00:00' },
    ]
    mockFetch({ current: [], predictions: rows, predictionsTotal: 6 })
    renderPage()
    // 两个不同 testid 的汇总行（旧实现两 testid 相同,getByTestId 抛「found multiple」）
    const g1 = await screen.findByTestId('dup-group-000858.SH-2026-10-05-d1')
    expect(g1).toHaveTextContent('同日重复 ×3')
    expect(screen.getByTestId('dup-group-000858.SH-2026-10-05-d4')).toHaveTextContent('同日重复 ×2')
    // 点第一组只展开第一组:第二组明细不出现
    fireEvent.click(screen.getByTestId('dup-group-000858.SH-2026-10-05-d1'))
    expect(screen.getByTestId('prediction-row-d1')).toBeInTheDocument()
    expect(screen.queryByTestId('prediction-row-d4')).not.toBeInTheDocument()
    // 收起第一组;展开第二组只出第二组明细,第一组不受影响
    fireEvent.click(screen.getByTestId('dup-group-000858.SH-2026-10-05-d1'))
    expect(screen.queryByTestId('prediction-row-d1')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('dup-group-000858.SH-2026-10-05-d4'))
    expect(screen.getByTestId('prediction-row-d4')).toBeInTheDocument()
    expect(screen.getByTestId('prediction-row-d5')).toBeInTheDocument()
    expect(screen.queryByTestId('prediction-row-d1')).not.toBeInTheDocument()
  })
})

// 切片空态折叠 + 术语消歧（update-track-record-display-clarity）：settled=0 时切片区
// 折叠为一行说明；横幅「已判定」→「已结算」；resolved_neutral 状态标签「中性」→「带内中性」
// （与方向「中性」消歧，后端 _STATUS_LABELS 已同步）
describe('切片空态折叠与术语消歧（update-track-record-display-clarity）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('切片空态:settled=0 时折叠为一行说明,不渲染分桶表格', async () => {
    mockFetch({ current: [], predictions: [] })
    renderPage()
    expect(await screen.findByTestId('track-record-segments-empty')).toHaveTextContent('切片指标将在首批观点结算后可用')
    expect(screen.queryByTestId('track-record-segments')).not.toBeInTheDocument()
  })

  it('术语消歧:横幅用已结算,带内中性标签替换中性', async () => {
    mockFetch({ current: [], predictions: [
      { ...PREDICTIONS[0], status: 'resolved_neutral', resolution_rule: 'superseded' },
    ] })
    renderPage()
    // 横幅:已结算(原「已判定 0 条」)
    expect(await screen.findByTestId('track-record-insufficient')).toHaveTextContent('已结算 0 条')
    expect(screen.getByTestId('track-record-insufficient').textContent).not.toContain('已判定 0 条')
    // 状态标签:带内中性
    expect(screen.getByTestId('prediction-log')).toHaveTextContent('带内中性')
  })
})
