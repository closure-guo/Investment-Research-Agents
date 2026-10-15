// EvalOpsPane 评估运维分区（delta add-eval-ops-console Task 6）
//
// 契约来源：后端 `src/finance_agent/ops_api.py`（字段名逐字对齐，非猜测）：
//   GET  /api/v1/ops/jobs              → { scheduler_running, jobs[{job_id,label,schedule,next_fire_time,last_run,history}], cohort{...} }
//   POST /api/v1/ops/jobs/{id}/run     → 202 { run_id } | 409 { detail } | 500 { detail }
//   GET  /api/v1/ops/runs/{id}         → 运行行（含 summary/error）
//   PUT  /api/v1/ops/cohort            → cohort 块 + audit_run_id + rescheduled
//   GET  /api/v1/ops/reports           → [{name,path,status,target,positioning,probe_direction_hit_rate}]
//   POST /api/v1/ops/backtest|probe|health → 202 { run_id }（backtest 门禁不过 → 409/422 { detail }）
//   GET  /api/v1/ops/prereg            → [{path,fields,valid,issues,locked}]
//   PUT  /api/v1/ops/prereg            → 201 { path } | 422 { detail }
//   GET  /api/v1/ops/caliber           → { knobs, source }
//   POST /api/v1/ops/caliber-draft     → 201 { draft_dir } | 409 { detail:"draft_exists" } | 422 { detail }
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { EvalOpsPane } from '../../pages/settings/panes/EvalOpsPane'
import { SettingsCenterPage } from '../../pages/settings/SettingsCenterPage'
import { emptyLlmConfig } from '../../llmConfig'

/* ── 夹具：与后端契约同形状 ── */

const COHORT_OFF = {
  enabled: false, hour: 18, minute: 0, budget_tokens: 2_000_000,
  today_spend: 0, today_success: 0, today_failure: 0,
}

function jobCard(jobId: string, label: string, extra: Record<string, unknown> = {}) {
  return {
    job_id: jobId, label,
    schedule: { day_of_week: 'mon-fri', hour: 16, minute: 0, timezone: 'Asia/Shanghai' },
    next_fire_time: null, last_run: null, history: [],
    ...extra,
  }
}

const JOB_IDS = ['decision_settle_daily', 'daily_marking', 'metrics_snapshot', 'integrity_check', 'cohort_batch']
const JOB_LABELS: Record<string, string> = {
  decision_settle_daily: '判定', daily_marking: '盯市', metrics_snapshot: '指标快照',
  integrity_check: '完整性校验', cohort_batch: 'cohort 跑批',
}

function fiveJobs() {
  return JOB_IDS.map((id) => jobCard(id, JOB_LABELS[id]))
}

function runRow(over: Record<string, unknown> = {}) {
  return {
    run_id: 1, job_id: 'integrity_check', kind: 'scheduled', source: 'scheduled', status: 'ok',
    started_at: '2026-09-23T16:00:00', finished_at: '2026-09-23T16:00:05',
    summary: { issues: 0 }, error: null, ...over,
  }
}

const JOBS_NOT_RUNNING = { scheduler_running: false, jobs: fiveJobs(), cohort: COHORT_OFF }
const JOBS_RUNNING = { ...JOBS_NOT_RUNNING, scheduler_running: true }

const PREREG_FIELDS: Record<string, string> = {
  主指标: '逐决策 T+20 交易日相对沪深300超额收益的均值与胜率',
  MDE: '均值超额 n=30 → 5.1pp；换算依据见 §4',
  决策阈值: 'CI 判定（95% CI 下限 > 0）——依据：标的簇 bootstrap CI',
  样本量依据: 'forward 腿红线 ≥10 可执行 settled、完整结论 ≥30',
  停止规则: '健康检查不过 → 本批读数作废、先修数据',
  成本分型: 'forward 腿 = 每标的 1 次 deep 全流程（pilot 实测 ≈166k tokens）；判定与统计零 LLM 调用',
  泄漏控制: '干净窗口（决策日距跑批日 ≥20 交易日）+ 泄漏探针披露（阈值 0.60）',
}

function preregVersion(over: Record<string, unknown> = {}) {
  return {
    path: 'evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md',
    fields: { ...PREREG_FIELDS }, valid: true, issues: [], locked: false, ...over,
  }
}

const CALIBER = {
  knobs: { PRIMARY_WINDOW_DAYS: 20, NEUTRAL_BAND: 0.02, LEAKAGE_PROBE_THRESHOLD: 0.6, MIN_SETTLED_FOR_WINRATE: 10 },
  source: 'evals/outcome/caliber.py',
}

/* ── fetch stub：按「方法 + 规范化路径」路由；支持按调用序号返回不同响应 ── */

interface Resp { status?: number; body?: unknown }
type Entry = Resp | ((n: number) => Resp)
interface Call { url: string; method: string; body: unknown }

function normalize(url: string, method: string): string {
  if (/^\/api\/v1\/ops\/jobs\/[^/]+\/run$/.test(url)) return `${method} /api/v1/ops/jobs/:id/run`
  if (/^\/api\/v1\/ops\/runs\/\d+$/.test(url)) return `${method} /api/v1/ops/runs/:id`
  return `${method} ${url}`
}

function mockFetch(routes: Record<string, Entry> = {}): { calls: Call[] } {
  const calls: Call[] = []
  const counters: Record<string, number> = {}

  const defaults: Record<string, Entry> = {
    'GET /api/v1/ops/jobs': { body: JOBS_NOT_RUNNING },
    'GET /api/v1/ops/prereg': { body: [preregVersion()] },
    'GET /api/v1/ops/caliber': { body: CALIBER },
    'GET /api/v1/ops/reports': { body: [] },
  }
  const table = { ...defaults, ...routes }

  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === 'string' ? input : input.toString()
    const url = raw.split('?')[0]
    const method = (init?.method ?? 'GET').toUpperCase()
    const key = normalize(url, method)
    counters[key] = (counters[key] ?? 0) + 1
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ url: raw, method, body })
    const entry = table[key]
    if (entry === undefined) {
      return Promise.resolve(new Response(JSON.stringify({ detail: `not_mocked: ${key}` }), { status: 404 }))
    }
    const resolved: Resp = typeof entry === 'function' ? entry(counters[key]) : entry
    return Promise.resolve(new Response(
      resolved.body === undefined ? '' : JSON.stringify(resolved.body),
      { status: resolved.status ?? 200 },
    ))
  }))
  return { calls }
}

const callsOf = (calls: Call[], method: string, fragment: string) =>
  calls.filter((c) => c.method === method && c.url.includes(fragment))

function renderPane() {
  return render(<EvalOpsPane />)
}

async function openTab(id: string) {
  fireEvent.click(await screen.findByTestId(`eval-ops-tab-${id}`))
}

describe('EvalOpsPane 评估运维分区（add-eval-ops-console Task 6）', () => {
  beforeEach(() => { mockFetch() })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  /* ── 1. 载入状态机 ── */

  it('调度器未运行时展示显式横幅，并渲染五个任务卡片', async () => {
    renderPane()
    expect(await screen.findByTestId('eval-ops-pane')).toBeInTheDocument()
    // 未运行显式提示（不得空白）
    expect(await screen.findByTestId('eval-ops-not-running')).toHaveTextContent('调度器未运行')
    // 五任务卡片
    expect(screen.getAllByTestId(/^eval-ops-job-/)).toHaveLength(5)
    // 卡片披露：排程 + 下次触发 + 最近一次运行
    const card = screen.getByTestId('eval-ops-job-integrity_check')
    expect(card).toHaveTextContent('完整性校验')
    expect(card).toHaveTextContent('mon-fri')
    expect(card).toHaveTextContent('16:00')
    expect(card).toHaveTextContent('Asia/Shanghai')
    expect(within(card).getByTestId('eval-ops-next-integrity_check')).toHaveTextContent('—')
    expect(within(card).getByTestId('eval-ops-last-run-integrity_check')).toHaveTextContent('尚无运行记录')
  })

  it('月粒度排程文案：「每月 N 日 HH:MM」；既有日批文案逐字不变', async () => {
    const monthly = [
      jobCard('memory_extract_monthly', '记忆提炼', {
        schedule: { day_of_month: 1, hour: 9, minute: 0, timezone: 'Asia/Shanghai' },
      }),
      jobCard('factor_proposal_monthly', '因子提案', {
        schedule: { day_of_month: 1, hour: 10, minute: 0, timezone: 'Asia/Shanghai' },
      }),
    ]
    mockFetch({
      'GET /api/v1/ops/jobs': { body: { ...JOBS_NOT_RUNNING, jobs: [...fiveJobs(), ...monthly] } },
    })
    renderPane()

    const memoryCard = await screen.findByTestId('eval-ops-job-memory_extract_monthly')
    expect(memoryCard).toHaveTextContent('每月 1 日 09:00（Asia/Shanghai）')
    expect(screen.getByTestId('eval-ops-job-factor_proposal_monthly'))
      .toHaveTextContent('每月 1 日 10:00（Asia/Shanghai）')
    // 既有日批卡片文案逐字不变（周粒度分支）
    expect(screen.getByTestId('eval-ops-job-integrity_check'))
      .toHaveTextContent('mon-fri 16:00（Asia/Shanghai）')
  })

  it('调度器运行中时不展示未运行横幅，且展示下次触发时间', async () => {
    mockFetch({
      'GET /api/v1/ops/jobs': {
        body: {
          ...JOBS_RUNNING,
          jobs: fiveJobs().map((j) => (
            j.job_id === 'integrity_check'
              ? { ...j, next_fire_time: '2026-09-24T16:00:00+08:00' }
              : j
          )),
        },
      },
    })
    renderPane()
    await screen.findByTestId('eval-ops-pane')
    expect(screen.queryByTestId('eval-ops-not-running')).not.toBeInTheDocument()
    expect(screen.getByTestId('eval-ops-next-integrity_check'))
      .toHaveTextContent('2026-09-24T16:00:00+08:00')
  })

  it('「最近一次运行」跳过 config-change 审计行，审计行按 kind 单独标注', async () => {
    const audit = runRow({
      run_id: 9, kind: 'config-change', source: 'manual', status: 'ok',
      started_at: '2026-09-24T10:00:00', finished_at: '2026-09-24T10:00:00',
      summary: { from: { enabled: false }, to: { enabled: true }, rescheduled: true },
    })
    const real = runRow({ run_id: 8, kind: 'manual', summary: { issues: 0 }, started_at: '2026-09-23T16:00:00' })
    mockFetch({
      'GET /api/v1/ops/jobs': {
        body: {
          ...JOBS_NOT_RUNNING,
          jobs: fiveJobs().map((j) => (
            j.job_id === 'integrity_check' ? { ...j, last_run: audit, history: [audit, real] } : j
          )),
        },
      },
    })
    renderPane()
    const card = await screen.findByTestId('eval-ops-job-integrity_check')
    // 最近一次运行 = 最新非 config-change 行（issues=0），不是审计行
    expect(within(card).getByTestId('eval-ops-last-run-integrity_check')).toHaveTextContent('issues=0')
    // 审计行按 kind 单独披露
    expect(within(card).getByTestId('eval-ops-job-audit-integrity_check')).toHaveTextContent('配置变更')
  })

  it('加载失败渲染错误态与重试按钮，重试后恢复', async () => {
    mockFetch({ 'GET /api/v1/ops/jobs': (n) => (n === 1 ? { status: 500, body: { detail: 'boom' } } : { body: JOBS_NOT_RUNNING }) })
    renderPane()
    expect(await screen.findByTestId('eval-ops-pane-error')).toBeInTheDocument()
    expect(screen.queryByTestId('eval-ops-pane')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('eval-ops-retry'))
    expect(await screen.findByTestId('eval-ops-pane')).toBeInTheDocument()
    expect(screen.getAllByTestId(/^eval-ops-job-/)).toHaveLength(5)
  })

  /* ── 2. 手动补跑 ── */

  it('立即运行：先显示「运行中」，轮询 GET /jobs 到终态后展示刷新结果', async () => {
    const running = runRow({ run_id: 7, kind: 'manual', status: 'running', finished_at: null, summary: null })
    const finished = runRow({ run_id: 7, kind: 'manual', status: 'ok', summary: { issues: 3 }, finished_at: '2026-09-24T11:00:01' })
    const { calls } = mockFetch({
      'GET /api/v1/ops/jobs': (n) => ({
        body: n === 1
          ? JOBS_NOT_RUNNING
          : {
              ...JOBS_NOT_RUNNING,
              jobs: fiveJobs().map((j) => (
                j.job_id === 'integrity_check'
                  ? { ...j, last_run: n === 2 ? running : finished, history: [n === 2 ? running : finished] }
                  : j
              )),
            },
      }),
      'POST /api/v1/ops/jobs/:id/run': { status: 202, body: { run_id: 7 } },
    })
    renderPane()
    const card = await screen.findByTestId('eval-ops-job-integrity_check')
    fireEvent.click(within(card).getByTestId('eval-ops-run-integrity_check'))
    // 运行中反馈（按钮禁用 + 显式标记）
    expect(await within(card).findByTestId('eval-ops-running-integrity_check')).toBeInTheDocument()
    expect(within(card).getByTestId('eval-ops-run-integrity_check')).toBeDisabled()
    // 轮询到终态后展示刷新结果，运行中标记消失
    await waitFor(() => {
      expect(within(screen.getByTestId('eval-ops-job-integrity_check'))
        .getByTestId('eval-ops-last-run-integrity_check')).toHaveTextContent('issues=3')
    }, { timeout: 4000 })
    expect(screen.queryByTestId('eval-ops-running-integrity_check')).not.toBeInTheDocument()
    expect(callsOf(calls, 'POST', '/api/v1/ops/jobs/integrity_check/run')).toHaveLength(1)
    expect(callsOf(calls, 'GET', '/api/v1/ops/jobs').length).toBeGreaterThanOrEqual(2)
  })

  it('cohort 关闭时手动跑批被拒：展示服务端原因，不静默', async () => {
    mockFetch({
      'POST /api/v1/ops/jobs/:id/run': { status: 409, body: { detail: 'cohort_disabled' } },
    })
    renderPane()
    const card = await screen.findByTestId('eval-ops-job-cohort_batch')
    fireEvent.click(within(card).getByTestId('eval-ops-run-cohort_batch'))
    expect(await screen.findByTestId('eval-ops-error')).toHaveTextContent('开关未开启')
  })

  it('手动补跑 500（run_failed 详情）必须可见，不被吞成通用文案', async () => {
    mockFetch({
      'POST /api/v1/ops/jobs/:id/run': {
        status: 500,
        body: { detail: 'run_failed（run_id=7）：RuntimeError: db locked' },
      },
    })
    renderPane()
    const card = await screen.findByTestId('eval-ops-job-integrity_check')
    fireEvent.click(within(card).getByTestId('eval-ops-run-integrity_check'))
    const banner = await screen.findByTestId('eval-ops-error')
    expect(banner).toHaveTextContent('run_failed')
    expect(banner).toHaveTextContent('run_id=7')
    expect(banner).toHaveTextContent('db locked')
  })

  /* ── 3. cohort 开关 / 时刻 ── */

  it('开启 cohort 开关先弹确认（含成本估算与预算上限），取消零请求', async () => {
    const { calls } = mockFetch()
    renderPane()
    await openTab('cohort')
    expect(await screen.findByTestId('eval-ops-cohort-summary')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('eval-ops-cohort-toggle'))
    const dialog = await screen.findByTestId('eval-ops-confirm-dialog')
    expect(dialog).toHaveTextContent('开启 cohort')
    // 成本估算（来自预登记成本分型）+ 预算上限
    expect(screen.getByTestId('eval-ops-confirm-cost')).toHaveTextContent(/tokens/)
    expect(screen.getByTestId('eval-ops-confirm-cost')).toHaveTextContent('≈1.7M tokens/日')
    expect(screen.getByTestId('eval-ops-confirm-cost')).toHaveTextContent('pilot 实测 ≈166k tokens')
    expect(screen.getByTestId('eval-ops-confirm-budget')).toHaveTextContent(/2,000,000/)
    // 取消：零请求（不产生任何配置变更）
    fireEvent.click(screen.getByTestId('eval-ops-confirm-cancel'))
    await waitFor(() => expect(screen.queryByTestId('eval-ops-confirm-dialog')).not.toBeInTheDocument())
    expect(callsOf(calls, 'PUT', '/api/v1/ops/cohort')).toHaveLength(0)
    expect(callsOf(calls, 'POST', '/api/v1/ops')).toHaveLength(0)
  })

  it('确认后 PUT /cohort 提交开启，并展示审计与即时重排结果', async () => {
    const { calls } = mockFetch({
      'PUT /api/v1/ops/cohort': {
        body: { ...COHORT_OFF, enabled: true, audit_run_id: 42, rescheduled: true },
      },
    })
    renderPane()
    await openTab('cohort')
    fireEvent.click(await screen.findByTestId('eval-ops-cohort-toggle'))
    fireEvent.click(await screen.findByTestId('eval-ops-confirm-ok'))
    await waitFor(() => expect(callsOf(calls, 'PUT', '/api/v1/ops/cohort')).toHaveLength(1))
    expect(callsOf(calls, 'PUT', '/api/v1/ops/cohort')[0].body).toEqual({ enabled: true })
    expect(await screen.findByTestId('eval-ops-cohort-enabled')).toHaveTextContent('已开启')
    expect(screen.getByTestId('eval-ops-cohort-audit')).toHaveTextContent('42')
  })

  it('关闭开关无需确认，直接 PUT（即时零花费）', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/ops/jobs': { body: { ...JOBS_NOT_RUNNING, cohort: { ...COHORT_OFF, enabled: true } } },
      'PUT /api/v1/ops/cohort': { body: { ...COHORT_OFF, enabled: false, audit_run_id: 43, rescheduled: true } },
    })
    renderPane()
    await openTab('cohort')
    fireEvent.click(await screen.findByTestId('eval-ops-cohort-toggle'))
    await waitFor(() => expect(callsOf(calls, 'PUT', '/api/v1/ops/cohort')).toHaveLength(1))
    expect(screen.queryByTestId('eval-ops-confirm-dialog')).not.toBeInTheDocument()
    expect(callsOf(calls, 'PUT', '/api/v1/ops/cohort')[0].body).toEqual({ enabled: false })
  })

  it('保存跑批时刻 PUT {hour,minute}，越界本地拦截不出请求', async () => {
    const { calls } = mockFetch({
      'PUT /api/v1/ops/cohort': { body: { ...COHORT_OFF, hour: 19, minute: 30, audit_run_id: 44, rescheduled: true } },
    })
    renderPane()
    await openTab('cohort')
    const hour = await screen.findByTestId('eval-ops-cohort-hour')
    fireEvent.change(hour, { target: { value: '24' } })
    fireEvent.click(screen.getByTestId('eval-ops-cohort-save-time'))
    expect(await screen.findByTestId('eval-ops-error')).toHaveTextContent('时应在 0–23')
    expect(callsOf(calls, 'PUT', '/api/v1/ops/cohort')).toHaveLength(0)
    fireEvent.change(hour, { target: { value: '19' } })
    fireEvent.change(screen.getByTestId('eval-ops-cohort-minute'), { target: { value: '30' } })
    fireEvent.click(screen.getByTestId('eval-ops-cohort-save-time'))
    await waitFor(() => expect(callsOf(calls, 'PUT', '/api/v1/ops/cohort')).toHaveLength(1))
    expect(callsOf(calls, 'PUT', '/api/v1/ops/cohort')[0].body).toEqual({ hour: 19, minute: 30 })
  })

  /* ── 4. 回测与探针 ── */

  it('正式批：本地预登记无效时拒绝发起并列出缺失字段（零请求）', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/ops/prereg': { body: [preregVersion({ valid: false, issues: ['缺字段: MDE', '决策阈值为裸数字，缺换算依据'] })] },
    })
    renderPane()
    await openTab('backtest')
    fireEvent.change(await screen.findByTestId('eval-ops-backtest-codes'), { target: { value: '600519' } })
    await waitFor(() => expect(screen.getByTestId('eval-ops-backtest-prereg')).toHaveTextContent('无效'))
    fireEvent.click(screen.getByTestId('eval-ops-backtest-formal'))
    expect(await screen.findByTestId('eval-ops-backtest-refusal')).toHaveTextContent('MDE')
    expect(screen.getByTestId('eval-ops-backtest-refusal')).toHaveTextContent('缺换算依据')
    expect(screen.queryByTestId('eval-ops-confirm-dialog')).not.toBeInTheDocument()
    expect(callsOf(calls, 'POST', '/api/v1/ops/backtest')).toHaveLength(0)
  })

  it('正式批：服务端 409 门禁拒绝时展示原因（非静默/非转圈），且不落报告结果', async () => {    const { calls } = mockFetch({
      'POST /api/v1/ops/backtest': { status: 409, body: { detail: '距 as_of 仅 3 个交易日，干净窗口未过' } },
    })
    renderPane()
    await openTab('backtest')
    fireEvent.change(await screen.findByTestId('eval-ops-backtest-codes'), { target: { value: '600519' } })
    await waitFor(() => expect(screen.getByTestId('eval-ops-backtest-prereg')).toHaveTextContent('字段齐备'))
    fireEvent.click(screen.getByTestId('eval-ops-backtest-formal'))
    fireEvent.click(await screen.findByTestId('eval-ops-confirm-ok'))
    expect(await screen.findByTestId('eval-ops-error')).toHaveTextContent('干净窗口未过')
    expect(screen.queryByTestId('eval-ops-backtest-result')).not.toBeInTheDocument()
    expect(callsOf(calls, 'POST', '/api/v1/ops/backtest')).toHaveLength(1)
  })

  it('回测批：标的列表为空时本地拒绝，不发请求', async () => {
    const { calls } = mockFetch()
    renderPane()
    await openTab('backtest')
    fireEvent.click(await screen.findByTestId('eval-ops-backtest-pathway'))
    expect(await screen.findByTestId('eval-ops-error')).toHaveTextContent('标的列表不得为空')
    expect(callsOf(calls, 'POST', '/api/v1/ops/backtest')).toHaveLength(0)
  })

  it('通路验证批：确认后 202 派发，轮询 /runs/{id} 后展示结论与定位', async () => {
    mockFetch({
      'POST /api/v1/ops/backtest': { status: 202, body: { run_id: 31 } },
      'GET /api/v1/ops/runs/:id': {
        body: {
          ...runRow({ run_id: 31, job_id: 'backtest', status: 'ok', summary: null }),
          summary: {
            batch_kind: 'pathway', conclusion: '通路验证定位：机制可用', positioning: 'pathway',
            n_sample: 12, leakage_probe: { state: 'measurable', direction_hit_rate: 0.55, threshold: 0.6 },
            report_paths: ['evals/backtest/results/pathway-1.md'],
          },
        },
      },
    })
    renderPane()
    await openTab('backtest')
    fireEvent.change(await screen.findByTestId('eval-ops-backtest-codes'), { target: { value: '600519' } })
    await waitFor(() => expect(screen.getByTestId('eval-ops-backtest-prereg')).toHaveTextContent('字段齐备'))
    fireEvent.click(screen.getByTestId('eval-ops-backtest-pathway'))
    fireEvent.click(await screen.findByTestId('eval-ops-confirm-ok'))
    const result = await screen.findByTestId('eval-ops-backtest-result')
    expect(result).toHaveTextContent('通路验证定位')
    expect(result).toHaveTextContent('pathway-1.md')
  })

  it('正式批确认弹窗取消：零请求（不启动任何回放）', async () => {
    const { calls } = mockFetch()
    renderPane()
    await openTab('backtest')
    fireEvent.change(await screen.findByTestId('eval-ops-backtest-codes'), { target: { value: '600519' } })
    await waitFor(() => expect(screen.getByTestId('eval-ops-backtest-prereg')).toHaveTextContent('字段齐备'))
    fireEvent.click(screen.getByTestId('eval-ops-backtest-formal'))
    await screen.findByTestId('eval-ops-confirm-dialog')
    fireEvent.click(screen.getByTestId('eval-ops-confirm-cancel'))
    await waitFor(() => expect(screen.queryByTestId('eval-ops-confirm-dialog')).not.toBeInTheDocument())
    expect(callsOf(calls, 'POST', '/api/v1/ops/backtest')).toHaveLength(0)
    expect(screen.queryByTestId('eval-ops-backtest-result')).not.toBeInTheDocument()
  })

  it('探针单跑确认弹窗取消：零请求（不产生 LLM 调用）', async () => {
    const { calls } = mockFetch()
    renderPane()
    await openTab('backtest')
    fireEvent.change(await screen.findByTestId('eval-ops-probe-codes'), { target: { value: '600519' } })
    fireEvent.change(screen.getByTestId('eval-ops-probe-date'), { target: { value: '2024-06-03' } })
    fireEvent.click(screen.getByTestId('eval-ops-probe-submit'))
    await screen.findByTestId('eval-ops-confirm-dialog')
    fireEvent.click(screen.getByTestId('eval-ops-confirm-cancel'))
    await waitFor(() => expect(screen.queryByTestId('eval-ops-confirm-dialog')).not.toBeInTheDocument())
    expect(callsOf(calls, 'POST', '/api/v1/ops/probe')).toHaveLength(0)
    expect(screen.queryByTestId('eval-ops-probe-result')).not.toBeInTheDocument()
  })

  it('探针单跑：确认含规模估算；不可测态展示「不可测」而非 0', async () => {
    const { calls } = mockFetch({
      'POST /api/v1/ops/probe': { status: 202, body: { run_id: 51 } },
      'GET /api/v1/ops/runs/:id': {
        body: {
          ...runRow({ run_id: 51, job_id: 'probe', status: 'ok', summary: null }),
          summary: {
            state: 'unmeasurable', probe_n: 10, questions_per_ticker: 3,
            direction_hit_rate: null, magnitude_hit_rate: null, event_hit_rate: null,
            unknown_ratio: 1.0, threshold: 0.6, downgraded: false,
            per_window: [{ window: '2024-06-03', direction_hit_rate: null }],
          },
        },
      },
    })
    renderPane()
    await openTab('backtest')
    fireEvent.change(await screen.findByTestId('eval-ops-probe-codes'), { target: { value: '600519, 300308' } })
    fireEvent.change(screen.getByTestId('eval-ops-probe-date'), { target: { value: '2024-06-03' } })
    fireEvent.click(screen.getByTestId('eval-ops-probe-submit'))
    const cost = await screen.findByTestId('eval-ops-confirm-cost')
    expect(cost).toHaveTextContent('规模')
    fireEvent.click(screen.getByTestId('eval-ops-confirm-ok'))
    const result = await screen.findByTestId('eval-ops-probe-result')
    expect(result).toHaveTextContent('不可测')
    expect(within(result).getByTestId('eval-ops-probe-state')).toHaveTextContent('不可测')
    // 不可测态：方向命中率读数为空（「—」）而非折算 0
    expect(within(result).getByTestId('eval-ops-probe-direction')).toHaveTextContent('—')
    expect(callsOf(calls, 'POST', '/api/v1/ops/probe')).toHaveLength(1)
  })

  /* ── 5. 健康检查 ── */

  it('健康检查：门禁不过显式 FAIL + 实测值/阈值，总体不通过', async () => {
    mockFetch({
      'POST /api/v1/ops/health': { status: 202, body: { run_id: 61 } },
      'GET /api/v1/ops/runs/:id': {
        body: {
          ...runRow({ run_id: 61, job_id: 'health', status: 'ok', summary: null }),
          summary: {
            settled: 10, settlement_success_rate: 0.8333, unresolvable_rate: 0.1667,
            bookkeeping_completeness: 1.0, integrity_mismatches: 0,
            checks: { settlement_success: false, unresolvable: false, integrity: true },
            warnings: ['trace 缺失 1 行'], passed: false,
          },
        },
      },
    })
    renderPane()
    await openTab('health')
    fireEvent.click(await screen.findByTestId('eval-ops-health-run'))
    const table = await screen.findByTestId('eval-ops-health-result')
    expect(within(table).getByTestId('eval-ops-health-gate-settlement_success')).toHaveTextContent('FAIL')
    expect(within(table).getByTestId('eval-ops-health-gate-settlement_success')).toHaveTextContent('83.3%')
    expect(within(table).getByTestId('eval-ops-health-gate-settlement_success')).toHaveTextContent('90.0%')
    expect(within(table).getByTestId('eval-ops-health-gate-integrity')).toHaveTextContent('PASS')
    expect(screen.getByTestId('eval-ops-health-verdict')).toHaveTextContent('不通过')
  })

  it('不可判定率 0.11（略超 0.10 阈值）渲染 FAIL 与「≤ 10.0%」——常量被测试钉住', async () => {
    mockFetch({
      'POST /api/v1/ops/health': { status: 202, body: { run_id: 63 } },
      'GET /api/v1/ops/runs/:id': {
        body: {
          ...runRow({ run_id: 63, job_id: 'health', status: 'ok', summary: null }),
          summary: {
            settled: 89, settlement_success_rate: 0.89, unresolvable_rate: 0.11,
            bookkeeping_completeness: 1.0, integrity_mismatches: 0,
            checks: { settlement_success: false, unresolvable: false, integrity: true },
            warnings: [], passed: false,
          },
        },
      },
    })
    renderPane()
    await openTab('health')
    fireEvent.click(await screen.findByTestId('eval-ops-health-run'))
    const row = await screen.findByTestId('eval-ops-health-gate-unresolvable')
    expect(row).toHaveTextContent('11.0%')   // 实测值
    expect(row).toHaveTextContent('10.0%')   // 阈值展示（MAX_UNRESOLVABLE_RATE 字面同步）
    expect(row).toHaveTextContent('≤')
    expect(row).toHaveTextContent('FAIL')
    expect(screen.getByTestId('eval-ops-health-verdict')).toHaveTextContent('不通过')
  })

  it('健康检查：读数缺失展示「无读数」，不冒充 0%/100%', async () => {
    mockFetch({
      'POST /api/v1/ops/health': { status: 202, body: { run_id: 62 } },
      'GET /api/v1/ops/runs/:id': {
        body: {
          ...runRow({ run_id: 62, job_id: 'health', status: 'ok', summary: null }),
          summary: {
            settled: 0, settlement_success_rate: null, unresolvable_rate: null,
            bookkeeping_completeness: null, integrity_mismatches: 3,
            checks: { settlement_success: false, unresolvable: true, integrity: false },
            warnings: [], passed: false,
          },
        },
      },
    })
    renderPane()
    await openTab('health')
    fireEvent.click(await screen.findByTestId('eval-ops-health-run'))
    const row = await screen.findByTestId('eval-ops-health-gate-settlement_success')
    expect(row).toHaveTextContent('无读数')
    // 实测值列不得以 0% 冒充（无读数 → 「—」）
    expect(within(row).getAllByRole('cell')[1]).toHaveTextContent('—')
    expect(screen.getByTestId('eval-ops-health-gate-unresolvable')).toHaveTextContent('无读数')
  })

  /* ── 6. 报告注册表 ── */

  it('报告注册表：status 徽章 + 定位标签 + 探针读数 + 取代者路径', async () => {
    mockFetch({
      'GET /api/v1/ops/reports': {
        body: [
          {
            name: 'pilot-2023-shock-pathway', path: 'evals/backtest/results/pilot-2023-shock-pathway.md',
            status: 'active', target: null, positioning: 'pathway', probe_direction_hit_rate: 0.62,
          },
          {
            name: 'formal-2024-a', path: 'evals/backtest/results/formal-2024-a.md',
            status: 'superseded-by', target: 'evals/backtest/results/formal-2024-b.md',
            positioning: 'upper_bound', probe_direction_hit_rate: 0.71,
          },
        ],
      },
    })
    renderPane()
    await openTab('reports')
    const table = await screen.findByTestId('eval-ops-reports')
    expect(within(table).getByTestId('eval-ops-report-pilot-2023-shock-pathway')).toHaveTextContent('生效中')
    expect(within(table).getByTestId('eval-ops-report-pilot-2023-shock-pathway')).toHaveTextContent('通路验证')
    expect(within(table).getByTestId('eval-ops-report-pilot-2023-shock-pathway')).toHaveTextContent('62.0%')
    const superseded = within(table).getByTestId('eval-ops-report-formal-2024-a')
    expect(superseded).toHaveTextContent('已被取代')
    expect(superseded).toHaveTextContent('formal-2024-b.md')
    expect(superseded).toHaveTextContent('泄漏污染下的上界证据')
  })

  /* ── 7. 预登记与口径 ── */

  it('预登记：最新版本七字段表单可编辑，保存走 PUT 新版本', async () => {
    const { calls } = mockFetch({
      'PUT /api/v1/ops/prereg': {
        status: 201, body: { path: 'evals/ablation/preregister/2026-09-24-outcome-prereg-v1.md' },
      },
    })
    renderPane()
    await openTab('prereg')
    const form = await screen.findByTestId('eval-ops-prereg-form')
    const fields = ['主指标', 'MDE', '决策阈值', '样本量依据', '停止规则', '成本分型', '泄漏控制']
    for (const f of fields) expect(within(form).getByTestId(`eval-ops-prereg-${f}`)).toBeInTheDocument()
    // 回填最新版本值
    expect(within(form).getByTestId('eval-ops-prereg-MDE')).toHaveValue(PREREG_FIELDS['MDE'])
    fireEvent.change(within(form).getByTestId('eval-ops-prereg-MDE'), { target: { value: 'n=30 → 4.5pp（依据见 §4）' } })
    fireEvent.click(screen.getByTestId('eval-ops-prereg-save'))
    await waitFor(() => expect(callsOf(calls, 'PUT', '/api/v1/ops/prereg')).toHaveLength(1))
    const body = callsOf(calls, 'PUT', '/api/v1/ops/prereg')[0].body as Record<string, string>
    expect(Object.keys(body).sort()).toEqual([...fields].sort())
    expect(body['MDE']).toBe('n=30 → 4.5pp（依据见 §4）')
    expect(await screen.findByTestId('eval-ops-prereg-saved')).toHaveTextContent('2026-09-24-outcome-prereg-v1.md')
  })

  it('预登记：服务端 422 缺字段时展示原因', async () => {
    mockFetch({
      'PUT /api/v1/ops/prereg': { status: 422, body: { detail: '预登记无效:缺字段: MDE' } },
    })
    renderPane()
    await openTab('prereg')
    fireEvent.click(await screen.findByTestId('eval-ops-prereg-save'))
    expect(await screen.findByTestId('eval-ops-error')).toHaveTextContent('缺字段: MDE')
  })

  it('预登记：表单缺字段时本地拒绝保存，不写入任何版本', async () => {
    const { calls } = mockFetch({
      'GET /api/v1/ops/prereg': { body: [preregVersion({ fields: { ...PREREG_FIELDS, MDE: '' } })] },
    })
    renderPane()
    await openTab('prereg')
    const mde = await screen.findByTestId('eval-ops-prereg-MDE')
    expect(mde).toHaveValue('')
    fireEvent.click(screen.getByTestId('eval-ops-prereg-save'))
    expect(await screen.findByTestId('eval-ops-error')).toHaveTextContent('预登记缺字段：MDE')
    expect(callsOf(calls, 'PUT', '/api/v1/ops/prereg')).toHaveLength(0)
  })

  it('预登记：已锁定版本只读 + 展示锁定原因 + 引导新建版本', async () => {
    mockFetch({
      'GET /api/v1/ops/prereg': {
        body: [preregVersion({ locked: true, readings: ['evals/backtest/results/formal-1.md'] })],
      },
    })
    renderPane()
    await openTab('prereg')
    const locked = await screen.findByTestId('eval-ops-prereg-locked')
    expect(locked).toHaveTextContent('已有读数，锁定')
    expect(locked).toHaveTextContent('新建版本')
    expect(within(screen.getByTestId('eval-ops-prereg-form')).getByTestId('eval-ops-prereg-MDE')).toBeDisabled()
    // 引导新建版本 → 恢复可编辑空表单
    fireEvent.click(screen.getByTestId('eval-ops-prereg-new'))
    expect(within(screen.getByTestId('eval-ops-prereg-form')).getByTestId('eval-ops-prereg-MDE')).not.toBeDisabled()
    expect(within(screen.getByTestId('eval-ops-prereg-form')).getByTestId('eval-ops-prereg-MDE')).toHaveValue('')
  })

  it('口径：展示当前旋钮现值，提交生成 delta 草稿并明示「草稿待评审」', async () => {
    const { calls } = mockFetch({
      'POST /api/v1/ops/caliber-draft': {
        status: 201, body: { draft_dir: 'openspec/changes/ops-caliber-draft-20260924-120000' },
      },
    })
    renderPane()
    await openTab('prereg')
    const knobs = await screen.findByTestId('eval-ops-caliber')
    expect(within(knobs).getByTestId('eval-ops-knob-LEAKAGE_PROBE_THRESHOLD')).toHaveValue('0.6')
    expect(knobs).toHaveTextContent('evals/outcome/caliber.py')
    // 未改动旋钮 → 零请求（避免空变更 422）
    fireEvent.click(screen.getByTestId('eval-ops-caliber-submit'))
    expect(await screen.findByTestId('eval-ops-caliber-hint')).toHaveTextContent('未修改任何旋钮')
    expect(callsOf(calls, 'POST', '/api/v1/ops/caliber-draft')).toHaveLength(0)
    // 改探针阈值 0.60 → 0.55 并提交
    fireEvent.change(screen.getByTestId('eval-ops-knob-LEAKAGE_PROBE_THRESHOLD'), { target: { value: '0.55' } })
    fireEvent.click(screen.getByTestId('eval-ops-caliber-submit'))
    await waitFor(() => expect(callsOf(calls, 'POST', '/api/v1/ops/caliber-draft')).toHaveLength(1))
    expect(callsOf(calls, 'POST', '/api/v1/ops/caliber-draft')[0].body).toEqual({ LEAKAGE_PROBE_THRESHOLD: 0.55 })
    const draft = await screen.findByTestId('eval-ops-caliber-draft')
    expect(draft).toHaveTextContent('ops-caliber-draft-20260924-120000')
    expect(draft).toHaveTextContent('草稿待评审，生效须走 delta 流程')
  })

  it('口径：已有同旋钮草稿（409 draft_exists）时展示原因', async () => {
    mockFetch({
      'POST /api/v1/ops/caliber-draft': { status: 409, body: { detail: 'draft_exists' } },
    })
    renderPane()
    await openTab('prereg')
    fireEvent.change(await screen.findByTestId('eval-ops-knob-NEUTRAL_BAND'), { target: { value: '0.03' } })
    fireEvent.click(screen.getByTestId('eval-ops-caliber-submit'))
    expect(await screen.findByTestId('eval-ops-error')).toHaveTextContent('已有未处理草稿')
  })
})

describe('设置中心注册「评估运维」分区（add-eval-ops-console Task 6.3）', () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('左侧导航含「评估运维」，点击后挂载 eval-ops-pane', async () => {
    mockFetch()
    render(
      <SettingsCenterPage
        config={emptyLlmConfig()}
        backendDefaults={{ model: 'deepseek/deepseek-chat', baseUrl: '', thinking: 'enabled' }}
        profileStore={{ profiles: [], activeId: '' }}
        capability={null}
        onProbeCapability={vi.fn()}
        onSave={vi.fn()}
        onSaveAs={vi.fn()}
        onSwitchProfile={vi.fn()}
        onDeleteProfile={vi.fn()}
        onBack={vi.fn()}
      />,
    )
    expect(screen.getByTestId('settings-nav-eval-ops')).toHaveTextContent('评估运维')
    fireEvent.click(screen.getByTestId('settings-nav-eval-ops'))
    expect(await screen.findByTestId('eval-ops-pane')).toBeInTheDocument()
    // 六页签齐备
    for (const id of ['schedule', 'cohort', 'backtest', 'health', 'reports', 'prereg']) {
      expect(screen.getByTestId(`eval-ops-tab-${id}`)).toBeInTheDocument()
    }
  })
})

describe('降级载荷披露', () => {
  it('运行状态读取异常时展示原因，而非显示成「从未跑过」', async () => {
    mockFetch({
      'GET /api/v1/ops/jobs': {
        body: { ...JOBS_NOT_RUNNING, error: 'last_job_run: database is locked' },
      },
    })
    render(<EvalOpsPane />)
    const banner = await screen.findByTestId('eval-ops-degraded')
    expect(banner).toHaveTextContent('database is locked')
  })

  it('健康载荷不渲染降级提示', async () => {
    mockFetch({})
    render(<EvalOpsPane />)
    await screen.findByTestId('eval-ops-pane')
    expect(screen.queryByTestId('eval-ops-degraded')).not.toBeInTheDocument()
  })
})
