import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within, cleanup } from '@testing-library/react'
import App from '../App'
import { FactorLibraryPage } from '../pages/factors/FactorLibraryPage'
import type { FactorDetail, FactorRow } from '../types'

// ECharts 需要 canvas，jsdom 不支持：stub 组件并捕获 option（trackRecordPage 同款方案）
const capturedChartOptions: unknown[] = []
vi.mock('echarts-for-react', () => ({
  default: ({ option }: { option: unknown }) => {
    capturedChartOptions.push(option)
    return <div data-testid="mock-chart" />
  },
}))

/**
 * 因子库页单测（delta add-factor-library-page T2.2；spec frontend「因子库页」）。
 *
 * - 列表：列齐（名称/表达式/状态/来源/ICIR/最近闸门结论/衰减标记）+ 行字段渲染；
 * - 筛选：状态（全部/active/retired）与来源（全部/builtin/llm）逐维过滤；
 * - 徽章：状态 StatusBadge（active/retired）、`llm` 来源 AiBadge（提示文案由组件契约钉死）；
 * - 行展开：完整表达式 + IC 序列 + 事件历史（含时间与事件类型）——详情经
 *   GET /api/factors/{id} 懒取（spec：行展开 SHALL 展示，即按需取数）；
 * - 空态如实：库空 → 空态文案，MUST NOT 以加载中/错误态冒充；取数失败另走错误态；
 * - 入口：/factors 直达、侧边栏折叠图标（aria-label=因子库）、命令面板项。
 *
 * fetch 全 mock（页面契约层单测；真实页面行为由 E2E 门禁覆盖）。
 */

const LLM_ID = 11
const BUILTIN_ID = 12

const LLM_ROW: FactorRow = {
  id: LLM_ID,
  name: null, // LLM 提案无注册名：界面回退显示「因子 {id}」
  expression: 'ts_rank(close,20)',
  hypothesis: '截面排名动量：排名抬升的标的短期收益分布偏正。',
  category: '动量',
  status: 'active',
  ast_json: 'ts_rank(close,20)',
  source: 'llm',
  alignment_score: 0.72,
  pool_increment: 0.12,
  created_at: '2026-10-12T15:33:00',
  updated_at: '2026-10-12T15:33:01',
  ic_mean_60d: 0.041,
  icir_60d: 0.521,
  ic_points: 2,
  ic_window: 60,
  min_samples_for_scoring: 10,
  expected_factor_mode_date: '2026-11-02',
  decayed: false,
  last_event: {
    event: 'status_change',
    from_status: 'candidate',
    to_status: 'active',
    reason: 'ADMITTED',
    created_at: '2026-10-12T15:33:01',
  },
}

const BUILTIN_ROW: FactorRow = {
  id: BUILTIN_ID,
  name: 'momentum_20', // builtin 注册词表作品名
  expression: 'delta(close,20)',
  hypothesis: '过去 20 个交易日涨幅高的标的短期内倾向继续跑赢。',
  category: 'builtin',
  status: 'retired',
  ast_json: 'delta(close,20)',
  source: 'builtin',
  alignment_score: null,
  pool_increment: 0.05,
  created_at: '2026-10-11T15:30:00',
  updated_at: '2026-10-13T15:45:00',
  ic_mean_60d: 0.0,
  icir_60d: 0.0,
  ic_points: 0, // 无 IC 记录 → 列显示「—」而非 0.000
  ic_window: 60,
  min_samples_for_scoring: 10,
  expected_factor_mode_date: null,
  decayed: true, // 衰减降级史（退役后标记仍在）
  last_event: {
    event: 'status_change',
    from_status: 'degraded',
    to_status: 'retired',
    reason: 'BUCKET_QUOTA',
    created_at: '2026-10-13T15:45:00',
  },
}

const LLM_DETAIL: FactorDetail = {
  ...LLM_ROW,
  style_exposure: {
    correlations: {
      momentum_20: 0.43,
      reversal_5: -0.62,
      turnover_proxy: 1.0,
      volatility_20: -0.38,
      size_proxy: -0.19,
    },
    dominant: 'turnover_proxy',
    dominant_correlation: 1.0,
    flagged: true,
    n_days: 229,
    threshold: 0.7,
    basket: ['momentum_20', 'reversal_5', 'turnover_proxy', 'volatility_20', 'size_proxy'],
  },
  ic_series: [
    { trade_date: '2026-10-08', rank_ic: 0.033 },
    { trade_date: '2026-10-09', rank_ic: 0.049 },
  ],
  events: [
    {
      id: 21,
      factor_id: LLM_ID,
      event: 'created',
      from_status: null,
      to_status: 'candidate',
      reason: null,
      detail: null,
      created_at: '2026-10-12T15:33:00',
    },
    {
      id: 22,
      factor_id: LLM_ID,
      event: 'status_change',
      from_status: 'candidate',
      to_status: 'active',
      reason: 'ADMITTED',
      detail: { reason: 'ADMITTED' },
      created_at: '2026-10-12T15:33:01',
    },
  ],
}

function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  )
}

/** 有状态假后端：GET 列表 + GET 详情（未知 id 404），仅复刻 API 契约。 */
function mockFactorsApi(
  rows: FactorRow[] = [LLM_ROW, BUILTIN_ROW],
  details: Record<number, FactorDetail> = { [LLM_ID]: LLM_DETAIL }
) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/api/factors') return jsonResponse({ factors: rows.map(r => ({ ...r })) })
    const detailMatch = url.match(/^\/api\/factors\/(\d+)$/)
    if (detailMatch) {
      const detail = details[Number(detailMatch[1])]
      if (!detail) return jsonResponse({ detail: `未知因子: ${detailMatch[1]}` }, 404)
      return jsonResponse(detail)
    }
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

describe('因子库页（delta add-factor-library-page T2.2）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  it('列表渲染：列齐 + 名称/表达式/ICIR/最近闸门结论逐行渲染', async () => {
    mockFactorsApi()
    render(<FactorLibraryPage onBack={() => {}} />)

    const table = await screen.findByTestId('page-shell-table')
    for (const header of ['名称', '表达式', '状态', '来源', 'ICIR', '最近闸门结论', '衰减标记']) {
      expect(within(table).getByText(header)).toBeTruthy()
    }

    // LLM 因子：无注册名 → 回退「因子 {id}」；表达式截断展示（CSS truncate）
    const llmRow = within(screen.getByTestId(`page-shell-row-${LLM_ID}`))
    expect(llmRow.getByTestId(`factor-name-${LLM_ID}`).textContent).toContain(`因子 ${LLM_ID}`)
    const expression = llmRow.getByTestId(`factor-expression-${LLM_ID}`)
    expect(expression.textContent).toContain(LLM_ROW.expression)
    expect(expression.getAttribute('title')).toBe(LLM_ROW.expression)
    expect(llmRow.getByTestId(`factor-icir-${LLM_ID}`).textContent).toContain('0.521')
    expect(llmRow.getByTestId(`factor-icir-samples-${LLM_ID}`).textContent).toContain('样本 2')
    expect(llmRow.getByTestId(`factor-gate-${LLM_ID}`).textContent).toContain('准入通过')

    // builtin 因子：注册名逐字；无 IC 记录 → ICIR 显示「—」（不把 0.0 冒充为读数）
    const builtinRow = within(screen.getByTestId(`page-shell-row-${BUILTIN_ID}`))
    expect(builtinRow.getByTestId(`factor-name-${BUILTIN_ID}`).textContent).toContain('momentum_20')
    expect(builtinRow.getByTestId(`factor-icir-${BUILTIN_ID}`).textContent).toContain('—')
    expect(builtinRow.getByTestId(`factor-gate-${BUILTIN_ID}`).textContent).toContain('桶内保种淘汰')
  })

  it('徽章：状态用 StatusBadge（data-status），llm 来源带 AiBadge，衰减标记可辨', async () => {
    mockFactorsApi()
    render(<FactorLibraryPage onBack={() => {}} />)
    await screen.findByTestId('page-shell-table')

    // 状态徽章（StatusBadge 复用：active/retired）
    const llmStatus = within(screen.getByTestId(`factor-status-${LLM_ID}`)).getByTestId('status-badge')
    expect(llmStatus.getAttribute('data-status')).toBe('active')
    const builtinStatus = within(screen.getByTestId(`factor-status-${BUILTIN_ID}`)).getByTestId('status-badge')
    expect(builtinStatus.getAttribute('data-status')).toBe('retired')

    // 来源：llm 带 AiBadge（提示文案逐字），builtin 不带
    const llmSource = within(screen.getByTestId(`factor-source-${LLM_ID}`))
    expect(llmSource.getByTestId('ai-badge').getAttribute('aria-label')).toBe('由模型生成，仅供参考')
    expect(within(screen.getByTestId(`factor-source-${BUILTIN_ID}`)).queryByTestId('ai-badge')).toBeNull()

    // 衰减标记：decayed 因子显示 degraded StatusBadge；未衰减显示「—」
    const decay = within(screen.getByTestId(`factor-decay-${BUILTIN_ID}`)).getByTestId('status-badge')
    expect(decay.getAttribute('data-status')).toBe('degraded')
    expect(decay.textContent).toContain('衰减降级')
    expect(screen.getByTestId(`factor-decay-${LLM_ID}`).textContent).toContain('—')
  })

  it('筛选：来源 llm → 过滤 retired 因子；状态 retired → 只剩内置退役因子', async () => {
    mockFactorsApi()
    render(<FactorLibraryPage onBack={() => {}} />)
    await screen.findByTestId('page-shell-table')
    expect(screen.getByTestId(`page-shell-row-${LLM_ID}`)).toBeTruthy()
    expect(screen.getByTestId(`page-shell-row-${BUILTIN_ID}`)).toBeTruthy()

    // 来源筛选 llm（spec 场景：仅显示 active/llm 因子，retired 因子被过滤）
    fireEvent.click(screen.getByTestId('factors-filter-source-llm'))
    expect(screen.getByTestId(`page-shell-row-${LLM_ID}`)).toBeTruthy()
    expect(screen.queryByTestId(`page-shell-row-${BUILTIN_ID}`)).toBeNull()
    expect(screen.getByTestId('factors-filter-source-llm').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByTestId('factors-count').textContent).toContain('1')

    // 叠加状态 retired → 无匹配（如实空态，不冒充库空）
    fireEvent.click(screen.getByTestId('factors-filter-status-retired'))
    expect(screen.getByTestId('factors-filter-empty')).toBeTruthy()
    expect(screen.queryByTestId('factors-empty')).toBeNull()

    // 状态 retired + 来源全部 → 只剩内置退役因子
    fireEvent.click(screen.getByTestId('factors-filter-source-all'))
    expect(screen.getByTestId(`page-shell-row-${BUILTIN_ID}`)).toBeTruthy()
    expect(screen.queryByTestId(`page-shell-row-${LLM_ID}`)).toBeNull()

    // 全部复位 → 两行都在
    fireEvent.click(screen.getByTestId('factors-filter-status-all'))
    expect(screen.getByTestId(`page-shell-row-${LLM_ID}`)).toBeTruthy()
    expect(screen.getByTestId(`page-shell-row-${BUILTIN_ID}`)).toBeTruthy()
    expect(screen.getByTestId('factors-count').textContent).toContain('2')
  })

  it('行展开：懒取详情 → 完整表达式 + IC 序列 + 事件历史（含时间与事件类型）', async () => {
    const fetchMock = mockFactorsApi()
    render(<FactorLibraryPage onBack={() => {}} />)
    await screen.findByTestId('page-shell-table')

    expect(screen.queryByTestId(`page-shell-row-detail-${LLM_ID}`)).toBeNull()
    expect(fetchMock).not.toHaveBeenCalledWith(`/api/factors/${LLM_ID}`)

    const toggle = screen.getByTestId(`page-shell-row-expand-${LLM_ID}`)
    fireEvent.click(toggle)

    // 详情懒取：展开才请求（列表不携带完整事件历史）
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`/api/factors/${LLM_ID}`))
    const detail = within(await screen.findByTestId(`page-shell-row-detail-${LLM_ID}`))
    expect(detail.getByTestId(`factor-detail-expression-${LLM_ID}`).textContent).toContain('ts_rank(close,20)')

    // IC 序列：折线图（jsdom stub 捕获 option，断言数据点与顺序）
    expect(detail.getByTestId('factor-ic-chart')).toBeTruthy()
    const chartOption = capturedChartOptions[
      capturedChartOptions.length - 1
    ] as {
      series: Array<{ data: number[] }>
      xAxis: { data: string[] }
    }
    expect(chartOption.xAxis.data).toEqual(['2026-10-08', '2026-10-09'])
    expect(chartOption.series[0].data).toEqual([0.033, 0.049])

    // IC 就绪度：窗口 60、门槛 10、预计恢复日、样本不足标注
    expect(detail.getByTestId(`factor-ic-samples-${LLM_ID}`).textContent).toContain('2/60')
    expect(detail.getByTestId(`factor-ic-threshold-${LLM_ID}`).textContent).toContain('2/10')
    expect(detail.getByTestId(`factor-ic-expected-${LLM_ID}`).textContent).toContain('2026-11-02')
    expect(detail.getByTestId(`factor-ic-insufficient-${LLM_ID}`).textContent).toContain('样本不足（2/10）')

    // 事件历史：准入（created candidate → active ADMITTED）+ 时间
    const events = detail.getByTestId(`factor-detail-events-${LLM_ID}`)
    expect(events.textContent).toContain('创建')
    expect(events.textContent).toContain('candidate')
    expect(events.textContent).toContain('active')
    expect(events.textContent).toContain('准入通过')
    expect(events.textContent).toContain('2026')

    // 风格暴露披露：主导风格 + ρ + 重合标注（仅披露不裁决）
    const style = detail.getByTestId(`factor-detail-style-${LLM_ID}`)
    expect(style.textContent).toContain('风格暴露')
    expect(style.textContent).toContain('量比（换手代理）')
    expect(style.textContent).toContain('ρ=1.00')
    expect(style.textContent).toContain('229 日')
    expect(detail.getByTestId(`factor-style-flag-${LLM_ID}`).textContent).toContain(
      '与标准风格高度重合'
    )

    // 再次点击收起
    fireEvent.click(screen.getByTestId(`page-shell-row-expand-${LLM_ID}`))
    expect(screen.queryByTestId(`page-shell-row-detail-${LLM_ID}`)).toBeNull()
  })

  it('风格暴露数据不足（style_exposure=null）→ 如实展示，不臆造 0', async () => {
    mockFactorsApi()
    const detailWithoutStyle = { ...LLM_DETAIL, style_exposure: null }
    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === `/api/factors/${LLM_ID}`) {
        return jsonResponse(detailWithoutStyle)
      }
      return originalFetch(input as RequestInfo)
    }) as unknown as typeof fetch
    render(<FactorLibraryPage onBack={() => {}} />)
    await screen.findByTestId('page-shell-table')
    fireEvent.click(screen.getByTestId(`page-shell-row-expand-${LLM_ID}`))
    const detail = within(await screen.findByTestId(`page-shell-row-detail-${LLM_ID}`))
    expect(detail.getByTestId(`factor-style-unavailable-${LLM_ID}`).textContent).toContain(
      '数据不足'
    )
    globalThis.fetch = originalFetch
  })

  it('空态如实：库空 → 说明等待 builtin 注册或提案过闸，不显示表格/骨架屏/错误态', async () => {
    mockFactorsApi([])
    render(<FactorLibraryPage onBack={() => {}} />)

    const empty = await screen.findByTestId('factors-empty')
    expect(empty.textContent).toContain('因子库为空')
    expect(empty.textContent).toContain('五道闸')
    // MUST NOT 以加载中或错误态冒充（spec 场景：空库不报错）
    expect(screen.queryByTestId('page-shell-skeleton')).toBeNull()
    expect(screen.queryByTestId('factors-error')).toBeNull()
    expect(screen.queryByTestId('page-shell-table')).toBeNull()
  })

  it('取数失败 → 错误态（可重试）与空态严格区分', async () => {
    let failing = true
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === 'string' ? input : input.toString()
        if (url === '/api/factors') {
          if (failing) return jsonResponse({ detail: 'boom' }, 500)
          return jsonResponse({ factors: [LLM_ROW] })
        }
        if (url === '/api/sessions') return jsonResponse({ sessions: [] })
        return jsonResponse({})
      })
    )
    render(<FactorLibraryPage onBack={() => {}} />)

    expect(await screen.findByTestId('factors-error')).toBeTruthy()
    expect(screen.queryByTestId('factors-empty')).toBeNull()

    failing = false
    fireEvent.click(screen.getByTestId('factors-retry'))
    expect(await screen.findByTestId(`page-shell-row-${LLM_ID}`)).toBeTruthy()
    expect(screen.queryByTestId('factors-error')).toBeNull()
  })

  it('直达 /factors 渲染因子库页', async () => {
    mockFactorsApi()
    window.history.pushState({}, '', '/factors')
    render(<App />)

    expect(await screen.findByTestId('factors-page')).toBeTruthy()
    expect(await screen.findByTestId('page-shell-table')).toBeTruthy()
  })

  it('侧边栏折叠图标（aria-label=因子库）进入 /factors', async () => {
    localStorage.setItem('fa_sidebar_collapsed', '1')
    mockFactorsApi([])
    render(<App />)

    const entry = await screen.findByTestId('sidebar-factors-collapsed')
    expect(entry.getAttribute('aria-label')).toBe('因子库')
    fireEvent.click(entry)

    expect(await screen.findByTestId('factors-page')).toBeTruthy()
    expect(window.location.pathname).toBe('/factors')
  })

  it('命令面板项进入 /factors', async () => {
    mockFactorsApi([])
    render(<App />)
    await screen.findByTestId('sidebar-new')

    fireEvent.keyDown(window, { key: 'k', metaKey: true, bubbles: true })
    const action = await screen.findByTestId('action-factors')
    expect(action.textContent).toContain('因子库')
    fireEvent.click(action)

    expect(await screen.findByTestId('factors-page')).toBeTruthy()
    expect(window.location.pathname).toBe('/factors')
  })
})
