import { useState, useRef, useEffect, useCallback, useMemo, cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { toast } from 'sonner'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ComposerPrimitive, ThreadPrimitive } from '@assistant-ui/react'
import type { PipelineStep, UIMessage, SessionMeta, SessionDetail, ToolCallEntry, PipelineSnapshot, Citation } from './types'
import { ChartsSection } from './Charts'
import { ReportFileDrawer } from './ReportFileDrawer'
import { ReportNameBanner, AllFilesBanner, isExportableReport, formatReportTitle } from './ReportEntryBanners'
import { SearchBanner } from './SearchBanner'
import { nodeDisplayName } from './timeline'
import { estimateTotalMs, estimateRemainingMs, formatDurationMs, loadDurations } from './eta'
import { buildLayerTree } from './pipelineTree'
import { PipelineTimeline } from './PipelineTimeline'
import { PipelineGraph } from './PipelineGraph'
import { TimelineRenderer, type TimelineBannerComponents } from './TimelineRenderer'
import { useClickOutside } from './useClickOutside'
import { usePathname, navigate } from './route'
import { DownloadCenter } from './pages/downloads/DownloadCenter'
import { ScreeningPage } from './pages/screening/ScreeningPage'
import { MemoryPage } from './pages/memory/MemoryPage'
import { FactorLibraryPage } from './pages/factors/FactorLibraryPage'
import { BrokerConsolePage } from './pages/broker/BrokerConsolePage'
import { DailyOpsPage } from './pages/ops/DailyOpsPage'
import { TrackRecordPage } from './pages/trackRecord/TrackRecordPage'
import { CalibrationPage } from './pages/trackRecord/CalibrationPage'
import { PredictionDetailPage } from './pages/trackRecord/PredictionDetailPage'
import { getStreamStore } from './stores/streamStore'
import { useSessionStream } from './stores/streamStore/useSessionStream'
import QuickThread, { type QuickThreadHandle } from './chat/QuickThread'
import { AnalysisRuntimeProvider, ThreadMessages } from './chat/AnalysisThread'
import { SidebarProvider, Sidebar, SidebarFixedToggle, SidebarIcon, useSidebar } from './components/ui/sidebar'
import { motion } from 'framer-motion'
import { ReportSidePanel } from './components/ReportSidePanel'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './components/ui/dropdown-menu'
import { SUGGESTION_CARDS } from './config/suggestions'
import { formatSessionTime } from './lib/format'
import { CommandPalette } from './CommandPalette'
import { MenuToggleIcon } from './components/ui/menu-toggle-icon'
import { loadThemeChoice, saveThemeChoice, applyTheme, watchSystemTheme, type ThemeChoice } from './theme'
import { useHotkeys } from './hooks/useHotkeys'
import { Toaster } from './components/ui/sonner'
import { SnapshotBanner } from './SnapshotBanner'
import { Button } from './components/ui/button'
import { Input } from './components/ui/input'
import { Textarea } from './components/ui/textarea'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from './components/ui/dialog'
import {
  loadProfiles,
  saveProfiles,
  addProfile,
  deleteProfile,
  activateProfile,
  getActiveConfig,
  getActiveProfileName,
  buildLlmConfigPayload,
  type LLMConfig,
  type LLMProfile,
  type ProfileStore,
  canEnterMode,
  clearCapability,
  type CapabilityMatrix,
} from './llmConfig'
import { SettingsCenterPage, type ModuleId } from './pages/settings/SettingsCenterPage'

// 搜索类工具集合：这类工具的状态与结果由独立搜索横幅（SearchBanner）承载，
// 不进入工具调用横幅（ToolCallBanner），避免同一搜索行为同时出现两个横幅。
export const SEARCH_TOOL_NAMES = new Set<string>(['web_search', 'batch_web_search'])

// 判断工具是否为搜索类（web_search / batch_web_search）
export function isSearchTool(name: string): boolean {
  return SEARCH_TOOL_NAMES.has(name)
}

// 从思考内容中提取首个 ## 二级标题作为思考横幅展示标题。
// 策略对齐：LLM 按信息密度决定输出 ## 标题分层 / **加粗** 分段 / 无标题，
// 仅 ## 层级标题提取为横幅标题，**加粗** 与无格式返回 undefined。
export function extractThinkingTitle(content: string): string | undefined {
  if (!content) return undefined
  // 允许 ## 前有空白（Markdown 缩进），## 后必须有空格，标题末尾空白被裁剪
  const match = content.match(/^\s*##\s+(.+?)\s*$/m)
  return match ? match[1] : undefined
}

// ── Pipeline steps 定义已迁移至 pipelineTree.LAYER_TREE_CONFIG（分层时间轴）──

// 轮询超时上限（Final Review Fix 2）：ReAct 路径切走后 status 可能永久 running，
// 轮询无限进行会泄漏资源。超过该时长后停止轮询并提示用户刷新或重新发起。
const MAX_POLLING_MS = 5 * 60 * 1000 // 5 分钟（150 次 × 2s）

// TimelineRenderer 注入的横幅组件集合（ThinkingBanner/ToolCallBanner 为函数声明，提升后可用）
const timelineBannerComponents: TimelineBannerComponents = {
  ThinkingBanner: (props) => <ThinkingBanner {...props} />,
  SearchBanner,
  ToolCallBanner: (props) => <ToolCallBanner {...props} />,
}

const getUserId = (): string => {
  const KEY = 'fa_user_id'
  let uid = localStorage.getItem(KEY)
  if (!uid) {
    uid = `user-${crypto.randomUUID()}`
    localStorage.setItem(KEY, uid)
  }
  return uid
}

// 从 stream.phase 派生视图状态（替代原 appState 的流相关部分）
// 空会话（无消息且无进行中流）显示首页；其余按 phase 映射。
function deriveAppState(
  phase: string,
  currentSessionId: string | null,
  messages: UIMessage[],
  sessions: SessionMeta[],
): 'empty' | 'analyzing' | 'report' | 'clarifying' {
  const running = phase === 'connecting' || phase === 'streaming' || phase === 'resuming'
  if (running && currentSessionId) {
    const session = sessions.find((s) => s.session_id === currentSessionId)
    const isChat = session?.session_type === 'chat'
    // 按会话类型判定视图：chat → 聊天视图（clarifying），analysis → 分析视图（analyzing）。
    // 不依赖 messages 是否已有 pipeline 消息——新架构下刷新恢复的 running 会话在
    // journal replay/轮询创建 pipeline 前 messages 只有 user 消息，但仍是分析视图。
    return isChat ? 'clarifying' : 'analyzing'
  }
  if (!currentSessionId) {
    // 新会话提交后 session_created 未到达：有消息则进入聊天视图
    return messages.length > 0 ? 'clarifying' : 'empty'
  }
  switch (phase) {
    case 'done':
      return 'report'
    case 'awaiting_input':
    case 'interrupted':
    case 'error':
      return 'clarifying'
    default:
      return messages.length > 0 ? 'clarifying' : 'empty'
  }
}

export default function App() {
  // 多配置管理（profiles，delta Decision 10）
  const [profileStore, setProfileStore] = useState<ProfileStore>(() => loadProfiles())
  // 激活配置从 profileStore 派生（替代直接读 fa_llm_config）
  const llmConfig = useMemo(() => getActiveConfig(profileStore), [profileStore])
  // apiKey 由激活 profile 派生（EmptyState/请求拦截保持语义不变）
  const apiKey = llmConfig.apiKey
  // 当前激活 profile 的能力矩阵（probe 事实；null = 未探测 → 门禁放行）
  const capability = llmConfig.capability ?? null
  // 切换激活 profile（LLM 切换下拉框使用）
  const switchProfile = useCallback((id: string) => {
    setProfileStore(prev => {
      const next = activateProfile(prev, id)
      saveProfiles(next)
      return next
    })
  }, [])
  // 保存配置到激活 profile（LlmConfigPane 确认按钮使用）
  // 无激活 profile 时自动创建「我的配置」profile 并保存（向前兼容：确认即视为显式配置）
  const handleSaveConfig = useCallback((cfg: LLMConfig) => {
    setProfileStore(prev => {
      // 无 profile 时自动创建默认 profile（含配置）
      if (prev.profiles.length === 0) {
        const next = addProfile(prev, '我的配置', clearCapability(cfg))
        saveProfiles(next)
        return next
      }
      // 更新激活 profile 的 config
      const newProfiles = prev.profiles.map(p => {
        if (p.id !== prev.activeId) return p
        // 连接三要素（apiKey/model/baseUrl）任一变更 → 旧 probe 事实失效，清空 capability；
        // 未变更 → 保留 probe 事实（含本次会话内新探测结果）
        const connectionChanged =
          p.config.apiKey !== cfg.apiKey || p.config.model !== cfg.model || p.config.baseUrl !== cfg.baseUrl
        const config = connectionChanged
          ? clearCapability(cfg)
          : { ...cfg, capability: cfg.capability ?? p.config.capability ?? null }
        return { ...p, config }
      })
      const next = { ...prev, profiles: newProfiles }
      saveProfiles(next)
      return next
    })
  }, [])
  // probe 事实落库：连通性测试成功后把 capability 写入激活 profile（无 profile 时不落库，仅在弹窗内展示）
  const handleProbeCapability = useCallback((capability: CapabilityMatrix | null) => {
    setProfileStore(prev => {
      const active = prev.profiles.find(p => p.id === prev.activeId)
      if (!active) return prev
      const newProfiles = prev.profiles.map(p =>
        p.id === prev.activeId ? { ...p, config: { ...p.config, capability } } : p
      )
      const next = { ...prev, profiles: newProfiles }
      saveProfiles(next)
      return next
    })
  }, [])
  // 另存为新 profile（LlmConfigPane「另存为」按钮使用）
  const handleSaveAsConfig = useCallback((cfg: LLMConfig, name: string) => {
    setProfileStore(prev => {
      const next = addProfile(prev, name, cfg)
      saveProfiles(next)
      return next
    })
  }, [])
  // 删除 profile（LlmConfigPane 列表删除按钮使用）
  const handleDeleteProfile = useCallback((id: string) => {
    setProfileStore(prev => {
      const next = deleteProfile(prev, id)
      saveProfiles(next)
      return next
    })
  }, [])
  // 设置中心页激活分区（add-agent-settings-center）：undefined = 默认 LLM 配置；
  // 强制配置入口（无 apiKey 提交等）跳转时聚焦 LLM 分区
  const [settingsFocus, setSettingsFocus] = useState<ModuleId | undefined>(undefined)
  // 后端默认 LLM 配置（GET /api/llm-config），用作设置面板输入框 placeholder（delta 5.3）
  const [backendDefaults, setBackendDefaults] = useState<{ model: string; baseUrl: string; thinking: string }>({ model: '', baseUrl: '', thinking: 'enabled' })
  // add-collapsible-sidebar：侧边栏折叠状态由 SidebarProvider 管理（localStorage 持久化 + Ctrl/Cmd+B）
  useEffect(() => {
    let cancelled = false
    fetch('/api/llm-config')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return
        setBackendDefaults({
          model: typeof data.model === 'string' ? data.model : '',
          baseUrl: typeof data.base_url === 'string' ? data.base_url : '',
          thinking: typeof data.thinking === 'string' ? data.thinking : 'enabled',
        })
      })
      .catch(() => {
        // 后端不可用时静默回退到内置默认 placeholder，不阻塞使用
      })
    return () => { cancelled = true }
  }, [])
  // 轮询起始时间（超时保护基准，cleanup 时重置）
  const pollStartRef = useRef<number | null>(null)

  // Session state
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  // 刷新加载骨架：/api/sessions 首次成功前为 true 取反（侧边栏显示骨架而非闪「暂无历史会话」）
  const [sessionsLoaded, setSessionsLoaded] = useState(false)
  // 刷新恢复指示：有持久化会话且恢复未落定前，主区显示「恢复会话中」而非闪首页空态。
  // 初值直接读 localStorage，避免首帧就闪空态落地。
  const [bootRestoring, setBootRestoring] = useState(() => !!localStorage.getItem('fa_current_session_id'))
  // 包装 setCurrentSessionId：同步持久化到 localStorage（fa_current_session_id），
  // 供刷新后自动恢复当前会话（delta spec: restore-session-on-refresh）
  const setAndPersistSession = useCallback((id: string | null) => {
    if (id) {
      localStorage.setItem('fa_current_session_id', id)
    } else {
      localStorage.removeItem('fa_current_session_id')
    }
    setCurrentSessionId(id)
  }, [])
  // add-collapsible-sidebar：侧边栏折叠状态由 SidebarProvider 管理（localStorage 持久化 + Ctrl/Cmd+B）
  // 下载中心路由（add-download-center）：/downloads 直达/刷新时主区域渲染下载管理页
  const pathname = usePathname()
  const [mode, setMode] = useState<'quick' | 'deep'>('deep')
  // 临时警告提示（如"该会话正在生成中"）
  const [warningMessage, setWarningMessage] = useState<string | null>(null)

  // ── AG-UI quick 通道（add-assistant-ui-thread）──
  // quick 模式对话改走 assistant-ui Thread + POST /api/agui/quick（双轨隔离：
  // 不进 streamStore/registry，深度模式渲染零影响）。历史消息仍由 MessageItem
  // 渲染（rebuildSession 快照），Thread 只接管挂载后发起的新 run（调研 §3.3 路径 a）。
  const [quickActive, setQuickActive] = useState(false)
  const [quickRunning, setQuickRunning] = useState(false)
  // 空态退场动画（replicate-chat-home）：首条消息发出后空态 200ms 淡出再卸载
  const [emptyLeaving, setEmptyLeaving] = useState(false)
  // 会话切换加载过渡（fix-session-switch-flash）：目标会话置 pending 后 detail
  // 加载期间 viewState 会派生出 'empty'，用加载圈过渡避免闪空态首页
  const [sessionSwitching, setSessionSwitching] = useState(false)
  // 命令面板（polish-dark-mode-shortcuts）：Cmd/Ctrl+K 打开
  const [paletteOpen, setPaletteOpen] = useState(false)
  // 主题三态（polish-dark-mode-shortcuts）：浅色/深色/跟随系统，持久化 + 系统跟随
  const [themeChoice, setThemeChoice] = useState<ThemeChoice>(() => loadThemeChoice())
  useEffect(() => {
    applyTheme(themeChoice)
    const unwatch = watchSystemTheme(() => {
      if (loadThemeChoice() === 'system') applyTheme('system')
    })
    return unwatch
  }, [themeChoice])
  const cycleTheme = useCallback(() => {
    setThemeChoice((prev) => {
      const next: ThemeChoice = prev === 'light' ? 'dark' : prev === 'dark' ? 'system' : 'light'
      saveThemeChoice(next)
      return next
    })
  }, [])
  // `/` 聚焦输入框：经包装容器定位 Composer 内部 textarea
  const composerWrapRef = useRef<HTMLDivElement>(null)
  // 重挂载纪元：会话切换/新建时 +1 → Thread 重置（切换守卫：abort 旧 run + 快照恢复不重复）
  const [aguiEpoch, setAguiEpoch] = useState(0)
  const quickThreadRef = useRef<QuickThreadHandle>(null)
  // EmptyState 首条 quick 消息：QuickThread 在 viewState==='empty' 时未挂载（ref=null），
  // 直接 send 会静默丢弃（Task 4 E2E 发现）。先排队，视图切到聊天、Thread 挂载后补发。
  const [pendingQuickMessage, setPendingQuickMessage] = useState<string | null>(null)
  // 导出抽屉：非 null 时渲染 ReportFileDrawer（报告「全部文件」横幅入口）
  const [drawerMessage, setDrawerMessage] = useState<UIMessage | null>(null)
  // 报告右侧面板（add-report-side-panel）：非 null 时从右侧滑出展示完整报告
  const [panelMessage, setPanelMessage] = useState<UIMessage | null>(null)
  // 切换会话时关闭面板与抽屉（跨会话不串内容；同会话内开合由用户控制保持）
  useEffect(() => {
    setDrawerMessage(null)
    setPanelMessage(null)
  }, [currentSessionId])
  const showWarning = useCallback((text: string) => {
    setWarningMessage(text)
    setTimeout(() => setWarningMessage(null), 3000)
  }, [])

  // ── StreamStore 集成：流状态唯一事实源 ──
  const store = getStreamStore()
  const stream = useSessionStream(currentSessionId)
  const messages = stream.messages
  const appState = deriveAppState(stream.phase, currentSessionId, messages, sessions)

  // 会话级导出入口：口径 B（已完成报告且 filePaths 含已生成文件）
  const exportableReports = messages.filter(isExportableReport)
  const lastExportableReport = exportableReports[exportableReports.length - 1] ?? null

  // ── Session management ──
  // 返回加载到的会话数组（失败返回 null）：调用方可据此决定是否重试
  // （useEffect 初始化时退避重试），成功时亦可直接拿到列表做存在性校验
  const loadSessions = useCallback(async (): Promise<SessionMeta[] | null> => {
    try {
      const resp = await fetch('/api/sessions')
      if (!resp.ok) return null
      const data = await resp.json()
      // 200 但 body 缺 sessions 字段（代理/中间件异常返回 {} 等）视为失败，
      // 不用空数组覆盖已有列表（否则分析运行期间一次异常响应就清空侧边栏）
      if (!Array.isArray(data?.sessions)) return null
      setSessions(data.sessions)
      return data.sessions as SessionMeta[]
    } catch (e) {
      console.error('Failed to load sessions:', e)
      return null
    }
  }, [])

  // store 回调桥接：session_created 绑定视图、终态/报告刷新会话列表
  useEffect(() => {
    store.setCallbacks({
      onSessionCreated: (id) => setAndPersistSession(id),
      onSessionsChanged: () => { void loadSessions() },
    })
  }, [store, setAndPersistSession, loadSessions])

  // Auto-scroll to bottom：仅在用户未手动上拉时自动滚动（避免抢占手动滚动）
  const userScrolledUpRef = useRef(false)
  const scrollTimerRef = useRef<number | null>(null)
  useEffect(() => {
    const onScroll = () => {
      const nearBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 100
      userScrolledUpRef.current = !nearBottom
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const scrollToBottom = useCallback(() => {
    if (userScrolledUpRef.current) return
    // 流式高频更新（chat_token / thinking_token）合并为单一 scroll：clearTimeout
    // 防多个 smooth scroll 叠加导致页面抖动（多思考窗口展开时尤其明显）
    if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current)
    scrollTimerRef.current = window.setTimeout(() => {
      scrollTimerRef.current = null
      if (userScrolledUpRef.current) return
      window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })
    }, 100)
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  // selectSession 引用：mount 自动恢复在 selectSession 定义之前执行，经 ref 取最新引用
  const selectSessionRef = useRef<((id: string, forceRebuild?: boolean, opts?: { skipHomeRedirect?: boolean }) => Promise<boolean>) | null>(null)
  // 刷新自动恢复仅执行一次（避免 loadSessions 后续触发时重复恢复覆盖用户已切换视图）
  const restoredRef = useRef(false)

  // 首次加载：后端启动需要数秒（uvicorn + init_db），且深度分析运行期间
  // 事件循环被阻塞会导致 /api/sessions 长时间挂起。失败时持续退避重试
  // （间隔封顶 10s），后端恢复后列表自动出现，不再因重试次数耗尽而永久空白。
  useEffect(() => {
    let cancelled = false
    let retryCount = 0
    // 恢复指示退场阈值（fix: 后端持续不可用时「恢复会话中」永久占屏锁死主区）：
    // 连续 3 次失败（约 3.5s）后退出恢复指示、放行空态首页，退避重试继续——
    // 列表后续加载成功时 restoredRef 分支仍会自动恢复会话。
    const BOOT_FAIL_LIMIT = 3

    const loadWithRetry = async () => {
      const loaded = await loadSessions()
      if (cancelled || loaded === null) {
        if (cancelled) return
        retryCount++
        if (retryCount >= BOOT_FAIL_LIMIT) setBootRestoring(false)
        // 退避：500ms, 1s, 2s, ... 封顶 10s，无限重试直到成功
        const delay = Math.min(500 * Math.pow(2, retryCount - 1), 10000)
        setTimeout(loadWithRetry, delay)
        return
      }
      // 列表加载成功：侧边栏骨架退场
      setSessionsLoaded(true)
      // 列表加载成功：刷新后自动恢复此前查看的会话（delta spec: restore-session-on-refresh）
      if (!restoredRef.current) {
        restoredRef.current = true
        const persistedId = localStorage.getItem('fa_current_session_id')
        if (persistedId) {
          if (loaded.some(s => s.session_id === persistedId)) {
            // 会话仍存在：复用 selectSession 恢复（含 running 时 SSE 重连）。
            // 恢复落定后退出「恢复会话中」指示；失败也要退场避免卡死。
            const p = selectSessionRef.current?.(persistedId, false, { skipHomeRedirect: true })
            if (p) {
              p.finally(() => { if (!cancelled) setBootRestoring(false) })
            } else {
              setBootRestoring(false)
            }
          } else {
            // 持久化会话已被删除：清除并停留空态首页
            localStorage.removeItem('fa_current_session_id')
            setBootRestoring(false)
          }
        } else {
          // 无持久化会话：本就不在恢复中（防御，初值已为 false）
          setBootRestoring(false)
        }
      }
    }

    loadWithRetry()
    return () => { cancelled = true }
  }, [loadSessions])

  // ── 会话选择与生命周期 ──

  // 切换会话：store 统一管理订阅断开与状态重建
  // live 在途状态直接展示；pending 状态从后端重建并按需 resume 续传。
  // forceRebuild：轮询发现后台任务 completed 时强制从后端重建（拿报告/终态），
  // 跳过 live 短路——resume 把 origin 标为 live，completed 后需重建才有报告。
  const selectSession = async (sessionId: string, forceRebuild = false, opts?: { skipHomeRedirect?: boolean }) => {
    // AG-UI 通道切换守卫（add-assistant-ui-thread）：quick run 流式中切换会话 →
    // abort 当前 run（HttpAgent.abortRun → 服务端 CancelledError → 中断落库），
    // 并重挂载 Thread 清空本 mount 的新 run 消息；切回走 rebuildSession 快照。
    // （重新选择当前会话不重置，避免误清在看的消息）
    if (sessionId !== currentSessionId) {
      if (quickThreadRef.current?.isRunning()) quickThreadRef.current.abort()
      setAguiEpoch(e => e + 1)
      setQuickActive(false)
      setQuickRunning(false)
      setPendingQuickMessage(null)
    }
    store.switchSession(sessionId)
    setAndPersistSession(sessionId)
    // 全页视图(战绩/下载中心)下点会话也应回聊天首页——否则切了会话但主内容
    // 仍渲染全页,所选会话不可见。
    // boot 恢复（刷新后自动恢复会话）例外：用户显式停在 /downloads 等全页路由时
    // 刷新应保持原路由，恢复会话只更新侧边栏选中态，不抢主区路由（ADR/回归 2026-09-05）
    if (pathname !== '/' && !opts?.skipHomeRedirect) navigate('/')

    const state = store.getSnapshot(sessionId)
    if (!forceRebuild && state.origin === 'live') {
      // 在途会话：messages 由事件流实时维护，直接展示即可；
      // 若后端任务仍运行但本地无订阅（切换中断/页面刷新的孤立态），补一次 resume 续传
      const stillRunning = state.phase === 'streaming' || state.phase === 'connecting' || state.phase === 'resuming'
      if (stillRunning && !store.hasActiveReader()) {
        void store.resume(sessionId)
      }
      return true
    }

    // pending：从后端重建消息（chat_history + 管线快照 + 报告锚点定位）
    setSessionSwitching(true)
    try {
      const data = await store.loadSession(sessionId)
      if (!data) return false
      setMode(data.session_type === 'chat' ? 'quick' : 'deep')
      store.rebuildSession(sessionId, data)
      // running/clarifying 会话恢复事件流（ReAct 路径 status 为 clarifying 但任务可能仍在运行）。
      // 刷新后必须 resume 续传增量事件（thinking_token/chat_token 等）——思考/文本不进入
      // pipeline_snapshot，轮询 effect 只刷新管线快照，不 resume 则新生思考内容停止输出。
      // after_seq 用 rebuild 的 lastSeq：已持久化事件经 chat_history/thinking 重建并被
      // seq 守门去重，仅续传 journal 之后的新事件；无新事件时后端回 204，resume 收敛 idle。
      if (data.status === 'running' || data.status === 'clarifying') {
        void store.resume(sessionId)
      }
      return true
    } finally {
      setSessionSwitching(false)
    }
  }
  // 同步到 ref：供 mount 自动恢复（定义顺序在 selectSession 之前的 effect）调用
  selectSessionRef.current = selectSession

  const deleteSession = async (sessionId: string) => {
    try {
      // 先取消后端活跃任务（delta spec Task 5.3：删除会话时取消生成任务）
      // 后端 DELETE 端点也会 cancel，但显式调用确保取消
      try {
        await fetch(`/api/sessions/${sessionId}/cancel`, { method: 'POST' })
      } catch {
        // 忽略取消失败（可能无活跃任务），继续删除
      }
      // 清理本地流状态（abort 订阅 + 移除会话状态）
      store.dropStream(sessionId)
      await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' })
      setSessions(prev => prev.filter(s => s.session_id !== sessionId))
      // 同步后端列表（确保顺序/其他字段一致，乐观更新可能遗漏后端副作用）
      loadSessions()
      if (currentSessionId === sessionId) {
        setAndPersistSession(null)
      }
    } catch (e) {
      console.error('Failed to delete session:', e)
    }
  }

  const renameSession = async (sessionId: string, displayName: string) => {
    try {
      await fetch(`/api/sessions/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: displayName }),
      })
      setSessions(prev => prev.map(s => s.session_id === sessionId ? { ...s, display_name: displayName } : s))
      // 同步后端列表（确保顺序/其他字段一致）
      loadSessions()
    } catch (e) {
      console.error('Failed to rename session:', e)
    }
  }

  const newAnalysis = () => {
    // AG-UI 通道守卫：重置 quick Thread（abort 在途 run + 清空新 run 消息）
    if (quickThreadRef.current?.isRunning()) quickThreadRef.current.abort()
    setAguiEpoch(e => e + 1)
    setQuickActive(false)
    setQuickRunning(false)
    setPendingQuickMessage(null)
    // 断开当前会话的本地 SSE 订阅（不调后端 cancel，后台任务继续运行）
    // delta spec Task 5.3：新建分析仅断开本地订阅
    store.switchSession(null)
    setAndPersistSession(null)
    // 全页视图(战绩/下载中心)下回到聊天首页——否则新建后 pathname 不变,
    // 主内容仍渲染全页,新建会话不可见
    if (pathname !== '/') navigate('/')
  }

  // 清空全部会话（settings 会话管理分区清空成功后回调，M1 修复）：
  // 复用 newAnalysis 的「回到空态」重置语义——重置 currentSessionId、断开本地 SSE 订阅、
  // 清空消息流/phase 使视图派生成空态首页——并重载会话列表（后端已清空，避免侧边栏残留
  // 已删会话的幽灵项），再回到首页。若仅 navigate('/') 而不重置，返回后主区会渲染
  // 已删除会话的幽灵视图、侧边栏仍显示已删会话。
  const handleSessionsCleared = useCallback(() => {
    // AG-UI 通道守卫：同 newAnalysis，重置 quick Thread（abort 在途 run + 清空新 run 消息）
    if (quickThreadRef.current?.isRunning()) quickThreadRef.current.abort()
    setAguiEpoch(e => e + 1)
    setQuickActive(false)
    setQuickRunning(false)
    setPendingQuickMessage(null)
    // 断开当前会话本地 SSE 订阅 + 清空消息/phase → 派生空态首页
    store.switchSession(null)
    setAndPersistSession(null)
    // 后端已清空，重载会话列表刷新侧边栏（去除已删会话）
    void loadSessions()
    if (pathname !== '/') navigate('/')
  }, [store, setAndPersistSession, loadSessions, pathname])

  // 停止当前会话的生成任务（quick AG-UI 通道 abort；深度模式本地 abort + 后端 cancel）
  const stopGeneration = async () => {
    if (quickThreadRef.current?.isRunning()) {
      quickThreadRef.current.abort()
      return
    }
    if (!currentSessionId) return
    await store.cancel(currentSessionId)
  }

  // 页面卸载时断开所有本地 SSE 订阅（仅退订，不调后端 cancel）
  useEffect(() => {
    const handleBeforeUnload = () => store.abortAll()
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [store])

  // ── 输入提交（SSE 流由 store.submit 统一驱动）──

  const startAnalysis = async (query: string, sessionId: string | null = null) => {
    if (!apiKey.trim()) {
      setSettingsFocus('llm')
      navigate('/settings')
      return
    }
    // 拦截：当前会话正在运行时不允许提交新消息（delta spec Task 6.2）
    if (sessionId && store.isSessionRunning(sessionId)) {
      showWarning('该会话正在生成中，可停止后再发')
      return
    }
    // 请求级 LLM 配置（delta 5.4）：仅在有非空配置时携带 llm_config 字段
    const llmConfigPayload = buildLlmConfigPayload(llmConfig)
    try {
      await store.submit(
        {
          query,
          api_key: apiKey,
          user_id: getUserId(),
          analysis_type: 'comprehensive',
          ...(sessionId ? { session_id: sessionId } : {}),
          ...(llmConfigPayload ? { llm_config: llmConfigPayload } : {}),
        },
        { currentView: currentSessionId },
      )
    } catch {
      // 连接错误已在 store 内写入 error 消息；409 busy 额外提示
      const errText = store.getSnapshot(sessionId || currentSessionId || '')?.error
      if (errText) showWarning(errText)
    }
  }

  const quickChat = async (message: string) => {
    // AG-UI 通道（add-assistant-ui-thread）：quick 对话不再走 streamStore.submit，
    // 由 assistant-ui runtime 驱动 POST /api/agui/quick（SSE 事件流 → Thread 渲染）。
    // 单飞守卫：Thread run 进行中不允许新消息（与原 409 busy 提示语义一致）。
    if (quickThreadRef.current?.isRunning()) {
      showWarning('该会话正在生成中，可停止后再发')
      return
    }
    if (!quickThreadRef.current) {
      // EmptyState 首发：QuickThread 未挂载，排队待挂载后补发（不丢首条消息）
      setQuickActive(true)
      setPendingQuickMessage(message)
      return
    }
    setQuickActive(true)
    quickThreadRef.current.send(message)
  }

  const handleSendFromEmpty = (text: string, sendMode: string = 'deep') => {
    const query = text.trim()
    if (!query) return
    // 空态退场动画（replicate-chat-home）：首条消息发出后空态 200ms 淡出
    setEmptyLeaving(true)
    window.setTimeout(() => setEmptyLeaving(false), 200)
    if (sendMode === 'quick') {
      quickChat(query)
    } else {
      startAnalysis(query, null)
    }
  }

  const handleSendFromChat = (text: string) => {
    const t = text.trim()
    if (!t) return

    if (mode === 'quick') {
      quickChat(t)
      return
    }

    // Deep mode：直接走 /api/analyze，由 Agent 决定是否反问或执行分析
    startAnalysis(t, currentSessionId)
  }

  // adopt-assistant-ui-chat：assistant-ui Composer 提交桥接（onNew → App 分发）。
  // 拦截语义迁移至 runtime 状态判定（运行中 Composer 发送键变停止），此处的
  // isSessionRunning/单飞守卫作为兜底保留（toast 提示语义不变）。
  const handleComposerSubmit = (text: string) => {
    const t = text.trim()
    if (!t) return
    if (mode === 'quick') {
      quickChat(t)
      return
    }
    startAnalysis(t, currentSessionId)
  }

  // 运行中会话快照轮询（resume-pipeline-across-sessions Task 5）：
  // 切回 running 会话进入 analyzing 且无活跃 SSE（仅恢复态、非实时订阅）时，
  // 每 2s 拉取会话详情刷新分层时间轴；completed 则走 selectSession 完整恢复报告并自然停止；
  // failed 仅停止轮询（MVP 不展示失败态）。
  // 超时保护（Final Review Fix 2）：超过 MAX_POLLING_MS（5 分钟）后停止轮询并提示
  // 「管线可能已中断」，避免 ReAct 路径 status 永久 running 时轮询无限泄漏。
  // 前提不变量：store.hasActiveReader() 作为「SSE 在线」信号；
  // 用户在恢复态发起新分析时 store.submit 会建立新 reader，interval 回调内必须复查，
  // 避免 SSE 与轮询双写消息、以及轮询误调 selectSession 掐断新 SSE。
  useEffect(() => {
    if (appState !== 'analyzing' || !currentSessionId) return
    if (store.hasActiveReader()) return // 有活跃 SSE 订阅，进度由事件流驱动，无需轮询
    pollStartRef.current = Date.now() // 记录轮询起始时间（超时保护基准）
    const sessionId = currentSessionId
    const timer = setInterval(async () => {
      // 复查 SSE 在线信号：用户在恢复态发起新分析时轮询立即让位，
      // 等 effect 因状态变化重跑后 interval 自然清理
      if (store.hasActiveReader()) return
      // 超时保护：超过 MAX_POLLING_MS 则停止轮询并提示（ReAct 路径 status 可能永久 running）
      if (pollStartRef.current && Date.now() - pollStartRef.current >= MAX_POLLING_MS) {
        clearInterval(timer)
        // 尝试最后一次获取会话状态，检查是否有 failure_reason
        try {
          const finalResp = await fetch(`/api/sessions/${sessionId}`)
          if (finalResp.ok) {
            const finalData: SessionDetail = await finalResp.json()
            if (finalData.failure_reason) {
              store.updatePipelineContent(sessionId, `分析失败：${finalData.failure_reason}`)
              return
            }
          }
        } catch {
          // 忽略，回退到默认提示
        }
        store.updatePipelineContent(sessionId, '管线可能已中断，请刷新或重新发起')
        return
      }
      try {
        const resp = await fetch(`/api/sessions/${sessionId}`)
        if (!resp.ok) return
        const data: SessionDetail = await resp.json()
        if (data.status === 'running' && data.pipeline_snapshot) {
          let snap: PipelineSnapshot | null = null
          try {
            snap = JSON.parse(data.pipeline_snapshot)
          } catch {
            snap = null
          }
          if (snap) {
            store.updatePipelineSnapshot(sessionId, snap)
          }
          // running 但无快照（或快照解析失败）：本周期静默忽略，等下个周期重试
        } else if (data.status === 'completed') {
          // 后台管线完成：走 selectSession 完整重建（复用锚点定位、agentTimeline
          // 恢复、pipeline_timelines 结构化时序等逻辑，避免报告插入位置错误或时序丢失）。
          // clearInterval 先停止轮询，selectSession 内部状态变更触发
          // effect 重跑时 timer 已清理，不会递归。
          clearInterval(timer)
          await selectSession(sessionId, true)
        } else if (data.status === 'failed') {
          // 管线失败：展示中断原因，停止轮询
          clearInterval(timer)
          const reason = data.failure_reason || '管线执行失败'
          store.updatePipelineContent(sessionId, `分析失败：${reason}`)
        }
      } catch {
        // 轮询失败静默，下个周期重试
      }
    }, 2000)
    return () => {
      clearInterval(timer)
      pollStartRef.current = null // cleanup 重置，下次轮询重新计时
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appState, currentSessionId, store, stream.phase])

  // ── Render ──
  // leftInset 由 MainContent 的 render prop 依据 SidebarProvider 状态派生

  // 视图状态：quick AG-UI 通道活跃（首页发起、streamStore 无消息/phase）时进入聊天视图。
  // 附加派生不改深度模式语义（quickActive 仅在 quick 发送后为 true）。
  const viewState = appState === 'empty' && quickActive ? 'clarifying' : appState

  // 排队的首条 quick 消息：QuickThread 挂载（视图切换渲染完成）后立即补发。
  // deps 含 viewState/emptyLeaving：空态退场动画期间聊天视图尚未挂载，
  // 动画结束（emptyLeaving→false）后视图真正挂载 Thread，本 effect 重跑补发不丢
  // （replicate-chat-home 空态退场与 add-assistant-ui-thread 补发机制的衔接）。
  useEffect(() => {
    if (pendingQuickMessage === null) return
    if (!quickThreadRef.current) return // 等待 QuickThread 挂载
    const message = pendingQuickMessage
    setPendingQuickMessage(null)
    quickThreadRef.current.send(message)
  }, [pendingQuickMessage, viewState, emptyLeaving])

  // AG-UI quick 通道回调：新会话绑定（RUN_STARTED.thread_id）+ 完成刷新列表 + 错误提示
  const handleAguiSessionCreated = useCallback((id: string) => {
    setCurrentSessionId(prev => {
      if (prev) return prev
      localStorage.setItem('fa_current_session_id', id)
      return id
    })
    void loadSessions()
  }, [loadSessions])
  const handleAguiRunFinished = useCallback(() => {
    void loadSessions()
  }, [loadSessions])

  // 计算正在运行的会话 ID 集合（后端 status=running 或 store 中有进行中流）
  const runningSessionIds = new Set<string>()
  for (const s of sessions) {
    if (s.status === 'running') runningSessionIds.add(s.session_id)
  }
  for (const id of store.getActiveSessionIds()) {
    runningSessionIds.add(id)
  }

  // ── assistant-ui Thread 接线（adopt-assistant-ui-chat）──
  // pipeline 消息渲染时机：运行中（analyzing）始终显示；completed 会话恢复的
  // 静态完成时间轴（progress===1）随报告一并展示；其余（空树回退）不显示
  const visibleMessages = messages.filter((msg) => {
    if (msg.type === 'pipeline') return appState === 'analyzing' || msg.progress === 1
    return true
  })
  // Composer 运行中判定（对齐 store 拦截语义 isSessionRunning：前端有活跃 reader 在消费），
  // 叠加消息级流式标记（澄清阶段）与 quick run 进行中；恢复态（无 reader）不拦截发送
  const composerRunning =
    store.isSessionRunning(currentSessionId ?? '') ||
    messages.some((m) => m.streaming) || quickRunning

  // 消息 hover「重新生成」：定位该 assistant 消息前驱最近的 user 查询并重提交
  // （运行中拦截由 handleComposerSubmit → startAnalysis/quickChat 守卫兜底）
  const handleRegenerate = (messageId: string) => {
    const idx = visibleMessages.findIndex((m) => m.id === messageId)
    for (let i = idx - 1; i >= 0; i--) {
      const m = visibleMessages[i]
      if (m.type === 'user' && m.content.trim()) {
        handleComposerSubmit(m.content)
        return
      }
    }
  }

  // Composer 插槽：assistant-ui 输入区（Enter 发送 / Shift+Enter 换行 / 空禁用发送）
  const composerInput = (
    <div ref={composerWrapRef} className="flex-1 flex" data-testid="composer-wrap">
      <ComposerPrimitive.Input
        placeholder={mode === 'deep' ? '输入股票名称或代码，如 茅台、300750' : '输入问题，如：茅台、宁德时代怎么样'}
        className="flex-1 border-0 bg-transparent px-2 py-3 shadow-none resize-none text-sm leading-relaxed focus-visible:ring-0 min-h-[40px] max-h-[100px]"
        style={{ color: 'var(--text-default)' }}
      />
    </div>
  )
  const composerSend = (
    <>
      {/* 运行中：发送键变停止按钮（runtime 状态判定，拦截语义迁移） */}
      <ThreadPrimitive.If running>
        <ComposerPrimitive.Cancel
          data-testid="composer-stop"
          className="w-9 h-9 rounded-xl mb-0.5 mr-0.5 flex items-center justify-center shrink-0"
          style={{ background: 'var(--bg-brand)' }}
        >
          <i className="fas fa-stop text-xs text-white"></i>
        </ComposerPrimitive.Cancel>
      </ThreadPrimitive.If>
      <ThreadPrimitive.If running={false}>
        <ComposerPrimitive.Send
          data-testid="send-button"
          className="w-9 h-9 rounded-xl mb-0.5 mr-0.5 flex items-center justify-center shrink-0 disabled:opacity-50"
          style={{ background: 'var(--bg-brand)' }}
        >
          <i className="fas fa-arrow-up text-xs" style={{ color: 'var(--text-onbrand)' }}></i>
        </ComposerPrimitive.Send>
      </ThreadPrimitive.If>
    </>
  )

  // 快捷键集中注册（polish-dark-mode-shortcuts Task 3.1/3.2）：
  // Ctrl/Cmd+K 命令面板；Ctrl/Cmd+Shift+N 新建会话；`/` 聚焦输入框（输入态抑制在 useHotkeys 内）
  useHotkeys([
    { key: 'k', modifiers: ['ctrl', 'meta'], description: '打开命令面板', handler: () => setPaletteOpen(true), allowInInput: true },
    {
      key: 'n',
      modifiers: ['ctrl', 'meta', 'shift'],
      description: '新建会话',
      handler: () => newAnalysis(),
      allowInInput: true,
    },
    {
      key: '/',
      description: '聚焦输入框',
      handler: () => {
        const ta = composerWrapRef.current?.querySelector('textarea')
        ta?.focus()
      },
    },
  ])

  return (
    <SidebarProvider>
      <SidebarFixedToggle />
      <AppSidebar
        sessions={sessions}
        currentSessionId={currentSessionId}
        onSelect={selectSession}
        onDelete={deleteSession}
        onRename={renameSession}
        onNew={newAnalysis}
        onOpenDownloads={() => navigate('/downloads')}
        onOpenDecisions={() => navigate('/track-record')}
        onOpenScreening={() => navigate('/screening')}
        onOpenMemory={() => navigate('/memory')}
        onOpenFactors={() => navigate('/factors')}
        onOpenBroker={() => navigate('/broker')}
        onOpenOps={() => navigate('/ops')}
        onOpenSettings={() => { setSettingsFocus(undefined); navigate('/settings') }}
        themeChoice={themeChoice}
        onCycleTheme={cycleTheme}
        runningSessionIds={runningSessionIds}
        loading={!sessionsLoaded}
      />
      <MainContent>{(leftInset) => (
      <div>
        {pathname.startsWith('/track-record/predictions/') ? (
          // add-track-record-stage-c:观点详情页（prediction_id 为路径末段）
          <PredictionDetailPage
            predictionId={decodeURIComponent(pathname.split('/').pop() ?? '')}
            onBack={() => navigate('/track-record')}
          />
        ) : pathname === '/track-record/calibration' ? (
          // add-track-record-stage-c:置信度校准页
          <CalibrationPage onBack={() => navigate('/track-record')} />
        ) : pathname === '/decisions' || pathname === '/track-record' ? (
          // add-track-record:旧 /decisions 战绩页重定向语义,与新 /track-record 同渲染
          <TrackRecordPage onBack={() => navigate('/')} />
        ) : pathname === '/downloads' ? (
          <DownloadCenter onBack={() => navigate('/')} />
        ) : pathname === '/screening' ? (
          // 选股页（add-screening-pipeline T4）：最近成功批次池 + 批量深度分析
          <ScreeningPage onBack={() => navigate('/')} />
        ) : pathname === '/memory' ? (
          // 记忆确认页（add-memory-confirmation T2）：语义记忆条款审批队列
          <MemoryPage onBack={() => navigate('/')} />
        ) : pathname === '/factors' ? (
          // 因子库页（delta add-factor-library-page T2）：资产目录（筛选 + 行展开，只读）
          <FactorLibraryPage onBack={() => navigate('/')} />
        ) : pathname === '/broker' ? (
          // 执行台账页（delta add-broker-console T2.1）：资金/持仓/订单四态/门禁拒绝（只读）
          <BrokerConsolePage onBack={() => navigate('/')} />
        ) : pathname === '/ops' ? (
          // 日批运维页（delta add-daily-ops-page T2.1）：当日各批时间线 + 一键补跑 + 选股批次历史
          <DailyOpsPage onBack={() => navigate('/')} onOpenEvalOps={() => { setSettingsFocus('eval-ops'); navigate('/settings') }} />
        ) : pathname === '/settings' ? (
          // 设置中心页（add-agent-settings-center Task 7）：左导航六分区 + 右内容区
          <SettingsCenterPage
            config={llmConfig}
            backendDefaults={backendDefaults}
            profileStore={profileStore}
            capability={capability}
            onProbeCapability={handleProbeCapability}
            onSave={handleSaveConfig}
            onSaveAs={handleSaveAsConfig}
            onSwitchProfile={switchProfile}
            onDeleteProfile={handleDeleteProfile}
            onBack={() => navigate('/')}
            onCleared={handleSessionsCleared}
            initialModule={settingsFocus}
          />
        ) : bootRestoring && viewState === 'empty' ? (
          // 刷新恢复中：有持久化会话但尚未重建消息，显示恢复指示而非闪首页空态
          <div data-testid="restoring-state" className="flex flex-col items-center justify-center h-screen gap-3">
            <i className="fas fa-circle-notch fa-spin text-2xl" style={{ color: 'var(--bg-brand)' }}></i>
            <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>恢复会话中…</p>
          </div>
        ) : sessionSwitching ? (
          // 会话切换加载过渡：加载圈（motion.dev loading-circle-spinner 样式）
          <div data-testid="session-switch-loading" className="flex items-center justify-center h-screen">
            <motion.div
              className="w-10 h-10 rounded-full"
              style={{ border: '4px solid var(--bg-overlay-l2)', borderTopColor: 'var(--bg-brand)' }}
              animate={{ rotate: 360 }}
              transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
            />
          </div>
        ) : viewState === 'empty' || emptyLeaving ? (
          // 空态首页（replicate-chat-home）：首条消息发出后 200ms 淡出再切消息流
          <div
            data-testid="empty-state"
            className={`transition-opacity duration-200 ease-out ${emptyLeaving ? 'opacity-0' : 'opacity-100'}`}
          >
            <EmptyState
              onSend={handleSendFromEmpty}
              apiKey={apiKey}
              capability={capability}
              onOpenSettings={() => { setSettingsFocus('llm'); navigate('/settings') }}
              mode={mode}
              setMode={setMode}
              profileName={getActiveProfileName(profileStore)}
              profiles={profileStore.profiles}
              activeProfileId={profileStore.activeId}
              onSwitchProfile={switchProfile}
            />
          </div>
        ) : (
          <>
            {/* Header */}
            <header
              className="fixed top-0 right-0 z-50 flex items-center justify-between px-6 py-3 transition-[left] duration-200 ease-out"
              style={{ left: leftInset, background: 'var(--bg-base-default)' }}
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'var(--bg-brand)' }}>
                  <i className="fas fa-chart-line text-white text-sm"></i>
                </div>
                <span className="font-semibold text-sm tracking-wide" style={{ color: 'var(--text-default)' }}>FinAgent</span>
              </div>
              <div className="flex items-center gap-4">
                {lastExportableReport && (
                  <Button data-testid="topbar-files-button" variant="ghost" size="sm"
                    onClick={() => setDrawerMessage(lastExportableReport!)}>
                    <i className="fas fa-folder-open mr-1"></i>查看全部文件
                  </Button>
                )}
                <Button variant="ghost" size="sm" onClick={() => { setSettingsFocus(undefined); navigate('/settings') }}>
                  <i className="fas fa-cog mr-1"></i>设置
                </Button>
              </div>
            </header>

            {/* Chat messages（adopt-assistant-ui-chat）：assistant-ui Thread 渲染。
                消息区为固定高度滚动容器，Viewport autoScroll 提供流式跟随/上翻暂停。 */}
            <AnalysisRuntimeProvider
              messages={visibleMessages}
              isRunning={composerRunning}
              onSubmit={handleComposerSubmit}
              onCancel={stopGeneration}
            >
              <div className="fixed top-0 bottom-0 right-0 transition-[left] duration-200 ease-out" style={{ left: leftInset }} data-testid="thread-main">
                <ThreadMessages
                  onRegenerate={handleRegenerate}
                  onOpenReport={setPanelMessage}
                  onFeedback={(value) => {
                    // add-user-feedback:点赞/点踩上报(按当前会话解析 trace,后端旁路容错)
                    if (!currentSessionId) return
                    void fetch('/api/feedback', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ session_id: currentSessionId, value }),
                    }).catch(() => {})
                  }}
                  footer={
                    <>
                      {/* 会话级文件导出入口：报告名横幅（每份报告，标题「名称（代码）」）尾部追加全部文件横幅 */}
                      {exportableReports.map((msg) => (
                        <ReportNameBanner key={`rb-${msg.id}`} msg={msg} onOpen={setDrawerMessage} />
                      ))}
                      {lastExportableReport && (
                        <AllFilesBanner onOpen={() => setDrawerMessage(lastExportableReport!)} />
                      )}

                      {/* AG-UI quick 通道（add-assistant-ui-thread）：历史消息由 Thread 渲染
                          （rebuildSession 快照），Thread 只接管本 mount 的新 run。
                          key= 纪元+apiKey：会话切换/新建重置 Thread；apiKey 变更重建 agent。 */}
                      {mode === 'quick' && (
                        <QuickThread
                          key={`agui-${aguiEpoch}-${apiKey}`}
                          ref={quickThreadRef}
                          apiKey={apiKey}
                          llmConfig={buildLlmConfigPayload(llmConfig)}
                          sessionId={currentSessionId}
                          onSessionCreated={handleAguiSessionCreated}
                          onRunFinished={handleAguiRunFinished}
                          onRunningChange={setQuickRunning}
                          onError={showWarning}
                        />
                      )}
                    </>
                  }
                />
              </div>

            {/* 「会话生成中」警告：fixed 顶部 toast，浮于 header(z-50)/输入框(z-40) 之上 */}
            {warningMessage && (
              <div
                className="fixed top-16 left-1/2 -translate-x-1/2 z-[60] px-4 py-2 rounded-lg text-xs flex items-center gap-2 shadow-lg"
                style={{ background: 'var(--status-warning-default)', color: 'white' }}
              >
                <i className="fas fa-exclamation-triangle"></i>
                {warningMessage}
              </div>
            )}

            {/* 停止按钮（流式输出时显示；quick AG-UI 通道由 quickRunning 驱动） */}
            {(appState === 'analyzing' || messages.some(m => m.streaming) || quickRunning) && (
              <div
                className="fixed right-0 z-40 flex flex-col items-center gap-2 px-4 transition-[left] duration-200 ease-out"
                style={{ left: leftInset, bottom: '90px' }}
              >
                {(appState === 'analyzing' || quickRunning) && (
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={stopGeneration}
                    className="rounded-lg"
                  >
                    <i className="fas fa-stop text-[10px]"></i>
                    停止生成
                  </Button>
                )}
              </div>
            )}

            {/* Fixed input at bottom（Composer 插槽：assistant-ui 输入区，须在 Provider 内） */}
            <ChatInputBar
              onSend={handleSendFromChat}
              leftInset={leftInset}
              mode={mode}
              setMode={setMode}
              capability={capability}
              onNewAnalysis={newAnalysis}
              apiKey={apiKey}
              onOpenSettings={() => { setSettingsFocus('llm'); navigate('/settings') }}
              profileName={getActiveProfileName(profileStore)}
              profiles={profileStore.profiles}
              activeProfileId={profileStore.activeId}
              onSwitchProfile={switchProfile}
              composerInput={composerInput}
              composerSend={composerSend}
            />
              </AnalysisRuntimeProvider>
            </>
        )}
      </div>
      )}</MainContent>

      {/* 报告导出抽屉（「全部文件」横幅入口） */}
      {drawerMessage && (
        <ReportFileDrawer drawerMessage={drawerMessage} onClose={() => setDrawerMessage(null)} />
      )}

      {/* 报告右侧面板（add-report-side-panel）：桌面端展示；移动端不渲染（消息流全宽回退，
          摘要卡「打开报告」点击时已同步展开消息流内完整区作为回退） */}
      <ReportSidePanelWithViewport msg={panelMessage} onClose={() => setPanelMessage(null)} />

      {/* 命令面板（polish-dark-mode-shortcuts）：Cmd/Ctrl+K */}
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        sessions={sessions}
        onSelectSession={selectSession}
        onNewSession={newAnalysis}
        onOpenDownloads={() => navigate('/downloads')}
        onOpenMemory={() => navigate('/memory')}
        onOpenFactors={() => navigate('/factors')}
        onOpenBroker={() => navigate('/broker')}
        onOpenOps={() => navigate('/ops')}
        onCycleTheme={cycleTheme}
        themeChoice={themeChoice}
      />

      {/* 行情快照新鲜度全局横幅（add-market-snapshot D5/T9）：挂载即轮询
          GET /api/snapshot/status，stale 时才渲染。挂 App 壳层（与 Toaster 同级）
          而非「会话生成中」警告条（其在聊天分支内，空态首页不渲染）。 */}
      <SnapshotBanner />

      {/* 全局 toast 容器（shadcn/sonner 封装；仅挂载，暂无调用方） */}
      <Toaster />
    </SidebarProvider>
  )
}

// ── Sidebar（add-collapsible-sidebar：shadcn Sidebar 原语重构）──
function AppSidebar({ sessions, currentSessionId, onSelect, onDelete, onRename, onNew, onOpenDownloads, onOpenDecisions, onOpenScreening, onOpenMemory, onOpenFactors, onOpenBroker, onOpenOps, onOpenSettings, runningSessionIds, loading = false, themeChoice, onCycleTheme }: {
  sessions: SessionMeta[]
  currentSessionId: string | null
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onRename: (id: string, name: string) => void
  onNew: () => void
  onOpenDownloads: () => void
  onOpenDecisions: () => void
  onOpenScreening: () => void
  onOpenMemory: () => void
  onOpenFactors: () => void
  onOpenBroker: () => void
  onOpenOps: () => void
  onOpenSettings: () => void
  runningSessionIds: Set<string>
  loading?: boolean
  themeChoice: ThemeChoice
  onCycleTheme: () => void
}) {
  const { state, isMobile, setOpenMobile } = useSidebar()
  const collapsed = state === 'collapsed'
  const [search, setSearch] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  // 删除二次确认（delta spec: 删除 SHALL 二次确认）
  const [deletingSession, setDeletingSession] = useState<SessionMeta | null>(null)

  const filtered = sessions.filter(s =>
    (s.stock_name || '').toLowerCase().includes(search.toLowerCase()) ||
    (s.stock_code || '').includes(search) ||
    (s.display_name || '').toLowerCase().includes(search.toLowerCase())
  )

  const handleSelect = (id: string) => {
    onSelect(id)
    // 移动端：选中会话后抽屉自动关闭（delta spec）
    setOpenMobile(false)
  }

  // ── 收起态图标栏（仅桌面；移动端抽屉恒渲染展开 rail，否则收起态持久化时抽屉为空）──
  // 与展开态列表同挂载于 Sidebar 内层（display 切换显隐），避免每次展开重挂载会话列表
  const collapsedRail = collapsed && !isMobile ? (
    <div className="flex flex-col items-start pl-3.5 pt-14 gap-2 h-full">
          <SidebarIcon label="新建分析">
            <Button variant="ghost" size="icon" onClick={onNew} aria-label="新建分析" data-testid="sidebar-new-collapsed">
              <i className="fas fa-plus text-xs"></i>
            </Button>
          </SidebarIcon>
          <SidebarIcon label={`主题：${themeChoice === 'system' ? '跟随系统' : themeChoice === 'dark' ? '深色' : '浅色'}`}>
            <Button variant="ghost" size="icon" onClick={onCycleTheme} aria-label="切换主题" data-testid="theme-toggle-collapsed">
              {themeChoice === 'light' ? (
                // 空心太阳：FA6 free 无描边款，用内联 SVG（圆 + 八条放射线）
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
                </svg>
              ) : (
                <i className={`fas ${themeChoice === 'dark' ? 'fa-moon' : 'fa-adjust'} text-xs`}></i>
              )}
            </Button>
          </SidebarIcon>
          <div className="flex-1" />
          <SidebarIcon label="选股池">
            <Button variant="ghost" size="icon" onClick={onOpenScreening} aria-label="选股池" data-testid="sidebar-screening-collapsed">
              <i className="fas fa-filter text-xs"></i>
            </Button>
          </SidebarIcon>
          <SidebarIcon label="记忆">
            <Button variant="ghost" size="icon" onClick={onOpenMemory} aria-label="记忆" data-testid="sidebar-memory-collapsed">
              <i className="fas fa-brain text-xs"></i>
            </Button>
          </SidebarIcon>
          <SidebarIcon label="因子库">
            <Button variant="ghost" size="icon" onClick={onOpenFactors} aria-label="因子库" data-testid="sidebar-factors-collapsed">
              <i className="fas fa-flask text-xs"></i>
            </Button>
          </SidebarIcon>
          <SidebarIcon label="执行台账">
            <Button variant="ghost" size="icon" onClick={onOpenBroker} aria-label="执行台账" data-testid="sidebar-broker-collapsed">
              <i className="fas fa-book-open text-xs"></i>
            </Button>
          </SidebarIcon>
          <SidebarIcon label="日批运维">
            <Button variant="ghost" size="icon" onClick={onOpenOps} aria-label="日批运维" data-testid="sidebar-ops-collapsed">
              <i className="fas fa-list-check text-xs"></i>
            </Button>
          </SidebarIcon>
          <SidebarIcon label="决策战绩">
            <Button variant="ghost" size="icon" onClick={onOpenDecisions} aria-label="决策战绩" data-testid="sidebar-decisions-collapsed">
              <i className="fas fa-chart-line text-xs"></i>
            </Button>
          </SidebarIcon>
          <SidebarIcon label="下载管理">
            <Button variant="ghost" size="icon" onClick={onOpenDownloads} aria-label="下载管理" data-testid="sidebar-downloads-collapsed">
              <i className="fas fa-download text-xs"></i>
            </Button>
          </SidebarIcon>
          <SidebarIcon label="设置">
            <Button variant="ghost" size="icon" onClick={onOpenSettings} aria-label="设置" data-testid="sidebar-settings-collapsed">
              <i className="fas fa-cog text-xs"></i>
            </Button>
          </SidebarIcon>
        </div>
  ) : null

  // ── 展开态（桌面）与移动端抽屉共用 ──
  return (
    <Sidebar collapsedRail={collapsedRail} expandedRail={
      // min-w-0：本节点是 sidebar.tsx 显隐包裹层（display:flex）的 flex item，
      // 默认 min-width:auto 会拒绝收缩到会话名 min-content 以下，长会话名
      // （truncate/nowrap）把整个 rail 撑出 256px 内层，按钮/搜索框 w-full
      // 跟随拉伸后被 aside 裁剪；min-w-0 恢复收缩到包裹层宽度
      <div className="flex min-w-0 flex-col h-full">
      {/* 顶部占位：撑起高度使下方「新建分析」按钮避开悬浮折叠按钮（fixed top-3 left-3，h-10） */}
      <div className="h-11 shrink-0" />

      {/* New analysis button */}
      <div className="p-3">
        <Button
          onClick={onNew}
          data-testid="sidebar-new"
          className="w-full rounded-xl text-sm font-medium"
        >
          <i className="fas fa-plus text-xs"></i>
          新建分析
        </Button>
      </div>

      {/* 选股池入口（add-screening-pipeline T4）：与折叠态图标栏/命令面板同源 navigate */}
      <div className="px-3 -mt-1 pb-1">
        <Button
          onClick={onOpenScreening}
          data-testid="sidebar-screening"
          variant="ghost"
          className="w-full justify-start gap-2 rounded-lg text-xs h-8 font-normal"
        >
          <i className="fas fa-filter text-[10px]"></i>
          选股池
        </Button>
      </div>

      {/* Search */}
      <div className="px-3 pb-3">
        <Input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="搜索股票..."
          className="h-9 text-xs"
        />
      </div>

      {/* Session list（展开渐入场：fade + 左移滑入，与 aside 宽度动画同节奏） */}
      <motion.div
        data-testid="session-list"
        className="flex-1 overflow-y-auto px-2 pb-2"
        initial={{ opacity: 0, x: -10 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
      >
        {loading && sessions.length === 0 ? (
          // 加载骨架：/api/sessions 首次返回前显示占位行，避免闪「暂无历史会话」
          <div data-testid="sidebar-skeleton" className="space-y-2 px-1 pt-1">
            {[0, 1, 2, 3].map(i => (
              <div key={i} className="rounded-lg px-3 py-2 animate-pulse" style={{ background: 'var(--bg-overlay-l1)' }}>
                <div className="h-3 rounded w-2/3 mb-1.5" style={{ background: 'var(--bg-overlay-l2)' }} />
                <div className="h-2 rounded w-1/3" style={{ background: 'var(--bg-overlay-l2)' }} />
              </div>
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <p className="text-center text-xs py-4" style={{ color: 'var(--text-tertiary)' }}>暂无历史会话</p>
        ) : (
          filtered.map(s => (
            <div
              key={s.session_id}
              onClick={() => handleSelect(s.session_id)}
              className="group relative px-3 py-2 rounded-lg cursor-pointer transition-colors mb-1"
              style={currentSessionId === s.session_id ? { background: 'var(--bg-overlay-l2)' } : { background: 'transparent' }}
              onMouseEnter={(e) => { if (currentSessionId !== s.session_id) e.currentTarget.style.background = 'var(--bg-overlay-l1)' }}
              onMouseLeave={(e) => { if (currentSessionId !== s.session_id) e.currentTarget.style.background = 'transparent' }}
            >
              {editingId === s.session_id ? (
                <Input
                  type="text"
                  value={editText}
                  onChange={e => setEditText(e.target.value)}
                  onBlur={() => {
                    if (editText.trim()) onRename(s.session_id, editText.trim())
                    setEditingId(null)
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter') {
                      if (editText.trim()) onRename(s.session_id, editText.trim())
                      setEditingId(null)
                    }
                    if (e.key === 'Escape') setEditingId(null)
                  }}
                  onClick={e => e.stopPropagation()}
                  autoFocus
                  className="h-7 rounded px-2 py-1 text-xs"
                />
              ) : (
                <>
                  <div
                    className="text-sm truncate flex items-center gap-1.5"
                    style={{ color: 'var(--text-default)' }}
                    onDoubleClick={e => {
                      e.stopPropagation()
                      setEditingId(s.session_id)
                      setEditText(s.display_name)
                    }}
                  >
                    {runningSessionIds.has(s.session_id) && (
                      <span className="inline-block w-2 h-2 rounded-full bg-[color:var(--status-success-default)] animate-pulse flex-shrink-0" />
                    )}
                    {s.display_name}
                  </div>
                  <div className="text-[10px] flex items-center gap-2" style={{ color: 'var(--text-tertiary)' }}>
                    <span>{s.stock_name}</span>
                    <span>{formatSessionTime(s.created_at)}</span>
                  </div>
                  {/* 会话项操作菜单（hover 出现「···」）：重命名（原地输入）/ 删除（二次确认） */}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={e => e.stopPropagation()}
                        aria-label={`会话操作 ${s.display_name}`}
                        data-testid={`session-menu-${s.session_id}`}
                        className="absolute right-2 top-2 h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        <i className="fas fa-ellipsis-h text-xs"></i>
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" onClick={e => e.stopPropagation()}>
                      <DropdownMenuItem onClick={() => { setEditingId(s.session_id); setEditText(s.display_name) }}>
                        <i className="fas fa-pen text-[10px] mr-1.5"></i>重命名
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => setDeletingSession(s)}
                        className="text-destructive focus:text-destructive"
                      >
                        <i className="fas fa-trash text-[10px] mr-1.5"></i>删除
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </>
              )}
            </div>
          ))
        )}
      </motion.div>

      {/* 底部固定区已移除：下载管理入口保留在折叠态图标栏与命令面板（⌘K） */}

      {/* 删除二次确认弹窗 */}
      <Dialog open={deletingSession !== null} onOpenChange={(o) => { if (!o) setDeletingSession(null) }}>
        <DialogContent className="max-w-sm">
          <DialogTitle className="text-sm font-semibold" style={{ color: 'var(--text-default)' }}>删除会话</DialogTitle>
          <DialogDescription className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
            确定删除「{deletingSession?.display_name}」吗？删除后不可恢复。
          </DialogDescription>
          <div className="flex gap-2 justify-end mt-4">
            <Button variant="secondary" size="sm" onClick={() => setDeletingSession(null)}>取消</Button>
            <Button
              variant="destructive"
              size="sm"
              data-testid="confirm-delete"
              onClick={() => {
                if (deletingSession) onDelete(deletingSession.session_id)
                setDeletingSession(null)
              }}
            >
              删除
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      </div>
    } />
  )
}

// ── 主内容区包装：依据 SidebarProvider 状态派生左边距（200ms 过渡）──
// render prop 注入 leftInset（fixed 定位的 header/输入栏/停止按钮使用）
function MainContent({ children }: { children: (leftInset: number) => ReactNode }) {
  const { state, isMobile } = useSidebar()
  const collapsed = state === 'collapsed'
  const leftInset = isMobile ? 0 : collapsed ? 52 : 256
  return (
    <div
      className={isMobile ? '' : 'transition-[margin] duration-200 ease-out'}
      style={isMobile ? undefined : { marginLeft: leftInset }}
    >
      {children(leftInset)}
    </div>
  )
}

// 报告面板视口宿主：从 SidebarProvider 读移动端标记（add-report-side-panel 移动端回退）
function ReportSidePanelWithViewport({ msg, onClose }: { msg: UIMessage | null; onClose: () => void }) {
  const { isMobile } = useSidebar()
  return (
    <ReportSidePanel msg={msg} onClose={onClose} isMobile={isMobile}>
      {msg && <ReportCard msg={msg} variant="panel" />}
    </ReportSidePanel>
  )
}

// ── Empty State ──
export function EmptyState({ onSend, apiKey, capability, onOpenSettings, mode, setMode, profileName, profiles, activeProfileId, onSwitchProfile }: {
  onSend: (text: string, mode?: string) => void
  apiKey: string
  capability: CapabilityMatrix | null
  onOpenSettings: () => void
  mode: 'quick' | 'deep'
  setMode: (m: 'quick' | 'deep') => void
  profileName: string
  profiles: LLMProfile[]
  activeProfileId: string
  onSwitchProfile: (id: string) => void
}) {
  const [text, setText] = useState('')
  const [dropdownOpen, setDropdownOpen] = useState(false)
  // LLM 切换下拉框展开状态（delta Decision 11）
  const [llmDropdownOpen, setLlmDropdownOpen] = useState(false)
  // 点击下拉框外部区域关闭（delta fix-dropdown-outside-close）：
  // rowRef 包裹触发按钮与弹层两者，避免「点触发按钮先关后开」竞态
  const rowRef = useRef<HTMLDivElement>(null)
  useClickOutside(rowRef, dropdownOpen || llmDropdownOpen, () => {
    setDropdownOpen(false)
    setLlmDropdownOpen(false)
  })

  const handleKeydown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const query = text.trim()
      if (!query) return
      if (!apiKey) { onOpenSettings(); return }
      onSend(query, mode)
      setText('')
    }
  }

  const handleSend = () => {
    const query = text.trim()
    if (!query) return
    if (!apiKey) { onOpenSettings(); return }
    onSend(query, mode)
    setText('')
  }

  const modes = [
    { id: 'quick' as const, label: '快速模式', icon: 'fa-bolt', color: 'text-[var(--status-warning-default)]', desc: '单次 LLM + Web Search，秒级响应' },
    { id: 'deep' as const, label: '深度研究', icon: 'fa-layer-group', color: 'text-[var(--text-brand)]', desc: '5 层 Agent 流水线，2-5 分钟完整报告' },
  ]
  const currentMode = modes.find(m => m.id === mode)!

  return (
    <div className="flex flex-col items-center justify-center flex-1 px-4 transition-all duration-700" style={{ minHeight: '100vh' }}>
      {/* Logo & Greeting（replicate-chat-home：问候语 + 输入提示副标题） */}
      <div className="text-center mb-10 animate-fade-in-up">
        <div className="w-16 h-16 rounded-xl flex items-center justify-center mx-auto mb-5" style={{ background: 'var(--bg-brand)' }}>
          <i className="fas fa-chart-line text-white text-2xl"></i>
        </div>
        <h1 className="text-3xl font-bold mb-2" style={{ color: 'var(--text-default)', fontFamily: 'var(--font-family-heading)' }}>
          今天想研究什么？
        </h1>
        <p className="text-sm" style={{ color: 'var(--text-tertiary)' }}>AI 驱动的 A 股投研分析系统</p>
        <p className="text-xs mt-1" style={{ color: 'var(--text-tertiary)' }}>
          <i className="fas fa-keyboard mr-1"></i>
          支持输入股票名称、代码或自然语言指令
        </p>
      </div>

      {/* Input Box */}
      <div className="w-full max-w-2xl animate-fade-in-up relative z-10" style={{ animationDelay: '0.1s' }}>
        <div className="glass-input rounded-2xl p-2">
          {/* Mode dropdown */}
          <div className="relative flex items-center gap-2 px-4 pt-1 pb-0" ref={rowRef}>
            <button
              onClick={() => { setLlmDropdownOpen(false); setDropdownOpen(!dropdownOpen) }}
              className="flex items-center gap-1.5 text-[10px] font-medium rounded px-2 py-0.5 transition-colors hover:bg-muted"
            >
              <span style={{ color: 'var(--text-tertiary)' }}>模式：</span>
              <i className={`fas ${currentMode.icon} ${currentMode.color}`}></i>
              <span className={currentMode.color}>{currentMode.label}</span>
              <i className={`fas fa-chevron-${dropdownOpen ? 'up' : 'down'} text-[8px] ml-0.5`} style={{ color: 'var(--text-tertiary)' }}></i>
            </button>
            {dropdownOpen && (
              <div className="absolute left-4 top-7 z-[70] w-72 glass-card rounded-lg overflow-hidden" style={{ border: '1px solid var(--border-neutral-l1)' }}>
                {modes.map(m => {
                  const gate = canEnterMode(m.id, capability)
                  return (
                  <button
                    key={m.id}
                    onClick={() => { if (gate.allowed) { setMode(m.id); setDropdownOpen(false) } }}
                    disabled={!gate.allowed}
                    title={gate.allowed ? undefined : gate.reason}
                    className={`w-full flex items-start gap-2 px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${mode === m.id ? 'bg-accent' : ''}`}
                    onMouseEnter={(e) => { if (mode !== m.id && gate.allowed) e.currentTarget.style.background = 'var(--bg-overlay-l1)' }}
                    onMouseLeave={(e) => { if (mode !== m.id) e.currentTarget.style.background = 'transparent' }}
                  >
                    <i className={`fas ${m.icon} ${m.color} text-xs mt-0.5`}></i>
                    <div className="flex-1 min-w-0">
                      <div className={`text-xs font-medium ${mode === m.id ? m.color : ''}`} style={mode !== m.id ? { color: 'var(--text-secondary)' } : {}}>
                        {m.label}
                        {mode === m.id && <i className="fas fa-check ml-1.5 text-[10px]"></i>}
                      </div>
                      <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-tertiary)' }}>{m.desc}</div>
                      {!gate.allowed && (
                        <div className="text-[10px] mt-0.5" style={{ color: 'var(--status-error-default)' }}>{gate.reason}</div>
                      )}
                    </div>
                  </button>
                  )
                })}
              </div>
            )}

            {/* LLM 切换下拉框（delta Decision 11）；无 profile 时点击引导配置 */}
            <div className="relative inline-block">
              <button
                onClick={() => {
                  if (profiles.length === 0) { onOpenSettings(); return }
                  setDropdownOpen(false)
                  setLlmDropdownOpen(!llmDropdownOpen)
                }}
                className="flex items-center gap-1 text-[10px] font-medium rounded px-2 py-0.5 transition-colors hover:bg-muted"
              >
                <i className="fas fa-microchip text-[var(--text-tertiary)]"></i>
                <span style={{ color: 'var(--text-secondary)' }}>{profileName}</span>
                <i className={`fas fa-chevron-${llmDropdownOpen ? 'up' : 'down'} text-[8px] ml-0.5`} style={{ color: 'var(--text-tertiary)' }}></i>
              </button>
              {llmDropdownOpen && profiles.length > 0 && (
                <div className="absolute left-0 top-7 z-[70] w-56 glass-card rounded-lg overflow-hidden" style={{ border: '1px solid var(--border-neutral-l1)' }}>
                  {profiles.map(p => (
                    <button
                      key={p.id}
                      onClick={() => { onSwitchProfile(p.id); setLlmDropdownOpen(false) }}
                      className={`w-full flex items-center gap-2 px-3 py-2 text-left text-xs transition-colors ${p.id === activeProfileId ? 'bg-accent' : ''}`}
                    >
                      {p.id === activeProfileId && <i className="fas fa-check text-[10px]" style={{ color: 'var(--text-brand)' }}></i>}
                      <span style={{ color: 'var(--text-secondary)' }}>{p.name}</span>
                    </button>
                  ))}
                  <div style={{ borderTop: '1px solid var(--border-neutral-l1)', margin: '4px 0' }} />
                  <button
                    onClick={() => { setLlmDropdownOpen(false); onOpenSettings() }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs transition-colors"
                  >
                    <i className="fas fa-sliders-h text-[10px]" style={{ color: 'var(--text-tertiary)' }}></i>
                    <span style={{ color: 'var(--text-secondary)' }}>LLM 配置…</span>
                  </button>
                </div>
              )}
            </div>
          </div>
          <div className="flex items-end gap-2">
            <Textarea
              rows={1}
              placeholder={mode === 'quick' ? '输入问题，如：茅台、宁德时代怎么样' : '输入股票名称或代码，如 茅台、300750'}
              className="flex-1 border-0 bg-transparent px-4 py-3 shadow-none resize-none text-sm leading-relaxed focus-visible:ring-0 min-h-[48px] max-h-[120px]"
              style={{ color: 'var(--text-default)' }}
              value={text}
              onChange={e => setText(e.target.value)}
              onKeyDown={handleKeydown}
            />
            <Button
              data-testid="send-button"
              variant="default"
              size="icon"
              onClick={handleSend}
              className="w-10 h-10 rounded-xl mb-1 mr-1"
            >
              <i className="fas fa-arrow-up text-white text-sm"></i>
            </Button>
          </div>
        </div>
        {!apiKey ? (
          <p className="text-center text-xs mt-2" style={{ color: 'var(--text-tertiary)' }}>
            <i className="fas fa-info-circle mr-1"></i>
            请先配置 LLM API 才能开始分析
            <Button variant="link" className="ml-1 h-auto p-0 text-xs" onClick={onOpenSettings}>去配置</Button>
          </p>
        ) : (
          <p className="text-center text-xs mt-2" style={{ color: 'var(--text-tertiary)' }}>
            <i className="fas fa-check-circle mr-1" style={{ color: 'var(--status-success-default)' }}></i>
            LLM API 已配置
            <Button variant="link" className="ml-1 h-auto p-0 text-xs" onClick={onOpenSettings}>修改</Button>
          </p>
        )}
      </div>

      {/* 建议卡片（replicate-chat-home）：点击填入输入框，不直接发送 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-5 max-w-2xl w-full px-4 animate-fade-in-up" style={{ animationDelay: '0.15s' }}>
        {SUGGESTION_CARDS.map(card => (
          <button
            key={card.title}
            type="button"
            data-testid={`suggestion-${card.title}`}
            onClick={() => { setText(card.prompt) }}
            className="glass-card rounded-xl px-3 py-3 text-left transition-transform hover:-translate-y-0.5 hover:shadow-md"
          >
            <div className="flex items-center gap-1.5 mb-1">
              <i className={`fas ${card.icon} text-xs`} style={{ color: 'var(--text-brand)' }}></i>
              <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>{card.title}</span>
            </div>
            <p className="text-[11px] leading-relaxed line-clamp-2" style={{ color: 'var(--text-tertiary)' }}>{card.prompt}</p>
          </button>
        ))}
      </div>

      {/* Feature cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-8 max-w-2xl w-full px-4 animate-fade-in-up" style={{ animationDelay: '0.2s' }}>
        {[
          { icon: 'users', color: 'text-[var(--text-brand)]', label: '4 维并行分析' },
          { icon: 'comments', color: 'text-[var(--status-warning-default)]', label: 'Bull/Bear 辩论' },
          { icon: 'shield-alt', color: 'text-[var(--status-error-default)]', label: 'Risk 压力测试' },
          { icon: 'file-alt', color: 'text-[var(--status-success-default)]', label: '结构化报告' },
        ].map(f => (
          <div key={f.label} className="glass-card rounded-xl p-3 text-center">
            <div className={`${f.color} text-lg mb-1`}><i className={`fas fa-${f.icon}`}></i></div>
            <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>{f.label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Thinking Banner (Kimi-style collapsible reasoning) ──
// 思考横幅：统一快速模式与深度模式澄清阶段的思考流式展示。
// 思考中显示"思考中"并自动展开；完成后按横幅展开/折叠状态与是否有标题分别展示。
// embedded=true 时嵌入 TimelineRenderer 统一白色容器：去掉自身灰底框与外边距，融入时间轴。
export function ThinkingBanner({ content, streaming, title, embedded = false }: { content: string; streaming: boolean; title?: string; embedded?: boolean }) {
  const [expanded, setExpanded] = useState(true)
  const contentRef = useRef<HTMLDivElement>(null)

  // 流式时展开（实时看思考过程），完成后自动折叠（与 Kimi 一致）
  useEffect(() => {
    setExpanded(streaming)
  }, [streaming])

  // 流式时自动滚底，但用户手动上拉后不抢占
  const contentScrolledUpRef = useRef(false)
  useEffect(() => {
    const el = contentRef.current
    if (!el) return
    const onScroll = () => {
      const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20
      contentScrolledUpRef.current = !nearBottom
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (expanded && streaming && contentRef.current && !contentScrolledUpRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [content, expanded, streaming])

  // 横幅标题文案：思考中显示"思考中"；完成折叠按标题有无显示标题/"思考已完成"；完成展开固定"思考已完成"
  const bannerText = streaming
    ? '思考中'
    : expanded
      ? '思考已完成'
      : (title || '思考已完成')

  return (
    <div className={embedded ? '' : 'mb-3'}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 rounded-lg transition-colors text-left"
        style={{ background: embedded ? 'transparent' : 'var(--bg-overlay-l1)' }}
        onMouseEnter={(e) => {e.currentTarget.style.background = 'var(--bg-overlay-l2)'}}
        onMouseLeave={(e) => {e.currentTarget.style.background = embedded ? 'transparent' : 'var(--bg-overlay-l1)'}}
      >
        {streaming ? (
          <span className="relative flex h-2 w-2 flex-shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: 'var(--bg-brand)' }}></span>
            <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: 'var(--bg-brand)' }}></span>
          </span>
        ) : (
          <i className="fas fa-check-circle text-xs flex-shrink-0" style={{ color: 'var(--text-brand)' }}></i>
        )}
        <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          {bannerText}
        </span>
        <i className={`fas fa-chevron-${expanded ? 'down' : 'right'} text-[10px] ml-auto transition-transform`} style={{ color: 'var(--text-tertiary)' }}></i>
      </button>
      <div
        className="overflow-hidden transition-all duration-300 ease-out"
        style={{ maxHeight: expanded ? '240px' : '0px', opacity: expanded ? 1 : 0 }}
      >
        <div
          ref={contentRef}
          className="px-3 py-2 max-h-[240px] overflow-y-auto mt-1 rounded-lg"
          style={embedded
            ? { background: 'transparent', borderLeft: '2px solid var(--border-neutral-l1)', borderRadius: 0, marginLeft: '12px' }
            : { background: 'var(--bg-overlay-l1)', border: '1px solid var(--border-neutral-l1)' }
          }
        >
          {/* 有标题时标题加粗置顶 */}
          {!streaming && title && (
            <p className="font-bold text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{title}</p>
          )}
          {/* 思考正文按 Markdown 渲染（支持 ## 标题分层与 **加粗** 分段） */}
          <div className="text-xs leading-relaxed prose prose-sm max-w-none" style={{ color: 'var(--text-tertiary)' }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Tool Call Banner (工具调用，与思考过程分离展示) ──
// embedded=true 时嵌入 TimelineRenderer 统一白色容器：去掉自身灰底框与外边距，融入时间轴。
export function ToolCallBanner({ toolCalls, streaming, embedded = false }: { toolCalls: ToolCallEntry[]; streaming: boolean; embedded?: boolean }) {
  const [expanded, setExpanded] = useState(true)
  const pendingCount = toolCalls.filter(t => !t.done).length
  const prevStreamingRef = useRef(streaming)

  useEffect(() => {
    // 与 ThinkingBanner 对称：流式（有 pending 工具）时展开实时看调用过程，
    // 完成后自动折叠（深度模式 run_deep_analysis 使工具停留末尾 streaming 滞留 true，
    // 但 done=true 无 pending 时 streaming 已转 false，此处随 streaming 变 false 折叠）
    setExpanded(streaming)
    prevStreamingRef.current = streaming
  }, [streaming])

  const isJustFinished = !streaming && prevStreamingRef.current

  return (
    <div className={embedded ? '' : 'mb-3'}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 rounded-lg transition-colors text-left"
        style={{ background: embedded ? 'transparent' : 'var(--bg-overlay-l1)' }}
        onMouseEnter={(e) => {e.currentTarget.style.background = 'var(--bg-overlay-l2)'}}
        onMouseLeave={(e) => {e.currentTarget.style.background = embedded ? 'transparent' : 'var(--bg-overlay-l1)'}}
      >
        {streaming && pendingCount > 0 ? (
          <span className="relative flex h-2 w-2 flex-shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: 'var(--bg-brand)' }}></span>
            <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: 'var(--bg-brand)' }}></span>
          </span>
        ) : (
          <i className="fas fa-tools text-xs flex-shrink-0" style={{ color: 'var(--text-brand)' }}></i>
        )}
        <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          {streaming && pendingCount > 0 ? '调用工具中' : isJustFinished ? '已调用工具' : '工具调用'}
          <span className="ml-1.5" style={{ color: 'var(--text-tertiary)' }}>· {toolCalls.length} 次</span>
        </span>
        <i className={`fas fa-chevron-${expanded ? 'down' : 'right'} text-[10px] ml-auto transition-transform`} style={{ color: 'var(--text-tertiary)' }}></i>
      </button>
      <div
        className="overflow-hidden transition-all duration-300 ease-out"
        style={{ maxHeight: expanded ? '240px' : '0px', opacity: expanded ? 1 : 0 }}
      >
        <div
          className="px-3 py-2 max-h-[240px] overflow-y-auto mt-1 rounded-lg flex flex-col gap-2"
          style={embedded
            ? { background: 'transparent', borderLeft: '2px solid var(--border-neutral-l1)', borderRadius: 0, marginLeft: '12px' }
            : { background: 'var(--bg-overlay-l1)', border: '1px solid var(--border-neutral-l1)' }
          }
        >
          {toolCalls.map((tc, i) => (
            <div key={i} className="text-xs leading-relaxed break-words">
              <div className="flex items-start gap-1.5" style={{ color: 'var(--text-secondary)' }}>
                <span className="flex-shrink-0">{tc.icon}</span>
                <span className="font-medium">[{tc.label}]</span>
                {tc.argText && <span style={{ color: 'var(--text-tertiary)' }}>{tc.argText}</span>}
              </div>
              {tc.resultText && (
                <div className="ml-5 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>↳ {tc.resultText}</div>
              )}
              {!tc.done && (
                <div className="ml-5 mt-0.5 flex items-center gap-1.5" style={{ color: 'var(--text-tertiary)' }}>
                  <div className="w-2.5 h-2.5 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: 'var(--bg-brand)' }}></div>
                  <span>执行中...</span>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Utility functions ──
function getDomain(url: string) {
  try {
    return new URL(url).hostname.replace('www.', '')
  } catch {
    return url
  }
}

function getFavicon(url: string) {
  try {
    const domain = new URL(url).hostname
    return `https://www.google.com/s2/favicons?domain=${domain}&sz=32`
  } catch {
    return ''
  }
}

// ── Pipeline Card ──
// adopt-assistant-ui-chat：经 AnalysisThread 的 data-pipeline 部件挂载（导出供部件复用）。
// enhance-pipeline-progress：管线完成后时间线折叠为单行摘要条（阶段数 + 总用时），点击可再展开。
export function PipelineCard({ msg }: { msg: UIMessage }) {
  const [showLog, setShowLog] = useState(false)
  // 图/列表视图切换（add-pipeline-graph-view）：偏好持久化 fa_pipeline_view，窄屏强制列表
  const [view, setView] = useState<'graph' | 'list'>(() => {
    const stored = localStorage.getItem('fa_pipeline_view')
    return stored === 'list' ? 'list' : 'graph'
  })
  const [isNarrow, setIsNarrow] = useState(() => window.innerWidth < 768)
  const [expandLayerId, setExpandLayerId] = useState<string | null>(null)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    const onChange = () => setIsNarrow(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  const effectiveView: 'graph' | 'list' = isNarrow ? 'list' : view
  const switchView = (v: 'graph' | 'list') => {
    setView(v)
    try {
      localStorage.setItem('fa_pipeline_view', v)
    } catch {
      /* localStorage 不可用时仅会话内生效 */
    }
  }
  const handleViewDetails = (layerId: string) => {
    switchView('list')
    setExpandLayerId(layerId)
  }
  const completed = msg.completedNodes || []
  const current = msg.currentNode || ''
  const progress = msg.progress || 0
  const outputs = msg.nodeOutputs || {}
  // 完成折叠（delta spec）：progress=1 为完成态，摘要条默认收起、点击展开全部节点
  const isCompleted = progress === 1
  const [summaryExpanded, setSummaryExpanded] = useState(false)

  // 计时器生命周期（fix-pipeline-timer，systematic-debugging 2026-09-12）：
  // 以真实终态为准——terminated（done/interrupted 事件落的消息标记）或 progress===1
  // （恢复的已完成会话，即上方 isCompleted）。旧判据「completedNodes 含
  // fund_manager/generate_file」是节点代理，三处失真：中断/取消时节点永不完成
  // （计时器永不停）；恢复会话 completedNodes 为空（永不停）；FM 退回时 backend
  // 对 ≤FM 整体标记完成（提前停）。
  const pipelineDone = isCompleted || msg.terminated === true
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    if (pipelineDone) return
    const timer = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [pipelineDone])

  const elapsedMs = msg.startedAt ? Math.max(0, nowMs - msg.startedAt) : 0
  // 完成摘要条总用时：重建路径 durationMs（报告耗时）优先，live 路径用 completedAt-startedAt
  const totalMs = msg.durationMs ?? (msg.completedAt && msg.startedAt ? msg.completedAt - msg.startedAt : elapsedMs)
  const estimatedTotalMs = estimateTotalMs(loadDurations())
  const remainingMs = estimateRemainingMs(elapsedMs, progress, estimatedTotalMs)
  // 完成态显示 totalMs（恢复会话 startedAt 缺失，elapsedMs 恒 0，须回退 durationMs）
  const etaText = pipelineDone
    ? `总耗时 ${formatDurationMs(totalMs)}`
    : `已用时 ${formatDurationMs(elapsedMs)} · 预计剩余 ~${formatDurationMs(remainingMs)}`
  // 阶段数：completedNodes 优先；重建会话该字段为空，从 layerTree 统计已完成子节点回退
  const completedCount =
    completed.length > 0
      ? completed.length
      : (msg.layerTree ?? []).reduce((acc, layer) => acc + layer.children.filter(c => c.status === 'completed').length, 0)

  // 分层时间轴状态树（无事件数据的历史会话回退为空树，PipelineTimeline 空渲染）
  const layerTree = msg.layerTree ?? buildLayerTree()

  // 当前运行节点的实时思考单行预览（从 nodeTimelines 提取末尾 thinking 内容尾 80 字符）
  const thinkingPreviewFor = (nodeId: string): string | undefined => {
    const items = msg.nodeTimelines?.[nodeId]
    if (!items || items.length === 0) return undefined
    const last = items[items.length - 1]
    if (last.type !== 'thinking' || !last.content) return undefined
    return last.content.slice(-80)
  }

  return (
    <div className="flex justify-start animate-slide-in">
      <div className="max-w-[95%] md:max-w-[90%] w-full">
        <div className="flex items-start gap-3">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-1" style={{ background: 'var(--bg-brand)' }}>
            <i className="fas fa-robot text-white text-xs"></i>
          </div>
          <div className="msg-system rounded-xl rounded-tl-sm overflow-hidden flex-1">
            {/* 完成摘要条（enhance-pipeline-progress）：折叠为单行（阶段数 + 总用时），点击展开/收起 */}
            {isCompleted && (
              <button
                type="button"
                data-testid="pipeline-summary"
                aria-expanded={summaryExpanded}
                onClick={() => setSummaryExpanded(v => !v)}
                className="w-full flex items-center gap-2 px-5 py-3 text-left"
              >
                <i className={`fas ${summaryExpanded ? 'fa-chevron-down' : 'fa-chevron-right'} text-[10px]`} style={{ color: 'var(--text-tertiary)' }}></i>
                <i className="fas fa-check-circle text-xs" style={{ color: 'var(--status-success-default)' }}></i>
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  分析完成 · {completedCount} 个阶段 · 总耗时 {formatDurationMs(totalMs)}
                </span>
              </button>
            )}
            {(!isCompleted || summaryExpanded) && (
            <>
            {/* Progress Pipeline */}
            <div className="px-5 pt-4 pb-2">
              {/* 图/列表切换（add-pipeline-graph-view）：窄屏隐藏控件仅列表 */}
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>分析进度</span>
                  <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>{msg.content || '准备中...'}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span data-testid="pipeline-eta" className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>{etaText}</span>
                  {!isNarrow && (
                    <div data-testid="pipeline-view-toggle" className="flex rounded-md overflow-hidden" style={{ border: '1px solid var(--border-neutral-l1)' }}>
                      <button
                        type="button"
                        data-testid="pipeline-view-graph"
                        onClick={() => switchView('graph')}
                        className="px-2 py-0.5 text-[11px]"
                        style={effectiveView === 'graph'
                          ? { background: 'var(--bg-brand)', color: '#fff' }
                          : { background: 'transparent', color: 'var(--text-tertiary)' }}
                      >
                        图
                      </button>
                      <button
                        type="button"
                        data-testid="pipeline-view-list"
                        onClick={() => switchView('list')}
                        className="px-2 py-0.5 text-[11px]"
                        style={effectiveView === 'list'
                          ? { background: 'var(--bg-brand)', color: '#fff' }
                          : { background: 'transparent', color: 'var(--text-tertiary)' }}
                      >
                        列表
                      </button>
                    </div>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-1 mb-2">
                <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-overlay-l1)' }}>
                  <div
                    className="h-full rounded-full transition-all duration-1000"
                    style={{ width: `${Math.max(5, progress * 100)}%`, background: 'var(--bg-brand)' }}
                  />
                </div>
              </div>
              {/* 视图渲染（add-pipeline-graph-view）：graph DAG / 层级时间轴，同一状态树 */}
              {effectiveView === 'graph' ? (
                <div data-testid="pipeline-graph">
                  <PipelineGraph
                    tree={layerTree}
                    startCounts={msg.nodeStartCounts ?? {}}
                    onViewDetails={handleViewDetails}
                  />
                </div>
              ) : (
                <PipelineTimeline tree={layerTree} nowMs={nowMs} thinkingPreviewFor={thinkingPreviewFor} expandLayerId={expandLayerId} />
              )}
            </div>

            {/* 各 agent 阶段的思考/工具调用时序：按 node 分组，阶段间用角色名标题分隔（非折叠框）。
                阶段内按时间序列渲染该 agent 的 timeline items；当前活动 node 的横幅展开流式。 */}
            {msg.nodeTimelines && Object.keys(msg.nodeTimelines).length > 0 && (
              <div className="px-5 py-3" style={{ borderTop: '1px solid var(--border-neutral-l1)' }}>
                {Object.entries(msg.nodeTimelines).map(([node, items]) => (
                  <div key={node} className="mb-2">
                    {node && (
                      <div className="text-xs font-semibold mb-1" style={{ color: 'var(--text-secondary)' }}>
                        {nodeDisplayName(node)}
                      </div>
                    )}
                    <TimelineRenderer
                      timeline={items}
                      streaming={current === node}
                      components={timelineBannerComponents}
                    />
                  </div>
                ))}
              </div>
            )}

            {/* Expandable log */}
            <div className="px-5 py-3" style={{ borderTop: '1px solid var(--border-neutral-l1)' }}>
              <button
                onClick={() => setShowLog(!showLog)}
                className="flex items-center gap-2 text-xs transition-colors w-full"
                style={{ color: 'var(--text-tertiary)' }}
                onMouseEnter={(e) => {e.currentTarget.style.color = 'var(--text-secondary)'}}
                onMouseLeave={(e) => {e.currentTarget.style.color = 'var(--text-tertiary)'}}
              >
                <i className={`fas fa-chevron-down transition-transform duration-300 ${showLog ? '' : 'rotate-[-90deg]'}`}></i>
                <span>查看实时输出日志</span>
                {completed.length > 0 && (
                  <span className="text-[10px] ml-1" style={{ color: 'var(--text-tertiary)' }}>· {completed.length} 条</span>
                )}
              </button>
              <div
                className="overflow-hidden transition-all duration-300 ease-out"
                style={{ maxHeight: showLog ? '300px' : '0px', opacity: showLog ? 1 : 0 }}
              >
                <div className="mt-2 rounded-lg p-3 font-mono text-[11px] leading-relaxed overflow-y-auto max-h-[300px]" style={{ background: 'var(--bg-base-secondary)' }}>
                  {completed.map(node => (
                    <div key={node} className="flex items-start gap-2 py-0.5">
                      <i className="fas fa-check-circle text-[10px] flex-shrink-0 mt-0.5" style={{ color: 'var(--status-success-default)' }}></i>
                      <span className="text-[10px] flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>{new Date().toLocaleTimeString()}</span>
                      <span className="flex-shrink-0" style={{ color: 'var(--text-brand)' }}>{node.toUpperCase()}</span>
                      <span className="truncate" style={{ color: 'var(--text-tertiary)' }}>{outputs[node]?.summary || 'completed'}</span>
                    </div>
                  ))}
                  {current && (
                    <div className="flex items-start gap-2 py-0.5">
                      <span className="relative flex h-2 w-2 flex-shrink-0 mt-0.5">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: 'var(--status-warning-default)' }}></span>
                        <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: 'var(--status-warning-default)' }}></span>
                      </span>
                      <span className="text-[10px] flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>{new Date().toLocaleTimeString()}</span>
                      <span className="flex-shrink-0" style={{ color: 'var(--status-warning-default)' }}>{current.toUpperCase()}</span>
                      <span style={{ color: 'var(--text-tertiary)' }}>running...</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
            </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Citation display（add-citation-display）──

// 引用校验状态视觉：verified 绿 / failed 红 / unchecked 灰
const VERDICT_STYLE: Record<string, { color: string; label: string; icon: string }> = {
  verified: { color: 'var(--status-success-default)', label: '已验证', icon: 'fa-check-circle' },
  failed: { color: 'var(--status-error-default)', label: '校验未通过', icon: 'fa-times-circle' },
  unchecked: { color: 'var(--text-tertiary)', label: '未校验', icon: 'fa-minus-circle' },
}

// 行内引用上标：hover 懒渲染预览卡（claim/来源/校验状态）
function CitationSup({ citation }: { citation: Citation }) {
  const [hovered, setHovered] = useState(false)
  const style = VERDICT_STYLE[citation.verdict] ?? VERDICT_STYLE.unchecked
  return (
    <span
      className="relative inline-block"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <sup
        data-testid={`citation-sup-${citation.id}`}
        className="cursor-pointer font-mono text-[10px] mx-0.5 px-1 rounded"
        style={{ color: style.color, background: 'var(--bg-overlay-l1)' }}
      >
        {citation.id.replace('cite-', '')}
      </sup>
      {/* 预览卡：hover 时才挂载（懒渲染，长报告上百标记不预挂） */}
      {hovered && (
        <span
          data-testid={`citation-preview-${citation.id}`}
          className="absolute bottom-full left-1/2 -translate-x-1/2 z-[80] w-64 mb-1.5 px-3 py-2 rounded-lg text-left shadow-lg block"
          style={{ background: 'var(--bg-base-default)', border: '1px solid var(--border-neutral-l1)' }}
        >
          <span className="flex items-center gap-1.5 text-[10px] font-medium" style={{ color: style.color }}>
            <i className={`fas ${style.icon}`}></i>
            {style.label}
            <span className="font-mono" style={{ color: 'var(--text-tertiary)' }}>[{citation.id.replace('cite-', '')}]</span>
          </span>
          <span className="block text-[11px] leading-relaxed mt-1" style={{ color: 'var(--text-secondary)' }}>
            {citation.claim}
          </span>
          <span className="block text-[10px] mt-1 truncate font-mono" style={{ color: 'var(--text-tertiary)' }}>
            <i className="fas fa-database mr-1"></i>
            {citation.source}
          </span>
          <span className="block text-[10px] mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
            {citation.detail}
          </span>
        </span>
      )}
    </span>
  )
}

// 递归替换 React children 中的 [[cite-<id>]] 文本标记为上标组件。
// 旧报告无引用数据时 citations 为空 → 原样返回（不渲染任何标记）。
function withCitationMarks(children: ReactNode, citations: Citation[] | undefined): ReactNode {
  if (!citations || citations.length === 0) return children
  const byId = new Map(citations.map((c) => [c.id, c] as const))
  const replaceText = (text: string, keyPrefix: string): ReactNode => {
    const parts = text.split(/(\[\[cite-[A-Za-z0-9_-]+\]\])/g)
    if (parts.length === 1) return text
    return parts.map((part, i) => {
      const m = part.match(/^\[\[cite-([A-Za-z0-9_-]+)\]\]$/)
      if (!m) return part
      const citation = byId.get(`cite-${m[1]}`) ?? byId.get(part.slice(2, -2))
      return citation ? <CitationSup key={`${keyPrefix}-${i}`} citation={citation} /> : null
    })
  }
  const walk = (node: ReactNode, keyPrefix: string): ReactNode => {
    if (typeof node === 'string') return replaceText(node, keyPrefix)
    if (typeof node === 'number') return node
    if (Array.isArray(node)) return node.map((n, i) => walk(n, `${keyPrefix}-${i}`))
    if (isValidElement(node)) {
      const el = node as ReactElement<{ children?: ReactNode }>
      return cloneElement(el, undefined, walk(el.props.children, `${keyPrefix}-c`))
    }
    return node
  }
  return walk(children, 'r')
}

// 报告结论要点（add-report-side-panel 摘要卡）：取二级标题前 4 条，无标题回退首段截断。
// 剥离 [[cite-N]] 标记（摘要不渲染上标）
export function reportKeyPoints(markdown: string | undefined): string[] {
  const md = (markdown ?? '').replace(/\[\[cite-[A-Za-z0-9_-]+\]\]/g, '')
  if (!md.trim()) return []
  const headings = md
    .split('\n')
    .map((l) => l.match(/^##\s+(.+?)\s*$/)?.[1])
    .filter((h): h is string => !!h)
  if (headings.length > 0) return headings.slice(0, 4)
  const firstLine = md.split('\n').find((l) => l.trim()) ?? ''
  return [firstLine.replace(/[#*`>\s]+/g, '').slice(0, 60)]
}

// ── Report Card ──
// adopt-assistant-ui-chat：经 AnalysisThread 的 data-report 部件挂载（导出供部件复用）。
// add-report-side-panel：完成态消息流收敛为摘要卡（要点 + 打开报告 + 折叠完整区）；
// 面板内（variant="panel"）渲染完整报告。
export function ReportCard({ msg, variant = 'inline', onOpenPanel }: {
  msg: UIMessage
  /** inline=消息流（完成态收敛摘要卡）；panel=右侧面板（完整渲染） */
  variant?: 'inline' | 'panel'
  /** 「打开报告」回调（App 据视口决定开面板或移动端回退）；缺省时按钮不渲染 */
  onOpenPanel?: (msg: UIMessage) => void
}) {
  // 摘要卡（add-report-side-panel）：完成态 inline 收敛为摘要卡；完整区折叠保留渲染
  const summaryMode = variant === 'inline' && !msg.streaming
  const [inlineExpanded, setInlineExpanded] = useState(false)
  const keyPoints = reportKeyPoints(msg.reportMarkdown)
  return (
    <div className="flex justify-start animate-slide-in">
      <div className="max-w-[95%] md:max-w-[90%] w-full">
        <div className="flex items-start gap-3">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-1" style={{ background: 'var(--bg-brand)' }}>
            <i className="fas fa-robot text-white text-xs"></i>
          </div>
          <div className="msg-system rounded-xl rounded-tl-sm overflow-hidden flex-1">
            {/* Streaming indicator */}
            {msg.streaming && (
              <div className="px-5 py-2 flex items-center gap-2" style={{ borderBottom: '1px solid var(--border-neutral-l1)' }}>
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: 'var(--bg-brand)' }}></span>
                  <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: 'var(--bg-brand)' }}></span>
                </span>
                <span className="text-xs" style={{ color: 'var(--text-brand)' }}>正在生成报告</span>
                <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>· 流式输出中</span>
              </div>
            )}

            {/* Report Header */}
            {!msg.streaming && (
              <div className="px-5 pt-4 pb-3" style={{ borderBottom: summaryMode ? 'none' : '1px solid var(--border-neutral-l1)' }}>
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="text-lg font-bold" style={{ color: 'var(--text-default)' }}>{formatReportTitle(msg)}</h3>
                    <span className="px-2 py-0.5 rounded-md text-[10px] font-semibold" style={{ background: 'var(--bg-brand-popup)', color: 'var(--text-brand)' }}>深度分析</span>
                  </div>
                  <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>深度分析报告 · 5 层 Agent 架构 · {(msg.durationMs ?? 0) > 0 ? `耗时 ${Math.round((msg.durationMs ?? 0) / 1000)}s` : '耗时未知'}</p>
                </div>
              </div>
            )}

            {/* 摘要卡（add-report-side-panel）：结论要点 + 打开报告 + 展开完整区入口 */}
            {summaryMode && (
              <div className="px-5 py-3" data-testid="report-summary-card">
                {keyPoints.length > 0 && (
                  <ul className="flex flex-col gap-1 mb-3">
                    {keyPoints.map((p, i) => (
                      <li key={i} className="text-xs leading-relaxed flex items-start gap-1.5" style={{ color: 'var(--text-secondary)' }}>
                        <i className="fas fa-angle-right text-[10px] mt-0.5 flex-shrink-0" style={{ color: 'var(--text-brand)' }}></i>
                        <span className="min-w-0">{p}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="flex items-center gap-2">
                  {onOpenPanel && (
                    <Button
                      data-testid="open-report-button"
                      size="sm"
                      onClick={() => { setInlineExpanded(true); onOpenPanel(msg) }}
                    >
                      <i className="fas fa-book-open text-[10px] mr-1"></i>
                      打开报告
                    </Button>
                  )}
                  <Button
                    variant="secondary"
                    size="sm"
                    data-testid="toggle-inline-report"
                    onClick={() => setInlineExpanded(v => !v)}
                  >
                    <i className={`fas ${inlineExpanded ? 'fa-chevron-up' : 'fa-chevron-down'} text-[10px] mr-1`}></i>
                    {inlineExpanded ? '收起完整报告' : '在此展开完整报告'}
                  </Button>
                </div>
              </div>
            )}

            {/* 完整报告区（摘要卡模式下折叠保留渲染；panel/流式/展开时可见） */}
            <div
              data-testid="report-full-section"
              className={summaryMode && !inlineExpanded ? 'overflow-hidden' : undefined}
              style={summaryMode && !inlineExpanded ? { maxHeight: 0, opacity: 0 } : undefined}
            >
            {/* Charts Section */}
            {msg.chartData && msg.chartData.annual && msg.chartData.annual.length > 0 && (
              <div className="px-5 py-3" style={{ borderBottom: '1px solid var(--border-neutral-l1)' }}>
                <div className="flex items-center gap-2 mb-3">
                  <i className="fas fa-chart-bar text-xs" style={{ color: 'var(--text-brand)' }}></i>
                  <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--text-secondary)' }}>财务图表</span>
                </div>
                <ChartsSection data={msg.chartData} />
              </div>
            )}

            {/* Report Markdown */}
            {msg.reportMarkdown && (
              <div className="px-5 py-3" style={{ borderBottom: '1px solid var(--border-neutral-l1)' }}>
                <div className="text-sm leading-relaxed max-h-[600px] overflow-y-auto markdown-body">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      img: () => null,
                      a: ({href, children}) => (
                        <a href={href} target="_blank" rel="noopener noreferrer" className="hover:underline inline-flex items-center gap-0.5" style={{ color: 'var(--bg-brand)' }}>
                          {children}
                          <i className="fas fa-external-link-alt text-[8px]"></i>
                        </a>
                      ),
                      h1: ({children}) => <h1 className="text-lg font-bold mt-4 mb-2" style={{ color: 'var(--text-default)' }}>{children}</h1>,
                      h2: ({children}) => <h2 className="text-base font-bold mt-4 mb-2 pb-1" style={{ color: 'var(--text-default)', borderBottom: '1px solid var(--border-neutral-l1)' }}>{children}</h2>,
                      h3: ({children}) => <h3 className="text-sm font-semibold mt-3 mb-1" style={{ color: 'var(--text-default)' }}>{children}</h3>,
                      p: ({children}) => <p className="text-sm mb-2 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{withCitationMarks(children, msg.citations)}</p>,
                      ul: ({children}) => <ul className="text-sm mb-2 ml-4 list-disc" style={{ color: 'var(--text-secondary)' }}>{withCitationMarks(children, msg.citations)}</ul>,
                      ol: ({children}) => <ol className="text-sm mb-2 ml-4 list-decimal" style={{ color: 'var(--text-secondary)' }}>{withCitationMarks(children, msg.citations)}</ol>,
                      li: ({children}) => <li className="mb-0.5">{withCitationMarks(children, msg.citations)}</li>,
                      strong: ({children}) => <strong className="font-semibold" style={{ color: 'var(--text-default)' }}>{withCitationMarks(children, msg.citations)}</strong>,
                      table: ({children}) => <table className="w-full text-xs border-collapse mb-2">{children}</table>,
                      th: ({children}) => <th className="border px-2 py-1 text-left" style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }}>{children}</th>,
                      td: ({children}) => <td className="border px-2 py-1" style={{ color: 'var(--text-secondary)', borderColor: 'var(--border-neutral-l1)' }}>{withCitationMarks(children, msg.citations)}</td>,
                      hr: () => <hr className="my-3" style={{ borderColor: 'var(--border-neutral-l1)' }} />,
                      blockquote: ({children}) => <blockquote className="border-l-2 pl-3 italic my-2" style={{ borderColor: 'var(--bg-brand)', color: 'var(--text-secondary)' }}>{withCitationMarks(children, msg.citations)}</blockquote>,
                    }}
                  >
                    {msg.reportMarkdown}
                  </ReactMarkdown>
                </div>
              </div>
            )}

            {/* Source Cards (Kimi-style citation cards) */}
            {!msg.streaming && msg.webSources && msg.webSources.length > 0 && (
              <div className="px-5 py-4 border-t" style={{ borderColor: 'var(--border-neutral-l1)' }}>
                <div className="flex items-center gap-2 mb-3">
                  <i className="fas fa-link text-xs" style={{ color: 'var(--text-tertiary)' }}></i>
                  <span className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>
                    参考资料（{msg.webSources.length} 个信源）
                  </span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {msg.webSources.map((src, i) => (
                    <a
                      key={i}
                      href={src.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block px-3 py-2 rounded-lg border transition-all hover:shadow-sm group"
                      style={{
                        background: 'var(--bg-overlay-l1)',
                        borderColor: 'var(--border-neutral-l1)',
                      }}
                    >
                      <div className="flex items-start gap-2">
                        <img
                          src={getFavicon(src.url)}
                          alt=""
                          className="w-4 h-4 rounded-sm flex-shrink-0 mt-0.5"
                          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-[10px] font-mono flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>[{i + 1}]</span>
                            <span className="text-xs truncate font-medium" style={{ color: 'var(--text-secondary)' }}>
                              {src.title}
                            </span>
                          </div>
                          <p className="text-[10px] mt-0.5 line-clamp-2" style={{ color: 'var(--text-tertiary)' }}>
                            {src.content}
                          </p>
                          <div className="flex items-center gap-1 mt-1">
                            <i className="fas fa-external-link-alt text-[8px]" style={{ color: 'var(--text-tertiary)' }}></i>
                            <span className="text-[10px] truncate" style={{ color: 'var(--text-tertiary)' }}>
                              {getDomain(src.url)}
                            </span>
                          </div>
                        </div>
                      </div>
                    </a>
                  ))}
                </div>
              </div>
            )}

            {/* 引用与校验列表（add-citation-display）：编号/声明/来源/校验状态，failed 红标可辨 */}
            {!msg.streaming && msg.citations && msg.citations.length > 0 && (
              <div className="px-5 py-4 border-t" data-testid="citation-list" style={{ borderColor: 'var(--border-neutral-l1)' }}>
                <div className="flex items-center gap-2 mb-3">
                  <i className="fas fa-clipboard-check text-xs" style={{ color: 'var(--text-tertiary)' }}></i>
                  <span className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>
                    引用与校验（{msg.citations.length} 条声明）
                  </span>
                </div>
                <div className="flex flex-col gap-1.5">
                  {msg.citations.map((c) => {
                    const style = VERDICT_STYLE[c.verdict] ?? VERDICT_STYLE.unchecked
                    return (
                      <div
                        key={c.id}
                        data-testid={`citation-item-${c.id}`}
                        className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs"
                        style={{ background: 'var(--bg-overlay-l1)' }}
                      >
                        <span className="font-mono flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>[{c.id.replace('cite-', '')}]</span>
                        <div className="flex-1 min-w-0">
                          <div className="leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{c.claim}</div>
                          <div className="text-[10px] mt-0.5 truncate font-mono" style={{ color: 'var(--text-tertiary)' }}>
                            <i className="fas fa-database mr-1"></i>{c.source}
                          </div>
                          {c.detail && (
                            <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-tertiary)' }}>{c.detail}</div>
                          )}
                        </div>
                        <span
                          data-testid={`citation-verdict-${c.id}`}
                          className="flex-shrink-0 flex items-center gap-1 text-[10px] font-medium"
                          style={{ color: style.color }}
                        >
                          <i className={`fas ${style.icon}`}></i>
                          {style.label}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Disclaimer */}
            {!msg.streaming && (
              <div className="px-5 py-3">
                <p className="text-[10px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
                  <i className="fas fa-info-circle mr-1"></i>
                  本报告由 AI 系统基于公开数据自动生成，仅供参考研究，不构成投资建议。投资有风险，入市需谨慎。
                </p>
              </div>
            )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Chat Input Bar ──
// adopt-assistant-ui-chat：输入区插槽化——App 内（assistant-ui Runtime Provider 内）
// 传入 composerInput/composerSend 插槽（ComposerPrimitive 输入/发送/停止），保持
// 「Enter 发送、Shift+Enter 换行、空禁用发送、运行中发送键变停止」；未传插槽时
// 使用受控 Textarea+按钮（保持本组件可独立渲染，dropdownOutsideClick 等测试不变）。
export function ChatInputBar({ onSend, leftInset, mode, setMode, capability, onNewAnalysis, apiKey, onOpenSettings, profileName, profiles, activeProfileId, onSwitchProfile, composerInput, composerSend }: {
  onSend: (text: string) => void
  leftInset: number
  mode: 'quick' | 'deep'
  setMode: (m: 'quick' | 'deep') => void
  capability: CapabilityMatrix | null
  onNewAnalysis: () => void
  apiKey: string
  onOpenSettings: () => void
  profileName: string
  profiles: LLMProfile[]
  activeProfileId: string
  onSwitchProfile: (id: string) => void
  composerInput?: ReactNode
  composerSend?: ReactNode
}) {
  const [text, setText] = useState('')
  const [modeDropdownOpen, setModeDropdownOpen] = useState(false)
  // LLM 切换下拉框展开状态（delta Decision 11）
  const [llmDropdownOpen, setLlmDropdownOpen] = useState(false)
  // 点击下拉框外部区域关闭（delta fix-dropdown-outside-close）：
  // rowRef 包裹触发按钮与弹层两者，避免「点触发按钮先关后开」竞态
  const rowRef = useRef<HTMLDivElement>(null)
  useClickOutside(rowRef, modeDropdownOpen || llmDropdownOpen, () => {
    setModeDropdownOpen(false)
    setLlmDropdownOpen(false)
  })

  const handleKeydown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!apiKey) { onOpenSettings(); return }
      onSend(text)
      setText('')
    }
  }

  const handleSendClick = () => {
    if (!apiKey) { onOpenSettings(); return }
    onSend(text)
    setText('')
  }

  const modes = [
    { id: 'quick' as const, label: '快速模式', icon: 'fa-bolt', color: 'text-[var(--status-warning-default)]', desc: '单次 LLM + Web Search，秒级响应' },
    { id: 'deep' as const, label: '深度研究', icon: 'fa-layer-group', color: 'text-[var(--text-brand)]', desc: '5 层 Agent 流水线，2-5 分钟完整报告' },
  ]
  const currentMode = modes.find(m => m.id === mode)!

  // 会话中切换模式：更新模式并直接开启新会话
  const handleModeSelect = (m: 'quick' | 'deep') => {
    setModeDropdownOpen(false)
    if (m === mode) return
    setMode(m)
    onNewAnalysis()
  }

  return (
    <div
      className="fixed bottom-0 right-0 z-40 px-4 pb-4 pt-2 transition-[left] duration-200 ease-out"
      style={{ left: leftInset, background: 'linear-gradient(to top, var(--bg-base-default) 80%, transparent)' }}
    >
      <div className="max-w-3xl mx-auto">
        <div className="glass-input rounded-2xl p-2">
          {/* Mode switcher：下拉框，会话中切换模式直接开启新会话 */}
          <div className="relative flex items-center gap-1 px-1 pb-1" ref={rowRef}>
            <button
              onClick={() => { setLlmDropdownOpen(false); setModeDropdownOpen(!modeDropdownOpen) }}
              className="flex items-center gap-1.5 text-[11px] font-medium rounded-lg px-2.5 py-1 transition-colors hover:bg-muted"
            >
              <i className={`fas ${currentMode.icon} ${currentMode.color} text-[10px]`}></i>
              <span className={currentMode.color}>{currentMode.label}</span>
              <i className={`fas fa-chevron-${modeDropdownOpen ? 'down' : 'up'} text-[8px] ml-0.5`} style={{ color: 'var(--text-tertiary)' }}></i>
            </button>
            {modeDropdownOpen && (
              <div className="absolute left-1 bottom-8 z-[70] w-72 glass-card rounded-lg overflow-hidden" style={{ border: '1px solid var(--border-neutral-l1)' }}>
                {modes.map(m => {
                  const gate = canEnterMode(m.id, capability)
                  return (
                  <button
                    key={m.id}
                    onClick={() => handleModeSelect(m.id)}
                    disabled={!gate.allowed}
                    title={gate.allowed ? undefined : gate.reason}
                    className={`w-full flex items-start gap-2 px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${mode === m.id ? 'bg-accent' : ''}`}
                    onMouseEnter={(e) => { if (mode !== m.id && gate.allowed) e.currentTarget.style.background = 'var(--bg-overlay-l1)' }}
                    onMouseLeave={(e) => { if (mode !== m.id) e.currentTarget.style.background = 'transparent' }}
                  >
                    <i className={`fas ${m.icon} ${m.color} text-xs mt-0.5`}></i>
                    <div className="flex-1 min-w-0">
                      <div className={`text-xs font-medium ${mode === m.id ? m.color : ''}`} style={mode !== m.id ? { color: 'var(--text-secondary)' } : {}}>
                        {m.label}
                        {mode === m.id && <i className="fas fa-check ml-1.5 text-[10px]"></i>}
                      </div>
                      <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-tertiary)' }}>{m.desc}</div>
                      {!gate.allowed && (
                        <div className="text-[10px] mt-0.5" style={{ color: 'var(--status-error-default)' }}>{gate.reason}</div>
                      )}
                    </div>
                    {mode !== m.id && gate.allowed && (
                      <span className="text-[10px] mt-0.5 flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>新会话</span>
                    )}
                  </button>
                  )
                })}
              </div>
            )}

            {/* LLM 切换（delta Decision 11）；无 profile 时点击引导配置 */}
            <div className="relative inline-block">
              <button
                onClick={() => {
                  if (profiles.length === 0) { onOpenSettings(); return }
                  setModeDropdownOpen(false)
                  setLlmDropdownOpen(!llmDropdownOpen)
                }}
                className="flex items-center gap-1 text-[11px] font-medium rounded-lg px-2.5 py-1 transition-colors hover:bg-muted"
              >
                <i className="fas fa-microchip text-[10px]" style={{ color: 'var(--text-tertiary)' }}></i>
                <span style={{ color: 'var(--text-secondary)' }}>{profileName}</span>
                <i className={`fas fa-chevron-${llmDropdownOpen ? 'down' : 'up'} text-[8px] ml-0.5`} style={{ color: 'var(--text-tertiary)' }}></i>
              </button>
              {llmDropdownOpen && profiles.length > 0 && (
                <div className="absolute left-32 bottom-8 z-[70] w-56 glass-card rounded-lg overflow-hidden" style={{ border: '1px solid var(--border-neutral-l1)' }}>
                  {profiles.map(p => (
                    <button
                      key={p.id}
                      onClick={() => { onSwitchProfile(p.id); setLlmDropdownOpen(false) }}
                      className={`w-full flex items-center gap-2 px-3 py-2 text-left text-xs transition-colors ${p.id === activeProfileId ? 'bg-accent' : ''}`}
                    >
                      {p.id === activeProfileId && <i className="fas fa-check text-[10px]" style={{ color: 'var(--text-brand)' }}></i>}
                      <span style={{ color: 'var(--text-secondary)' }}>{p.name}</span>
                    </button>
                  ))}
                  <div style={{ borderTop: '1px solid var(--border-neutral-l1)', margin: '4px 0' }} />
                  <button
                    onClick={() => { setLlmDropdownOpen(false); onOpenSettings() }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs transition-colors"
                  >
                    <i className="fas fa-sliders-h text-[10px]" style={{ color: 'var(--text-tertiary)' }}></i>
                    <span style={{ color: 'var(--text-secondary)' }}>LLM 配置…</span>
                  </button>
                </div>
              )}
            </div>
          </div>
          <div className="flex items-end gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="w-8 h-8 rounded-lg mb-1 text-muted-foreground"
            >
              <i className="fas fa-plus text-xs"></i>
            </Button>
            {composerInput ?? (
              <Textarea
                rows={1}
                placeholder={mode === 'deep' ? '输入股票名称或代码，如 茅台、300750' : '输入问题，如：茅台、宁德时代怎么样'}
                className="flex-1 border-0 bg-transparent px-2 py-3 shadow-none resize-none text-sm leading-relaxed focus-visible:ring-0 min-h-[40px] max-h-[100px]"
                style={{ color: 'var(--text-default)' }}
                value={text}
                onChange={e => setText(e.target.value)}
                onKeyDown={handleKeydown}
              />
            )}
            {composerSend ?? (
              <Button
                data-testid="send-button"
                variant="default"
                size="icon"
                onClick={handleSendClick}
                className="w-9 h-9 rounded-xl mb-0.5 mr-0.5"
              >
                <i className="fas fa-arrow-up text-xs" style={{ color: 'var(--text-onbrand)' }}></i>
              </Button>
            )}
          </div>
        </div>
        <div className="text-center mt-1">
          <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>AI 生成仅供参考，不构成投资建议</span>
        </div>
      </div>
    </div>
  )
}
