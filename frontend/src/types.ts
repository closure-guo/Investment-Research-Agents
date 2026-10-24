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

// 策略族成绩单（delta add-strategy-family-scorecards：GET /api/scorecards）。
// 键名权威源 src/finance_agent/outcome/scorecards.py::build_family_payload；
// 槽位缺数据 = null + 随行 *_reason 原因码（MUST NOT 折算 0），有值时原因码字段缺省。
export type ScorecardReason =
  | 'insufficient_sample'
  | 'insufficient_history'
  | 'no_liquidity_data'
  | 'no_samples'
  | 'no_loader'

export interface ScorecardExpectancy {
  gross: number
  net: number
  n: number
  insufficient: boolean
}

export interface ScorecardDrawdown {
  value: number
  n_days: number
}

export interface ScorecardCapacity {
  suggested_capital: number
  min_adv20: number
  excluded: number
}

export interface ScorecardExtremeSample {
  ticker: string
  date: string
  ret: number
}

export interface ScorecardFamily {
  family_id: string
  name: string
  as_of: string | null
  expectancy: ScorecardExpectancy | null
  expectancy_reason?: ScorecardReason | null
  max_drawdown: ScorecardDrawdown | null
  max_drawdown_reason?: ScorecardReason | null
  capacity: ScorecardCapacity | null
  capacity_reason?: ScorecardReason | null
  extreme_samples: ScorecardExtremeSample[]
  /** 展示位（四槽之后）；执行线无胜率口径 → null */
  win_rate: number | null
  caliber_note: string
}

export interface ScorecardsResponse {
  families: ScorecardFamily[]
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

// 排程粒度两形态（add-monthly-batch-scheduling）：
// - 日/周粒度（既有）：day_of_week（如 'mon-fri'）；
// - 月粒度：day_of_month（如 1 → 「每月 1 日」）。
// 二者互斥（后端 SCHEDULES 二分支 `_cron_from_spec`），时刻与时区恒有。
export interface OpsSchedule {
  day_of_week?: string
  day_of_month?: number
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
  /** 「当天计划时刻已过、且当天无任何运行行（含 running）」；服务器时钟权威（add-ops-missed-today-badge）。旧载荷缺失 → 前端回退既有「未跑」态 */
  missed_today?: boolean
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
  // 选择模式（add-screening-empty-library-fallback）：fallback = 降级模式（无因子信号）
  selection_mode: 'factor' | 'fallback' | null
  // 降级披露文案（后端单源；fallback 时非空，明示「非因子信号」）
  notice: string | null
  // 因子 IC 就绪度（delta add-factor-ic-visibility）：逐 active 因子进度 + 恢复日
  factor_signal: ScreeningFactorSignal
  pool: ScreeningPoolRow[]
  queue: ScreeningQueueEntry[]
}

export interface ScreeningFactorReadiness {
  id: number
  /** 近 60 日窗口内 IC 记录数 */
  ic_samples: number
  eligible: boolean
  /** 预计达标日（按工作日推算；已达标/无记录 → null） */
  expected_eligible_date: string | null
}

export interface ScreeningFactorSignal {
  selection_mode: 'factor' | 'fallback' | null
  /** 降级原因（后端逐字：如「IC 样本不足（门槛 10）」/「无 active 因子」） */
  reason: string
  min_samples: number
  /** 各因子预计达标日的最早值（工作日推算；无则 null） */
  expected_factor_mode_date: string | null
  factors: ScreeningFactorReadiness[]
}

// POST /api/screening/analyze 响应（池空为 409 显式错误）
export interface ScreeningAnalyzeResponse {
  started: ScreeningQueueEntry[]
  queued: ScreeningQueueEntry[]
  removed: ScreeningRemoval[]
}

// 选股页市场温度计（delta add-crowding-regime-positioning T5；GET /api/regime）。
// 键名权威源 `src/finance_agent/outcome/regime/payload.py`；history 行为
// `regime_daily` 原始行（升序，含分位列）。available=false 时仅携带原因码。
export type RegimeState = 'normal' | 'elevated' | 'danger'
export type RegimeUnavailableReason = 'missing' | 'corrupt'

export interface RegimeIndicatorValue {
  value: number | null
  pctile: number | null
}

export interface RegimeIndicators {
  small_cap_share: RegimeIndicatorValue
  up_count: RegimeIndicatorValue
  down_count: RegimeIndicatorValue
  limit_up_count: RegimeIndicatorValue
  limit_down_count: RegimeIndicatorValue
  total_amount: RegimeIndicatorValue
}

export interface RegimeHistoryRow {
  trade_date: string
  small_cap_share: number | null
  up_count: number | null
  down_count: number | null
  flat_count: number | null
  limit_up_count: number | null
  limit_down_count: number | null
  total_amount: number | null
  small_cap_share_pctile: number | null
  total_amount_pctile: number | null
  computed_at: string
}

export interface RegimeAvailablePayload {
  available: true
  as_of: string
  state: RegimeState
  multiplier: number
  reasons: string[]
  indicators: RegimeIndicators
  history: RegimeHistoryRow[]
  age_days: number
  stale: boolean
}

export interface RegimeUnavailablePayload {
  available: false
  reason: RegimeUnavailableReason
}

export type RegimePayload = RegimeAvailablePayload | RegimeUnavailablePayload

// 事件观察清单（delta add-event-driven-lane T7；GET /api/events/watchlist）。
// 字段名权威源 `src/finance_agent/api.py::_event_watchlist_payload`（逐字对齐，不猜测）；
// 清单列 + 预告要素 + 分诊理由/证据同源于该载荷，缺项后端给 null（前端一律显示 —）。
export type EventVerdict = 'outperform' | 'in_line' | 'risk'

export interface EventEvidenceRef {
  field: string
  value: unknown
}

export interface EventWatchlistItem {
  id: number
  forecast_key: string | null
  /** 清单来源：earnings（业绩预告，v1）／cbb（条款事件，未来增量，同表同端点） */
  source: string | null
  /** 清单状态：pending（待分析）／analyzing（已发起深度分析并关联 session） */
  status: string | null
  session_id: string | null
  created_at: string | null
  code: string | null
  name: string | null
  report_period: string | null
  forecast_type: string | null
  /** 净利润变动幅度区间（% 口径，与预告源同单位；不折算） */
  change_low: number | null
  change_high: number | null
  announce_date: string | null
  available_at: string | null
  verdict: string | null
  reason: string | null
  /** 证据引用；JSON 损坏时后端给 null（前端显示 —，不当空数组） */
  evidence: EventEvidenceRef[] | null
  model: string | null
  prompt_fingerprint: string | null
  triaged_at: string | null
  // 条款事件（source=cbb）风险要素：后端载荷扩展时可选携带；缺失一律显示 —。
  // 键名已冻结（对齐 add-convertible-bond-coverage spec 的四要素：现价 / 转股溢价率 /
  // 剩余规模（亿元口径）/ 强赎倒计时）；缺位与显式 null 同款退化。
  price?: number | null
  premium_rate?: number | null
  remaining_size?: number | null
  call_countdown_days?: number | null
}

export interface EventWatchlistResponse {
  items: EventWatchlistItem[]
}

// POST /api/events/watchlist/{id}/analyze 响应（重复发起为幂等返回：already_started=true）
export interface EventAnalyzeResponse {
  id: number
  session_id: string | null
  status: string | null
  /** 既有队列快照中的状态（queued/running/done/failed）；不在快照 → null */
  queue_state: string | null
  already_started: boolean
}

// 选股批次历史（delta add-daily-ops-page：GET /api/screening/batches 载荷行）。
// 字段名逐字对齐 `src/finance_agent/screening/store.py::list_batches`（不猜测）。
export interface ScreeningBatch {
  trade_date: string
  /** 批次状态：ok / failed（库内原值） */
  status: string
  /** 失败原因（失败行原样，不改写不归类）；成功行 null */
  error: string | null
  started_at: string
  finished_at: string
  l1_passed: number
  l2_top: number
  l3_keep: number
  pool_size: number
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
  /** IC 窗口（近 N 个交易日；后端 FACTOR_IC_WINDOW=60，delta add-factor-ic-visibility） */
  ic_window: number
  /** 参与 L2 打分所需的最少 IC 条数（后端 MIN_IC_SAMPLES_FOR_SCORING=10） */
  min_samples_for_scoring: number
  /** 预计恢复因子打分的首个批次日期（按工作日推算、不含节假日；已达标/无记录 → null） */
  expected_factor_mode_date: string | null
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

export interface FactorStyleExposure {
  /** 与各 builtin 风格代理的 IC 序列相关；不可算的风格为 null（不臆造 0） */
  correlations: Record<string, number | null>
  /** |ρ| 最大的风格（无可用读数 → null） */
  dominant: string | null
  dominant_correlation: number | null
  /** |主导 ρ| ≥ threshold（披露标注，不构成准入裁决） */
  flagged: boolean
  /** 主导风格相关所用共同 IC 日数 */
  n_days: number
  threshold: number
  basket: string[]
}

export interface FactorDetail extends FactorRow {
  ic_series: FactorIcPoint[]
  events: FactorEvent[]
  /** 风格暴露披露（add-factor-style-exposure）；面板不可得 → null（前端如实展示「数据不足」） */
  style_exposure: FactorStyleExposure | null
}

// ── 因子研发监控契约（delta add-factor-research-monitor：GET /api/factor-research/status） ──
// 快照由 distro 采集器（collect_factor_status.py）周期写入宿主 data/factor_research/status.json，
// 端点透传 + 派生 available/age_seconds/stale；缺失/损坏 → available=false + reason。

export interface FactorResearchSupervisor {
  running: boolean
  pid: number | null
  started_at: string | null
}

export interface FactorResearchSession {
  log_file: string
  started_at: string | null
  ended_at: string | null
  duration_s: number | null
}

export interface FactorResearchCurrentSession extends FactorResearchSession {
  alive: boolean
  loop_index: number | null
  step_index: number | null
  step_name: string | null
  steps_total: number | null
  step_elapsed_s: number | null
  step_eta_s: number | null
  factors: string[]
  log_tail: string[]
}

export interface FactorResearchTotals {
  sessions: number
  candidates: number
  workspace_tasks: number
}

export interface FactorResearchAgent {
  /** 最近一次期望态调和动作（控制文件缺失/损坏 → no_intent；一致 → noop） */
  last_action: 'no_intent' | 'noop' | 'started' | 'stopped' | 'start-failed' | 'stop-failed' | null
  last_action_at: string | null
  /** 动作时刻（Unix 秒；旧版代理可能缺省） */
  last_action_epoch?: number | null
  /** start-failed / stop-failed 时的可判读摘要 */
  last_error: string | null
  /** 研究侧实际读取的节奏配置（旧版代理不上报） */
  config?: FactorResearchConfigBlock | null
  /** 最近一次已处理的重启请求（无 → null；旧版代理不上报） */
  restart?: FactorResearchRestartRecord | null
}

export interface FactorResearchRestartRecord {
  request_id: string | null
  requested_at_epoch: number | null
  result: 'restarted' | 'restart-failed'
  handled_at: string | null
  handled_at_epoch: number | null
}

export interface FactorResearchConfigValues {
  /** 状态采集/控制轮询间隔（秒）；下一周期生效 */
  snapshot_interval_s: number
  /** 正常会话间隔（秒）；下次启动看护生效 */
  session_gap_s: number
  /** 短命会话退避（秒）；下次启动看护生效 */
  short_session_backoff_s: number
  /** 短命会话阈值（秒）；下次启动看护生效 */
  short_session_threshold_s: number
}

export interface FactorResearchConfigBlock {
  values: FactorResearchConfigValues
  /** file=配置文件；defaults=缺失；corrupt=不可解析 */
  source: 'file' | 'defaults' | 'corrupt'
  invalid_fields: string[]
  updated_at: string | null
}

export interface FactorResearchLauncher {
  available: boolean
  reason?: 'missing' | 'corrupt'
  checked_at?: string | null
  checked_at_epoch?: number | null
  age_seconds?: number
  /** age_seconds ≤ 300 */
  fresh?: boolean
  agent_pid?: number | null
  agent_ok?: boolean
  snapshot_age_s?: number | null
  detail?: string | null
}

/** distro 探活块（delta add-factor-research-distro-liveness-probe） */
export interface FactorResearchDistroProbe {
  available: boolean
  /** available=false 时如实二分：missing（未探活）/ corrupt */
  reason?: 'missing' | 'corrupt'
  probed_at?: string | null
  probed_at_epoch?: number | null
  distro?: string | null
  /** 探活是否成功（distro 能否执行） */
  ok?: boolean | null
  exit_code?: number | null
  /** 失败时可判读摘要 */
  detail?: string | null
  /** 连续探活失败次数 */
  consecutive_failures?: number | null
  terminated_at?: string | null
  terminated_at_epoch?: number | null
  terminate_count?: number | null
  age_seconds?: number
  fresh?: boolean
}

export interface FactorResearchControl {
  /** 期望态（控制文件缺失/不可解析 → null，如实不猜） */
  desired_running: boolean | null
  /** 控制请求发起时刻（ISO8601；缺失 → null） */
  requested_at: string | null
  /** 期望与新鲜快照实态不一致（切换中）；陈旧/不可用快照恒 false */
  pending: boolean
  /** 控制受理能力（后端单源判定） */
  honor: 'live' | 'launcher' | 'unavailable'
  /** 离线受理的预计拉起上界（秒；live=0，unavailable=null） */
  honor_eta_seconds: number | null
  /** 重启请求发起时刻（ISO8601；无 → null） */
  restart_requested_at: string | null
  /** 重启请求发起时刻（Unix 秒；无 → null） */
  restart_requested_at_epoch: number | null
  /** 重启请求标识（无 → null） */
  restart_request_id: string | null
  /** 有请求 + 快照新鲜 + 研究侧尚未回写同一 request_id 的终态结果 */
  restart_pending: boolean
}

export type FactorResearchActualState = 'running' | 'stopped' | 'unverified'

export interface FactorResearchProgress {
  /** 当前会话日志 mtime 距采集时刻的秒数（旧版采集器未上报 → null） */
  log_age_seconds: number | null
  /** 采集正常但会话无进展（阈值单源在后端；未上报 → false 且不可判定） */
  stalled: boolean
}

export interface FactorResearchStatusResponse {
  available: boolean
  /** available=false 时如实二分：missing（未接入）/ corrupt（快照损坏） */
  reason?: 'missing' | 'corrupt'
  age_seconds?: number
  stale?: boolean
  collected_at?: string | null
  collected_at_epoch?: number | null
  /** 验证过的实态（持久化意图不参与判定） */
  actual_state?: FactorResearchActualState
  /** 研发节奏配置块（delta add-factor-research-settings） */
  config?: FactorResearchConfigBlock | null
  /** 进度新鲜度派生块（delta add-factor-research-stall-detection） */
  progress?: FactorResearchProgress | null
  supervisor?: FactorResearchSupervisor | null
  current_session?: FactorResearchCurrentSession | null
  sessions?: FactorResearchSession[]
  totals?: FactorResearchTotals | null
  /** 代理调和动作块（独立采集器路径可能为 null） */
  agent?: FactorResearchAgent | null
  /** 启动器心跳块（delta add-factor-research-selfheal） */
  launcher?: FactorResearchLauncher | null
  /** 控制块（delta add-factor-research-control；快照不可用/陈旧时同样返回） */
  control?: FactorResearchControl | null
  /** distro 探活块（delta add-factor-research-distro-liveness-probe） */
  distro?: FactorResearchDistroProbe | null
}

// ── 执行台账只读契约（delta add-broker-console：GET /api/broker/*） ──

export interface BrokerOverview {
  /** 可用资金（PaperBroker 惰性开户口径：首次读取即落初始 100 万） */
  cash: number
  /** 持仓市值（最新快照 close_raw 口径；快照不可得 → null，MUST NOT 填 0 冒充） */
  positions_value: number | null
  /** 总资产 = 资金 + 持仓市值（市值不可得 → null） */
  equity: number | null
  /** 浮动盈亏 = 持仓市值 − 持仓成本（市值不可得 → null） */
  unrealized_pnl: number | null
  /** 持仓成本合计（账本口径，不依赖快照） */
  position_cost: number
  positions_count: number
  /** mark 所用快照交易日（不可得 → null） */
  snapshot_date: string | null
  /** 缺收盘价的标的（市值不可得时的可判读明细） */
  missing_symbols: string[]
  /** 市值不可得原因的标注文案（可得 → null） */
  market_value_note: string | null
}

export interface BrokerPositionRow {
  symbol: string
  qty: number
  avg_cost: number
  updated_at: string | null
  /** 现价（快照 close_raw；不可得 → null） */
  close: number | null
  market_value: number | null
  unrealized_pnl: number | null
}

export interface BrokerPositionsResponse {
  snapshot_date: string | null
  positions: BrokerPositionRow[]
}

/** 订单状态枚举（spec 逐字四态） */
export type BrokerOrderStatus = 'pending' | 'filled' | 'rejected' | 'expired'

export interface BrokerOrderRow {
  order_id: string
  symbol: string
  side: string
  qty: number
  status: BrokerOrderStatus | string
  /** 未成交/拒单原因码（成交 → null） */
  reason: string | null
  fill_price: number | null
  fee: number
  /** 证据链（spec：订单记录含 decision_id 与 signal_id） */
  decision_id: string
  signal_id: string
  created_at: string
  attempts: number
}

export interface BrokerOrdersResponse {
  orders: BrokerOrderRow[]
}

/** 收割链路只读载荷（add-factor-harvest-ui）。 */
export interface HarvestJobBlock {
  status: string | null
  started_at: string | null
  finished_at: string | null
  schedule: { day_of_week: string; hour: number; minute: number; timezone: string } | null
  next_fire_time: string | null
}

export interface HarvestProgressBlock {
  batches_done: number
  batch_limit: number
  processed: number
  pending: number
  admitted_in_run: number
  rejected_in_run: number
  started_at: string | null
  eta_s: number | null
  /** 批内逐候选心跳（harvest 每处理一条写一次）；缺失或过旧为 null */
  in_batch?: {
    processed_in_batch: number
    total_in_batch: number
    phase: string
    updated_at: string | null
    age_s: number | null
  } | null
}

export interface HarvestFunnelBlock {
  candidates: number
  processed: number
  untranslatable: number
  transcription_error: number
  reached_gates: number
  admitted: number
  rejected_by_gate: Record<string, number>
}

export interface HarvestRecentAdmission {
  expression: string | null
  factor_id: number
  created_at: string | null
  icir_60d: number
}

export interface HarvestRecentRejection {
  expression: string | null
  gate: number | null
  reason: string | null
  at: string | null
}

export interface HarvestChainResponse {
  available: boolean
  job: HarvestJobBlock | null
  progress: HarvestProgressBlock | null
  funnel: HarvestFunnelBlock | null
  recent_admitted: HarvestRecentAdmission[]
  recent_rejected: HarvestRecentRejection[]
}
