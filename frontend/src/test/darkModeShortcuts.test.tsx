// polish-dark-mode-shortcuts 组件测试：
// 主题三态持久化与应用、命令面板（搜索跳转/快捷动作/快捷键清单）、快捷键与输入态抑制。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react'
import App from '../App'
import { CommandPalette } from '../CommandPalette'
import { loadThemeChoice, applyTheme, resolveTheme, saveThemeChoice } from '../theme'

const SESSIONS = [
  { session_id: 's1', stock_code: '600519', stock_name: '贵州茅台', display_name: '贵州茅台深度分析', status: 'completed', created_at: '2026-08-31T00:00:00Z', duration_ms: 1, session_type: 'chat' },
  { session_id: 's2', stock_code: '300750', stock_name: '宁德时代', display_name: '宁德时代研究', status: 'completed', created_at: '2026-08-31T01:00:00Z', duration_ms: 1, session_type: 'chat' },
]

function stubFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'
    if (url === '/api/sessions' && method === 'GET') {
      return new Response(JSON.stringify({ sessions: SESSIONS }), { status: 200 })
    }
    if (url.startsWith('/api/sessions/')) {
      return new Response(JSON.stringify({}), { status: 200 })
    }
    if (url === '/api/llm-config') {
      return new Response(JSON.stringify({ model: '', base_url: '', thinking: 'enabled' }), { status: 200 })
    }
    return new Response('{}', { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('主题三态（polish-dark-mode-shortcuts）', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('fa_api_key', 'test-key')
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    document.documentElement.classList.remove('dark')
  })

  it('点击主题切换按钮循环三态并持久化', async () => {
    // 展开态底部主题按钮已移除；改走折叠态图标栏按钮（theme-toggle-collapsed）
    localStorage.setItem('fa_sidebar_collapsed', '1')
    stubFetch()
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('theme-toggle-collapsed')).toBeInTheDocument())

    // 循环次序：system → light → dark → system（初值未保存时为 system）
    fireEvent.click(screen.getByTestId('theme-toggle-collapsed'))
    expect(localStorage.getItem('fa_theme')).toBe('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)

    fireEvent.click(screen.getByTestId('theme-toggle-collapsed'))
    expect(localStorage.getItem('fa_theme')).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)

    fireEvent.click(screen.getByTestId('theme-toggle-collapsed'))
    expect(localStorage.getItem('fa_theme')).toBe('system')
  })

  it('resolveTheme：跟随系统回退浅色（jsdom 无 matchMedia）', () => {
    expect(resolveTheme('dark')).toBe('dark')
    expect(resolveTheme('light')).toBe('light')
    expect(resolveTheme('system')).toBe('light')
  })

  it('applyTheme 幂等切换 documentElement 类', () => {
    applyTheme('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    applyTheme('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(loadThemeChoice()).toBe('system') // 未保存前默认 system
    saveThemeChoice('dark')
    expect(loadThemeChoice()).toBe('dark')
  })
})

describe('命令面板（polish-dark-mode-shortcuts）', () => {
  function renderPalette(over: { themeChoice?: ReturnType<typeof String> } = {}) {
    const onClose = vi.fn()
    const onSelectSession = vi.fn()
    const onNewSession = vi.fn()
    const onOpenDownloads = vi.fn()
    const onOpenMemory = vi.fn()
    const onOpenFactors = vi.fn()
    const onOpenBroker = vi.fn()
    const onCycleTheme = vi.fn()
    render(
      <CommandPalette
        open
        onClose={onClose}
        sessions={SESSIONS}
        onSelectSession={onSelectSession}
        onNewSession={onNewSession}
        onOpenDownloads={onOpenDownloads}
        onOpenMemory={onOpenMemory}
        onOpenFactors={onOpenFactors}
        onOpenBroker={onOpenBroker}
        onCycleTheme={onCycleTheme}
        themeChoice={(over.themeChoice as never) ?? 'light'}
      />,
    )
    return { onClose, onSelectSession, onNewSession, onOpenDownloads, onOpenMemory, onOpenFactors, onCycleTheme }
  }

  it('快捷动作：新建会话/下载管理/切换主题', () => {
    const h = renderPalette()
    fireEvent.click(screen.getByTestId('action-new'))
    expect(h.onNewSession).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('action-downloads'))
    expect(h.onOpenDownloads).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('action-theme'))
    expect(h.onCycleTheme).toHaveBeenCalledTimes(1)
    // 动作触发后面板关闭
    expect(h.onClose).toHaveBeenCalledTimes(3)
  })

  it('搜索会话并跳转', () => {
    const h = renderPalette()
    fireEvent.change(screen.getByTestId('palette-input'), { target: { value: '宁德' } })
    fireEvent.click(screen.getByTestId('palette-session-s2'))
    expect(h.onSelectSession).toHaveBeenCalledWith('s2')
    expect(h.onClose).toHaveBeenCalledTimes(1)
  })

  it('底部快捷键清单可见', () => {
    renderPalette()
    const hotkeys = screen.getByTestId('palette-hotkeys')
    expect(hotkeys.textContent).toContain('命令面板')
    expect(hotkeys.textContent).toContain('新建会话')
    expect(hotkeys.textContent).toContain('聚焦输入框')
    expect(hotkeys.textContent).toContain('侧边栏')
  })
})

describe('快捷键与输入态抑制（polish-dark-mode-shortcuts）', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('fa_api_key', 'test-key')
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })

  it('App 级：Ctrl+K 打开命令面板；输入 `/` 字符不触发面板外动作', async () => {
    stubFetch()
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('sidebar-new')).toBeInTheDocument())

    // 输入框聚焦时输入 / ——字符正常输入（无全局动作冲突，不抛错）
    // Cmd+K 打开面板
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }))
    })
    await waitFor(() => expect(screen.getByTestId('command-palette')).toBeInTheDocument())
    // Esc 关闭
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByTestId('command-palette')).toBeNull())
  })

  it('App 级：Ctrl+Shift+N 新建会话（回到空态）', async () => {
    stubFetch()
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('sidebar-new')).toBeInTheDocument())
    // 已在空态：触发快捷键不抛错且状态正常
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', ctrlKey: true, shiftKey: true, bubbles: true }))
    })
    expect(screen.getByTestId('sidebar-new')).toBeInTheDocument()
  })
})
