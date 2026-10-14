import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within, cleanup } from '@testing-library/react'
import App from '../App'
import { MemoryPage } from '../pages/memory/MemoryPage'

/**
 * 记忆确认页单测（delta add-memory-confirmation T2.2；spec frontend「记忆确认页」）。
 *
 * - 三态筛选（默认待确认）：`?status=` 逐态取数，列表只回本态；
 * - 确认流：POST confirm → 待确认列表移除 C，切到已确认列表 C 出现（徽章「已确认」）；
 * - 驳回流：POST reject → 待确认移除，已驳回列表出现（徽章「已驳回」）；
 * - 空态如实：文案说明等待月批提炼，MUST NOT 以骨架屏/错误态冒充；
 * - AiBadge（正文 LLM 提炼产物）+ 确认后果提示（确认后即刻生效注入风控辩论）；
 * - 入口：/memory 直达、侧边栏折叠图标（aria-label=记忆）、命令面板项。
 *
 * fetch 全 mock（页面契约层单测，含 URL/method 断言；真实页面行为由 E2E 门禁覆盖）。
 * mock 为有状态假后端：仅复刻 API 契约（三态过滤/确认/驳回/404），不 mock 被测组件。
 */

type ClauseStatus = 'pending' | 'confirmed' | 'rejected'

interface ClauseRow {
  id: string
  text: string
  status: ClauseStatus
  source: string
  created_at: string
  evidence: string | null
}

function clause(id: string, text: string, status: ClauseStatus = 'pending', extra: Partial<ClauseRow> = {}): ClauseRow {
  return {
    id,
    text,
    status,
    source: 'monthly_batch',
    created_at: '2026-10-14T10:30:00+00:00',
    evidence: null,
    ...extra,
  }
}

function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  )
}

/** 有状态假后端：GET 三态过滤 + POST confirm/reject（未知 id 404），行状态随操作迁移。 */
function mockMemoryApi(rows: ClauseRow[] = []) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.startsWith('/api/memory/clauses')) {
      const [path, query] = url.split('?')
      const action = path.match(/^\/api\/memory\/clauses\/([^/]+)\/(confirm|reject)$/)
      if (method === 'POST' && action) {
        const id = decodeURIComponent(action[1])
        const row = rows.find(r => r.id === id)
        if (!row) return jsonResponse({ detail: `未知条款: ${id}` }, 404)
        row.status = action[2] === 'confirm' ? 'confirmed' : 'rejected'
        return jsonResponse({ id: row.id, status: row.status })
      }
      const status = new URLSearchParams(query ?? '').get('status') ?? 'pending'
      return jsonResponse({ status, clauses: rows.filter(r => r.status === status).map(r => ({ ...r })) })
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

describe('记忆确认页（delta add-memory-confirmation T2.2）', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('fa_api_key', 'test-key')
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  it('三态筛选：默认待确认只回 pending；切已确认/已驳回各回本态', async () => {
    const fetchMock = mockMemoryApi([
      clause('c1', '待确认条款一'),
      clause('c2', '已确认条款一', 'confirmed'),
      clause('c3', '已驳回条款一', 'rejected'),
    ])
    render(<MemoryPage onBack={() => {}} />)

    // 默认待确认：只渲染 pending，筛选按钮 aria-pressed 标记当前态
    expect(await screen.findByTestId('page-shell-list-item-c1')).toBeTruthy()
    expect(screen.queryByTestId('page-shell-list-item-c2')).toBeNull()
    expect(screen.queryByTestId('page-shell-list-item-c3')).toBeNull()
    expect(screen.getByTestId('memory-filter-pending').getAttribute('aria-pressed')).toBe('true')

    // 首请求带默认 status=pending
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/memory/clauses?status=pending'))

    // 切已确认
    fireEvent.click(screen.getByTestId('memory-filter-confirmed'))
    expect(await screen.findByTestId('page-shell-list-item-c2')).toBeTruthy()
    expect(screen.queryByTestId('page-shell-list-item-c1')).toBeNull()
    expect(screen.getByTestId('memory-filter-confirmed').getAttribute('aria-pressed')).toBe('true')

    // 切已驳回
    fireEvent.click(screen.getByTestId('memory-filter-rejected'))
    expect(await screen.findByTestId('page-shell-list-item-c3')).toBeTruthy()
    expect(screen.queryByTestId('page-shell-list-item-c2')).toBeNull()
  })

  it('确认流：确认后待确认列表移除 C，已确认列表出现 C（状态徽章为已确认）', async () => {
    const fetchMock = mockMemoryApi([clause('c1', '待确认条款一'), clause('c2', '待确认条款二')])
    render(<MemoryPage onBack={() => {}} />)

    fireEvent.click(await screen.findByTestId('page-shell-list-item-c1'))
    expect(await screen.findByTestId('memory-detail-text')).toBeTruthy()
    fireEvent.click(screen.getByTestId('page-shell-confirm'))

    // 确认请求对准条款 id；随后待确认列表移除 C（且无页面级报错）
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/memory/clauses/c1/confirm', expect.objectContaining({ method: 'POST' }))
    )
    await waitFor(() => expect(screen.queryByTestId('page-shell-list-item-c1')).toBeNull())
    expect(screen.getByTestId('page-shell-list-item-c2')).toBeTruthy()
    expect(screen.queryByTestId('memory-error')).toBeNull()

    // 切已确认列表 → C 出现且徽章为「已确认」（spec 场景）
    fireEvent.click(screen.getByTestId('memory-filter-confirmed'))
    const item = await screen.findByTestId('page-shell-list-item-c1')
    expect(item.textContent).toContain('待确认条款一')
    expect(within(screen.getByTestId('memory-status-c1')).getByText('已确认')).toBeTruthy()
  })

  it('驳回流：驳回后待确认列表移除，已驳回列表出现（徽章已驳回）', async () => {
    const fetchMock = mockMemoryApi([clause('c1', '待确认条款一')])
    render(<MemoryPage onBack={() => {}} />)

    fireEvent.click(await screen.findByTestId('page-shell-list-item-c1'))
    fireEvent.click(screen.getByTestId('page-shell-reject'))

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/memory/clauses/c1/reject', expect.objectContaining({ method: 'POST' }))
    )
    await waitFor(() => expect(screen.queryByTestId('page-shell-list-item-c1')).toBeNull())

    fireEvent.click(screen.getByTestId('memory-filter-rejected'))
    await screen.findByTestId('page-shell-list-item-c1')
    expect(within(screen.getByTestId('memory-status-c1')).getByText('已驳回')).toBeTruthy()
  })

  it('空态如实：无 pending 条款 → 说明等待月批提炼，不显示骨架屏/错误态', async () => {
    mockMemoryApi([])
    render(<MemoryPage onBack={() => {}} />)

    const empty = await screen.findByTestId('memory-empty')
    expect(empty.textContent).toContain('等待月批提炼')
    expect(empty.textContent).toContain('#327')
    // MUST NOT 以加载中或错误态冒充（spec 场景：空态如实）
    expect(screen.queryByTestId('page-shell-skeleton')).toBeNull()
    expect(screen.queryByTestId('memory-error')).toBeNull()
    expect(screen.queryByTestId('page-shell-list')).toBeNull()
  })

  it('取数失败 → 错误态（可重试）与空态严格区分', async () => {
    const respond = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
    let failing = true
    const rows = [clause('c1', '待确认条款一')]
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === 'string' ? input : input.toString()
        if (url.startsWith('/api/memory/clauses')) {
          if (failing) return respond({ detail: 'boom' }, 500)
          return respond({ status: 'pending', clauses: rows })
        }
        if (url === '/api/sessions') return respond({ sessions: [] })
        return respond({})
      })
    )
    render(<MemoryPage onBack={() => {}} />)

    expect(await screen.findByTestId('memory-error')).toBeTruthy()
    expect(screen.queryByTestId('memory-empty')).toBeNull()

    failing = false
    fireEvent.click(screen.getByTestId('memory-retry'))
    expect(await screen.findByTestId('page-shell-list-item-c1')).toBeTruthy()
    expect(screen.queryByTestId('memory-error')).toBeNull()
  })

  it('详情：完整正文带 AiBadge、来源、提炼时间、结算样本与确认后果提示', async () => {
    mockMemoryApi([
      clause('c1', '看多方向已结算读数 5 条：胜率 60%（命中 3 / 未中 2）', 'pending', {
        source: 'monthly_batch',
        evidence: JSON.stringify({ direction: 'long', resolved_win: 3, resolved_loss: 2 }),
      }),
    ])
    render(<MemoryPage onBack={() => {}} />)

    fireEvent.click(await screen.findByTestId('page-shell-list-item-c1'))

    // 完整正文 + AiBadge（LLM 提炼产物，提示文案由组件契约钉死）
    const text = await screen.findByTestId('memory-detail-text')
    expect(text.textContent).toContain('看多方向已结算读数 5 条：胜率 60%（命中 3 / 未中 2）')
    const badge = within(text).getByTestId('ai-badge')
    expect(badge.getAttribute('aria-label')).toBe('由模型生成，仅供参考')

    // 来源 + 提炼时间
    expect(screen.getByTestId('memory-detail-source').textContent).toContain('月批提炼')
    expect(screen.getByTestId('memory-detail-created-at').textContent).toContain('2026')

    // 结算样本（evidence JSON → 逐键可读行）
    const evidence = screen.getByTestId('memory-detail-evidence')
    expect(evidence.textContent).toContain('resolved_win')
    expect(evidence.textContent).toContain('3')

    // 确认动作旁明示后果（spec 文案逐字）
    expect(screen.getByTestId('memory-confirm-effect-note').textContent).toContain('确认后即刻生效注入风控辩论')

    // 状态徽章（StatusBadge 复用，label 为条款态文案）
    expect(within(screen.getByTestId('memory-status-c1')).getByText('待确认')).toBeTruthy()
  })

  it('直达 /memory 渲染记忆确认页', async () => {
    mockMemoryApi([clause('c1', '待确认条款一')])
    window.history.pushState({}, '', '/memory')
    render(<App />)

    expect(await screen.findByTestId('memory-page')).toBeTruthy()
    expect(await screen.findByTestId('page-shell-list-item-c1')).toBeTruthy()
  })

  it('侧边栏折叠图标（aria-label=记忆）进入 /memory', async () => {
    localStorage.setItem('fa_sidebar_collapsed', '1')
    mockMemoryApi([])
    render(<App />)

    const entry = await screen.findByTestId('sidebar-memory-collapsed')
    expect(entry.getAttribute('aria-label')).toBe('记忆')
    fireEvent.click(entry)

    expect(await screen.findByTestId('memory-page')).toBeTruthy()
    expect(window.location.pathname).toBe('/memory')
  })

  it('命令面板项进入 /memory', async () => {
    mockMemoryApi([])
    render(<App />)
    await screen.findByTestId('sidebar-new')

    fireEvent.keyDown(window, { key: 'k', metaKey: true, bubbles: true })
    const action = await screen.findByTestId('action-memory')
    expect(action.textContent).toContain('记忆')
    fireEvent.click(action)

    expect(await screen.findByTestId('memory-page')).toBeTruthy()
    expect(window.location.pathname).toBe('/memory')
  })
})
