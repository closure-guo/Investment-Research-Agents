import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, within, cleanup } from '@testing-library/react'
import App from '../App'
import { BrokerConsolePage } from '../pages/broker/BrokerConsolePage'
import type { BrokerOrdersResponse, BrokerOverview, BrokerPositionsResponse } from '../types'

/**
 * 执行台账页单测（delta add-broker-console T2.2；spec frontend「执行台账页」）。
 *
 * - 指标条：总资产 / 可用资金 / 持仓市值 / 浮动盈亏；市值不可得（null）→ 显式标注
 *   （「—」+ 标注文案），MUST NOT 显示 0 冒充；
 * - 主图表 = 持仓市值构成（按标的饼图，值取持仓行 market_value）；空仓 → 空态不画空图；
 * - Tab 表：持仓 / 订单 / 门禁拒绝三页签切换；订单四态 StatusBadge 映射
 *   （pending→running、filled→active、rejected→failed、expired→retired）；
 * - 门禁拒绝页签含原因码与关联决策（decision_id/signal_id）；
 * - 空账户空态如实：指标条显示资金、图表/表格空态文案，MUST NOT 渲染伪持仓/伪订单；
 * - 入口：/broker 直达、侧边栏折叠图标（aria-label="执行台账"）、命令面板项。
 *
 * ECharts 以 option 捕获替身断言（同 chartsMarkLine.test 先例：jsdom 不渲染 canvas）；
 * fetch 全 mock（页面契约层单测；真实页面行为由 E2E 门禁覆盖）。
 */

const capturedOptions: unknown[] = []
vi.mock('echarts-for-react', () => ({
  default: ({ option }: { option: unknown }) => {
    capturedOptions.push(option)
    return null
  },
}))

const OVERVIEW: BrokerOverview = {
  cash: 876_543.21,
  positions_value: 178_000,
  equity: 1_054_543.21,
  unrealized_pnl: 4_000,
  position_cost: 174_000,
  positions_count: 2,
  snapshot_date: '2026-10-13',
  missing_symbols: [],
  market_value_note: null,
}

const POSITIONS: BrokerPositionsResponse = {
  snapshot_date: '2026-10-13',
  positions: [
    {
      symbol: '000001.SZ',
      qty: 2000,
      avg_cost: 12,
      updated_at: '2026-10-13',
      close: 13,
      market_value: 26_000,
      unrealized_pnl: 2_000,
    },
    {
      symbol: '600519.SH',
      qty: 100,
      avg_cost: 1500,
      updated_at: '2026-10-13',
      close: 1520,
      market_value: 152_000,
      unrealized_pnl: 2_000,
    },
  ],
}

const ORDER_STATES = [
  {
    order_id: 'sig-pending:600519.SH:buy',
    symbol: '600519.SH',
    side: 'buy',
    qty: 100,
    status: 'pending',
    reason: 'LIMIT_UP_UNFILLABLE',
    fill_price: null,
    fee: 0,
    decision_id: 'decision-sig-pending',
    signal_id: 'sig-pending',
    created_at: '2026-10-13T09:31:00',
    attempts: 0,
  },
  {
    order_id: 'sig-filled:000001.SZ:buy',
    symbol: '000001.SZ',
    side: 'buy',
    qty: 2000,
    status: 'filled',
    reason: null,
    fill_price: 13,
    fee: 5,
    decision_id: 'decision-sig-filled',
    signal_id: 'sig-filled',
    created_at: '2026-10-13T09:32:00',
    attempts: 0,
  },
  {
    order_id: 'sig-gate:600519.SH:sell',
    symbol: '600519.SH',
    side: 'sell',
    qty: 100,
    status: 'rejected',
    reason: 'ST_FLAG',
    fill_price: null,
    fee: 0,
    decision_id: 'decision-sig-gate',
    signal_id: 'sig-gate',
    created_at: '2026-10-13T09:33:00',
    attempts: 0,
  },
  {
    order_id: 'sig-expired:000001.SZ:sell',
    symbol: '000001.SZ',
    side: 'sell',
    qty: 500,
    status: 'expired',
    reason: 'CANCELED',
    fill_price: null,
    fee: 0,
    decision_id: 'decision-sig-expired',
    signal_id: 'sig-expired',
    created_at: '2026-10-13T09:34:00',
    attempts: 3,
  },
] as unknown as BrokerOrdersResponse['orders']

const ORDERS: BrokerOrdersResponse = { orders: ORDER_STATES }
// 门禁拒绝视图 = status=rejected 子集（后端已过滤；前端原样呈现）
const GATE_LOG: BrokerOrdersResponse = {
  orders: ORDER_STATES.filter(order => order.status === 'rejected'),
}

const EMPTY_OVERVIEW: BrokerOverview = {
  cash: 1_000_000,
  positions_value: 0,
  equity: 1_000_000,
  unrealized_pnl: 0,
  position_cost: 0,
  positions_count: 0,
  snapshot_date: null,
  missing_symbols: [],
  market_value_note: null,
}

/** 快照缺失态：有持仓但市值不可得（null + 标注，MUST NOT 填 0） */
const MISSING_MARK_OVERVIEW: BrokerOverview = {
  cash: 876_543.21,
  positions_value: null,
  equity: null,
  unrealized_pnl: null,
  position_cost: 174_000,
  positions_count: 2,
  snapshot_date: null,
  missing_symbols: ['000001.SZ', '600519.SH'],
  market_value_note: '快照缺失（无行情数据），持仓市值不可得，未以 0 冒充',
}

const MISSING_MARK_POSITIONS: BrokerPositionsResponse = {
  snapshot_date: null,
  positions: POSITIONS.positions.map(row => ({
    ...row,
    close: null,
    market_value: null,
    unrealized_pnl: null,
  })),
}

function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  )
}

function mockBrokerApi(
  opts: {
    overview?: BrokerOverview
    positions?: BrokerPositionsResponse
    orders?: BrokerOrdersResponse
    gateLog?: BrokerOrdersResponse
  } = {}
) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/api/broker/overview') return jsonResponse(opts.overview ?? OVERVIEW)
    if (url === '/api/broker/positions') return jsonResponse(opts.positions ?? POSITIONS)
    if (url === '/api/broker/orders') return jsonResponse(opts.orders ?? ORDERS)
    if (url === '/api/broker/gate-log') return jsonResponse(opts.gateLog ?? GATE_LOG)
    if (url === '/api/sessions') return jsonResponse({ sessions: [] })
    if (url === '/api/snapshot/status') {
      return jsonResponse({ latest_date: null, stale: false, message: null, last_error: null, last_error_date: null })
    }
    if (url === '/api/llm-config') return jsonResponse({ model: '', base_url: '', thinking: 'enabled' })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('执行台账页（delta add-broker-console T2.2）', () => {
  beforeEach(() => {
    localStorage.clear()
    capturedOptions.length = 0
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  it('指标条：总资产/可用资金/持仓市值/浮动盈亏 逐项渲染（资金与市值口径分离）', async () => {
    mockBrokerApi()
    render(<BrokerConsolePage onBack={() => {}} />)

    const cash = await screen.findByTestId('page-shell-metric-cash')
    expect(cash.textContent).toContain('可用资金')
    expect(cash.textContent).toContain('876,543.21')

    const positionsValue = screen.getByTestId('page-shell-metric-positions_value')
    expect(positionsValue.textContent).toContain('持仓市值')
    expect(positionsValue.textContent).toContain('178,000.00')

    const equity = screen.getByTestId('page-shell-metric-equity')
    expect(equity.textContent).toContain('总资产')
    expect(equity.textContent).toContain('1,054,543.21')

    // 浮动盈亏带符号（+ 精确到分）
    const pnl = screen.getByTestId('page-shell-metric-unrealized_pnl')
    expect(pnl.textContent).toContain('浮动盈亏')
    expect(pnl.textContent).toContain('+¥4,000.00')
  })

  it('指标条 null 标注：市值不可得 → 「—」+ 标注，MUST NOT 显示 0 冒充', async () => {
    mockBrokerApi({ overview: MISSING_MARK_OVERVIEW, positions: MISSING_MARK_POSITIONS })
    render(<BrokerConsolePage onBack={() => {}} />)

    const positionsValue = await screen.findByTestId('page-shell-metric-positions_value')
    expect(positionsValue.textContent).toContain('—')
    expect(positionsValue.textContent).not.toContain('0.00')

    const equity = screen.getByTestId('page-shell-metric-equity')
    expect(equity.textContent).toContain('—')

    // 显式标注（可判读文案：快照缺失/不可得）
    const note = screen.getByTestId('broker-market-value-note')
    expect(note.textContent).toContain('快照')
    expect(note.textContent).toContain('不可得')

    // 市值不可得时不画空图：构成图空态如实说明（与「无持仓」文案区分）
    expect(screen.getByTestId('broker-chart-empty').textContent).toContain('快照缺失')
    expect(capturedOptions.length).toBe(0)

    // 资金仍如实（账本口径可得）
    expect(screen.getByTestId('page-shell-metric-cash').textContent).toContain('876,543.21')
  })

  it('主图表：持仓市值构成按标的出饼图（值取持仓行 market_value）', async () => {
    mockBrokerApi()
    render(<BrokerConsolePage onBack={() => {}} />)
    await screen.findByTestId('page-shell-chart')

    const option = capturedOptions[capturedOptions.length - 1] as {
      series: { type: string; data: { name: string; value: number }[] }[]
    }
    expect(option.series[0].type).toBe('pie')
    expect(option.series[0].data).toEqual([
      { name: '000001.SZ', value: 26_000 },
      { name: '600519.SH', value: 152_000 },
    ])
  })

  it('Tab 切换：持仓 → 订单 → 门禁拒绝（内容随页签切换）', async () => {
    mockBrokerApi()
    render(<BrokerConsolePage onBack={() => {}} />)
    await screen.findByTestId('page-shell-tab-positions')

    // 默认持仓页签
    expect(screen.getByTestId('page-shell-tab-content').getAttribute('data-tab')).toBe('positions')
    expect(screen.getByTestId('broker-position-row-600519.SH').textContent).toContain('1,520.00')

    fireEvent.click(screen.getByTestId('page-shell-tab-orders'))
    expect(screen.getByTestId('page-shell-tab-content').getAttribute('data-tab')).toBe('orders')
    expect(screen.getByTestId('broker-order-row-sig-gate:600519.SH:sell')).toBeTruthy()

    fireEvent.click(screen.getByTestId('page-shell-tab-gate-log'))
    expect(screen.getByTestId('page-shell-tab-content').getAttribute('data-tab')).toBe('gate-log')
    const gateRow = screen.getByTestId('broker-gate-row-sig-gate:600519.SH:sell')
    expect(gateRow.textContent).toContain('ST_FLAG')
    expect(gateRow.textContent).toContain('decision-sig-gate') // 关联决策
  })

  it('状态徽章映射：pending→running、filled→active、rejected→failed、expired→retired', async () => {
    mockBrokerApi()
    render(<BrokerConsolePage onBack={() => {}} />)
    await screen.findByTestId('page-shell-tab-orders')
    fireEvent.click(screen.getByTestId('page-shell-tab-orders'))

    const expectStatus = (orderId: string, status: string) => {
      const cell = within(screen.getByTestId(`broker-order-status-${orderId}`))
      expect(cell.getByTestId('status-badge').getAttribute('data-status')).toBe(status)
    }
    expectStatus('sig-pending:600519.SH:buy', 'running')
    expectStatus('sig-filled:000001.SZ:buy', 'active')
    expectStatus('sig-gate:600519.SH:sell', 'failed')
    expectStatus('sig-expired:000001.SZ:sell', 'retired')
  })

  it('空账户空态如实：指标条显示资金、图表/三页签空态，MUST NOT 伪持仓/伪订单', async () => {
    mockBrokerApi({
      overview: EMPTY_OVERVIEW,
      positions: { snapshot_date: null, positions: [] },
      orders: { orders: [] },
      gateLog: { orders: [] },
    })
    render(<BrokerConsolePage onBack={() => {}} />)

    expect((await screen.findByTestId('page-shell-metric-cash')).textContent).toContain('1,000,000.00')
    // 空仓不画空图：不渲染 ECharts 实例，给空态文案
    expect(screen.getByTestId('broker-chart-empty').textContent).toContain('无持仓')
    expect(capturedOptions.length).toBe(0)

    // 持仓页签空态
    expect(screen.getByTestId('broker-positions-empty').textContent).toContain('暂无持仓')

    fireEvent.click(screen.getByTestId('page-shell-tab-orders'))
    expect(screen.getByTestId('broker-orders-empty').textContent).toContain('暂无订单')
    expect(screen.queryByTestId('broker-order-row-sig-filled:000001.SZ:buy')).toBeNull()

    fireEvent.click(screen.getByTestId('page-shell-tab-gate-log'))
    expect(screen.getByTestId('broker-gate-empty').textContent).toContain('暂无门禁拒绝记录')
  })

  it('直达 /broker 渲染执行台账页', async () => {
    mockBrokerApi()
    window.history.pushState({}, '', '/broker')
    render(<App />)

    expect(await screen.findByTestId('broker-page')).toBeTruthy()
    expect(await screen.findByTestId('page-shell-a')).toBeTruthy()
  })

  it('侧边栏折叠图标（aria-label=执行台账）进入 /broker', async () => {
    localStorage.setItem('fa_sidebar_collapsed', '1')
    mockBrokerApi()
    render(<App />)

    const entry = await screen.findByTestId('sidebar-broker-collapsed')
    expect(entry.getAttribute('aria-label')).toBe('执行台账')
    fireEvent.click(entry)

    expect(await screen.findByTestId('broker-page')).toBeTruthy()
    expect(window.location.pathname).toBe('/broker')
  })

  it('命令面板项进入 /broker', async () => {
    mockBrokerApi()
    render(<App />)
    await screen.findByTestId('sidebar-new')

    fireEvent.keyDown(window, { key: 'k', metaKey: true, bubbles: true })
    const action = await screen.findByTestId('action-broker')
    expect(action.textContent).toContain('执行台账')
    fireEvent.click(action)

    expect(await screen.findByTestId('broker-page')).toBeTruthy()
    expect(window.location.pathname).toBe('/broker')
  })
})
