// remove-standalone-stop-button 组件测试：
// 1) 运行态不渲染页面级「停止生成」独立按钮，Composer 停止按钮为唯一停止入口
// 2) 终态后 Composer 停止按钮消失、发送键恢复，页面无残留停止按钮
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act, cleanup } from '@testing-library/react'
import App from '../App'

describe('停止入口唯一（remove-standalone-stop-button）', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('fa_api_key', 'test-key')
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('运行态无独立「停止生成」按钮，composer-stop 唯一；终态后恢复发送键', { timeout: 20000 }, async () => {
    const encoder = new TextEncoder()
    let controllerRef: ReadableStreamDefaultController<Uint8Array> | null = null
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/sessions') return new Response(JSON.stringify({ sessions: [] }), { status: 200 })
      if (url === '/api/llm-config') return new Response(JSON.stringify({ model: '', base_url: '', thinking: 'enabled' }), { status: 200 })
      if (url === '/api/analyze') {
        // 流保持打开：session_created + 首 token 后挂起，维持运行态
        return new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controllerRef = controller
              controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: 'session_created', session_id: 's1', seq: 1 })}\n\n`))
              controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: 'chat_token', token: '你好', seq: 2 })}\n\n`))
            },
          }),
          { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
        )
      }
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    const input = screen.getByPlaceholderText(/输入股票名称或代码|输入问题/)
    await act(async () => {
      fireEvent.change(input, { target: { value: '茅台' } })
      fireEvent.click(screen.getByTestId('send-button'))
    })

    // 运行态：Composer 切为停止按钮
    await waitFor(() => expect(screen.getByTestId('composer-stop')).toBeInTheDocument(), { timeout: 8000 })

    // 关键断言（红→绿）：页面级独立「停止生成」按钮不渲染
    expect(screen.queryByText('停止生成')).toBeNull()

    // 关闭流 → 终态：发送键恢复，无任何残留停止按钮
    await act(async () => {
      controllerRef?.enqueue(encoder.encode(`data: ${JSON.stringify({ type: 'done', seq: 3 })}\n\n`))
      controllerRef?.close()
    })
    await waitFor(() => expect(screen.getByTestId('send-button')).toBeInTheDocument(), { timeout: 8000 })
    expect(screen.queryByTestId('composer-stop')).toBeNull()
    expect(screen.queryByText('停止生成')).toBeNull()
  })
})
