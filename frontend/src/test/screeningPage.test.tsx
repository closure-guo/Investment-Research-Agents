import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import App from '../App'
import { ScreeningPage } from '../pages/screening/ScreeningPage'
import type { ScreeningPoolResponse, ScreeningQueueEntry } from '../types'

/**
 * 选股页单测（add-screening-pipeline T4；S5 + spec frontend delta）。
 *
 * - 表格列齐（标的/综合分/连续在榜天数/L3 verdict+reason/批次日期）；
 * - 综合分 hover → 浮层（testid screening-contribs-tooltip）按 |contribution| 降序；
 * - 批次 stale → 横幅（testid screening-batch-stale-banner）标注最近成功批次日期；
 * - 空态；「对精选池跑深度分析」→ POST /api/screening/analyze → 排队徽标
 *   （testid screening-queue-badge-{symbol}）+ 移除原因展示 + 2s 轮询刷新徽标；
 * - 路由：/screening 直达与侧边栏入口（App 壳层）。
 *
 * fetch 全 mock（页面契约层单测；真实页面行为由 E2E 门禁覆盖）。
 */

const BATCH_DATE = '2026-10-13'

function poolBody(overrides: Partial<ScreeningPoolResponse> = {}): ScreeningPoolResponse {
  return {
    batch_date: BATCH_DATE,
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
    ],
    queue: [],
    ...overrides,
  }
}

function queueEntry(symbol: string, state: string): ScreeningQueueEntry {
  return { symbol, session_id: `${symbol}-s`, state }
}

function mockFetch(
  opts: {
    pool?: ScreeningPoolResponse[]
    analyze?: unknown
    analyzeStatus?: number
  } = {}
) {
  const respond = (body: unknown, status = 200) =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })
    )
  const poolBodies = opts.pool ?? [poolBody()]
  let poolCalls = 0
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/api/screening/pool' && (!init || !init.method || init.method === 'GET')) {
      const body = poolBodies[Math.min(poolCalls, poolBodies.length - 1)]
      poolCalls += 1
      return respond(body)
    }
    if (url === '/api/screening/analyze') {
      return respond(opts.analyze ?? { started: [], queued: [], removed: [] }, opts.analyzeStatus ?? 200)
    }
    if (url === '/api/sessions') return respond({ sessions: [] })
    if (url === '/api/snapshot/status') {
      return respond({ latest_date: null, stale: false, message: null, last_error: null, last_error_date: null })
    }
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('降级模式披露（add-screening-empty-library-fallback）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  it('fallback 批次 → 降级横幅（含「非因子信号」）+ 列标题/数值口径切换', async () => {
    const fallbackPool = [
      {
        symbol: '600519',
        composite_score: 102000000, // 1.02 亿元（降级模式下 score = 近 20 日日均成交额）
        rank: 1,
        consecutive_days: 5,
        l3_verdict: 'keep',
        l3_reason: '无重大风险事件',
        factor_contribs: [],
      },
    ]
    mockFetch({
      pool: [
        poolBody({
          selection_mode: 'fallback',
          notice: '因子库无可打分因子：本次为降级模式，按近 20 日日均成交额排序（非因子信号）',
          pool: fallbackPool,
        }),
      ],
    })
    render(<ScreeningPage onBack={() => {}} />)

    const banner = await screen.findByTestId('screening-fallback-banner')
    expect(banner.textContent).toContain('非因子信号')

    const table = await screen.findByTestId('screening-pool-table')
    expect(within(table).getByText('近 20 日日均成交额（亿元）')).toBeTruthy()
    expect(within(table).queryByText('综合分')).toBeNull()
    expect(within(screen.getByTestId('screening-row-600519')).getByText('1.02')).toBeTruthy()
  })

  it('factor 批次 → 无降级横幅（回归钉死）', async () => {
    mockFetch()
    render(<ScreeningPage onBack={() => {}} />)

    await screen.findByTestId('screening-pool-table')
    expect(screen.queryByTestId('screening-fallback-banner')).toBeNull()
    expect(screen.getByText('综合分')).toBeTruthy()
  })
})

describe('选股页（add-screening-pipeline T4）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  it('表格渲染池行：列齐 + 批次日期 + L3 理由 + 连续在榜天数', async () => {
    mockFetch()
    render(<ScreeningPage onBack={() => {}} />)

    const table = await screen.findByTestId('screening-pool-table')
    expect(within(table).getByText('标的')).toBeTruthy()
    expect(within(table).getByText('综合分')).toBeTruthy()
    expect(within(table).getByText('连续在榜天数')).toBeTruthy()
    expect(within(table).getByText('L3 判定')).toBeTruthy()
    expect(within(table).getByText('批次日期')).toBeTruthy()

    const row = within(screen.getByTestId('screening-row-600519'))
    expect(row.getByText('600519')).toBeTruthy()
    expect(row.getByText('1.823')).toBeTruthy()
    expect(row.getByText('5')).toBeTruthy()
    expect(row.getByText('无重大风险事件')).toBeTruthy()
    expect(row.getByText(BATCH_DATE)).toBeTruthy() // 批次日期列
  })

  it('hover 综合分 → 浮层按 |contribution| 降序展示 factor_contribs', async () => {
    mockFetch()
    render(<ScreeningPage onBack={() => {}} />)
    await screen.findByTestId('screening-pool-table')

    expect(screen.queryByTestId('screening-contribs-tooltip')).toBeNull() // 未 hover 不渲染

    fireEvent.mouseEnter(screen.getByTestId('screening-score-600519'))
    const tooltip = await screen.findByTestId('screening-contribs-tooltip')

    // |−0.83| > |0.42| > |0.11| → 3 → 11 → 7
    const items = within(tooltip).getAllByTestId(/^screening-contrib-/)
    expect(items.map(node => node.getAttribute('data-testid'))).toEqual([
      'screening-contrib-3',
      'screening-contrib-11',
      'screening-contrib-7',
    ])
    expect(tooltip.textContent).toContain('因子 3')
    expect(tooltip.textContent).toContain('-0.8300')
    expect(tooltip.textContent).toContain('0.4200')

    fireEvent.mouseLeave(screen.getByTestId('screening-score-600519'))
    await waitFor(() => expect(screen.queryByTestId('screening-contribs-tooltip')).toBeNull())
  })

  it('批次 stale → 横幅标注最近成功批次日期', async () => {
    mockFetch({
      pool: [poolBody({ batch_date: '2026-10-09', batch_status: 'failed', stale: true })],
    })
    render(<ScreeningPage onBack={() => {}} />)

    const banner = await screen.findByTestId('screening-batch-stale-banner')
    expect(banner.textContent).toContain('2026-10-09')
    expect(banner.textContent).toContain('选股批次失败')
    await screen.findByTestId('screening-pool-table') // 仍展示最近成功批次池
  })

  it('空态：无成功批次 → 空提示 + 分析按钮禁用', async () => {
    mockFetch({ pool: [poolBody({ batch_date: null, batch_status: null, pool: [] })] })
    render(<ScreeningPage onBack={() => {}} />)

    expect(await screen.findByTestId('screening-empty')).toBeTruthy()
    expect(screen.queryByTestId('screening-pool-table')).toBeNull()
    expect((screen.getByTestId('screening-analyze-btn') as HTMLButtonElement).disabled).toBe(true)
  })

  it('点击批量分析 → POST + 排队徽标 + 移除原因展示，并按轮询刷新徽标', async () => {
    const fetchMock = mockFetch({
      pool: [
        poolBody(), // 首屏
        poolBody({
          queue: [
            queueEntry('600519', 'done'), // 轮询后：600519 已完成（徽标消失）
            queueEntry('000001', 'running'),
          ],
        }),
      ],
      analyze: {
        started: [queueEntry('000001', 'running')],
        queued: [queueEntry('600519', 'queued')],
        removed: [{ symbol: '300750', reason: 'SUSPENDED', detail: '停牌' }],
      },
    })
    render(<ScreeningPage onBack={() => {}} pollIntervalMs={20} />)
    await screen.findByTestId('screening-pool-table')

    fireEvent.click(screen.getByTestId('screening-analyze-btn'))

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/screening/analyze', expect.objectContaining({ method: 'POST' }))
    )
    // 排队/运行态徽标（三只标的：600519 排队、000001 运行、300750 移除）
    const queuedBadge = await screen.findByTestId('screening-queue-badge-600519')
    expect(queuedBadge.textContent).toContain('排队中')
    expect(screen.getByTestId('screening-queue-badge-000001').textContent).toContain('分析中')
    // 移除原因展示（symbol + reason）
    const removed = await screen.findByTestId('screening-removed-300750')
    expect(removed.textContent).toContain('300750')
    expect(removed.textContent).toContain('SUSPENDED')

    // 2s 轮询刷新（注入 20ms）：600519 终态后徽标消失，运行中保留
    await waitFor(() => expect(screen.queryByTestId('screening-queue-badge-600519')).toBeNull())
    expect(screen.getByTestId('screening-queue-badge-000001')).toBeTruthy()
  })

  it('直达 /screening 渲染选股页（刷新保持路由语义）', async () => {
    mockFetch()
    window.history.pushState({}, '', '/screening')
    render(<App />)

    expect(await screen.findByTestId('screening-page')).toBeTruthy()
    expect(await screen.findByTestId('screening-pool-table')).toBeTruthy()
  })

  it('侧边栏入口点击进入 /screening', async () => {
    localStorage.setItem('fa_api_key', 'test-key')
    mockFetch()
    render(<App />)

    const entry = await screen.findByTestId('sidebar-screening')
    fireEvent.click(entry)

    expect(await screen.findByTestId('screening-page')).toBeTruthy()
    expect(window.location.pathname).toBe('/screening')
  })
})
