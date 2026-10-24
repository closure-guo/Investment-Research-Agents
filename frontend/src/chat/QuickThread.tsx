import { createContext, forwardRef, useCallback, useContext, useEffect, useImperativeHandle, useRef, useState } from 'react'
import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from '@assistant-ui/react'
import type { AssistantRuntime } from '@assistant-ui/core'
import type {
  ReasoningMessagePartComponent,
  TextMessagePartComponent,
  ToolCallMessagePartProps,
} from '@assistant-ui/core/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { createQuickAgent } from './aguiAgent'
import type { LLMConfigPayload } from '../llmConfig'

// add-assistant-ui-thread Task 3a：quick 模式 assistant-ui Thread。
//
// 渲染路径（调研 §3.3 路径 (a)）：历史消息由 App 现有 MessageItem 渲染
// （streamStore rebuildSession 快照），本组件只接管挂载后发起的新 run——
// agentTimeline 横幅语义不变，Thread 不参与历史初始化。
//
// 发送路径：App.quickChat 经 handle.send() → runtime.thread.append →
// useAgUiRuntime 驱动 HttpAgent.runAgent（POST /api/agui/quick，SSE）。
// 切换守卫：App 在 selectSession/newAnalysis 时调用 handle.abort()
// （HttpAgent.abortRun，官方 API）→ 服务端 CancelledError → 中断落库。
//
// 视觉：design-system 令牌（var(--bg-*)/var(--text-*)），无硬编码色值。

export interface QuickThreadHandle {
  /** 发送一条用户消息并启动 run（发送前由调用方负责 isRunning 守卫） */
  send: (message: string) => void
  /** 中止当前 run（HttpAgent.abortRun） */
  abort: () => void
  isRunning: () => boolean
}

export interface QuickThreadProps {
  apiKey: string
  /** 请求级 LLM 配置（forwardedProps 透传，语义对齐现有通道 llm_config） */
  llmConfig?: LLMConfigPayload | null
  /** 挂载时已绑定的会话 id（恢复/切回会话后追问携带之；新会话为 null → 空线程 id 新建） */
  sessionId?: string | null
  /** RUN_STARTED.thread_id 回传（新会话创建时由 App 绑定会话） */
  onSessionCreated?: (sessionId: string) => void
  /** run 正常结束（RUN_FINISHED） */
  onRunFinished?: () => void
  /** run 生命周期（流式指示/停止按钮显隐由 App 据此驱动） */
  onRunningChange?: (running: boolean) => void
  /** run 错误（RUN_ERROR / HTTP 失败 / 断连）——409 busy 等在此等价呈现 */
  onError?: (message: string) => void
  /** 打开派生分析会话（start_stock_analysis 回执里的 session_id） */
  onOpenSession?: (sessionId: string) => void
}

// 工具结果 → 结构化卡片所需的最小上下文（避免把 onOpenSession 逐层透传）
const QuickOperatorContext = createContext<((sessionId: string) => void) | undefined>(undefined)

// Markdown 文本渲染（与 App 中 chat response 同款 remark-gfm 配置）
const MarkdownText: TextMessagePartComponent = ({ text }) => (
  <div className="prose prose-sm max-w-none">
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children }) => (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:underline"
            style={{ color: 'var(--bg-brand)' }}
          >
            {children}
          </a>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  </div>
)

// 思考段（REASONING_MESSAGE_* → reasoning part）：弱化色块呈现
const ReasoningBlock: ReasoningMessagePartComponent = ({ text }) => (
  <div
    className="text-xs leading-relaxed whitespace-pre-wrap rounded-lg px-3 py-2 mb-2"
    style={{ color: 'var(--text-tertiary)', background: 'var(--bg-overlay-l1)' }}
  >
    {text}
  </div>
)

// 工具结果解析：字符串 JSON 或对象；畸形结果静默回退为无结构化卡片
function parseToolResult(result: unknown): Record<string, unknown> | null {
  if (typeof result === 'string') {
    try {
      const parsed = JSON.parse(result)
      return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null
    } catch {
      return null
    }
  }
  return result && typeof result === 'object' ? (result as Record<string, unknown>) : null
}

// 工具调用段（TOOL_CALL_* → tool-call part）：横幅 + 可选的「打开分析会话」按钮
const ToolCallBlock = ({ toolName, result }: ToolCallMessagePartProps) => {
  const onOpenSession = useContext(QuickOperatorContext)
  const parsed = parseToolResult(result)
  const sessionId =
    parsed && typeof parsed.session_id === 'string' && parsed.session_id
      ? parsed.session_id
      : null
  const pendingConfirm = parsed?.status === 'confirmation_required'

  return (
    <div className="mb-2 flex flex-col items-start gap-1.5">
      <div
        className="text-xs rounded-lg px-3 py-1.5 inline-flex items-center gap-1.5"
        style={{ color: 'var(--text-secondary)', background: 'var(--bg-overlay-l1)' }}
      >
        <i className="fas fa-tools text-[10px]" style={{ color: 'var(--text-brand)' }}></i>
        <span>调用工具 · {toolName}</span>
        {pendingConfirm && (
          <span
            className="rounded px-1.5 py-0.5 text-[10px]"
            style={{ color: 'var(--status-warning-default)', background: 'var(--bg-overlay-l2)' }}
          >
            等待确认
          </span>
        )}
      </div>
      {sessionId && onOpenSession && (
        <button
          type="button"
          data-testid="agui-open-analysis"
          onClick={() => onOpenSession(sessionId)}
          className="text-xs rounded-lg px-3 py-1.5 inline-flex items-center gap-1.5 transition-colors"
          style={{ color: 'var(--text-onbrand)', background: 'var(--bg-brand)' }}
        >
          <i className="fas fa-arrow-right text-[10px]"></i>
          <span>打开分析会话</span>
        </button>
      )}
    </div>
  )
}

const QuickUserMessage = () => (
  <div className="flex justify-end animate-slide-in" data-testid="agui-user-message">
    <div className="max-w-[85%] md:max-w-[75%]">
      <div
        className="msg-user rounded-2xl rounded-tr-sm px-5 py-3 text-sm leading-relaxed"
        style={{ color: 'var(--text-onbrand)' }}
      >
        <MessagePrimitive.Parts components={{ Text: ({ text }) => <span>{text}</span> }} />
      </div>
    </div>
  </div>
)

// 同一轮回复的头像归属：AG-UI 每个 LLM turn 发一条 TEXT_MESSAGE_*，assistant-ui
// 渲染为连续多条 assistant 消息。逐消息挂头像会让同一条回复出现两个/多个机器人
// 头像（2026-10-23 用户报告：用户气泡左上、左下各一个）。头像只由本轮首条承载，
// 后续消息用等宽占位保持正文缩进一致。
function isFirstAssistantOfRun(
  messages: readonly { id: string; role: string }[],
  messageId: string,
): boolean {
  const idx = messages.findIndex((m) => m.id === messageId)
  return idx <= 0 || messages[idx - 1]?.role !== 'assistant'
}

const QuickAssistantMessage = () => {
  const showAvatar = useAuiState((s) => isFirstAssistantOfRun(s.thread.messages, s.message.id))
  return (
    <div className="flex justify-start animate-slide-in" data-testid="agui-assistant-message">
      <div className="max-w-[95%] md:max-w-[90%] w-full">
        <div className="flex items-start gap-3">
          {showAvatar ? (
            <div
              className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-1"
              style={{ background: 'var(--bg-brand)' }}
            >
              <i className="fas fa-robot text-white text-xs"></i>
            </div>
          ) : (
            <div className="w-7 flex-shrink-0" aria-hidden="true" />
          )}
          <div className="flex-1 min-w-0 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <MessagePrimitive.Parts
              components={{ Text: MarkdownText, Reasoning: ReasoningBlock, tools: { Fallback: ToolCallBlock } }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

const QuickThread = forwardRef<QuickThreadHandle, QuickThreadProps>(function QuickThread(
  { apiKey, llmConfig = null, sessionId = null, onSessionCreated, onRunFinished, onRunningChange, onError, onOpenSession },
  ref,
) {
  // 会话 id 生命周期：初始 = App 传入（恢复/切回场景），RUN_STARTED.thread_id
  // 回传后更新（新会话场景）——后续追问经 getSessionId 携带正确 thread_id。
  const sessionIdRef = useRef<string | null>(sessionId)
  // agent 每个 mount 创建一次（会话切换守卫由 App 通过 key 重挂载实现）
  const [agent] = useState(() => createQuickAgent({ apiKey, llmConfig, getSessionId: () => sessionIdRef.current }))
  const runtimeRef = useRef<AssistantRuntime | null>(null)
  // 回调经 ref 透传最新引用（避免回调 identity 变化重建 runtime/订阅）
  const callbacksRef = useRef({ onSessionCreated, onRunFinished, onRunningChange, onError })
  callbacksRef.current = { onSessionCreated, onRunFinished, onRunningChange, onError }

  const runtime = useAgUiRuntime({
    agent,
    showThinking: true,
    onError: (e) => callbacksRef.current.onError?.(e.message || '生成失败，请稍后重试'),
  })
  runtimeRef.current = runtime

  // run 生命周期内部跟踪：HttpAgent.isRunning 在 abortRun 后不复位（0.0.59 实测），
  // 守卫语义以本组件状态为准（abort 即中断，允许立即发起新 run）
  const runningRef = useRef(false)
  const setRunning = useCallback((v: boolean) => {
    if (runningRef.current === v) return
    runningRef.current = v
    callbacksRef.current.onRunningChange?.(v)
  }, [])

  // agent 事件订阅：会话创建回传 + run 生命周期（与 runtime 订阅并行，官方 Subscriber API）
  useEffect(() => {
    const sub = agent.subscribe({
      onRunStartedEvent: ({ event }) => {
        setRunning(true)
        const threadId = (event as { threadId?: string }).threadId
        if (threadId) {
          // 服务端新建会话后，同 mount 追问必须携带该会话 id（thread_id 生命周期）
          sessionIdRef.current = threadId
          callbacksRef.current.onSessionCreated?.(threadId)
        }
      },
      onRunFinishedEvent: () => {
        setRunning(false)
        callbacksRef.current.onRunFinished?.()
      },
      onRunErrorEvent: ({ event }) => {
        setRunning(false)
        callbacksRef.current.onError?.(event.message || '生成失败，请稍后重试')
      },
      onRunFailed: ({ error }) => {
        setRunning(false)
        callbacksRef.current.onError?.(error.message || '生成失败，请稍后重试')
      },
    })
    return () => sub.unsubscribe()
  }, [agent, setRunning])

  useImperativeHandle(
    ref,
    () => ({
      send: (message: string) => {
        try {
          // 经 core 层 append（返回原始 Promise task）以便捕获失败（断连/HTTP 错误已由
          // runtime onError 上报）。公开 facade thread.append 内部对非 MessageNotSentError
          // 重新抛出且无人接住，会产生 unhandled rejection（0.15.17 实测）。
          // __internal_threadBinding 在 0.15.17 存在；缺失时回退公开 facade。
          type AppendState = {
            append: (msg: unknown) => Promise<void>
            messages: ReadonlyArray<{ id: string }>
          }
          const thread = runtimeRef.current?.thread as unknown as {
            __internal_threadBinding?: { getState: () => AppendState }
            append?: (message: string) => void
          }
          const state = thread?.__internal_threadBinding?.getState()
          if (state) {
            // parentId 必须是当前线程末尾消息：传 null 会把追问挂成新的兄弟分支
            // （root），第一轮对话从当前视图脱离、且第二轮 RunAgentInput 丢失历史
            // 上下文（多步交互中 agent 输出/action 序列错乱的根因）。
            const parent = state.messages[state.messages.length - 1]
            const task = state.append({
              createdAt: new Date(),
              parentId: parent?.id ?? null,
              sourceId: null,
              runConfig: {},
              role: 'user',
              content: [{ type: 'text', text: message }],
              startRun: true,
            })
            void Promise.resolve(task).catch(() => {
              // 失败已经 runtime onError 上报，兜底吞掉
            })
          } else {
            thread?.append?.(message)
          }
        } catch {
          // 同步抛出同样由 runtime onError 上报，这里兜底不再抛出
        }
      },
      abort: () => {
        agent.abortRun()
        setRunning(false)
      },
      isRunning: () => runningRef.current,
    }),
    [agent, setRunning],
  )

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <QuickOperatorContext.Provider value={onOpenSession}>
        <div className="w-full" data-testid="agui-thread">
          <ThreadPrimitive.Root>
            <ThreadPrimitive.Viewport
              autoScroll
              className="flex flex-col gap-4 overflow-y-visible"
            >
              <ThreadPrimitive.Messages
                components={{ UserMessage: QuickUserMessage, AssistantMessage: QuickAssistantMessage }}
              />
              <ThreadPrimitive.If running>
                <div
                  data-testid="agui-stream-status"
                  className="flex items-center gap-2 px-1"
                  style={{ color: 'var(--text-tertiary)' }}
                >
                  <span
                    className="block w-3 h-3 rounded-full border-2 border-t-transparent animate-spin"
                    style={{ borderColor: 'var(--bg-brand)' }}
                  ></span>
                </div>
              </ThreadPrimitive.If>
            </ThreadPrimitive.Viewport>
          </ThreadPrimitive.Root>
        </div>
      </QuickOperatorContext.Provider>
    </AssistantRuntimeProvider>
  )
})

export default QuickThread
