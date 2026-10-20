import { useCallback, useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '../../components/ui/button'
import { PageShellD, type ShellBatch } from '../../components/shells/PageShellD'
import { StatusBadge } from '../../components/StatusBadge'
import type { StatusKey } from '../../design/tokens'
import type { OpsJobStatus, OpsJobsResponse, OpsRun, ScreeningBatch } from '../../types'

/**
 * 日批运维页（delta add-daily-ops-page T2.1；spec frontend delta「日批运维页」）。
 *
 * 页面形态 PageShellD（运行记录：批次列表 + 步骤级状态图标）——本页把「当日各批」
 * 映射为批次卡（每卡一个日批任务）：
 * - **当日时间线**：GET /api/v1/ops/jobs 的排程时刻升序渲染（数据驱动，不硬编码
 *   任务清单——注册表扩容自动跟随）；每卡 = 任务名/排程时刻 + 状态徽章 + 最近运行
 *   摘要 + 失败原因逐字 + 一键补跑；排程文案两粒度（add-monthly-batch-scheduling）：
 *   日/周 →「HH:MM」、月 →「每月 N 日 HH:MM」（非排程日如实「未到本期排程日」）；
 * - **五态映射**：ok→active（成功）、failed→failed（失败）、今日无运行行→retired
 *   （未跑）、running→running（运行中）、skipped-disabled→degraded（跳过）；
 * - **一键补跑**：POST /api/v1/ops/jobs/{id}/run（复用既有补跑通道）；202 → 刷新
 *   运行历史；409 already_running / cohort_disabled 与其它失败按端点返回如实提示
 *   （文案口径与「设置 → 评估运维」同一套语义）；
 * - **选股批次历史表**：GET /api/screening/batches（日期倒序；各层计数/池数/错误逐字）；
 * - 配置类操作（cohort 时刻/开关、回测、健康检查、预登记）留在评估运维页，本页只
 *   提供跳转（onOpenEvalOps）；两区块空数据各自如实空态。
 *
 * 批次卡的 ``steps`` 留空：ops 任务载荷无子步骤分解，壳的步骤槽无对应事实——
 * 不以臆造步骤填充（同「不把缺失显示成正常」的如实纪律）。
 */

// 批次历史窗口（与后端 DEFAULT_BATCH_HISTORY_LIMIT 对齐；后端再按 [1,100] 钳制）
const BATCH_HISTORY_LIMIT = 20

/** 运行终态 → 徽章（五态映射唯一入口；文案用业务态名，不用生命周期文案） */
const RUN_BADGE: Record<string, { status: StatusKey; label: string }> = {
  running: { status: 'running', label: '运行中' },
  ok: { status: 'active', label: '成功' },
  failed: { status: 'failed', label: '失败' },
  'skipped-disabled': { status: 'degraded', label: '跳过' },
}
/** 今日无运行行 → 未跑（retired 灰；语义见 design「未跑（今日无运行行）」） */
const NOT_RUN_BADGE = { status: 'retired' as StatusKey, label: '未跑' }
/** 批次历史状态 → 徽章（ok/failed 两态，库内原值） */
const BATCH_BADGE: Record<string, { status: StatusKey; label: string }> = {
  ok: { status: 'active', label: '成功' },
  failed: { status: 'failed', label: '失败' },
}

const RUN_STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  ok: '成功',
  failed: '失败',
  'skipped-disabled': '跳过（开关关闭）',
}

const pad2 = (n: number) => String(n).padStart(2, '0')

/** 本机日期 YYYY-MM-DD（不用 toISOString——UTC 换算会把本地晚间算成前一天） */
function localISODate(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

function scheduleMinutes(job: OpsJobStatus): number {
  return job.schedule.hour * 60 + job.schedule.minute
}

/**
 * 排程文案（add-monthly-batch-scheduling 粒度通用化）：
 * - 月粒度（含 day_of_month）→「每月 N 日 HH:MM」；
 * - 日/周粒度（既有）→「HH:MM」逐字不变。
 */
function scheduleText(schedule: OpsJobStatus['schedule']): string {
  const time = `${pad2(schedule.hour)}:${pad2(schedule.minute)}`
  return schedule.day_of_month !== undefined ? `每月 ${schedule.day_of_month} 日 ${time}` : time
}

/** 摘要单值渲染（长对象截断；口径同「评估运维」面板，避免 summary 全量铺开） */
function shortValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value).slice(0, 60)
  return String(value)
}

function fmtSummary(summary: Record<string, any> | null | undefined): string {
  if (summary === null || summary === undefined || Object.keys(summary).length === 0) return '（无摘要）'
  const text = Object.entries(summary).slice(0, 4).map(([k, v]) => `${k}=${shortValue(v)}`).join(' · ')
  return text.length > 160 ? `${text.slice(0, 160)}…` : text
}

/** 「最近一次运行」= 最新一条非 config-change 行（后端 last_run 可能是配置变更审计行） */
function latestRealRun(job: OpsJobStatus): OpsRun | null {
  const rows = [job.last_run, ...job.history].filter((row): row is OpsRun => row !== null)
  const unique = [...new Map(rows.map((row) => [row.run_id, row])).values()]
  unique.sort((a, b) =>
    a.started_at === b.started_at ? b.run_id - a.run_id : a.started_at < b.started_at ? 1 : -1
  )
  return unique.find((row) => row.kind !== 'config-change') ?? null
}

interface TaskState {
  badge: { status: StatusKey; label: string }
  /** 最近运行摘要（含状态与时刻）；未跑时如实区分「今日未跑」/「尚未到排程时刻」 */
  summary: string
  /** 失败原因逐字（仅失败行有） */
  error: string | null
}

interface DailyBriefRow {
  brief_date: string
  status: string
  markdown: string
  model: string | null
  prompt_version: string | null
  error: string | null
  created_at: string
}

type BriefState = 'loading' | 'ok' | 'missing' | 'error'

/** 每日汇报区：404=尚未生成、非 2xx=取数失败、failed=LLM 失败，三态严格区分 */
function DailyBriefCard({ brief, state }: { brief: DailyBriefRow | null; state: BriefState }) {
  return (
    <section data-testid="daily-brief" className="px-4 pb-4">
      <h2 className="mb-2 text-sm font-medium" style={{ color: 'var(--text-default)' }}>
        每日汇报
      </h2>
      {state === 'loading' && (
        <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
          加载中…
        </p>
      )}
      {state === 'missing' && (
        <p data-testid="daily-brief-empty" className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
          今日汇报尚未生成（20:30 自动生成）
        </p>
      )}
      {state === 'error' && (
        <p data-testid="daily-brief-load-error" className="text-xs" style={{ color: 'var(--status-error-default)' }}>
          日报加载失败（数据未取到，与「尚未生成」不同）
        </p>
      )}
      {state === 'ok' && brief && (
        <div className="rounded border p-3" style={{ borderColor: 'var(--border-default)' }}>
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
            <span data-testid="daily-brief-date">{brief.brief_date}</span>
            <StatusBadge
              status={brief.status === 'ok' ? 'active' : 'failed'}
              label={brief.status === 'ok' ? '已生成' : '生成失败'}
            />
            <span data-testid="daily-brief-model" style={{ color: 'var(--text-tertiary)' }}>
              {brief.model ?? '模型未记录'}
            </span>
          </div>
          {brief.status !== 'ok' && (
            <p
              data-testid="daily-brief-failure"
              className="mb-2 text-xs"
              style={{ color: 'var(--status-error-default)' }}
            >
              生成失败：{brief.error ?? '未知错误'}
            </p>
          )}
          <div
            data-testid="daily-brief-markdown"
            className="text-sm leading-relaxed"
            style={{ color: 'var(--text-default)' }}
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{brief.markdown}</ReactMarkdown>
          </div>
        </div>
      )}
    </section>
  )
}

/** 任务五态判定 + 摘要文案（纯函数，便于用例钉死日期/时刻） */
function describeJob(job: OpsJobStatus, now: Date): TaskState {
  const run = latestRealRun(job)
  if (run !== null && run.started_at.slice(0, 10) === localISODate(now)) {
    // 未知状态：灰徽章 + 原状态逐字（不臆造、不把未知读成「未跑」）
    const badge = RUN_BADGE[run.status] ?? { status: 'retired' as StatusKey, label: run.status }
    const when = run.finished_at ?? run.started_at
    const summary =
      run.status === 'failed' ? `${badge.label} · ${when}` : `${badge.label} · ${when} · ${fmtSummary(run.summary)}`
    return { badge, summary, error: run.status === 'failed' ? run.error : null }
  }
  // 今日无运行行：已过排程时刻 → 未跑；未到 → 尚未到排程时刻（design 口径）。
  // 月批（含 day_of_month）：仅排程日谈「今日未跑」；非排程日如实报「未到本期排程日」，
  // 不得把「还没轮到」谎称成「该跑没跑」。
  const isScheduledDay =
    job.schedule.day_of_month === undefined || now.getDate() === job.schedule.day_of_month
  const due = isScheduledDay && now.getHours() * 60 + now.getMinutes() >= scheduleMinutes(job)
  const head = !isScheduledDay
    ? `未到本期排程日（${scheduleText(job.schedule)}）`
    : due
      ? '今日未跑'
      : `尚未到排程时刻（${scheduleText(job.schedule)}）`
  const tail =
    run === null
      ? '（尚无运行历史）'
      : `（上次运行：${run.started_at.slice(0, 10)} ${RUN_STATUS_LABEL[run.status] ?? run.status}）`
  return { badge: NOT_RUN_BADGE, summary: `${head}${tail}`, error: null }
}

/** 补跑拒绝文案（409 两种业务语义各自明说；与 EvalOpsPane.runRefusalText 同口径） */
function runRefusalText(status: number, detail: string): string {
  if (status === 409 && detail === 'already_running') return '该任务已在运行（手动与定时互斥，未启动第二个实例）'
  if (status === 409 && detail === 'cohort_disabled') return 'cohort 开关未开启，手动跑批被拒绝（零 LLM 调用）'
  if (status === 404) return '未知任务'
  return detail
}

async function detailText(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    return body?.detail === undefined ? `HTTP ${res.status}` : String(body.detail)
  } catch {
    return `HTTP ${res.status}`
  }
}

/** 选股批次历史表（日期倒序；各层计数/池数/错误逐字） */
function BatchHistoryTable({ rows }: { rows: ScreeningBatch[] }) {
  if (rows.length === 0) {
    return (
      <p data-testid="ops-batches-empty" className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
        暂无选股批次历史
      </p>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table data-testid="ops-batches-table" className="w-full text-xs">
        <thead>
          <tr style={{ color: 'var(--text-tertiary)' }}>
            <th className="px-2 py-1.5 text-left font-normal">日期</th>
            <th className="px-2 py-1.5 text-left font-normal">状态</th>
            <th className="px-2 py-1.5 text-right font-normal">L1 通过</th>
            <th className="px-2 py-1.5 text-right font-normal">L2 入榜</th>
            <th className="px-2 py-1.5 text-right font-normal">L3 保留</th>
            <th className="px-2 py-1.5 text-right font-normal">池数</th>
            <th className="px-2 py-1.5 text-left font-normal">错误</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            // 未知状态：灰徽章 + 库内原值逐字（不把未知读成「失败」）
            const badge = BATCH_BADGE[row.status] ?? { status: 'retired' as StatusKey, label: row.status }
            return (
              <tr
                key={row.trade_date}
                data-testid={`ops-batch-row-${row.trade_date}`}
                className="border-t"
                style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
              >
                <td className="px-2 py-1.5 whitespace-nowrap">{row.trade_date}</td>
                <td className="px-2 py-1.5">
                  <StatusBadge status={badge.status} label={badge.label} />
                </td>
                <td className="px-2 py-1.5 text-right">{row.l1_passed}</td>
                <td className="px-2 py-1.5 text-right">{row.l2_top}</td>
                <td className="px-2 py-1.5 text-right">{row.l3_keep}</td>
                <td className="px-2 py-1.5 text-right">{row.pool_size}</td>
                {/* 失败原因逐字（不改写不归类）；成功行无错误 → 占位「—」 */}
                <td
                  data-testid={`ops-batch-error-${row.trade_date}`}
                  className="px-2 py-1.5 break-all"
                  style={{ color: row.error ? 'var(--status-error-default)' : 'var(--text-tertiary)' }}
                >
                  {row.error ?? '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function DailyOpsPage({ onBack, onOpenEvalOps }: { onBack: () => void; onOpenEvalOps: () => void }) {
  // jobs === null = 加载中；loadFailed = 取数失败（与「今日无排程任务」空态严格区分）
  const [jobs, setJobs] = useState<OpsJobStatus[] | null>(null)
  const [batches, setBatches] = useState<ScreeningBatch[]>([])
  const [batchesFailed, setBatchesFailed] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const [degraded, setDegraded] = useState<string | null>(null)
  const [schedulerRunning, setSchedulerRunning] = useState(true)
  const [pendingJobId, setPendingJobId] = useState<string | null>(null)
  const [notices, setNotices] = useState<Record<string, string>>({})
  const [brief, setBrief] = useState<DailyBriefRow | null>(null)
  const [briefState, setBriefState] = useState<BriefState>('loading')

  const load = useCallback(async () => {
    setLoadFailed(false)
    try {
      const [jobsResp, batchesResp] = await Promise.all([
        fetch('/api/v1/ops/jobs'),
        fetch(`/api/screening/batches?limit=${BATCH_HISTORY_LIMIT}`),
      ])
      if (!jobsResp.ok) throw new Error(String(jobsResp.status))
      const body = (await jobsResp.json()) as OpsJobsResponse
      setJobs(body.jobs ?? [])
      // 降级载荷（读运行历史/配置失败）带顶层 error：必须披露，否则「库读失败」与
      // 「从未跑过」在界面上不可分（types.ts OpsJobsResponse 契约）
      setDegraded(body.error ?? null)
      setSchedulerRunning(body.scheduler_running !== false)
      // 批次历史通道独立：失败只让该区块如实报错，不拖垮时间线；也不得把它
      // 读成「暂无历史」（缺失败与真空集在界面上必须可分）
      if (batchesResp.ok) {
        setBatches((await batchesResp.json()) as ScreeningBatch[])
        setBatchesFailed(false)
      } else {
        setBatches([])
        setBatchesFailed(true)
      }
      // 每日汇报独立取数：404=尚未生成（空态）、非 2xx=取数失败、200=渲染；
      // 失败不得拖垮时间线，也不得把失败读成「尚未生成」。
      try {
        const briefResp = await fetch('/api/v1/ops/daily-brief/latest')
        if (briefResp.status === 404) {
          setBrief(null)
          setBriefState('missing')
        } else if (!briefResp.ok) {
          setBrief(null)
          setBriefState('error')
        } else {
          setBrief((await briefResp.json()) as DailyBriefRow)
          setBriefState('ok')
        }
      } catch {
        setBrief(null)
        setBriefState('error')
      }
    } catch {
      setLoadFailed(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const rerun = useCallback(
    async (jobId: string) => {
      setPendingJobId(jobId)
      setNotices((prev) => {
        const next = { ...prev }
        delete next[jobId]
        return next
      })
      try {
        const resp = await fetch(`/api/v1/ops/jobs/${jobId}/run`, { method: 'POST' })
        const detail = resp.ok ? null : await detailText(resp)
        // 无论 2xx / 409 / 500 都刷新：补跑（含失败尝试）会落新运行行，
        // 不刷新会让「最近运行」停留在旧读数（409 已运行同理——在运行行可见）。
        await load()
        setNotices((prev) => ({
          ...prev,
          [jobId]: resp.ok ? '补跑完成，运行历史已刷新' : runRefusalText(resp.status, detail ?? ''),
        }))
      } catch {
        setNotices((prev) => ({ ...prev, [jobId]: '补跑请求失败（网络错误）' }))
      } finally {
        setPendingJobId(null)
      }
    },
    [load]
  )

  const now = new Date()
  // 排程时刻升序（数据驱动；同刻按载荷原始顺序稳定），不硬编码任务清单
  const timeline = (jobs ?? [])
    .map((job, index) => ({ job, index, state: describeJob(job, now) }))
    .sort((a, b) => scheduleMinutes(a.job) - scheduleMinutes(b.job) || a.index - b.index)

  const shellBatches: ShellBatch[] = timeline.map(({ job, state }) => ({
    id: job.job_id,
    title: (
      <span data-testid={`ops-job-title-${job.job_id}`}>
        {scheduleText(job.schedule)} · {job.label}
      </span>
    ),
    subtitle: (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span data-testid={`ops-job-summary-${job.job_id}`}>{state.summary}</span>
        {state.error && (
          <span data-testid={`ops-job-error-${job.job_id}`} className="break-all" style={{ color: 'var(--status-error-default)' }}>
            {state.error}
          </span>
        )}
        <Button
          size="sm"
          variant="outline"
          data-testid={`ops-job-run-${job.job_id}`}
          disabled={pendingJobId !== null}
          onClick={() => void rerun(job.job_id)}
        >
          {pendingJobId === job.job_id ? '补跑中…' : '补跑'}
        </Button>
        {notices[job.job_id] && (
          <span data-testid={`ops-job-notice-${job.job_id}`} style={{ color: 'var(--status-warning-default)' }}>
            {notices[job.job_id]}
          </span>
        )}
      </div>
    ),
    status: state.badge.status,
    statusLabel: state.badge.label,
    steps: [],
  }))

  return (
    <div data-testid="daily-ops-page" className="flex h-full flex-col overflow-y-auto">
      <header className="flex items-center gap-3 px-4 py-3">
        <Button variant="ghost" size="sm" onClick={onBack} data-testid="ops-back">
          <i className="fas fa-arrow-left mr-1"></i>返回
        </Button>
        <h1 className="text-sm font-medium" style={{ color: 'var(--text-default)' }}>
          日批运维
        </h1>
        <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
          当日各批状态一目了然；配置类操作见评估运维
        </span>
        <div className="flex-1" />
        {/* 全量控制台（cohort / 回测 / 健康检查 / 预登记）跳转：本页只读聚合 + 补跑 */}
        <Button variant="outline" size="sm" data-testid="ops-open-eval-ops" onClick={onOpenEvalOps}>
          评估运维
        </Button>
      </header>

      {schedulerRunning === false && !loadFailed && (
        <p data-testid="ops-scheduler-note" className="px-4 pb-2 text-xs" style={{ color: 'var(--status-warning-default)' }}>
          调度器未运行（TESTING 或显式禁用）：定时触发不会执行，任务可手动补跑。
        </p>
      )}
      {degraded && (
        <p data-testid="ops-degraded-note" className="px-4 pb-2 text-xs" style={{ color: 'var(--status-warning-default)' }}>
          状态读数不完整（降级披露）：{degraded}
        </p>
      )}

      {loadFailed ? (
        <div data-testid="ops-load-error" className="flex flex-col items-center gap-3 py-16">
          <i className="fas fa-triangle-exclamation text-2xl" style={{ color: 'var(--status-error-default)' }}></i>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            日批状态加载失败（与「今日无排程任务」不同：数据未取到）
          </p>
          <Button size="sm" onClick={() => void load()} data-testid="ops-retry">
            重试
          </Button>
        </div>
      ) : (
        <>
          <DailyBriefCard brief={brief} state={briefState} />
          <PageShellD
            batches={shellBatches}
            loading={jobs === null}
            emptyMessage="今日无排程任务"
            emptyIcon="fa-list-check"
          />
          <section className="px-4 pb-6">
            <h2 className="mb-2 text-sm font-medium" style={{ color: 'var(--text-default)' }}>
              选股批次历史（近 {BATCH_HISTORY_LIMIT} 批次）
            </h2>
            {batchesFailed ? (
              <p data-testid="ops-batches-error" className="text-xs" style={{ color: 'var(--status-error-default)' }}>
                批次历史加载失败（数据未取到，与「暂无历史」不同）
              </p>
            ) : (
              <BatchHistoryTable rows={batches} />
            )}
          </section>
        </>
      )}
    </div>
  )
}
