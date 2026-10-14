// SSE event types from the backend

export interface AnalysisStartEvent {
  type: 'analysis_start'
  analysis_id: string
  stock_code: string
  stock_name: string
  timestamp: string
}

export interface NodeStartEvent {
  type: 'node_start'
  node_id: string
  layer: string
  desc: string
  icon: string
  timestamp: string
  // 后端真实入口时间戳（fix-node-timer-real-lifecycle）
  server_start_ts?: number
}

export interface NodeCompleteEvent {
  type: 'node_complete'
  node_id: string
  layer: string
  desc: string
  completed: string[]
  progress: number
  output: Record<string, any>
  timestamp: string
}

// 节点真实耗时（node_end 到达时下发，覆盖 updates 近似值）
export interface NodeTimingEvent {
  type: 'node_timing'
  node_id: string
  server_start_ts?: number
  server_end_ts?: number
  server_duration_ms?: number
  timestamp: string
}

// 结构化引用（add-citation-display：后端 citation_report 转换下发，五字段契约）
export interface Citation {
  id: string            // 稳定锚点（cite-N），正文标记 [[cite-N]] 与之一一对应
  claim: string         // 原子声明（interpretation）
  source: string        // 数据来源（state 字段路径）
  verdict: 'verified' | 'failed' | 'unchecked'
  detail: string        // 重算说明（重算值/偏差/覆盖缺口）
}

export interface ReportReadyEvent {
  type: 'report_ready'
  analysis_id: string
  session_id: string
  report_markdown: string
  chart_data: ChartData
  file_paths: Record<string, string>
  stock_code: string
  stock_name: string
  duration_ms: number
  web_sources?: Array<{ query: string; title: string; url: string; content: string }>
  // 结构化引用（add-citation-display）；旧后端/旧数据缺省该字段
  citations?: Citation[]
  timestamp: string
}

// ── New streaming events ──

export interface ParsingEvent {
  type: 'parsing'
  query: string
  timestamp: string
}

export interface ResolvedEvent {
  type: 'resolved'
  stock_code: string
  stock_name: string
  timestamp: string
}

export interface ReportChunkEvent {
  type: 'report_chunk'
  chunk_index: number
  total_chunks: number
  text: string
  timestamp: string
}

export interface ChatTokenEvent {
  type: 'chat_token'
  token: string
  timestamp: string
}

export interface ThinkingTokenEvent {
  type: 'thinking_token'
  token: string
  node?: string
  timestamp: string
}

export interface ThinkingToAnswerEvent {
  type: 'thinking_to_answer'
  answer: string
  timestamp: string
}

export interface ThinkingReplaceEvent {
  type: 'thinking_replace'
  token: string
  timestamp: string
}

export interface ToolCallEvent {
  type: 'tool_call'
  name: string
  args: Record<string, any>
  iteration: number
  timestamp: string
}

export interface ToolResultEvent {
  type: 'tool_result'
  name: string
  result: any
  timestamp: string
}

export interface ChatDoneEvent {
  type: 'chat_done'
  timestamp: string
}

export interface SearchStartEvent {
  type: 'search_start'
  query: string
  timestamp: string
}

export interface SearchResultEvent {
  type: 'search_result'
  query: string
  results: Array<{ title: string; url: string; content: string }>
  count: number
  timestamp: string
}

export interface StockResolvedEvent {
  type: 'stock_resolved'
  stock_code: string
  stock_name: string
  timestamp: string
}

export interface SearchErrorEvent {
  type: 'search_error'
  message: string
  timestamp: string
}

// ── Chart data types ──

export interface AnnualEntry {
  year: string
  revenue: number | null
  net_profit: number | null
  gross_margin: number | null
  net_margin: number | null
  roe: number | null
  roa: number | null
  ocf: number | null
  total_assets: number | null
  equity: number | null
  contract_liab: number | null
  cip: number | null
  debt_ratio: number | null
}

export interface ChartData {
  stock_code: string
  stock_name: string
  annual: AnnualEntry[]
  growth: {
    years: string[]
    revenue_growth: (number | null)[]
    profit_growth: (number | null)[]
  }
  price: {
    daily: Array<{
      date: string
      close: number
      open?: number | null
      high?: number | null
      low?: number | null
      volume?: number | null
    }>
    earnings_dates: string[]
    ma?: { ma5: (number | null)[]; ma20: (number | null)[]; ma60: (number | null)[] }
    // trigger_high/trigger_low（add-watch-trigger-tracking）：watch 决策的触发价位，仅 watch 决策存在
    decision_levels?: { entry_price?: number; stop_loss?: number; target_price?: number; trigger_high?: number; trigger_low?: number }
  }
  kpi: {
    current_price?: number
    market_cap?: number
    pe?: number
    pb?: number
    '52w_high'?: number
    '52w_low'?: number
  }
  market_share: any
}

export interface ErrorEvent {
  type: 'error'
  node_id: string
  message: string
  timestamp: string
}

export interface DoneEvent {
  type: 'done'
  analysis_id: string
  duration_ms: number
  timestamp: string
}

export interface SessionCreatedEvent {
  type: 'session_created'
  session_id: string
  display_name: string
  timestamp: string
}

export interface AwaitingInputEvent {
  type: 'awaiting_input'
  session_id: string
  pending_intent: string
  timestamp: string
}

// 中断终态事件：生成任务被取消或异常中断时下发
export interface InterruptedEvent {
  type: 'interrupted'
  timestamp?: string
}

// 合成用户消息事件：journal 不落用户消息，刷新全量回放（after_seq=0）时
// 后端按 chat_history 的 ts 在对应位置注入，恢复 user 气泡的原始交错顺序。
// 仅出现在回放流中（实时路径不注入），无 seq。
export interface UserMessageEvent {
  type: 'user_message'
  content: string
}

// ── Session types ──
// seq 字段：事件日志架构核心，后端 session_events journal 中每个事件按 session 内
// 单调递增 seq 落库。前端用 seq 做断点续传（after_seq）和去重（跳过 seq <= lastSeq）。
// 所有 SSE 事件都可能带 seq，但部分非 journal 事件（如临时心跳）可能没有，故为可选。
export type SSEEvent = (
  | AnalysisStartEvent
  | NodeStartEvent
  | NodeCompleteEvent
  | NodeTimingEvent
  | ReportReadyEvent
  | ParsingEvent
  | ResolvedEvent
  | ReportChunkEvent
  | ChatTokenEvent
  | ThinkingTokenEvent
  | ThinkingToAnswerEvent
  | ThinkingReplaceEvent
  | ToolCallEvent
  | ToolResultEvent
  | ChatDoneEvent
  | SearchStartEvent
  | SearchResultEvent
  | StockResolvedEvent
  | SearchErrorEvent
  | ErrorEvent
  | DoneEvent
  | SessionCreatedEvent
  | AwaitingInputEvent
  | InterruptedEvent
  | UserMessageEvent
) & { seq?: number }

// ── Session types ──

export interface SessionMeta {
  session_id: string
  stock_code: string
  stock_name: string
  display_name: string
  status: string
  created_at: string
  duration_ms: number
  report_len?: number
  session_type?: string
  failure_reason?: string | null
}

export interface ChatHistoryEntry {
  role: string
  content: string
  ts: string
  thinking?: string
  tool_calls?: Array<{ name: string; args?: Record<string, any>; result_text?: string; done?: boolean }>
  // 结构化 agent 时序（persist-full-session-timeline）：存在时优先于 thinking/tool_calls 拍平恢复
  agentTimeline?: TimelineItem[]
}

// 管线进度快照（后端 sessions.pipeline_snapshot，JSON 字符串；layerTree 为内嵌的序列化 JSON 字符串）
export interface PipelineSnapshot {
  layerTree: string
  currentNodeId: string
  progress: number
  updatedAt: number
  // 管线启动时间戳（毫秒）。刷新重建 running 管线时用作「已用时」计时起点；缺省回退本地时间
  pipeline_start_ts?: number
}

export interface SessionDetail extends SessionMeta {
  report_markdown: string
  chart_data: ChartData
  analyst_reports: Record<string, any>
  agent_process: Record<string, any>
  analyst_summaries: Record<string, any>
  chat_history: ChatHistoryEntry[]
  // 管线进度快照（running/completed 会话切回时恢复分层时间轴；无快照为 null）
  pipeline_snapshot: string | null
  // 管线各节点的结构化时序（persist-full-session-timeline；后端已反序列化为 dict，可能为 null/缺失）
  pipeline_timelines?: Record<string, TimelineItem[]> | null
  // 管线触发锚点：管线启动时 chat_history 中最后一条 user 消息索引 + 1，
  // 供历史重建定位报告消息插入位置（null = 旧会话，回退第一个 user 后）
  pipeline_anchor?: number | null
  // 事件 journal 最大 seq，供前端断点续传使用
  last_seq?: number
  // 导出产物文件名映射（refresh-recover-export-files；缺省为旧会话/无导出）
  file_paths?: Record<string, string>
  // 结构化引用数组（add-citation-display）；旧会话缺省 null
  citations?: Citation[] | null
}

// Pipeline step definition
export interface PipelineStep {
  node: string
  layer: string
  desc: string
  icon: string
}

// UI message types
export type MessageType = 'user' | 'pipeline' | 'report' | 'chat' | 'system' | 'error'

// 单次工具调用记录（ToolCallBanner 渲染用，从 TimelineItem tool_call 转换而来）
export interface ToolCallEntry {
  name: string
  label: string
  icon: string
  argText: string
  resultText?: string
  done?: boolean
}

// Agent 时序条目：按 SSE 事件到达顺序追加，渲染时按 type 分发为独立横幅。
// - thinking：思考片段（被搜索/工具调用断开的多段思考各自独立，title 由 extractThinkingTitle 提取）
// - search：搜索（web_search / batch_web_search 由 search_* 事件驱动，不生成 tool_call 条目）
// - tool_call：其他工具调用（每次调用一个独立条目）
export type TimelineItem =
  | { type: 'thinking'; content: string; title?: string; done?: boolean }
  | { type: 'answer'; content: string }
  | {
      type: 'search'
      query: string
      results?: Array<{ title: string; url: string; content: string }>
      status: 'searching' | 'done' | 'error'
    }
  | { type: 'tool_call'; name: string; args: string; result?: string; done: boolean }

export interface UIMessage {
  id: string
  type: MessageType
  content: string
  // Pipeline-specific
  completedNodes?: string[]
  currentNode?: string
  nodeOutputs?: Record<string, any>
  progress?: number
  // 管线启动时间戳（ms epoch），用于 ETA 已用时长计算
  startedAt?: number
  // 管线完成时刻（enhance-pipeline-progress：live 路径 node_complete progress=1 落值，
  // 完成摘要条总用时数据源；重建路径用 durationMs）
  completedAt?: number
  // 分层时间轴状态树（redesign delta）：node_start/node_complete 驱动的 6 层→子节点状态
  layerTree?: import('./pipelineTree').LayerNode[]
  // 节点启动次数（add-pipeline-graph-view）：node_id → node_start 累计次数，
  // graph 视图迭代徽标数据源；与 layerTree 单调状态机独立
  nodeStartCounts?: Record<string, number>
  // 管线终态标记（fix-pipeline-timer）：收到 done/interrupted 终态事件时置 true。
  // 计时器据此停止——节点完成集合在「中断/恢复/退回」三场景都会失真（缺节点或超前），
  // 不能作为生命周期判据（systematic-debugging 2026-09-12）
  terminated?: boolean
  // Report-specific
  reportMarkdown?: string
  chartData?: ChartData
  filePaths?: Record<string, string>
  stockName?: string
  stockCode?: string
  durationMs?: number
  sessionId?: string
  streaming?: boolean
  webSources?: Array<{ query: string; title: string; url: string; content: string }>
  // 结构化引用（add-citation-display）：报告消息携带，驱动上标渲染与引用列表
  citations?: Citation[]
  // Chat-specific
  chatResponse?: string
  // Agent 时序：思考/搜索/工具调用按 SSE 事件到达顺序纵向排列（chat 消息与管线消息共用）
  agentTimeline?: TimelineItem[]
  // 管线模式：按 node 分组的时序（key 为 thinking_token 事件的 node 字段，如 bull_debater）
  nodeTimelines?: Record<string, TimelineItem[]>
}

// 导出文件元信息（add-download-center：GET /api/files 返回项）
export interface ExportFileInfo {
  file_name: string
  file_type: DownloadType
  size_bytes: number
  created_at: number
}

// 下载中心类型筛选（'all' 为全部）
export type DownloadType = 'docx' | 'pptx' | 'pdf' | 'md'

// 决策结算状态（expose-decision-outcomes：与后端 DECISION_STATUSES 一致）
export type DecisionStatus = 'open' | 'hit_stop' | 'hit_target' | 'expired'

// 决策记录（GET /api/decisions 返回项）
export interface DecisionRecord {
  decision_id: string
  session_id: string
  langfuse_trace_id: string | null
  timestamp: string
  ticker: string
  name: string | null
  action: string
  entry_price: number
  stop_loss: number | null
  target_price: number | null
  confidence: number
  position_size: number | null
  status: DecisionStatus
  settled_at: string | null
  settle_price: number | null
  hold_days: number | null
  decision_return: number | null
  benchmark_return: number | null
  decision_excess: number | null
  updated_at: string
}

// 聚合战绩（GET /api/decisions/stats 返回项）
export interface DecisionStats {
  total: number
  open: number
  settled: number
  by_status: Record<string, number>
  win_rate: number | null
  avg_return: number | null
  avg_excess: number | null
}

// 观点判定状态（add-track-record：与后端 PREDICTIONS_STATUSES 一致）
// avoidance = neutral 观点回避判定的生命周期终态（细粒度结果 avoidance_win/loss/neutral 在
// avoidance_status 列，不进胜率）——update-decision-settlement-contract
// duplicate_of_day = 同日重复观点关闭终态，无结算读数、不计统计分母——add-prediction-pool-integrity
export type PredictionStatus = 'open' | 'resolved_win' | 'resolved_loss' | 'resolved_neutral' | 'avoidance' | 'unresolvable' | 'duplicate_of_day'
export type PredictionDirection = 'long' | 'short' | 'neutral'

// 观点记录（GET /api/v1/track-record/predictions 返回项）
export interface PredictionRecord {
  prediction_id: string
  source_type: 'backtest' | 'live'
  symbol: string
  symbol_name: string | null
  direction: PredictionDirection
  entry_price: number | null
  target_price: number | null
  horizon_days: number
  confidence: number | null
  benchmark: string
  langfuse_trace_id: string | null
  status: PredictionStatus
  created_at: string
  resolved_at: string | null
  // 结算入场价（hfq 归属日收盘，判定时派生；判定前 NULL）——
  // update-track-record-settle-price-display：与 exit_price 同口径可比
  settle_entry_price: number | null
  exit_price: number | null
  raw_return: number | null
  excess_return: number | null
  resolution_rule: string | null
  // update-track-record-display-clarity:open 行最新盯市（浮动收益展示）；非 open 行为 null
  latest_mark?: { mark_date: string; cum_return: number | null; cum_excess: number | null } | null
  rationale_snapshot?: unknown
}

// 战绩总览（GET /api/v1/track-record/overview 返回项）
export interface TrackRecordPortfolio {
  available: boolean
  annual_return: number | null
  volatility: number | null
  sharpe: number | null
  max_drawdown: number | null
  risk_score: number | null
  risk_label: string | null
  as_of: string | null
  // β 市场敞口 / Jensen α 年化超额（add-portfolio-beta-alpha）
  beta: number | null
  jensen_alpha: number | null
}

// neutral 回避正确率读数（avoidance_stats() 形状：win/(win+loss) 口径，settled 为分母）
export interface TrackRecordAvoidance {
  avoidance_win: number
  avoidance_loss: number
  avoidance_neutral: number
  settled: number
  avoidance_rate: number | null
}

// 虚拟 vs 仿真双曲线块（add-paper-broker B3：overview.paper；无数据时后端返回 null）
export interface TrackRecordPaperPoint {
  date: string
  virtual_equity: number
  paper_equity: number
}

export interface TrackRecordPaper {
  series: TrackRecordPaperPoint[]
  // 最新共同日 (paper/virtual − 1) × 100，后端已保留 1 位小数
  loss_pct: number
  base_date: string
  note: string
}

export interface TrackRecordOverview {
  total: number
  open: number
  settled: number
  win_rate: number | null
  avg_excess: number | null
  status_counts: Record<string, number>
  source_type: string | null
  insufficient_sample: boolean
  as_of: string
  disclaimer: string
  portfolio: TrackRecordPortfolio
  version_seq: number | null
  versions: AgentVersion[]
  // Δ2（update-decision-settlement-contract）+ add-eval-ops-console Task 7 三披露。
  // 后端 overview 恒定返回；声明为可选以容忍旧会话/旧后端数据（前端如实占位，不折算 0）
  avoidance?: TrackRecordAvoidance
  caliber_horizon?: number
  legacy_settled?: number
  // update-track-record-display-clarity：旧口径进行中计数（与 legacy_settled 双计数分列；
  // 可选以容忍旧后端，0 或缺省时前端不渲染该分句）
  legacy_open?: number
  // add-paper-broker（B3）：虚拟 vs 仿真双曲线块；null = 无 paper 数据（隐藏系列）。
  // 可选以容忍旧后端（缺省视同 null，不伪造第二条曲线）
  paper?: TrackRecordPaper | null
}

// 净值曲线点（add-track-record-stage-b）
export interface EquityCurvePoint {
  date: string
  agent_nav: number
  benchmark_nav: number | null
}

// 净值曲线响应
export interface EquityCurveResponse {
  points: EquityCurvePoint[]
  as_of: string
  disclaimer: string
}

// 跑赢指数对比(add-index-performance-compare)
export interface IndexCompareIndex {
  code: string
  name: string
  return: number | null
  effective_start_date: string | null
  beat: boolean | null
}

export interface IndexCompareResponse {
  span: 'all' | '3m' | '6m' | '1y'
  window: { start: string | null; end: string | null }
  agent_return: number | null
  indices: IndexCompareIndex[]
  as_of: string
  disclaimer: string
}

// 校准分桶（add-track-record-stage-c）
export interface CalibrationBucket {
  bucket: string
  mid: number
  n: number
  hit_rate: number | null
}

// 校准响应
export interface CalibrationResponse {
  buckets: CalibrationBucket[]
  brier: number | null
  sample_size: number
  as_of: string
  disclaimer: string
}

// 切片桶（add-track-record-stage-c）
export interface SegmentBucket {
  name: string
  sample_size: number
  win_rate: number | null
  avg_excess: number | null
  insufficient: boolean
}

// 切片维度
export interface SegmentDimension {
  dimension: string
  total: number
  settled: number
  buckets: SegmentBucket[]
}

// 切片响应
export interface SegmentsResponse {
  dimensions: SegmentDimension[]
  as_of: string
  disclaimer: string
}

// 版本信息（P6 分段封存）
export interface AgentVersion {
  agent_id: string
  model_version: string
  strategy_version: string | null
  version_seq: number
  retired_at: string | null
  created_at: string
  note: string | null
}

// 观点详情（add-track-record-stage-c）
export interface PredictionDetail {
  prediction: PredictionRecord
  audit: Array<{
    log_id: number
    prediction_id: string
    action: string
    old_status: string | null
    new_status: string | null
    detail: string | null
    source: string | null
    created_at: string
  }>
  marks: Array<{
    mark_id: string
    prediction_id: string
    mark_date: string
    mark_price: number | null
    cum_return: number | null
    cum_excess: number | null
    benchmark_price: number | null
  }>
  as_of: string
  disclaimer: string
}

// 观点日志列表响应
export interface PredictionsResponse {
  predictions: PredictionRecord[]
  page: number
  page_size: number
  total: number
  as_of: string
  disclaimer: string
}

// 当前立场视图（GET /api/v1/track-record/current，add-current-stance-view）：
// 每股最新一条 open 观点，行结构与观点日志一致
export interface CurrentStanceResponse {
  current: PredictionRecord[]
  total: number
  as_of: string
  disclaimer: string
}

// ── 评估运维（delta add-eval-ops-console Task 6）──
// 字段名逐字对齐 `src/finance_agent/ops_api.py` 的 /api/v1/ops/* 契约（不猜测）。

export interface OpsSchedule {
  day_of_week: string
  hour: number
  minute: number
  timezone: string
}

// 运行历史行的生命周期：scheduled 定时 / manual 手动补跑 / config-change 配置变更审计
export type OpsRunKind = 'scheduled' | 'manual' | 'config-change'
// 终态：running 进行中 / ok 成功 / failed 失败 / skipped-disabled 空转（cohort 开关关闭）
export type OpsRunStatus = 'running' | 'ok' | 'failed' | 'skipped-disabled'

export interface OpsRun {
  run_id: number
  job_id: string
  kind: OpsRunKind | string
  source: string
  status: OpsRunStatus | string
  started_at: string
  finished_at: string | null
  summary: Record<string, any> | null
  error: string | null
}

export interface OpsJobStatus {
  job_id: string
  label: string
  schedule: OpsSchedule
  next_fire_time: string | null
  last_run: OpsRun | null
  history: OpsRun[]
}

export interface OpsCohortState {
  enabled: boolean
  hour: number
  minute: number
  budget_tokens: number
  today_spend: number
  today_success: number
  today_failure: number
}

// GET /api/v1/ops/jobs
// error：后端降级载荷（读运行历史/配置失败）时给出的人类可读原因；健康载荷下缺失。
// 界面必须消费它——否则「数据库读失败」与「从未跑过」在界面上不可分。
export interface OpsJobsResponse {
  scheduler_running: boolean
  jobs: OpsJobStatus[]
  cohort: OpsCohortState
  error?: string | null
}

// GET /api/v1/ops/reports（回测报告注册表）
export interface OpsReportEntry {
  name: string
  path: string
  status: string
  target: string | null
  positioning: string | null
  probe_direction_hit_rate: number | null
}

// GET /api/v1/ops/prereg（预登记版本；locked = 已产生读数，只读）
export interface OpsPreregVersion {
  path: string
  fields: Record<string, string>
  valid: boolean
  issues: string[]
  locked: boolean
}

// GET /api/v1/ops/caliber（当前口径旋钮现值；只读）
export interface OpsCaliber {
  knobs: Record<string, number>
  source: string
}

// 选股池（add-screening-pipeline S4/S5：GET /api/screening/pool 载荷）
export interface ScreeningFactorContrib {
  factor_id: number
  zscore?: number
  weight?: number
  contribution: number
}

export interface ScreeningPoolRow {
  symbol: string
  composite_score: number
  rank: number
  consecutive_days: number
  l3_verdict: string
  l3_reason: string
  factor_contribs: ScreeningFactorContrib[]
}

// 批量分析队列条目（S3 四态；前端只对 queued/running 渲染徽标）
export interface ScreeningQueueEntry {
  symbol: string
  session_id: string
  state: string
}

// 深度分析前的时效性校验移除项（reason ∈ SUSPENDED/LIMIT_LOCKED/NEW_EVENT）
export interface ScreeningRemoval {
  symbol: string
  reason: string
  detail: string
}

export interface ScreeningPoolResponse {
  batch_date: string | null
  batch_status: string | null
  stale: boolean
  pool: ScreeningPoolRow[]
  queue: ScreeningQueueEntry[]
}

// POST /api/screening/analyze 响应（池空为 409 显式错误）
export interface ScreeningAnalyzeResponse {
  started: ScreeningQueueEntry[]
  queued: ScreeningQueueEntry[]
  removed: ScreeningRemoval[]
}

// 语义记忆条款（add-memory-confirmation：GET /api/memory/clauses 载荷）
export type MemoryClauseStatus = 'pending' | 'confirmed' | 'rejected'

export interface MemoryClause {
  id: string
  text: string
  /** 条款状态三态；仅 confirmed 参与风控辩论注入（后端门禁） */
  status: MemoryClauseStatus
  /** 来源（monthly_batch / llm 等，后端原值） */
  source: string
  /** 提炼时间（落库时刻 ISO） */
  created_at: string
  /** 结算样本（JSON 文本或空，后端原值） */
  evidence: string | null
}

export interface MemoryClauseListResponse {
  status: MemoryClauseStatus
  clauses: MemoryClause[]
}

// 因子库（delta add-factor-library-page：GET /api/factors 载荷）。
// 状态为库内原值（candidate/active/degraded/retired）；来源为库内原值（builtin/
// llm/proposal）；name 为 builtin 注册词表作品名，非 builtin → null（界面回退显示因子 id）。
export interface FactorLastEvent {
  /** 事件类型（created / status_change） */
  event: string
  from_status: string | null
  to_status: string | null
  /** 闸门/状态变更结论（detail.reason 提升；无 → null） */
  reason: string | null
  created_at: string
}

export interface FactorRow {
  id: number
  name: string | null
  expression: string
  hypothesis: string
  category: string
  status: string
  /** 规范表达式（dsl.serialize 产出；库内行未存 → null） */
  ast_json: string | null
  source: string
  alignment_score: number | null
  pool_increment: number | null
  created_at: string
  updated_at: string
  /** 近 60 个交易日 IC 均值（recent_ic_stats 口径；无记录 → 0.0） */
  ic_mean_60d: number
  /** 近 60 个交易日 ICIR（mean/std，ddof=1；无记录 → 0.0，界面按 ic_points 区分） */
  icir_60d: number
  /** 窗口内 IC 记录数：0 → 界面显示「—」而非 0.000 */
  ic_points: number
  /** 衰减降级史（事件历史出现过 degraded；退役后标记仍在） */
  decayed: boolean
  last_event: FactorLastEvent | null
}

export interface FactorListResponse {
  factors: FactorRow[]
}

// GET /api/factors/{id}：摘要字段 + IC 序列（近 60 点升序）+ 事件历史（完整 detail）
export interface FactorIcPoint {
  trade_date: string
  rank_ic: number
}

export interface FactorEvent {
  id: number
  factor_id: number
  event: string
  from_status: string | null
  to_status: string | null
  reason: string | null
  detail: Record<string, unknown> | null
  created_at: string
}

export interface FactorDetail extends FactorRow {
  ic_series: FactorIcPoint[]
  events: FactorEvent[]
}
