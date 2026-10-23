// SessionsPane 会话管理分区（add-agent-settings-center Task 9）
// stub fetch 按 URL 分发并记录调用，仿 cachePane.test.tsx 范式（含刷新失败注入）
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SessionsPane } from '../../pages/settings/panes/SessionsPane'

// 记录全部 fetch 调用（url + init），便于断言 clear-all 是否被发起。
// 通过全局 __sessionsFailAt 可让第 N 次 /api/sessions 请求返回 500（0/缺省 = 永不失败）。
function stubFetch(calls: Array<{ url: string; init?: RequestInit }>) {
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url, init })
    if (url.endsWith('/api/settings/analysis-concurrency')) {
      if (init?.method === 'PUT') {
        const value = (JSON.parse(String(init.body)) as { value: number }).value
        return Promise.resolve(new Response(JSON.stringify({
          value, source: 'file', default: 10, min: 1, max: 20, env_value: null, file_status: 'ok',
        })))
      }
      return Promise.resolve(new Response(JSON.stringify({
        value: 10, source: 'default', default: 10, min: 1, max: 20,
        env_value: null, file_status: 'missing',
      })))
    }
    if (url.endsWith('/api/sessions/clear-all')) {
      return Promise.resolve(new Response(JSON.stringify({ cleared: 3 })))
    }
    if (url.endsWith('/api/sessions')) {
      const failAt = (globalThis as unknown as { __sessionsFailAt?: number }).__sessionsFailAt ?? 0
      const sessionsCount = calls.filter(c => c.url === '/api/sessions').length
      if (failAt > 0 && sessionsCount === failAt) {
        return Promise.resolve(new Response(JSON.stringify({ error: 'boom' }), { status: 500 }))
      }
      // 契约：GET /api/sessions 返回 { sessions: [...] }（对齐 src/finance_agent/api.py）
      return Promise.resolve(new Response(JSON.stringify({ sessions: [{}, {}, {}] })))
    }
    return Promise.resolve(new Response('{}'))
  }))
}

describe('SessionsPane 会话管理分区', () => {
  beforeEach(() => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    stubFetch(calls)
    ;(globalThis as unknown as { __calls?: Array<{ url: string; init?: RequestInit }> }).__calls = calls
    ;(globalThis as unknown as { __sessionsFailAt?: number }).__sessionsFailAt = 0
  })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  function captureCalls() {
    return (globalThis as unknown as { __calls: Array<{ url: string; init?: RequestInit }> }).__calls
  }

  it('展示会话总数并二次确认后清空', async () => {
    const onCleared = vi.fn()
    render(<SessionsPane onCleared={onCleared} />)
    expect(await screen.findByText('3')).toBeInTheDocument()
    fireEvent.click(screen.getByText('清空全部会话'))
    expect(screen.getByText(/确认/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('确认清空'))
    await waitFor(() => expect(onCleared).toHaveBeenCalled())
  })

  it('取消清空：关闭确认层、不回调 onCleared、不发起 clear-all', async () => {
    const onCleared = vi.fn()
    render(<SessionsPane onCleared={onCleared} />)
    await screen.findByText('3')
    fireEvent.click(screen.getByTestId('sessions-clear-all'))
    expect(screen.getByTestId('sessions-clear-all-confirm')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('sessions-clear-all-cancel'))
    await waitFor(() => {
      expect(screen.queryByTestId('sessions-clear-all-confirm')).not.toBeInTheDocument()
      expect(onCleared).not.toHaveBeenCalled()
      expect(captureCalls().filter(c => c.url === '/api/sessions/clear-all')).toHaveLength(0)
    })
  })

  it('清空后刷新 sessions 失败保留旧数据，不回落「加载中…」、不进错误态', async () => {
    // 第 1 次 /api/sessions 加载成功，清空后第 2 次刷新失败（500）
    ;(globalThis as unknown as { __sessionsFailAt: number }).__sessionsFailAt = 2
    const onCleared = vi.fn()
    render(<SessionsPane onCleared={onCleared} />)
    await screen.findByText('3')
    fireEvent.click(screen.getByTestId('sessions-clear-all'))
    fireEvent.click(screen.getByTestId('sessions-clear-all-submit'))
    await waitFor(() => {
      expect(captureCalls().filter(c => c.url === '/api/sessions').length).toBeGreaterThanOrEqual(2)
      expect(onCleared).toHaveBeenCalled()
    })
    // 刷新失败后仍保留已展示数据：不清空、不回落「加载中…」、不进入错误态
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.queryByText('会话列表加载中…')).not.toBeInTheDocument()
    expect(screen.queryByTestId('sessions-pane-error')).not.toBeInTheDocument()
  })

  it('sessions 响应缺 sessions 字段（非数组契约）时进入错误态', async () => {
    // 覆盖全局 stub：返回 200 但 body 无 sessions 字段，应视为加载失败
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}'))))
    render(<SessionsPane />)
    expect(await screen.findByTestId('sessions-pane-error')).toBeInTheDocument()
    expect(screen.getByText('会话列表加载失败')).toBeInTheDocument()
  })

  it('展示并发设置默认值并可保存', async () => {
    render(<SessionsPane />)
    const input = await screen.findByTestId('sessions-concurrency-input')
    expect(input).toHaveValue(10)

    fireEvent.change(input, { target: { value: '7' } })
    fireEvent.click(screen.getByTestId('sessions-concurrency-save'))

    expect(await screen.findByTestId('sessions-concurrency-saved')).toBeInTheDocument()
    expect(input).toHaveValue(7)
    const put = captureCalls().find(c => c.url === '/api/settings/analysis-concurrency' && c.init?.method === 'PUT')
    expect(put).toBeTruthy()
    expect(JSON.parse(String(put?.init?.body))).toEqual({ value: 7 })
  })

  it('越界输入不发起 PUT 并显示错误', async () => {
    render(<SessionsPane />)
    const input = await screen.findByTestId('sessions-concurrency-input')

    fireEvent.change(input, { target: { value: '21' } })
    fireEvent.click(screen.getByTestId('sessions-concurrency-save'))

    expect(await screen.findByTestId('sessions-concurrency-save-error')).toBeInTheDocument()
    expect(captureCalls().some(c => c.init?.method === 'PUT')).toBe(false)
  })
})