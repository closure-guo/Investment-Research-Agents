import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { createRef } from 'react'
import App from '../../App'
import QuickThread, { type QuickThreadHandle } from '../../chat/QuickThread'
import type { SessionDetail, SessionMeta } from '../../types'

// add-assistant-ui-thread Task 3b：quick 通道切换守卫与历史恢复。
//   1. 流式中切换会话 → HttpAgent.abortRun() 被调（fetch signal aborted）→
//      切回走 rebuildSession 快照渲染，消息不重复不错位
//   2. 历史恢复：旧消息由 MessageItem 呈现（stream-output），新 run 进 Thread
//   3. 409 busy 语义在 runtime onError 等价呈现（showWarning toast）

function chatSession(id: string, displayName: string, chatHistory: Array<{ role: string; content: string; ts?: string }>): { meta: SessionMeta; detail: SessionDetail } {
  return {
    meta: {
      session_id: id,
      stock_code: '',
      stock_name: '',
      display_name: displayName,
      status: 'completed',
      created_at: '2026-07-01T00:00:00Z',
      duration_ms: 0,
    },
    detail: {
      session_id: id,
      stock_code: '',
      stock_name: '',
      display_name: displayName,
      status: 'completed',
      created_at: '2026-07-01T00:00:00Z',
      duration_ms: 0,
      session_type: 'chat',
      report_markdown: '',
      chart_data: {} as SessionDetail['chart_data'],
      analyst_reports: {},
      agent_process: {},
      analyst_summaries: {},
      chat_history: chatHistory.map((h, i) => ({ ts: `2026-07-01T00:0${i}:00Z`, ...h })),
      pipeline_snapshot: null,
    },
  }
}

interface AguiRequest { signal: AbortSignal | null | undefined }

/** POST /api/agui/quick 的可编排 mock：模式 'hold'（挂起流）/ 'events'（下发后关闭）/ '409' */
function makeAguiHandler(mode: 'hold' | 'events' | '409', events: object[] = []) {
  const requests: AguiRequest[] = []
  let release: (() => void) | undefined
  const gate = mode === 'hold' ? new Promise<void>((r) => { release = r }) : undefined
  const handler = async (init?: RequestInit): Promise<Response> => {
    requests.push({ signal: init?.signal })
    if (mode === '409') {
      return new Response(JSON.stringify({ detail: 'session_busy' }), { status: 409 })
    }
    const enc = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        if (mode === 'hold') {
          // 挂起场景先下发 RUN_STARTED（run 进入 running 态），随后保持流打开。
          // events 可选：用于「run 已产出内容但尚未结束」的断言（如头像数量）。
          const initial = events.length
            ? events
            : [{ type: 'RUN_STARTED', threadId: 's1', runId: 'r-hold' }]
          for (const ev of initial) controller.enqueue(enc.encode(`data: ${JSON.stringify(ev)}\n\n`))
          if (gate) await gate
        } else {
          for (const ev of events) controller.enqueue(enc.encode(`data: ${JSON.stringify(ev)}\n\n`))
        }
        controller.close()
      },
    })
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }
  return { handler, requests, release: () => release?.() }
}

describe('QuickThread 切换守卫与历史恢复（Task 3b）', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('fa_api_key', 'test-key')
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('组件级：abort() 触发 HttpAgent.abortRun → fetch signal aborted', async () => {
    const agui = makeAguiHandler('hold')
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/agui/quick') return agui.handler(init)
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    const ref = createRef<QuickThreadHandle>()
    render(<QuickThread ref={ref} apiKey="sk-test" />)
    await act(async () => {
      ref.current?.send('你好')
    })
    await waitFor(() => expect(agui.requests.length).toBe(1))
    expect(ref.current?.isRunning()).toBe(true)

    await act(async () => {
      ref.current?.abort()
    })
    await waitFor(() => expect(agui.requests[0].signal?.aborted).toBe(true))
    await waitFor(() => expect(ref.current?.isRunning()).toBe(false))
  })

  it('App 级：流式中切会话 → abort 当前 run + 切回快照渲染不重复', async () => {
    const s1 = chatSession('s1', '会话一', [
      { role: 'user', content: '你好' },
      { role: 'assistant', content: '你好，请问有什么可以帮你？' },
    ])
    const s2 = chatSession('s2', '会话二', [
      { role: 'user', content: '看看另一会话' },
      { role: 'assistant', content: '这是会话二' },
    ])
    const agui = makeAguiHandler('hold')

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/sessions' && (!init || !init.method)) {
        return new Response(JSON.stringify({ sessions: [s1.meta, s2.meta] }), { status: 200 })
      }
      if (url === '/api/sessions/s1') return new Response(JSON.stringify(s1.detail), { status: 200 })
      if (url === '/api/sessions/s2') return new Response(JSON.stringify(s2.detail), { status: 200 })
      if (url === '/api/agui/quick') return agui.handler(init)
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await waitFor(() => expect(screen.getByText('会话一')).toBeInTheDocument())

    // 进入会话一（chat → quick 模式），历史消息渲染
    await act(async () => { fireEvent.click(screen.getByText('会话一')) })
    await waitFor(() => expect(screen.getByTestId('stream-output')).toBeInTheDocument())

    // quick 发送新消息 → AG-UI run 启动（流挂起中）
    const input = screen.getByPlaceholderText(/输入问题/)
    await act(async () => {
      fireEvent.change(input, { target: { value: '新问题' } })
      fireEvent.click(screen.getByTestId('send-button'))
    })
    await waitFor(() => expect(agui.requests.length).toBe(1))
    await waitFor(() => expect(screen.getByTestId('agui-user-message')).toBeInTheDocument())

    // 流式中切换会话：守卫 abort 当前 run，s2 快照渲染，无串流
    await act(async () => { fireEvent.click(screen.getByText('会话二')) })
    await waitFor(() => expect(agui.requests[0].signal?.aborted).toBe(true))
    await waitFor(() => expect(screen.getByText('这是会话二')).toBeInTheDocument())
    // Thread 已重挂载：新问题不残留
    expect(screen.queryByText('新问题')).toBeNull()

    // 切回会话一：rebuildSession 快照渲染，历史各出现一次，不重复追加
    await act(async () => { fireEvent.click(screen.getByText('会话一')) })
    await waitFor(() => expect(screen.getByTestId('stream-output')).toBeInTheDocument())
    expect(screen.getAllByText('你好')).toHaveLength(1)
    expect(screen.getAllByText('你好，请问有什么可以帮你？')).toHaveLength(1)
    expect(screen.queryByText('新问题')).toBeNull()
  })

  it('App 级：历史消息 MessageItem 呈现，新 run 渲染进 Thread 不重复', async () => {
    const s1 = chatSession('s1', '会话一', [
      { role: 'user', content: '你好' },
      { role: 'assistant', content: '历史回答' },
    ])
    const agui = makeAguiHandler('events', [
      { type: 'RUN_STARTED', threadId: 's1', runId: 'r1' },
      { type: 'TEXT_MESSAGE_START', messageId: 'm1', role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: 'm1', delta: '新回答内容' },
      { type: 'TEXT_MESSAGE_END', messageId: 'm1' },
      { type: 'RUN_FINISHED', threadId: 's1', runId: 'r1' },
    ])
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/sessions' && (!init || !init.method)) {
        return new Response(JSON.stringify({ sessions: [s1.meta] }), { status: 200 })
      }
      if (url === '/api/sessions/s1') return new Response(JSON.stringify(s1.detail), { status: 200 })
      if (url === '/api/agui/quick') return agui.handler(init)
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await waitFor(() => expect(screen.getByText('会话一')).toBeInTheDocument())
    await act(async () => { fireEvent.click(screen.getByText('会话一')) })

    // 历史：MessageItem 渲染（data-testid="stream-output"）
    await waitFor(() => expect(screen.getByTestId('stream-output')).toBeInTheDocument())
    expect(screen.getByText('历史回答')).toBeInTheDocument()

    // 新 run 进 Thread：用户气泡 + assistant 回复
    const input = screen.getByPlaceholderText(/输入问题/)
    await act(async () => {
      fireEvent.change(input, { target: { value: '新问题' } })
      fireEvent.click(screen.getByTestId('send-button'))
    })
    await waitFor(() => expect(screen.getByTestId('agui-user-message')).toBeInTheDocument())
    await waitFor(() => {
      expect(screen.getAllByText('新回答内容')).toHaveLength(1)
    })
    // RUN_FINISHED → 指示器消失
    await waitFor(() => expect(screen.queryByTestId('agui-stream-status')).toBeNull())
    // 历史仍只渲染一次
    expect(screen.getAllByText('历史回答')).toHaveLength(1)
  })

  it('App 级：HTTP 409 经 runtime onError 等价呈现为警告提示', async () => {
    const s1 = chatSession('s1', '会话一', [])
    const agui = makeAguiHandler('409')
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/sessions' && (!init || !init.method)) {
        return new Response(JSON.stringify({ sessions: [s1.meta] }), { status: 200 })
      }
      if (url === '/api/sessions/s1') return new Response(JSON.stringify(s1.detail), { status: 200 })
      if (url === '/api/agui/quick') return agui.handler(init)
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await waitFor(() => expect(screen.getByText('会话一')).toBeInTheDocument())
    await act(async () => { fireEvent.click(screen.getByText('会话一')) })
    await waitFor(() => expect(screen.getByPlaceholderText(/输入问题/)).toBeInTheDocument())

    const input = screen.getByPlaceholderText(/输入问题/)
    await act(async () => {
      fireEvent.change(input, { target: { value: 'busy 期间发送' } })
      fireEvent.click(screen.getByTestId('send-button'))
    })

    // onError → showWarning toast（top fixed 提示，3s 自动消失）
    await waitFor(() => {
      expect(screen.getByText(/HTTP 409/)).toBeInTheDocument()
    })
  })

  it('App 级：EmptyState 首条 quick 消息不丢失（Thread 挂载后补发 AG-UI run）', async () => {
    // Task 4 E2E 发现的回归：EmptyState 下 QuickThread 尚未挂载（ref=null），
    // quickChat 直接 ref.send() 会静默丢弃首条消息（视图切到聊天但 Thread 全空）。
    const agui = makeAguiHandler('hold')
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/sessions' && (!init || !init.method)) {
        return new Response(JSON.stringify({ sessions: [] }), { status: 200 })
      }
      if (url === '/api/agui/quick') return agui.handler(init)
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await waitFor(() => expect(screen.getByText('今天想研究什么？')).toBeInTheDocument())

    // EmptyState「模式：」下拉 → 快速模式（默认 deep，输入框占位符随之变化）
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /模式/ })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /快速模式/ })) })
    await waitFor(() => expect(screen.getByPlaceholderText(/输入问题/)).toBeInTheDocument())

    // 首条消息从 EmptyState 发出（此时 QuickThread 尚未挂载）
    const input = screen.getByPlaceholderText(/输入问题/)
    await act(async () => {
      fireEvent.change(input, { target: { value: '首页首条提问' } })
      fireEvent.click(screen.getByTestId('send-button'))
    })

    // 修复前：视图切到聊天但请求从未发出、Thread 无用户气泡
    await waitFor(() => expect(agui.requests.length).toBe(1), { timeout: 5_000 })
    await waitFor(() => expect(screen.getByTestId('agui-user-message')).toBeInTheDocument())
    expect(screen.getByTestId('agui-user-message')).toHaveTextContent('首页首条提问')
  })

  it('App 级：quick run 中 Composer 停止按钮中止 AG-UI run', async () => {
    // 外层 runtime 不再因 quickRunning 进入 running 态后，Composer 停止按钮改由
    // quickRunning 直接渲染并桥回 stopGeneration——本用例锁住取消链路。
    const agui = makeAguiHandler('hold')
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/sessions' && (!init || !init.method)) {
        return new Response(JSON.stringify({ sessions: [] }), { status: 200 })
      }
      if (url === '/api/agui/quick') return agui.handler(init)
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await waitFor(() => expect(screen.getByText('今天想研究什么？')).toBeInTheDocument())
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /模式/ })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /快速模式/ })) })

    const input = screen.getByPlaceholderText(/输入问题/)
    await act(async () => {
      fireEvent.change(input, { target: { value: '中途停止' } })
      fireEvent.click(screen.getByTestId('send-button'))
    })
    await waitFor(() => expect(agui.requests.length).toBe(1))
    await waitFor(() => expect(screen.getByTestId('composer-stop')).toBeInTheDocument())

    await act(async () => { fireEvent.click(screen.getByTestId('composer-stop')) })
    await waitFor(() => expect(agui.requests[0].signal?.aborted).toBe(true))
    await waitFor(() => expect(screen.getByTestId('send-button')).toBeInTheDocument())
  })

  it('App 级：quick run 进行中只渲染一个 agent 头像（外层 Thread 不产生占位消息）', async () => {
    // 缺陷复现（2026-10-23 用户报告）：quick 发送后消息区出现两个机器人头像——
    // 上层 AnalysisThread 被标记 isRunning（含 quickRunning）且外部消息为空，
    // assistant-ui 会插入一条待产出占位消息并套上头像；真正的 quick run 在
    // 下方 QuickThread 内渲染，于是同一回复出现上下两个头像。
    const agui = makeAguiHandler('hold', [
      { type: 'RUN_STARTED', threadId: 's1', runId: 'r-hold' },
      { type: 'TEXT_MESSAGE_START', messageId: 'm1', role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: 'm1', delta: '正在回答' },
    ])
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/sessions' && (!init || !init.method)) {
        return new Response(JSON.stringify({ sessions: [] }), { status: 200 })
      }
      if (url === '/api/agui/quick') return agui.handler(init)
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(<App />)
    await waitFor(() => expect(screen.getByText('今天想研究什么？')).toBeInTheDocument())
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /模式/ })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /快速模式/ })) })

    const input = screen.getByPlaceholderText(/输入问题/)
    await act(async () => {
      fireEvent.change(input, { target: { value: '只应有一个头像' } })
      fireEvent.click(screen.getByTestId('send-button'))
    })

    await waitFor(() => expect(screen.getByTestId('agui-user-message')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('正在回答')).toBeInTheDocument())
    // run 仍在进行中（流被挂起）时，整页只能有一个 agent 头像
    expect(container.querySelectorAll('i.fa-robot')).toHaveLength(1)

    await act(async () => { agui.release() })
  })
})
