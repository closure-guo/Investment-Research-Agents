import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { EventWatchlistCard } from '../pages/screening/EventWatchlistCard'
import { ScreeningPage } from '../pages/screening/ScreeningPage'
import type {
  EventAnalyzeResponse,
  EventWatchlistItem,
  EventWatchlistResponse,
  RegimePayload,
  ScreeningPoolResponse,
  ScreeningQueueEntry,
} from '../types'

/**
 * 选股页「事件观察」区块单测（delta add-event-driven-lane T7；spec frontend
 * 「选股页事件观察区块」）。
 *
 * 契约（testid 见任务简报）：
 * - `event-watchlist-card` / `event-watchlist-row-{id}` / `event-verdict-badge-{id}` /
 *   `event-analyze-btn-{id}` / `event-watchlist-empty` / `event-watchlist-error` /
 *   `event-source-filter`；
 * - 条目全要素：来源标识、标的与名称、来源相关要素（业绩预告：报告期/预告类型/
 *   净利润变动幅度区间；条款事件 cbb：现价/转股溢价率/强赎倒计时，缺字段 `—` 不折算）、
 *   三态语义色徽章（outperform/in_line/risk，组件内本地实现，不扩展 StatusKey）、
 *   理由与证据引用、公告日期、清单状态（是否已发起 + 排队/运行/完成）；
 * - 口径注释「研究标记，非投资建议」；
 * - 「发起深度分析」→ `POST /api/events/watchlist/{id}/analyze`（响应 queue_state
 *   即刻渲染徽标；幂等响应 already_started=true 不重复提交）；后续队列态跟随页面
 *   既有队列快照（queue prop，轮询节奏 = ScreeningPage 的 pollIntervalMs）；
 * - 空清单 → `event-watchlist-empty`；请求失败 → `event-watchlist-error`（重试）；
 *   两者严格区分，MUST NOT 骨架屏冒充；
 * - `?source=` 过滤：默认不带参数（避免后端 WHERE source='' 误过滤），选择来源后带参数重取。
 *
 * fetch 全 mock（组件契约层单测；真实页面行为由 E2E 门禁覆盖）。
 */

const ANNOUNCE_DATE = '2025-04-15'

function item(overrides: Partial<EventWatchlistItem> = {}): EventWatchlistItem {
  return {
    id: 1,
    forecast_key: '600519|2025-03-31|预增|2025-04-15',
    source: 'earnings',
    status: 'pending',
    session_id: null,
    created_at: '2025-04-15T16:15:00',
    code: '600519',
    name: '贵州茅台',
    report_period: '2025-03-31',
    forecast_type: '预增',
    change_low: 60,
    change_high: 90,
    announce_date: ANNOUNCE_DATE,
    available_at: ANNOUNCE_DATE,
    verdict: 'outperform',
    reason: '归母净利润预增 60%~90%，业绩显著超预期',
    evidence: [{ field: 'forecast_type', value: '预增' }],
    model: 'gpt-test',
    prompt_fingerprint: 'fp-1',
    triaged_at: '2025-04-15T16:15:30',
    ...overrides,
  }
}

function cbbItem(overrides: Partial<EventWatchlistItem> = {}): EventWatchlistItem {
  return item({
    id: 9,
    forecast_key: 'cbb|113050|强赎|2025-04-15',
    source: 'cbb',
    code: '113050',
    name: '南银转债',
    report_period: null,
    forecast_type: null,
    change_low: null,
    change_high: null,
    verdict: 'risk',
    reason: '强赎倒计时进行中',
    evidence: [{ field: 'count_text', value: '12/15｜30' }],
    ...overrides,
  })
}

function queueEntry(session_id: string, state: string): ScreeningQueueEntry {
  return { symbol: '600519', session_id, state }
}

function poolBody(overrides: Partial<ScreeningPoolResponse> = {}): ScreeningPoolResponse {
  return {
    batch_date: '2025-04-15',
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
    pool: [],
    queue: [],
    ...overrides,
  }
}

type WatchlistCall = { index: number; source: string | null }

// 清单响应：固定载荷 / 按调用序取值函数 / Error（模拟取数失败）
type WatchlistValue = EventWatchlistResponse | Error
type WatchlistInput = WatchlistValue | ((call: WatchlistCall) => WatchlistValue)

function mockFetch(
  opts: {
    watchlist?: WatchlistInput
    analyze?: Partial<EventAnalyzeResponse> | Error
    queue?: ScreeningQueueEntry[]
    regime?: RegimePayload
    /** 池载荷：固定体或按调用序推进（首屏 + 轮询刷新） */
    pool?: Partial<ScreeningPoolResponse> | Array<Partial<ScreeningPoolResponse>>
  } = {}
) {
  const respond = (body: unknown, status = 200) =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })
    )
  const watchlistCalls: WatchlistCall[] = []
  let poolCalls = 0
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (/^\/api\/events\/watchlist\/\d+\/analyze$/.test(url) && init?.method === 'POST') {
      const value = opts.analyze
      if (value instanceof Error) return Promise.reject(value)
      return respond(
        value ?? { id: 0, session_id: null, status: null, queue_state: null, already_started: false }
      )
    }
    if (url.startsWith('/api/events/watchlist')) {
      const source = new URL(url, 'http://localhost').searchParams.get('source')
      const call: WatchlistCall = { index: watchlistCalls.length, source }
      watchlistCalls.push(call)
      const configured = opts.watchlist
      const value = typeof configured === 'function' ? configured(call) : configured
      if (value instanceof Error) return Promise.reject(value)
      return respond(value ?? { items: [] })
    }
    if (url === '/api/regime') {
      return respond(opts.regime ?? { available: false, reason: 'missing' })
    }
    if (url === '/api/screening/pool') {
      const bodies = Array.isArray(opts.pool)
        ? opts.pool
        : [opts.pool ?? poolBody({ queue: opts.queue ?? [] })]
      const body = bodies[Math.min(poolCalls, bodies.length - 1)]
      poolCalls += 1
      return respond(body)
    }
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
  return {
    fetchMock,
    watchlistCalls,
    analyzeCalls: () =>
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/analyze')),
  }
}

beforeEach(() => {
  localStorage.clear()
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('EventWatchlistCard 清单渲染', () => {
  it('固定高度滚动框 + 默认只渲染前 50 条 + 加载更多', async () => {
    const many = Array.from({ length: 60 }, (_, index) => item({ id: index + 1 }))
    mockFetch({ watchlist: { items: many } })
    render(<EventWatchlistCard />)

    const scroll = await screen.findByTestId('event-watchlist-scroll')
    expect(scroll.className).toContain('overflow-y-auto')
    expect(scroll.getAttribute('data-max-height')).toBe('420')
    expect(screen.getByTestId('event-watchlist-count').textContent).toContain('60')
    expect(screen.getAllByTestId(/^event-watchlist-row-/)).toHaveLength(50)

    fireEvent.click(screen.getByTestId('event-watchlist-load-more'))
    expect(screen.getAllByTestId(/^event-watchlist-row-/)).toHaveLength(60)
    expect(screen.queryByTestId('event-watchlist-load-more')).toBeNull()
  })

  it('行默认折叠：明细不渲染，点击行标题后展开', async () => {
    mockFetch({ watchlist: { items: [item()] } })
    render(<EventWatchlistCard />)

    await screen.findByTestId('event-watchlist-row-1')
    expect(screen.queryByTestId('event-element-1-report_period')).toBeNull()
    expect(screen.queryByTestId('event-evidence-1')).toBeNull()

    fireEvent.click(screen.getByTestId('event-row-toggle-1'))
    expect(await screen.findByTestId('event-element-1-report_period')).toBeTruthy()
    expect(screen.getByTestId('event-evidence-1')).toBeTruthy()

    fireEvent.click(screen.getByTestId('event-row-toggle-1'))
    await waitFor(() => expect(screen.queryByTestId('event-element-1-report_period')).toBeNull())
  })

  it('渲染条目全要素（来源标识/标的与名称/预告要素/理由与证据/公告日期/发起入口）', async () => {
    mockFetch({ watchlist: { items: [item()] } })
    render(<EventWatchlistCard />)

    const row = await screen.findByTestId('event-watchlist-row-1')
    // 明细默认折叠（固定高度滚动框内），断言要素前先展开
    fireEvent.click(screen.getByTestId('event-row-toggle-1'))
    expect(screen.getByTestId('event-watchlist-card')).toBeTruthy()
    // 来源标识
    expect(within(row).getByTestId('event-source-badge-1').textContent).toContain('业绩预告')
    // 标的与名称
    expect(row.textContent).toContain('600519')
    expect(row.textContent).toContain('贵州茅台')
    // 来源相关要素（业绩预告：报告期/预告类型/净利润变动幅度区间）
    expect(within(row).getByTestId('event-element-1-report_period').textContent).toContain('2025-03-31')
    expect(within(row).getByTestId('event-element-1-forecast_type').textContent).toContain('预增')
    expect(within(row).getByTestId('event-element-1-change').textContent).toContain('60.0% ~ 90.0%')
    // 理由与证据引用
    expect(row.textContent).toContain('归母净利润预增 60%~90%')
    expect(within(row).getByTestId('event-evidence-1').textContent).toContain('forecast_type=预增')
    // 公告日期
    expect(row.textContent).toContain(ANNOUNCE_DATE)
    // 发起入口
    expect(within(row).getByTestId('event-analyze-btn-1').textContent).toContain('发起深度分析')
    expect(screen.queryByTestId('event-watchlist-empty')).toBeNull()
    expect(screen.queryByTestId('event-watchlist-error')).toBeNull()
  })

  it.each([
    ['outperform', '超预期', '--status-success-default'],
    ['in_line', '符合预期', '--status-warning-default'],
    ['risk', '风险', '--status-error-default'],
  ] as const)('verdict=%s → 徽章 %s 语义色', async (verdict, label, colorVar) => {
    mockFetch({ watchlist: { items: [item({ verdict })] } })
    render(<EventWatchlistCard />)

    const badge = await screen.findByTestId('event-verdict-badge-1')
    expect(badge.getAttribute('data-verdict')).toBe(verdict)
    expect(badge.textContent).toContain(label)
    expect(badge.getAttribute('style')).toContain(colorVar)
  })

  it('结论附口径注释「研究标记，非投资建议」', async () => {
    mockFetch({ watchlist: { items: [item()] } })
    render(<EventWatchlistCard />)

    const note = await screen.findByTestId('event-verdict-caliber-note')
    expect(note.textContent).toContain('研究标记，非投资建议')
    // 徽章自身也带口径 tooltip（结论级披露，避免只看徽章时误读）
    const badge = screen.getByTestId('event-verdict-badge-1')
    expect(badge.getAttribute('title')).toContain('研究标记，非投资建议')
  })

  it('缺字段照实渲染 —（不折算、不编造）', async () => {
    mockFetch({
      watchlist: {
        items: [
          item({
            name: null,
            report_period: null,
            forecast_type: null,
            change_low: null,
            change_high: null,
            announce_date: null,
            reason: null,
            evidence: null,
          }),
        ],
      },
    })
    render(<EventWatchlistCard />)

    const row = await screen.findByTestId('event-watchlist-row-1')
    fireEvent.click(screen.getByTestId('event-row-toggle-1'))
    expect(within(row).getByTestId('event-element-1-report_period').textContent).toContain('—')
    expect(within(row).getByTestId('event-element-1-forecast_type').textContent).toContain('—')
    expect(within(row).getByTestId('event-element-1-change').textContent).toContain('—')
    expect(within(row).getByTestId('event-evidence-1').textContent).toContain('—')
    // 证据 JSON 损坏（后端返回 null）不得伪装成空证据以外的编造内容
    expect(within(row).getByTestId('event-evidence-1').textContent).not.toContain('=')
  })

  it('条款事件（source=cbb）→ 现价/转股溢价率/剩余规模/强赎倒计时要素', async () => {
    mockFetch({
      watchlist: {
        items: [
          cbbItem({ price: 108.2, premium_rate: 12.4, remaining_size: 23.5, call_countdown_days: 3 }),
        ],
      },
    })
    render(<EventWatchlistCard />)

    const row = await screen.findByTestId('event-watchlist-row-9')
    fireEvent.click(screen.getByTestId('event-row-toggle-9'))
    expect(within(row).getByTestId('event-source-badge-9').textContent).toContain('条款事件')
    expect(within(row).getByTestId('event-element-9-price').textContent).toContain('108.20')
    expect(within(row).getByTestId('event-element-9-premium_rate').textContent).toContain('12.4%')
    expect(within(row).getByTestId('event-element-9-remaining_size').textContent).toContain('23.50 亿元')
    expect(within(row).getByTestId('event-element-9-countdown').textContent).toContain('3 个交易日')
  })

  it('条款事件（source=cbb）→ 不提供「发起深度分析」入口（v1 禁用，后端 409 同义）', async () => {
    mockFetch({
      watchlist: { items: [cbbItem(), item({ id: 1 })] },
    })
    render(<EventWatchlistCard />)

    const cbbRow = await screen.findByTestId('event-watchlist-row-9')
    expect(within(cbbRow).queryByTestId('event-analyze-btn-9')).toBeNull()
    // 业绩预告条目照常保留该入口（不影响既有权路径）
    const earningsRow = await screen.findByTestId('event-watchlist-row-1')
    expect(within(earningsRow).getByTestId('event-analyze-btn-1').textContent).toContain(
      '发起深度分析'
    )
  })

  it('条款事件要素缺失 → 四格均如实 —（显式 null 与键缺位两条路径都不折算、不隐藏）', async () => {
    // cbbItem 缺省即不带四要素键（真实缺位路径）；第二条例显式 null（未来后端置空路径）
    const missingKeys = cbbItem({ id: 10 })
    expect('price' in missingKeys).toBe(false)
    expect('remaining_size' in missingKeys).toBe(false)
    mockFetch({
      watchlist: {
        items: [
          missingKeys,
          cbbItem({
            id: 11,
            price: null,
            premium_rate: null,
            remaining_size: null,
            call_countdown_days: null,
          }),
        ],
      },
    })
    render(<EventWatchlistCard />)

    for (const [id, testId] of [
      [10, 'event-watchlist-row-10'],
      [11, 'event-watchlist-row-11'],
    ] as const) {
      const row = await screen.findByTestId(testId)
      fireEvent.click(screen.getByTestId(`event-row-toggle-${id}`))
      for (const key of ['price', 'premium_rate', 'remaining_size', 'countdown']) {
        expect(within(row).getByTestId(`event-element-${id}-${key}`).textContent).toContain('—')
      }
    }
  })

  it('未知 verdict（原型链键）→ 不误取语义色，按原值中性展示', async () => {
    mockFetch({ watchlist: { items: [item({ verdict: 'constructor' })] } })
    render(<EventWatchlistCard />)

    const badge = await screen.findByTestId('event-verdict-badge-1')
    expect(badge.getAttribute('data-verdict')).toBe('constructor')
    expect(badge.textContent).toContain('constructor')
    expect(badge.getAttribute('style')).toContain('--text-tertiary')
  })
})

describe('EventWatchlistCard 发起深度分析', () => {
  it('点击 → POST 入既有队列 → 排队中徽标 + 关联会话 + 通知页面开始轮询', async () => {
    const onAnalysisStarted = vi.fn()
    const { analyzeCalls } = mockFetch({
      watchlist: { items: [item()] },
      analyze: {
        id: 1,
        session_id: 'sess-1',
        status: 'analyzing',
        queue_state: 'queued',
        already_started: false,
      },
    })
    render(<EventWatchlistCard onAnalysisStarted={onAnalysisStarted} />)
    await screen.findByTestId('event-watchlist-row-1')

    fireEvent.click(screen.getByTestId('event-analyze-btn-1'))

    const badge = await screen.findByTestId('event-queue-badge-1')
    expect(badge.textContent).toContain('排队中')
    expect(screen.getByTestId('event-session-1').textContent).toContain('sess-1')
    expect(onAnalysisStarted).toHaveBeenCalledTimes(1)
    expect(analyzeCalls()).toHaveLength(1)
    expect(analyzeCalls()[0][0]).toBe('/api/events/watchlist/1/analyze')

    // 已发起 → 按钮不可重复提交（重复点击零新请求）
    const btn = screen.getByTestId('event-analyze-btn-1')
    expect(btn).toBeDisabled()
    fireEvent.click(btn)
    expect(analyzeCalls()).toHaveLength(1)
  })

  it('幂等响应（already_started=true）→ 按既有状态渲染「分析中」，不重复提交', async () => {
    const { analyzeCalls } = mockFetch({
      watchlist: { items: [item()] },
      analyze: {
        id: 1,
        session_id: 'sess-9',
        status: 'analyzing',
        queue_state: 'running',
        already_started: true,
      },
    })
    render(<EventWatchlistCard />)
    await screen.findByTestId('event-watchlist-row-1')

    fireEvent.click(screen.getByTestId('event-analyze-btn-1'))

    const badge = await screen.findByTestId('event-queue-badge-1')
    expect(badge.textContent).toContain('分析中')
    expect(analyzeCalls()).toHaveLength(1)
    expect(screen.getByTestId('event-analyze-btn-1')).toBeDisabled()
  })

  it('POST 失败 → 条目级错误提示 + 仍可重试，不渲染队列徽标', async () => {
    const { analyzeCalls } = mockFetch({
      watchlist: { items: [item()] },
      analyze: new Error('500'),
    })
    render(<EventWatchlistCard />)
    await screen.findByTestId('event-watchlist-row-1')

    fireEvent.click(screen.getByTestId('event-analyze-btn-1'))

    const message = await screen.findByTestId('event-analyze-message-1')
    expect(message.textContent).toContain('发起深度分析失败')
    expect(screen.queryByTestId('event-queue-badge-1')).toBeNull()
    expect(screen.getByTestId('event-analyze-btn-1')).toBeEnabled()
    expect(analyzeCalls()).toHaveLength(1)
  })

  it('清单已发起 + 队列快照 → 未点击即展示运行态与关联会话（按钮禁用，零 POST）', async () => {
    const { analyzeCalls } = mockFetch({
      watchlist: { items: [item({ status: 'analyzing', session_id: 'sess-7' })] },
      queue: [queueEntry('sess-7', 'running')],
    })
    render(<EventWatchlistCard queue={[queueEntry('sess-7', 'running')]} />)

    const badge = await screen.findByTestId('event-queue-badge-1')
    expect(badge.textContent).toContain('分析中')
    expect(screen.getByTestId('event-session-1').textContent).toContain('sess-7')
    expect(screen.getByTestId('event-analyze-btn-1')).toBeDisabled()
    expect(analyzeCalls()).toHaveLength(0)
  })

  it('队列快照终态 → 已完成徽标', async () => {
    mockFetch({ watchlist: { items: [item({ status: 'analyzing', session_id: 'sess-7' })] } })
    const { rerender } = render(<EventWatchlistCard queue={[queueEntry('sess-7', 'queued')]} />)
    expect((await screen.findByTestId('event-queue-badge-1')).textContent).toContain('排队中')

    // 页面轮询（pollIntervalMs）刷新快照 → 徽标随快照推进
    rerender(<EventWatchlistCard queue={[queueEntry('sess-7', 'done')]} />)
    expect(screen.getByTestId('event-queue-badge-1').textContent).toContain('已完成')
  })

  it('已发起但队列快照无该会话（后端重启）→ 如实「已发起」而非猜测排队态', async () => {
    mockFetch({ watchlist: { items: [item({ status: 'analyzing', session_id: 'sess-5' })] } })
    render(<EventWatchlistCard />)

    const badge = await screen.findByTestId('event-queue-badge-1')
    expect(badge.textContent).toContain('已发起')
    expect(badge.textContent).not.toContain('已完成')
    expect(screen.getByTestId('event-analyze-btn-1')).toBeDisabled()
  })
})

describe('EventWatchlistCard 空态与失败态', () => {
  it('空清单 → 空态文案（非错误态、非骨架屏）', async () => {
    mockFetch({ watchlist: { items: [] } })
    render(<EventWatchlistCard />)

    const empty = await screen.findByTestId('event-watchlist-empty')
    expect(empty.textContent).toContain('事件观察清单为空')
    expect(screen.queryByTestId('event-watchlist-error')).toBeNull()
    expect(screen.queryByTestId('event-watchlist-loading')).toBeNull()
    expect(screen.queryByTestId('event-watchlist-row-1')).toBeNull()
  })

  it('请求失败 → 错误态 + 重试（与空态严格区分，重试成功即恢复列表）', async () => {
    const { watchlistCalls } = mockFetch({
      watchlist: call => (call.index === 0 ? new Error('500') : { items: [item()] }),
    })
    render(<EventWatchlistCard />)

    const error = await screen.findByTestId('event-watchlist-error')
    expect(error.textContent).toContain('事件观察清单加载失败')
    expect(screen.queryByTestId('event-watchlist-empty')).toBeNull()

    fireEvent.click(screen.getByTestId('event-watchlist-retry'))

    expect(await screen.findByTestId('event-watchlist-row-1')).toBeTruthy()
    expect(screen.queryByTestId('event-watchlist-error')).toBeNull()
    expect(watchlistCalls).toHaveLength(2)
  })

  it('畸形载荷（items 非数组）→ 错误态，不炸整页', async () => {
    mockFetch({ watchlist: { items: null as unknown as EventWatchlistItem[] } })
    render(<EventWatchlistCard />)

    expect(await screen.findByTestId('event-watchlist-error')).toBeTruthy()
    expect(screen.queryByTestId('event-watchlist-empty')).toBeNull()
  })
})

describe('EventWatchlistCard source 过滤', () => {
  it('默认不带 source 参数；选择来源 → 带参数重取（不透传空值避免后端误过滤）', async () => {
    const { watchlistCalls } = mockFetch({
      watchlist: call => (call.source === 'cbb' ? { items: [cbbItem()] } : { items: [item()] }),
    })
    render(<EventWatchlistCard />)

    await screen.findByTestId('event-watchlist-row-1')
    expect(watchlistCalls[0].source).toBeNull()

    const filter = screen.getByTestId('event-source-filter') as HTMLSelectElement
    fireEvent.change(filter, { target: { value: 'cbb' } })

    expect(await screen.findByTestId('event-watchlist-row-9')).toBeTruthy()
    expect(watchlistCalls[watchlistCalls.length - 1].source).toBe('cbb')
    expect(screen.queryByTestId('event-watchlist-row-1')).toBeNull()
  })

  it('过滤结果为空 → 空态文案明示当前来源筛选（不与全局空态混淆）', async () => {
    const { watchlistCalls } = mockFetch({
      watchlist: call => (call.source === 'earnings' ? { items: [] } : { items: [cbbItem()] }),
    })
    render(<EventWatchlistCard />)
    await screen.findByTestId('event-watchlist-row-9')

    fireEvent.change(screen.getByTestId('event-source-filter'), { target: { value: 'earnings' } })

    const empty = await screen.findByTestId('event-watchlist-empty')
    expect(empty.textContent).toContain('当前来源筛选')
    expect(empty.textContent).toContain('业绩预告')
    expect(watchlistCalls[watchlistCalls.length - 1].source).toBe('earnings')
  })
})

describe('选股页挂载', () => {
  it('选股页顺序：精选池在滚动容器首位，事件观察排在上下文卡片之后', async () => {
    mockFetch({
      watchlist: { items: [item()] },
      queue: [],
      pool: { pool: [] },
    })
    render(<ScreeningPage onBack={() => {}} />)

    const poolCard = await screen.findByTestId('screening-pool-card')
    const regimeCard = await screen.findByTestId('market-regime-card')
    const eventCard = await screen.findByTestId('event-watchlist-card')
    const scroll = poolCard.parentElement
    expect(scroll).not.toBeNull()
    // 主体优先：池卡片是滚动容器首子节点
    expect(scroll?.firstElementChild).toBe(poolCard)
    const precedes = (first: HTMLElement, second: HTMLElement) =>
      Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(precedes(poolCard, regimeCard)).toBe(true)
    expect(precedes(regimeCard, eventCard)).toBe(true)
    expect(screen.getByTestId('screening-page').contains(eventCard)).toBe(true)
  })

  it('页面级发起流程：点击条目 → 即刻排队态，随后页面队列快照轮询推进徽标（无第二条轮询通道）', async () => {
    mockFetch({
      watchlist: { items: [item()] },
      analyze: {
        id: 1,
        session_id: 'sess-1',
        status: 'analyzing',
        queue_state: 'queued',
        already_started: false,
      },
      pool: [
        poolBody({ pool: [] }),
        poolBody({ pool: [], queue: [queueEntry('sess-1', 'running')] }),
      ],
    })
    render(<ScreeningPage onBack={() => {}} pollIntervalMs={20} />)
    await screen.findByTestId('event-watchlist-row-1')

    fireEvent.click(screen.getByTestId('event-analyze-btn-1'))

    // 响应即渲染（不等轮询）
    expect((await screen.findByTestId('event-queue-badge-1')).textContent).toContain('排队中')
    // 页面既有队列快照轮询（pollIntervalMs）推进到运行态
    await waitFor(() =>
      expect(screen.getByTestId('event-queue-badge-1').textContent).toContain('分析中')
    )
  })
})
