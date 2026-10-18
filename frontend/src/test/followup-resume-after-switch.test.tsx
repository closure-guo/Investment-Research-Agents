import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import App from '../App'

// Bug 复现测试：会话切换时 messagesRef.current 滞后于 setMessages（useEffect 异步），
// 导致 selectSession 保存快照时读取到旧值。切回时快照恢复的 assistantMsgIdRef
// 与实际 messages 不匹配，resumeStream 的 chat_token 更新了不存在的消息 ID，
// 内容丢失，UI 卡死在"调用工具中"状态。

function sse(events: object[]): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const ev of events) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`))
      }
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('会话切换时 messagesRef 同步，resumeStream 事件不丢失', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('fa_api_key', 'test-key')
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('切换会话再切回，resumeStream 的 chat_token 被正确渲染到 UI', { timeout: 20000 }, async () => {
    vi.useRealTimers()

    // 第一轮 SSE：session_created + thinking_token + tool_call + 流关闭（无 done，模拟切换中断）
    // 后端任务仍在运行（search_stock 执行中）
    const round1 = [
      { type: 'session_created', session_id: 's1', seq: 1 },
      { type: 'thinking_token', token: '让我搜索安孚科技', seq: 2 },
      { type: 'tool_call', name: 'search_stock', args: { query: '安孚科技' }, seq: 3 },
      // 无 done — 流关闭，模拟用户切换会话中断
    ]

    let analyzeCallCount = 0
    let streamAfterSeq = -1

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()

      if (url === '/api/sessions') {
        return new Response(JSON.stringify({
          sessions: [
            { session_id: 's1', display_name: '安孚科技分析', status: 'running', created_at: '2026-08-02T10:00:00', session_type: 'analysis' },
            { session_id: 's2', display_name: '其他会话', status: 'completed', created_at: '2026-08-02T09:00:00', session_type: 'chat' },
          ]
        }), { status: 200 })
      }

      if (url === '/api/analyze') {
        analyzeCallCount++
        return sse(round1)
      }

      // 会话详情：s1 状态为 running（后端任务仍在执行）
      if (url === '/api/sessions/s1') {
        return new Response(JSON.stringify({
          session_id: 's1',
          display_name: '安孚科技分析',
          status: 'running',
          session_type: 'analysis',
          stock_code: '',
          stock_name: '',
          chat_history: [
            { role: 'user', content: '安孚科技' },
          ],
          pipeline_anchor: 1,
          pipeline_snapshot: null,
          pipeline_timelines: null,
          last_seq: 3,
        }), { status: 200 })
      }

      if (url === '/api/sessions/s2') {
        return new Response(JSON.stringify({
          session_id: 's2',
          display_name: '其他会话',
          status: 'completed',
          session_type: 'chat',
          chat_history: [
            { role: 'user', content: '你好' },
            { role: 'assistant', content: '你好！' },
          ],
          pipeline_snapshot: null,
          pipeline_timelines: null,
        }), { status: 200 })
      }

      // 恢复端点：捕获 after_seq 参数，返回新事件
      if (url.includes('/api/sessions/s1/stream')) {
        const match = url.match(/after_seq=(\d+)/)
        streamAfterSeq = match ? parseInt(match[1]) : -1
        // 返回新事件：search_result + chat_token + done
        return sse([
          { type: 'search_result', query: '安孚科技', results: [{ title: '安孚科技股票信息' }], seq: 4 },
          { type: 'chat_token', token: '安孚科技分析完成', seq: 5 },
          { type: 'done', seq: 6 },
        ])
      }

      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    // 发送消息
    const input = screen.getByPlaceholderText(/输入股票名称或代码|输入问题/)
    await act(async () => {
      fireEvent.change(input, { target: { value: '安孚科技' } })
      fireEvent.click(screen.getByTestId('send-button'))
    })

    // 等待第一轮事件被处理（tool_call 出现）
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => {
        const url = typeof c[0] === 'string' ? c[0] : ''
        return url === '/api/analyze'
      })).toBe(true)
    }, { timeout: 5000 })

    // 等待第一轮 SSE 事件被消费（条件等待，取代固定 sleep——issue #331：
    // 慢机上固定窗口赌不中事件链时点，CI 曾因此超时）
    await screen.findByText('让我搜索安孚科技', undefined, { timeout: 10000 })
    await screen.findByText(/调用工具中/, undefined, { timeout: 10000 })

    // 切换到 s2（点击侧边栏中的"其他会话"）——等 s2 内容渲染为切完成条件
    await act(async () => {
      const s2Item = await screen.findByText('其他会话')
      fireEvent.click(s2Item)
    })
    await screen.findByText('你好！', undefined, { timeout: 10000 })

    // 切换回 s1（点击侧边栏中的"安孚科技分析"）——等 s1 历史恢复渲染为切回条件
    await act(async () => {
      const s1Item = await screen.findByText('安孚科技分析')
      fireEvent.click(s1Item)
    })
    await screen.findByText('让我搜索安孚科技', undefined, { timeout: 10000 })

    // 关键断言：resumeStream 收到的新事件（chat_token "安孚科技分析完成"）必须被正确处理并渲染到 UI
    // 修复前：messagesRef.current 滞后 → 快照保存错误内容 → assistantMsgIdRef 与 messages 不匹配 →
    //         chat_token 更新了不存在的消息 ID → 内容丢失 → UI 卡死在"调用工具中"状态
    // 修复后：commitMessages 同步更新 messagesRef → 快照保存正确内容 → assistantMsgIdRef 与 messages 匹配 →
    //         chat_token 正确更新消息 → 内容渲染到 UI
    await waitFor(() => {
      expect(screen.getByText('安孚科技分析完成')).toBeInTheDocument()
    }, { timeout: 10000 })
  })
})
