// EvalOpsPane 评估运维分区（delta add-eval-ops-console Task 6）
//
// 六个页签：调度 / cohort / 回测与探针 / 健康检查 / 报告注册表 / 预登记与口径。
// 契约来源：`src/finance_agent/ops_api.py`（/api/v1/ops/*，字段名逐字对齐，见 types.ts）。
// 状态机沿用 DataMonitorPane：loading / ready / error（首载失败可重试）。
//
// 红线：
// - 烧钱动作（cohort 开启 / 正式批 / 通路批 / 探针单跑）一律先过 ConfirmSpendDialog；
//   取消路径零请求（确认前不 fetch，取消只清确认态）。
// - 门禁拒绝（409/422）一律落到可见的 `eval-ops-error` 横幅，不静默、不转圈。
// - 「最近一次运行」跳过 kind=config-change 的审计行（后端 last_run 可能是审计行），
//   审计行按 kind 在卡片上单独披露。
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Button } from '../../../components/ui/button'
import { Textarea } from '../../../components/ui/textarea'
import type {
  OpsCaliber, OpsCohortState, OpsJobStatus, OpsJobsResponse, OpsPreregVersion,
  OpsReportEntry, OpsRun,
} from '../../../types'
import { ConfirmSpendDialog, type SpendConfirmRequest } from './ConfirmSpendDialog'

/* ── 常量（阈值与后端常量字面同步；口径变更须先改 docs/evals/metrics.md §1） ── */

// 轮询间隔/上限：手动补跑后轮询 GET /jobs 到终态；长任务轮询 GET /runs/{id}
const POLL_INTERVAL_MS = 1000
const POLL_MAX_TRIES = 60

// 预登记七个门禁字段（后端 OUTCOME_REQUIRED_FIELDS，顺序一致）
const PREREG_FIELDS = ['主指标', 'MDE', '决策阈值', '样本量依据', '停止规则', '成本分型', '泄漏控制'] as const
const COST_FIELD = '成本分型'

// 口径旋钮（后端 KNOB_KEYS）；展示顺序固定，未知键追加在后
const KNOB_ORDER = ['PRIMARY_WINDOW_DAYS', 'NEUTRAL_BAND', 'LEAKAGE_PROBE_THRESHOLD', 'MIN_SETTLED_FOR_WINRATE']
const KNOB_LABELS: Record<string, string> = {
  PRIMARY_WINDOW_DAYS: '判定窗口（交易日）',
  NEUTRAL_BAND: '中性带',
  LEAKAGE_PROBE_THRESHOLD: '泄漏探针阈值',
  MIN_SETTLED_FOR_WINRATE: '最小已结算样本',
}

// outcome 健康检查门禁阈值（与 evals/outcome/health.py 的 MIN_SETTLEMENT_SUCCESS_RATE /
// MAX_UNRESOLVABLE_RATE 字面同步；后端读数的 checks 才是判定真源，此处只作阈值对照展示）
const MIN_SETTLEMENT_SUCCESS_RATE = 0.90
const MAX_UNRESOLVABLE_RATE = 0.10

// cohort 单日成本量级（spec「开启需确认并审计」点名的估算数字；明细见预登记「成本分型」）
const COHORT_DAILY_ESTIMATE = '≈1.7M tokens/日'

const RUN_STATUS_LABEL: Record<string, string> = {
  running: '运行中', ok: '成功', failed: '失败', 'skipped-disabled': '空转（开关关闭）',
}
const KIND_LABEL: Record<string, string> = { scheduled: '定时', manual: '手动补跑', 'config-change': '配置变更' }
const POSITIONING_LABEL: Record<string, string> = {
  pathway: '通路验证', upper_bound: '泄漏污染下的上界证据', skill: '技能定位',
}
const REPORT_STATUS_LABEL: Record<string, string> = { active: '生效中', 'superseded-by': '已被取代' }
const PROBE_STATE_LABEL: Record<string, string> = {
  measurable: '可测', downgraded: '降级（上界证据）', unmeasurable: '不可测', missing: '缺读数',
}

type TabId = 'schedule' | 'cohort' | 'backtest' | 'health' | 'reports' | 'prereg'
type TaskKind = 'backtest' | 'probe' | 'health'

const TABS: Array<{ id: TabId; label: string }> = [
  { id: 'schedule', label: '调度' },
  { id: 'cohort', label: 'cohort' },
  { id: 'backtest', label: '回测与探针' },
  { id: 'health', label: '健康检查' },
  { id: 'reports', label: '报告注册表' },
  { id: 'prereg', label: '预登记与口径' },
]

/* ── 纯函数工具 ── */

const sleep = (ms: number) => new Promise<void>((resolve) => { setTimeout(resolve, ms) })

const pad2 = (n: number) => String(n).padStart(2, '0')

/** token 数千分位（预算/花费展示；两处口径一致） */
function fmtTokens(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toLocaleString('en-US')
}

/** 比例 → 百分数；null → 「无读数」占位（绝不折算 0） */
function fmtPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

/** 摘要单值渲染（长对象截断，避免把 summary 全量铺进卡片） */
function shortValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value).slice(0, 60)
  return String(value)
}

function fmtSummary(summary: Record<string, any> | null | undefined): string {
  if (summary === null || summary === undefined || Object.keys(summary).length === 0) return '（无摘要）'
  const entries = Object.entries(summary).slice(0, 6).map(([k, v]) => `${k}=${shortValue(v)}`)
  const text = entries.join(' · ')
  return text.length > 220 ? `${text.slice(0, 220)}…` : text
}

/** 该任务「最近一次运行」= 最新一条非 config-change 行（后端 last_run 可能是审计行） */
function latestRealRun(job: OpsJobStatus): OpsRun | null {
  const rows = [job.last_run, ...job.history].filter((row): row is OpsRun => row !== null)
  const unique = [...new Map(rows.map((row) => [row.run_id, row])).values()]
  unique.sort((a, b) => (a.started_at === b.started_at ? b.run_id - a.run_id : a.started_at < b.started_at ? 1 : -1))
  return unique.find((row) => row.kind !== 'config-change') ?? null
}

/** 最新一行若是配置变更审计行，单独披露（按 kind 标注，不冒充「最近一次运行」） */
function auditRunOf(job: OpsJobStatus): OpsRun | null {
  return job.last_run !== null && job.last_run.kind === 'config-change' ? job.last_run : null
}

/**
 * 排程文案（add-monthly-batch-scheduling 粒度通用化）：
 * - 月粒度（含 day_of_month）→「每月 N 日 HH:MM（时区）」；
 * - 日/周粒度（既有）→「day_of_week HH:MM（时区）」逐字不变。
 */
function scheduleText(job: OpsJobStatus): string {
  const { day_of_week, day_of_month, hour, minute, timezone } = job.schedule
  const time = `${pad2(hour)}:${pad2(minute)}（${timezone}）`
  if (day_of_month !== undefined) return `每月 ${day_of_month} 日 ${time}`
  return `${day_of_week} ${time}`
}

function lastRunText(run: OpsRun | null): string {
  if (run === null) return '尚无运行记录'
  const label = RUN_STATUS_LABEL[run.status] ?? run.status
  const when = run.finished_at ?? run.started_at
  if (run.status === 'failed' && run.error) return `${label} · ${when} · ${run.error}`
  return `${label} · ${when} · ${fmtSummary(run.summary)}`
}

async function detailText(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    return body?.detail === undefined ? `HTTP ${res.status}` : String(body.detail)
  } catch {
    return `HTTP ${res.status}`
  }
}

/** 手动补跑拒绝文案（409 的两种业务语义各自明说） */
function runRefusalText(status: number, detail: string): string {
  if (status === 409 && detail === 'already_running') return '该任务已在运行（手动与定时互斥，未启动第二个实例）'
  if (status === 409 && detail === 'cohort_disabled') return 'cohort 开关未开启，手动跑批被拒绝（零 LLM 调用）'
  if (status === 404) return '未知任务'
  return detail
}

/* ── 展示用小组件 ── */

function Card({ title, children, testId, extra }: {
  title: ReactNode; children: ReactNode; testId?: string; extra?: ReactNode
}) {
  return (
    <div data-testid={testId} className="rounded-xl p-4 space-y-2" style={{ background: 'var(--bg-overlay-l1)' }}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-medium" style={{ color: 'var(--text-default)' }}>{title}</h3>
        {extra}
      </div>
      {children}
    </div>
  )
}

function KeyValue({ label, value, testId }: { label: string; value: ReactNode; testId?: string }) {
  return (
    <div data-testid={testId} className="text-xs flex gap-2">
      <span className="shrink-0 w-24" style={{ color: 'var(--text-tertiary)' }}>{label}</span>
      <span className="break-all" style={{ color: 'var(--text-secondary)' }}>{value}</span>
    </div>
  )
}

function JobCard(props: {
  job: OpsJobStatus
  running: boolean
  onRun: (jobId: string) => void
}) {
  const { job, running } = props
  const real = latestRealRun(job)
  const audit = auditRunOf(job)
  return (
    <Card title={<span>{job.label}<span className="ml-2 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>{job.job_id}</span></span>}
      testId={`eval-ops-job-${job.job_id}`}>
      <KeyValue label="排程" value={scheduleText(job)} />
      <KeyValue label="下次触发" testId={`eval-ops-next-${job.job_id}`}
        value={job.next_fire_time ?? '—（调度器未运行或未注册）'} />
      <KeyValue label="最近一次运行" testId={`eval-ops-last-run-${job.job_id}`} value={lastRunText(real)} />
      {audit && (
        <KeyValue label="最近配置变更" testId={`eval-ops-job-audit-${job.job_id}`}
          value={`${KIND_LABEL[audit.kind] ?? audit.kind} · ${audit.finished_at ?? audit.started_at} · ${fmtSummary(audit.summary)}`} />
      )}
      <div className="flex items-center gap-2 pt-1">
        <Button size="sm" variant="outline" data-testid={`eval-ops-run-${job.job_id}`}
          disabled={running} onClick={() => props.onRun(job.job_id)}>
          {running ? '运行中…' : '立即运行'}
        </Button>
        {running && (
          <span data-testid={`eval-ops-running-${job.job_id}`} className="text-xs"
            style={{ color: 'var(--status-warning-default)' }}>
            运行中（等待运行历史刷新）
          </span>
        )}
      </div>
    </Card>
  )
}

/* ── 主组件 ── */

export function EvalOpsPane() {
  const [jobs, setJobs] = useState<OpsJobsResponse | null>(null)
  const [cohort, setCohort] = useState<OpsCohortState | null>(null)
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error'>('loading')
  const [prereg, setPrereg] = useState<OpsPreregVersion[] | null>(null)
  const [caliber, setCaliber] = useState<OpsCaliber | null>(null)
  const [reports, setReports] = useState<OpsReportEntry[] | null>(null)
  const [reportsPhase, setReportsPhase] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')

  const [tab, setTab] = useState<TabId>('schedule')
  // 最近一次操作失败原因（门禁拒绝/校验不过/网络错误）——统一可见横幅，绝不静默
  const [error, setError] = useState<string | null>(null)

  const [runningJob, setRunningJob] = useState<string | null>(null)
  const [taskRunning, setTaskRunning] = useState<TaskKind | null>(null)
  const [taskPending, setTaskPending] = useState<TaskKind | null>(null)
  const [taskResult, setTaskResult] = useState<{ kind: TaskKind; run: OpsRun } | null>(null)

  const [spend, setSpend] = useState<SpendConfirmRequest | null>(null)
  const pendingAction = useRef<(() => void) | null>(null)

  const [cohortHour, setCohortHour] = useState('')
  const [cohortMinute, setCohortMinute] = useState('')
  const [cohortAudit, setCohortAudit] = useState<{ runId: number; rescheduled: boolean } | null>(null)

  const [preregDraft, setPreregDraft] = useState<Record<string, string>>({})
  const [preregNewVersion, setPreregNewVersion] = useState(false)
  const [preregSavedPath, setPreregSavedPath] = useState<string | null>(null)
  const [knobEdits, setKnobEdits] = useState<Record<string, string>>({})
  const [caliberDraftDir, setCaliberDraftDir] = useState<string | null>(null)
  const [caliberHint, setCaliberHint] = useState<string | null>(null)

  const [backtestCodes, setBacktestCodes] = useState('')
  const [backtestPerRegime, setBacktestPerRegime] = useState('10')
  const [backtestRepeats, setBacktestRepeats] = useState('3')
  const [backtestRefusal, setBacktestRefusal] = useState<string | null>(null)
  const [probeCodes, setProbeCodes] = useState('')
  const [probeDate, setProbeDate] = useState('')
  const [probeTickers, setProbeTickers] = useState('10')

  /* ── 数据加载 ── */

  const loadJobs = useCallback(async (): Promise<OpsJobsResponse | null> => {
    try {
      const res = await fetch('/api/v1/ops/jobs')
      if (!res.ok) return null
      const data = (await res.json()) as OpsJobsResponse
      setJobs(data)
      return data
    } catch {
      return null
    }
  }, [])

  const loadPrereg = useCallback(async (): Promise<OpsPreregVersion[] | null> => {
    try {
      const res = await fetch('/api/v1/ops/prereg')
      if (!res.ok) return null
      const rows = (await res.json()) as OpsPreregVersion[]
      setPrereg(rows)
      return rows
    } catch {
      return null  // 旁路数据：失败只在预登记页签降级披露，不阻断任务状态
    }
  }, [])

  const loadCaliber = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/ops/caliber')
      if (res.ok) setCaliber((await res.json()) as OpsCaliber)
    } catch { /* 旁路 */ }
  }, [])

  const loadCore = useCallback(async (mode: 'full' | 'refresh' = 'full') => {
    if (mode === 'full') setPhase('loading')
    const data = await loadJobs()
    if (data === null) {
      if (mode === 'full') setPhase('error')
      return
    }
    setCohort(data.cohort)
    // 预登记与口径现值一并就绪后才置 ready：烧钱确认弹窗要引用预登记「成本分型」，
    // 半就绪会让估算缺来源（二者拉取失败仍按 null 降级，不阻断状态展示）。
    await Promise.all([loadPrereg(), loadCaliber()])
    setPhase('ready')
  }, [loadJobs, loadPrereg, loadCaliber])

  useEffect(() => { void loadCore('full') }, [loadCore])

  const loadReports = useCallback(async () => {
    setReportsPhase('loading')
    try {
      const res = await fetch('/api/v1/ops/reports')
      if (!res.ok) throw new Error(String(res.status))
      setReports((await res.json()) as OpsReportEntry[])
      setReportsPhase('ready')
    } catch {
      setReportsPhase('error')
    }
  }, [])

  // 报告注册表按需加载（首次切到该页签）
  useEffect(() => {
    if (tab === 'reports' && reportsPhase === 'idle') void loadReports()
  }, [tab, reportsPhase, loadReports])

  // cohort 时刻输入框与配置真源同步（PUT 后/刷新后回填）
  useEffect(() => {
    if (cohort === null) return
    setCohortHour(String(cohort.hour))
    setCohortMinute(String(cohort.minute))
  }, [cohort])

  // 预登记表单回填最新版本（新建版本模式=空表单，不回填）
  useEffect(() => {
    if (prereg === null || prereg.length === 0) return
    if (preregNewVersion) return
    const latest = prereg[prereg.length - 1]
    const next: Record<string, string> = {}
    for (const field of PREREG_FIELDS) next[field] = latest.fields[field] ?? ''
    setPreregDraft(next)
  }, [prereg, preregNewVersion])

  /* ── 烧钱确认 ── */

  const askSpend = (request: SpendConfirmRequest, action: () => void) => {
    pendingAction.current = action
    setSpend(request)
  }
  const cancelSpend = () => {
    // 取消路径：只清确认态，不发任何请求
    pendingAction.current = null
    setSpend(null)
  }
  const confirmSpend = () => {
    const action = pendingAction.current
    pendingAction.current = null
    setSpend(null)
    if (action) action()
  }

  const costFieldOfLatest = prereg !== null && prereg.length > 0
    ? prereg[prereg.length - 1].fields[COST_FIELD]
    : undefined

  const budgetLine = () => `当前预算上限：${fmtTokens(cohort?.budget_tokens ?? null)} tokens`
    + `（今日已花费 ${fmtTokens(cohort?.today_spend ?? 0)} tokens）`

  const withCostField = (lines: string[]): string[] => (
    costFieldOfLatest ? [...lines, `预登记「${COST_FIELD}」（${prereg![prereg!.length - 1].path}）：${costFieldOfLatest}`] : lines
  )

  /* ── 手动补跑 ── */

  const pollJobUntilIdle = useCallback(async (jobId: string) => {
    for (let i = 0; i < POLL_MAX_TRIES; i += 1) {
      const data = await loadJobs()
      if (data === null) return
      const job = data.jobs.find((item) => item.job_id === jobId)
      const run = job ? latestRealRun(job) : null
      if (run === null || run.status !== 'running') return
      await sleep(POLL_INTERVAL_MS)
    }
  }, [loadJobs])

  const runJobNow = async (jobId: string) => {
    setError(null)
    setRunningJob(jobId)
    try {
      const res = await fetch(`/api/v1/ops/jobs/${encodeURIComponent(jobId)}/run`, { method: 'POST' })
      if (!res.ok) {
        setError(`立即运行被拒绝：${runRefusalText(res.status, await detailText(res))}`)
        return
      }
      await pollJobUntilIdle(jobId)
    } catch {
      setError('立即运行失败：网络错误')
    } finally {
      setRunningJob(null)
    }
  }

  /* ── cohort 配置 ── */

  const putCohort = async (patch: { enabled?: boolean; hour?: number; minute?: number }) => {
    setError(null)
    try {
      const res = await fetch('/api/v1/ops/cohort', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!res.ok) {
        setError(`cohort 配置保存失败：${await detailText(res)}`)
        return
      }
      const data = (await res.json()) as OpsCohortState & { audit_run_id?: number; rescheduled?: boolean }
      setCohort({
        enabled: data.enabled, hour: data.hour, minute: data.minute, budget_tokens: data.budget_tokens,
        today_spend: data.today_spend, today_success: data.today_success, today_failure: data.today_failure,
      })
      setCohortAudit({ runId: data.audit_run_id ?? -1, rescheduled: data.rescheduled ?? false })
      await loadJobs()  // 刷新任务行（含 cohort 的 config-change 审计行）
    } catch {
      setError('cohort 配置保存失败：网络错误')
    }
  }

  const onToggleCohort = (next: boolean) => {
    if (!next) { void putCohort({ enabled: false }); return }  // 关闭零花费，无需确认
    askSpend({
      title: '开启 cohort 跑批',
      estimate: withCostField([
        `规模：每日一轮 cohort 跑批（默认 universe），${COHORT_DAILY_ESTIMATE}`,
        '开启后由调度器定时触发；预算熔断、串行与 usage 真值记账语义不变',
      ]),
      budget: budgetLine(),
      confirmLabel: '确认开启',
    }, () => { void putCohort({ enabled: true }) })
  }

  const saveCohortTime = () => {
    const hour = Number(cohortHour)
    const minute = Number(cohortMinute)
    if (!Number.isInteger(hour) || hour < 0 || hour > 23) {
      setError('跑批时刻保存被拒绝：时应在 0–23 之间')
      return
    }
    if (!Number.isInteger(minute) || minute < 0 || minute > 59) {
      setError('跑批时刻保存被拒绝：分应在 0–59 之间')
      return
    }
    void putCohort({ hour, minute })
  }

  /* ── 长任务（回测 / 探针 / 健康检查） ── */

  const awaitRun = useCallback(async (runId: number): Promise<OpsRun | null> => {
    let last: OpsRun | null = null
    for (let i = 0; i < POLL_MAX_TRIES; i += 1) {
      try {
        const res = await fetch(`/api/v1/ops/runs/${runId}`)
        if (!res.ok) return last
        last = (await res.json()) as OpsRun
      } catch {
        return last
      }
      if (last.status !== 'running') return last
      await sleep(POLL_INTERVAL_MS)
    }
    return last
  }, [])

  const dispatchTask = async (kind: TaskKind, path: string, body?: unknown) => {
    setError(null)
    setTaskResult(null)
    setTaskPending(null)
    setTaskRunning(kind)
    try {
      const res = await fetch(path, {
        method: 'POST',
        ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      })
      if (!res.ok) {
        setError(`${kind === 'backtest' ? '回测批' : kind === 'probe' ? '探针单跑' : '健康检查'}发起被拒绝：${await detailText(res)}`)
        return
      }
      const data = (await res.json()) as { run_id: number }
      const run = await awaitRun(data.run_id)
      if (run === null) {
        setError(`运行 #${data.run_id} 结果读取失败（可稍后经运行历史复查）`)
        return
      }
      if (run.status === 'running') { setTaskPending(kind); return }
      if (run.status === 'failed') {
        setError(`运行 #${run.run_id} 失败：${run.error ?? '无错误信息'}`)
        return
      }
      setTaskResult({ kind, run })
    } catch {
      setError('发起失败：网络错误')
    } finally {
      setTaskRunning(null)
    }
  }

  const codeList = (raw: string) => raw.split(/[\s,，;；]+/).map((s) => s.trim()).filter(Boolean)

  const submitBacktest = (batchKind: 'pathway' | 'formal') => {
    setError(null)
    setBacktestRefusal(null)
    const codes = codeList(backtestCodes)
    if (codes.length === 0) {
      setError('回测批被拒绝：标的列表不得为空')
      return
    }
    if (batchKind === 'formal') {
      // 本地预检（服务端仍是唯一裁决）：无有效预登记时直接拒绝，不发起任何回放
      if (prereg !== null) {
        if (prereg.length === 0) {
          setBacktestRefusal('正式批被拒绝：未找到预登记文件（门禁要求有效预登记 + 干净窗口 + 探针披露）')
          return
        }
        const latest = prereg[prereg.length - 1]
        if (!latest.valid) {
          setBacktestRefusal(`正式批被拒绝：预登记无效——${latest.issues.join('；')}（${latest.path}）`)
          return
        }
      }
    }
    const perRegime = Number(backtestPerRegime)
    const repeats = Number(backtestRepeats)
    const label = batchKind === 'formal' ? '正式批' : '通路验证批'
    askSpend({
      title: `发起${label}`,
      estimate: withCostField([
        `规模：${codes.length} 只标的 × 每 regime ${backtestPerRegime} 只 × ${backtestRepeats} 次重复回放`
        + '，另含泄漏探针（每标的 3–5 问）',
        batchKind === 'formal'
          ? '正式批须过门禁（有效预登记 + 干净窗口 + 探针披露），不过即拒绝、不启动回放'
          : '通路验证批结论恒为「通路验证定位」，不产 skill 结论',
      ]),
      budget: budgetLine(),
      confirmLabel: `确认发起${label}`,
    }, () => {
      void dispatchTask('backtest', '/api/v1/ops/backtest', {
        batch_kind: batchKind, codes, per_regime: perRegime, repeats,
      })
    })
  }

  const submitProbe = () => {
    setError(null)
    const codes = codeList(probeCodes)
    if (codes.length === 0) { setError('探针单跑被拒绝：标的列表不得为空'); return }
    if (!probeDate.trim()) { setError('探针单跑被拒绝：决策日不得为空'); return }
    const tickers = Number(probeTickers)
    askSpend({
      title: '探针单跑',
      estimate: withCostField([
        `规模：${Number.isFinite(tickers) ? tickers : probeTickers} 只标的 × 3–5 问（LLM 答题，属烧钱动作）`,
        '读数三态披露（可测 / 降级 / 不可测）与阈值对照随结果展示',
      ]),
      budget: budgetLine(),
      confirmLabel: '确认单跑',
    }, () => {
      void dispatchTask('probe', '/api/v1/ops/probe', {
        codes, decision_date: probeDate.trim(), n_tickers: Number.isFinite(tickers) ? tickers : 10,
      })
    })
  }

  /* ── 预登记 / 口径提交 ── */

  const savePrereg = async () => {
    setError(null)
    setPreregSavedPath(null)
    const body: Record<string, string> = {}
    for (const field of PREREG_FIELDS) body[field] = (preregDraft[field] ?? '').trim()
    const missing = PREREG_FIELDS.filter((field) => body[field] === '')
    if (missing.length > 0) {
      setError(`预登记缺字段：${missing.join('、')}（未写入任何版本）`)
      return
    }
    try {
      const res = await fetch('/api/v1/ops/prereg', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        setError(`预登记保存被拒绝（未写入任何版本）：${await detailText(res)}`)
        return
      }
      const data = (await res.json()) as { path: string }
      setPreregSavedPath(data.path)
      setPreregNewVersion(false)
      await loadPrereg()
    } catch {
      setError('预登记保存失败：网络错误')
    }
  }

  const knobKeys = useCallback((): string[] => {
    if (caliber === null) return []
    const keys = Object.keys(caliber.knobs)
    return [...KNOB_ORDER.filter((key) => keys.includes(key)), ...keys.filter((key) => !KNOB_ORDER.includes(key))]
  }, [caliber])

  const submitCaliber = async () => {
    setError(null)
    setCaliberDraftDir(null)
    setCaliberHint(null)
    if (caliber === null) { setError('口径现值尚未加载，无法提交'); return }
    const knobs: Record<string, number> = {}
    for (const key of Object.keys(caliber.knobs)) {
      const raw = knobEdits[key]
      if (raw === undefined || raw.trim() === '') continue
      const value = Number(raw)
      if (!Number.isFinite(value)) { setError(`旋钮 ${key} 的取值不是数字：${raw}`); return }
      if (value !== caliber.knobs[key]) knobs[key] = value
    }
    if (Object.keys(knobs).length === 0) {
      setCaliberHint('未修改任何旋钮——未生成草稿（空变更会被服务端拒绝）')
      return
    }
    try {
      const res = await fetch('/api/v1/ops/caliber-draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(knobs),
      })
      if (!res.ok) {
        const detail = await detailText(res)
        setError(detail === 'draft_exists'
          ? '口径草稿生成被拒绝：已有未处理草稿涉及同一旋钮（先评审/归档该草稿，或另开 delta 合并变更）'
          : `口径草稿生成被拒绝：${detail}`)
        return
      }
      const data = (await res.json()) as { draft_dir: string }
      setCaliberDraftDir(data.draft_dir)
      setKnobEdits({})
    } catch {
      setError('口径草稿生成失败：网络错误')
    }
  }

  /* ── 渲染 ── */

  if (phase === 'error') {
    return (
      <div data-testid="eval-ops-pane-error" className="py-6 text-sm space-y-3" style={{ color: 'var(--text-tertiary)' }}>
        <p>评估运维状态加载失败</p>
        <Button data-testid="eval-ops-retry" size="sm" variant="outline" onClick={() => void loadCore('full')}>
          重试
        </Button>
      </div>
    )
  }

  if (jobs === null || cohort === null) {
    return <div className="py-6 text-sm" style={{ color: 'var(--text-tertiary)' }}>评估运维加载中…</div>
  }

  const latestPrereg = prereg !== null && prereg.length > 0 ? prereg[prereg.length - 1] : null
  const preregLocked = latestPrereg !== null && latestPrereg.locked && !preregNewVersion
  const knobKeysList = knobKeys()
  const caliberSource = caliber?.source ?? ''

  return (
    <div data-testid="eval-ops-pane" className="space-y-4">
      {/* 调度器未运行显式提示（不得空白、不得空列表冒充正常） */}
      {jobs?.error ? (
        <div data-testid="eval-ops-degraded" className="rounded-lg px-4 py-3 text-xs"
          style={{ background: 'var(--bg-overlay-l1)', color: 'var(--status-error-default)' }}>
          运行状态读取异常（下方数据可能不完整）：{jobs.error}
        </div>
      ) : null}
      {!jobs.scheduler_running && (
        <div data-testid="eval-ops-not-running" className="rounded-lg px-4 py-3 text-xs"
          style={{ background: 'rgba(250, 204, 21, 0.12)', color: 'var(--text-secondary)' }}>
          调度器未运行（TESTING=1 或显式禁用）：定时触发不会执行，任务行可手动补跑；配置改动重启后生效。
        </div>
      )}

      {/* cohort 摘要条（常驻：开关状态 / 跑批时刻 / 今日花费与预算） */}
      <div data-testid="eval-ops-cohort-summary" className="rounded-lg px-4 py-3 text-xs flex flex-wrap gap-x-4 gap-y-1"
        style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-secondary)' }}>
        <span>cohort 跑批：<b data-testid="eval-ops-cohort-enabled" style={{ color: 'var(--text-default)' }}>
          {cohort.enabled ? '已开启' : '未开启'}</b></span>
        <span>跑批时刻 {pad2(cohort.hour)}:{pad2(cohort.minute)}（Asia/Shanghai）</span>
        <span>今日花费 {fmtTokens(cohort.today_spend)} / 预算 {fmtTokens(cohort.budget_tokens)} tokens</span>
      </div>

      {/* 最近一次操作失败原因（门禁拒绝 / 校验不过 / 网络错误） */}
      {error !== null && (
        <div data-testid="eval-ops-error" className="rounded-lg px-4 py-3 text-xs"
          style={{ background: 'rgba(239, 68, 68, 0.12)', color: 'var(--status-error-default)' }}>
          {error}
        </div>
      )}

      {/* 页签栏 */}
      <div data-testid="eval-ops-tabs" className="flex flex-wrap gap-1">
        {TABS.map((item) => (
          <button key={item.id} type="button" data-testid={`eval-ops-tab-${item.id}`} onClick={() => setTab(item.id)}
            className="text-xs rounded-md px-2 py-1"
            style={{
              border: 'none', cursor: 'pointer',
              background: tab === item.id ? 'var(--bg-brand)' : 'var(--bg-overlay-l1)',
              color: tab === item.id ? 'var(--text-onbrand)' : 'var(--text-secondary)',
            }}>
            {item.label}
          </button>
        ))}
      </div>

      {/* ── 调度 ── */}
      {tab === 'schedule' && (
        <div data-testid="eval-ops-schedule" className="space-y-3">
          {jobs.jobs.map((job) => (
            <JobCard key={job.job_id} job={job} running={runningJob === job.job_id} onRun={(id) => void runJobNow(id)} />
          ))}
        </div>
      )}

      {/* ── cohort ── */}
      {tab === 'cohort' && (
        <div data-testid="eval-ops-cohort" className="space-y-3">
          <Card title="cohort 开关（开启需确认；关闭即时零花费）">
            <div className="flex items-center gap-2 text-xs">
              <input type="checkbox" data-testid="eval-ops-cohort-toggle" checked={cohort.enabled}
                onChange={(e) => onToggleCohort(e.target.checked)} />
              <span style={{ color: 'var(--text-secondary)' }}>
                {cohort.enabled ? '已开启（定时触发会产生 LLM 调用）' : '未开启（定时触发零 LLM 调用）'}
              </span>
            </div>
            <div data-testid="eval-ops-cohort-spend" className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              今日花费 {fmtTokens(cohort.today_spend)} tokens · 预算上限 {fmtTokens(cohort.budget_tokens)} tokens ·
              今日成功 {cohort.today_success} · 失败 {cohort.today_failure}
            </div>
          </Card>

          <Card title="跑批时刻（修改即时重排，不影响结算链四任务）">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <label htmlFor="eval-ops-cohort-hour" style={{ color: 'var(--text-tertiary)' }}>时</label>
              <input id="eval-ops-cohort-hour" data-testid="eval-ops-cohort-hour" value={cohortHour}
                onChange={(e) => setCohortHour(e.target.value)} inputMode="numeric"
                className="w-16 rounded-lg px-2 py-1 border"
                style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }} />
              <label htmlFor="eval-ops-cohort-minute" style={{ color: 'var(--text-tertiary)' }}>分</label>
              <input id="eval-ops-cohort-minute" data-testid="eval-ops-cohort-minute" value={cohortMinute}
                onChange={(e) => setCohortMinute(e.target.value)} inputMode="numeric"
                className="w-16 rounded-lg px-2 py-1 border"
                style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }} />
              <Button size="sm" variant="outline" data-testid="eval-ops-cohort-save-time" onClick={saveCohortTime}>
                保存时刻
              </Button>
            </div>
            {cohortAudit !== null && (
              <div data-testid="eval-ops-cohort-audit" className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                已记配置变更审计行 #{cohortAudit.runId}（旧值 → 新值）
                {cohortAudit.rescheduled ? ' · 已即时重排调度' : ' · 调度未重排（无调度器，重启后生效）'}
              </div>
            )}
            <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              环境变量仅作启动引导默认值；运行期唯一真相源为持久化运维配置（重启保持）。
            </div>
          </Card>
        </div>
      )}

      {/* ── 回测与探针 ── */}
      {tab === 'backtest' && (
        <div data-testid="eval-ops-backtest" className="space-y-3">
          <Card title="回测批（正式批先过门禁：有效预登记 + 干净窗口 + 探针披露）">
            <div className="space-y-2 text-xs">
              <Textarea data-testid="eval-ops-backtest-codes" value={backtestCodes}
                onChange={(e) => setBacktestCodes(e.target.value)}
                placeholder="标的代码（逗号/换行分隔），如 600519, 300308" rows={2} className="text-xs" />
              <div className="flex flex-wrap items-center gap-2">
                <label style={{ color: 'var(--text-tertiary)' }}>每 regime 抽样</label>
                <input data-testid="eval-ops-backtest-per-regime" value={backtestPerRegime}
                  onChange={(e) => setBacktestPerRegime(e.target.value)} inputMode="numeric"
                  className="w-16 rounded-lg px-2 py-1 border"
                  style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }} />
                <label style={{ color: 'var(--text-tertiary)' }}>重复次数</label>
                <input data-testid="eval-ops-backtest-repeats" value={backtestRepeats}
                  onChange={(e) => setBacktestRepeats(e.target.value)} inputMode="numeric"
                  className="w-16 rounded-lg px-2 py-1 border"
                  style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }} />
                <Button size="sm" variant="outline" data-testid="eval-ops-backtest-pathway"
                  onClick={() => submitBacktest('pathway')}>通路验证批</Button>
                <Button size="sm" data-testid="eval-ops-backtest-formal"
                  onClick={() => submitBacktest('formal')}>正式批</Button>
              </div>
              <div data-testid="eval-ops-backtest-prereg" style={{ color: 'var(--text-tertiary)' }}>
                {prereg === null
                  ? '预登记状态未加载——正式批由服务端门禁裁决'
                  : prereg.length === 0
                    ? '未找到预登记文件——正式批将被拒绝'
                    : (() => {
                        const latest = prereg[prereg.length - 1]
                        return latest.valid
                          ? `预登记门禁预检：${latest.path}（字段齐备）`
                          : `预登记门禁预检：最新版本无效（${latest.issues.join('；')}）`
                      })()}
              </div>
              {backtestRefusal !== null && (
                <div data-testid="eval-ops-backtest-refusal" className="rounded-lg px-3 py-2"
                  style={{ background: 'rgba(239, 68, 68, 0.12)', color: 'var(--status-error-default)' }}>
                  {backtestRefusal}
                </div>
              )}
              {taskRunning === 'backtest' && <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>回测批执行中…</div>}
              {taskPending === 'backtest' && (
                <div data-testid="eval-ops-task-pending" className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  回测批仍在后台执行——结果落运行历史与报告注册表，可稍后回本页查看。
                </div>
              )}
              {taskResult?.kind === 'backtest' && <BacktestResult run={taskResult.run} />}
            </div>
          </Card>

          <Card title="泄漏探针单跑（烧钱动作：确认后执行）">
            <div className="space-y-2 text-xs">
              <Textarea data-testid="eval-ops-probe-codes" value={probeCodes}
                onChange={(e) => setProbeCodes(e.target.value)}
                placeholder="候选窗口标的代码（逗号/换行分隔）" rows={2} className="text-xs" />
              <div className="flex flex-wrap items-center gap-2">
                <label style={{ color: 'var(--text-tertiary)' }}>决策日</label>
                <input type="date" data-testid="eval-ops-probe-date" value={probeDate}
                  onChange={(e) => setProbeDate(e.target.value)}
                  className="rounded-lg px-2 py-1 border"
                  style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }} />
                <label style={{ color: 'var(--text-tertiary)' }}>抽样只数</label>
                <input data-testid="eval-ops-probe-tickers" value={probeTickers}
                  onChange={(e) => setProbeTickers(e.target.value)} inputMode="numeric"
                  className="w-16 rounded-lg px-2 py-1 border"
                  style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }} />
                <Button size="sm" data-testid="eval-ops-probe-submit" onClick={submitProbe}>探针单跑</Button>
              </div>
              {taskRunning === 'probe' && <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>探针执行中…</div>}
              {taskPending === 'probe' && (
                <div data-testid="eval-ops-task-pending" className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  探针仍在后台执行——结果落运行历史，可稍后回本页查看。
                </div>
              )}
              {taskResult?.kind === 'probe' && <ProbeResult run={taskResult.run} />}
            </div>
          </Card>
        </div>
      )}

      {/* ── 健康检查 ── */}
      {tab === 'health' && (
        <div data-testid="eval-ops-health" className="space-y-3">
          <Card title="outcome 收口健康检查（只读；读数缺失展示「无读数」）"
            extra={<Button size="sm" variant="outline" data-testid="eval-ops-health-run"
              disabled={taskRunning === 'health'} onClick={() => void dispatchTask('health', '/api/v1/ops/health')}>
              {taskRunning === 'health' ? '执行中…' : '运行健康检查'}</Button>}>
            {taskRunning === 'health' && <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>健康检查执行中…</div>}
            {taskPending === 'health' && (
              <div data-testid="eval-ops-task-pending" className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                健康检查仍在后台执行——结果落运行历史，可稍后回本页查看。
              </div>
            )}
            {taskResult?.kind === 'health' ? <HealthResult run={taskResult.run} /> : (
              taskRunning !== 'health' && taskPending !== 'health' && (
                <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>尚未运行——点右上「运行健康检查」。</div>
              )
            )}
          </Card>
        </div>
      )}

      {/* ── 报告注册表 ── */}
      {tab === 'reports' && (
        <div data-testid="eval-ops-reports-pane" className="space-y-3">
          <Card title="回测报告注册表（evals/backtest/results/*.md）"
            extra={<Button size="sm" variant="ghost" data-testid="eval-ops-reports-refresh"
              onClick={() => void loadReports()}>刷新</Button>}>
            {reportsPhase === 'loading' && <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>报告加载中…</div>}
            {reportsPhase === 'error' && (
              <div className="text-xs" style={{ color: 'var(--status-error-default)' }}>
                报告注册表加载失败（不静默跳过）
              </div>
            )}
            {reportsPhase === 'ready' && reports !== null && <ReportTable reports={reports} />}
          </Card>
        </div>
      )}

      {/* ── 预登记与口径 ── */}
      {tab === 'prereg' && (
        <div data-testid="eval-ops-prereg-pane" className="space-y-3">
          <Card title="预登记（七字段；保存 = 新建版本，历史版本永不覆盖）">
            {latestPrereg !== null && (
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                最新版本 {latestPrereg.path} · {latestPrereg.valid ? '字段齐备' : `无效：${latestPrereg.issues.join('；')}`}
              </div>
            )}
            {latestPrereg === null && (
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>尚无预登记版本——填写七字段后保存即新建 v1。</div>
            )}
            {preregLocked && (
              <div data-testid="eval-ops-prereg-locked" className="rounded-lg px-3 py-2 text-xs"
                style={{ background: 'rgba(250, 204, 21, 0.12)', color: 'var(--text-secondary)' }}>
                该版本已有读数，锁定不可编辑（防事后改靶）。如需修改，请点「新建版本」：保存会生成新版本文件，历史版本不动。
              </div>
            )}
            <div data-testid="eval-ops-prereg-form" className="space-y-2">
              {PREREG_FIELDS.map((field) => (
                <div key={field} className="space-y-1">
                  <label htmlFor={`eval-ops-prereg-${field}`} className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                    {field}
                  </label>
                  <Textarea id={`eval-ops-prereg-${field}`} data-testid={`eval-ops-prereg-${field}`}
                    value={preregDraft[field] ?? ''} disabled={preregLocked} rows={2} className="text-xs"
                    onChange={(e) => setPreregDraft((prev) => ({ ...prev, [field]: e.target.value }))} />
                </div>
              ))}
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <Button size="sm" data-testid="eval-ops-prereg-save" disabled={preregLocked}
                  onClick={() => void savePrereg()}>保存为新版本</Button>
                <Button size="sm" variant="outline" data-testid="eval-ops-prereg-new"
                  onClick={() => {
                    setPreregNewVersion(true)
                    setPreregDraft({})  // 新建版本 = 空表单（不回填已锁定的历史版本）
                    setPreregSavedPath(null)
                    setError(null)
                  }}>
                  新建版本
                </Button>
                {preregSavedPath !== null && (
                  <span data-testid="eval-ops-prereg-saved" className="text-xs" style={{ color: 'var(--status-success-default)' }}>
                    已保存新版本：{preregSavedPath}
                  </span>
                )}
              </div>
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                校验与 CLI 同一套（缺字段 / 决策阈值缺换算依据 → 拒绝保存，磁盘零改动）。
              </div>
            </div>
          </Card>

          <Card title="口径旋钮（§1.9）">
            <div data-testid="eval-ops-caliber" className="space-y-2 text-xs">
              <div style={{ color: 'var(--text-tertiary)' }}>
                现值只读自 {caliberSource === '' ? 'evals/outcome/caliber.py' : caliberSource}（唯一权威定义见 docs/evals/metrics.md §1）
              </div>
              {caliber === null && <div style={{ color: 'var(--text-tertiary)' }}>口径现值加载失败/未就绪。</div>}
              {knobKeysList.map((key) => (
                <div key={key} className="flex items-center gap-2">
                  <label htmlFor={`eval-ops-knob-${key}`} className="w-44" style={{ color: 'var(--text-secondary)' }}>
                    {KNOB_LABELS[key] ?? key}
                  </label>
                  <input id={`eval-ops-knob-${key}`} data-testid={`eval-ops-knob-${key}`}
                    value={knobEdits[key] ?? String(caliber?.knobs[key] ?? '')}
                    onChange={(e) => setKnobEdits((prev) => ({ ...prev, [key]: e.target.value }))}
                    className="w-24 rounded-lg px-2 py-1 border"
                    style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }} />
                  <span style={{ color: 'var(--text-tertiary)' }}>现值 {String(caliber?.knobs[key] ?? '—')}</span>
                </div>
              ))}
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" data-testid="eval-ops-caliber-submit"
                  onClick={() => void submitCaliber()}>生成 delta 草稿</Button>
              </div>
              {caliberHint !== null && (
                <div data-testid="eval-ops-caliber-hint" style={{ color: 'var(--text-secondary)' }}>{caliberHint}</div>
              )}
              {caliberDraftDir !== null && (
                <div data-testid="eval-ops-caliber-draft" className="rounded-lg px-3 py-2"
                  style={{ background: 'rgba(250, 204, 21, 0.12)', color: 'var(--text-secondary)' }}>
                  草稿已生成：{caliberDraftDir} —— 草稿待评审，生效须走 delta 流程（先改 metrics.md §1 口径定义，再改代码常量，
                  最后在 §2 追加切点行）；台账与代码常量本操作零改动。
                </div>
              )}
            </div>
          </Card>
        </div>
      )}

      {/* 烧钱动作统一确认弹窗（取消零请求） */}
      <ConfirmSpendDialog request={spend} onConfirm={confirmSpend} onCancel={cancelSpend} />
    </div>
  )
}

/* ── 结果/表格子组件（纯展示） ── */

/** 回测批结果卡（summary 为后端裁剪版，完整报告在 md/json） */
function BacktestResult({ run }: { run: OpsRun }) {
  const summary = run.summary ?? {}
  const probe = (summary.leakage_probe ?? {}) as Record<string, any>
  const paths: string[] = Array.isArray(summary.report_paths)
    ? (summary.report_paths as unknown[]).map(String)
    : summary.report_paths ? [String(summary.report_paths)] : []
  return (
    <div data-testid="eval-ops-backtest-result" className="rounded-lg p-3 space-y-1"
      style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-secondary)' }}>
      <KeyValue label="运行" value={`#${run.run_id} · ${RUN_STATUS_LABEL[run.status] ?? run.status}`} />
      <KeyValue label="结论" value={String(summary.conclusion ?? '—')} />
      <KeyValue label="定位" value={POSITIONING_LABEL[String(summary.positioning)] ?? String(summary.positioning ?? '—')} />
      <KeyValue label="样本量" value={String(summary.n_sample ?? '—')} />
      <KeyValue label="泄漏探针" value={`${PROBE_STATE_LABEL[String(probe.state)] ?? String(probe.state ?? '—')}`
        + ` · 方向命中率 ${fmtPct(probe.direction_hit_rate ?? null)}（阈值 ${fmtPct(probe.threshold ?? null)}）`} />
      <KeyValue label="报告" value={paths.length > 0 ? paths.join('、') : '—'} />
    </div>
  )
}

/** 探针三态读数卡（不可测态显式「不可测」，不折算 0） */
function ProbeResult({ run }: { run: OpsRun }) {
  const summary = run.summary ?? {}
  const state = String(summary.state ?? '')
  const perWindow = Array.isArray(summary.per_window) ? (summary.per_window as Record<string, any>[]) : []
  return (
    <div data-testid="eval-ops-probe-result" className="rounded-lg p-3 space-y-1"
      style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-secondary)' }}>
      <KeyValue label="运行" value={`#${run.run_id} · ${RUN_STATUS_LABEL[run.status] ?? run.status}`} />
      <KeyValue label="读数状态" testId="eval-ops-probe-state"
        value={PROBE_STATE_LABEL[state] ?? (state === '' ? '缺读数' : state)} />
      <KeyValue label="方向命中率" testId="eval-ops-probe-direction"
        value={`${fmtPct(summary.direction_hit_rate ?? null)}（阈值 ${fmtPct(summary.threshold ?? null)}）`} />
      <KeyValue label="幅度桶率 / 事件率"
        value={`${fmtPct(summary.magnitude_hit_rate ?? null)} / ${fmtPct(summary.event_hit_rate ?? null)}`} />
      <KeyValue label="未知占比" value={fmtPct(summary.unknown_ratio ?? null)} />
      <KeyValue label="样本" value={`${summary.probe_n ?? '—'} 只 × ${summary.questions_per_ticker ?? '—'} 问`} />
      {perWindow.length > 0 && (
        <table className="w-full text-xs mt-1">
          <thead>
            <tr style={{ color: 'var(--text-tertiary)' }}>
              <th className="py-1 text-left font-normal">窗口</th>
              <th className="py-1 text-right font-normal">方向命中率</th>
            </tr>
          </thead>
          <tbody>
            {perWindow.map((row, index) => (
              <tr key={`${String(row.window ?? index)}`} className="border-t" style={{ borderColor: 'var(--border-neutral-l1)' }}>
                <td className="py-1">{String(row.window ?? '—')}</td>
                <td className="py-1 text-right">{fmtPct(row.direction_hit_rate ?? null)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/** 健康检查门禁表（读数缺失 → 「无读数」；判定真源 = 后端 checks） */
function HealthResult({ run }: { run: OpsRun }) {
  const summary = (run.summary ?? {}) as Record<string, any>
  const checks = (summary.checks ?? {}) as Record<string, boolean | undefined>
  const rows: Array<{ key: string; label: string; value: string; threshold: string
    verdict: 'PASS' | 'FAIL' | '无读数' | '披露'; note?: string }> = [
    {
      key: 'settlement_success', label: '结算成功率', value: fmtPct(summary.settlement_success_rate ?? null),
      threshold: `≥ ${(MIN_SETTLEMENT_SUCCESS_RATE * 100).toFixed(1)}%`,
      verdict: summary.settlement_success_rate === null || summary.settlement_success_rate === undefined
        ? '无读数' : (checks.settlement_success ? 'PASS' : 'FAIL'),
    },
    {
      key: 'unresolvable', label: '不可判定率', value: fmtPct(summary.unresolvable_rate ?? null),
      threshold: `≤ ${(MAX_UNRESOLVABLE_RATE * 100).toFixed(1)}%`,
      verdict: summary.unresolvable_rate === null || summary.unresolvable_rate === undefined
        ? '无读数' : (checks.unresolvable ? 'PASS' : 'FAIL'),
    },
    {
      key: 'integrity', label: 'integrity 一致性', value: `${summary.integrity_mismatches ?? '—'} 处不一致`,
      threshold: '= 0', verdict: summary.integrity_mismatches === null || summary.integrity_mismatches === undefined
        ? '无读数' : (checks.integrity ? 'PASS' : 'FAIL'),
    },
    {
      key: 'bookkeeping', label: '记账完整率', value: fmtPct(summary.bookkeeping_completeness ?? null),
      threshold: '披露项（无阈值）', verdict: '披露',
    },
  ]
  const verdictColor: Record<string, string> = {
    PASS: 'var(--status-success-default)', FAIL: 'var(--status-error-default)',
    无读数: 'var(--text-tertiary)', 披露: 'var(--text-secondary)',
  }
  return (
    <div data-testid="eval-ops-health-result" className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <span data-testid="eval-ops-health-verdict" style={{ color: summary.passed ? 'var(--status-success-default)' : 'var(--status-error-default)' }}>
          总体结论：{summary.passed ? '通过' : '不通过'}
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>
          运行 #{run.run_id} · 已结算 {summary.settled ?? '—'} 条
        </span>
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr style={{ color: 'var(--text-tertiary)' }}>
            <th className="py-1 text-left font-normal">门禁</th>
            <th className="py-1 text-right font-normal">实测</th>
            <th className="py-1 text-right font-normal">阈值</th>
            <th className="py-1 text-right font-normal">结论</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} data-testid={`eval-ops-health-gate-${row.key}`} className="border-t"
              style={{ borderColor: 'var(--border-neutral-l1)' }}>
              <td className="py-1">{row.label}{row.note ? `（${row.note}）` : ''}</td>
              <td className="py-1 text-right">{row.value}</td>
              <td className="py-1 text-right">{row.threshold}</td>
              <td className="py-1 text-right" style={{ color: verdictColor[row.verdict] }}>{row.verdict}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Array.isArray(summary.warnings) && summary.warnings.length > 0 && (
        <div style={{ color: 'var(--text-tertiary)' }}>
          告警：{(summary.warnings as unknown[]).map(String).join('；')}
        </div>
      )}
    </div>
  )
}

/** 报告注册表（status 徽章 + 定位标签 + 取代者路径 + 探针读数摘要） */
function ReportTable({ reports }: { reports: OpsReportEntry[] }) {
  if (reports.length === 0) {
    return <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>暂无回测报告。</div>
  }
  return (
    <table data-testid="eval-ops-reports" className="w-full text-xs">
      <thead>
        <tr style={{ color: 'var(--text-tertiary)' }}>
          <th className="py-1 text-left font-normal">报告</th>
          <th className="py-1 text-left font-normal">状态</th>
          <th className="py-1 text-left font-normal">定位</th>
          <th className="py-1 text-right font-normal">探针方向命中率</th>
        </tr>
      </thead>
      <tbody>
        {reports.map((entry) => (
          <tr key={entry.path} data-testid={`eval-ops-report-${entry.name}`} className="border-t align-top"
            style={{ borderColor: 'var(--border-neutral-l1)' }}>
            <td className="py-2 pr-2" style={{ color: 'var(--text-default)' }}>
              {entry.name}
              {entry.status === 'superseded-by' && entry.target && (
                <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>取代者：{entry.target}</div>
              )}
            </td>
            <td className="py-2 pr-2">
              <span style={{
                color: entry.status === 'active' ? 'var(--status-success-default)' : 'var(--text-tertiary)',
              }}>
                {REPORT_STATUS_LABEL[entry.status] ?? entry.status}
              </span>
            </td>
            <td className="py-2 pr-2" style={{ color: 'var(--text-secondary)' }}>
              {entry.positioning === null ? '未标注' : (POSITIONING_LABEL[entry.positioning] ?? entry.positioning)}
            </td>
            <td className="py-2 text-right" style={{ color: 'var(--text-secondary)' }}>
              {fmtPct(entry.probe_direction_hit_rate)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
