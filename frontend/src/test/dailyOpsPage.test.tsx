import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import App from '../App'
import { DailyOpsPage } from '../pages/ops/DailyOpsPage'
import type { OpsJobStatus, OpsJobsResponse, OpsRun, ScreeningBatch } from '../types'

/**
 * 日批运维页单测（delta add-daily-ops-page T2.2；spec frontend delta「日批运维页」）。
 *
 * - 当日时间线：数据驱动（/api/v1/ops/jobs 载荷），按排程时刻升序——含注册表外
 *   任务（证明不硬编码任务清单）；
 * - 五态徽章映射：ok→active、failed→failed、未跑→retired、running→running、
 *   skipped-disabled→degraded；最近运行摘要与失败原因逐字；
 * - 一键补跑：POST /api/v1/ops/jobs/{id}/run → 刷新该任务最近运行；409
 *   already_running / cohort_disabled 与 500 按端点返回如实提示；
 * - 选股批次历史表：日期倒序 + 各层计数/池数/错误逐字；空态；
 * - 评估运维跳转；路由 /ops 直达与侧边栏收起态入口（App 壳层）。
 *
 * fetch 全 mock（页面契约层单测）；「今日」经 fake timers 钉死
 * （vi.setSystemTime，同 selectSession.test.tsx 先例）。
 */

const TODAY = '2026-10-14'

function opsRun(overrides: Partial<OpsRun> = {}): OpsRun {
  return {
    run_id: 1,
    job_id: 'screening_daily',
    kind: 'scheduled',
    source: 'scheduled',
    status: 'ok',
    started_at: `${TODAY}T15:40:00`,
    finished_at: `${TODAY}T15:43:00`,
    summary: { pool_size: 2 },
    error: null,
    ...overrides,
  }
}

function opsJob(jobId: string, hour: number, minute: number, overrides: Partial<OpsJobStatus> = {}): OpsJobStatus {
  return {
    job_id: jobId,
    label: jobId,
    schedule: { day_of_week: 'mon-fri', hour, minute, timezone: 'Asia/Shanghai' },
    next_fire_time: null,
    last_run: null,
    history: [],
    ...overrides,
  }
}

function jobsBody(jobs: OpsJobStatus[], overrides: Partial<OpsJobsResponse> = {}): OpsJobsResponse {
  return {
    scheduler_running: true,
    jobs,
    cohort: { enabled: true, hour: 18, minute: 0, budget_tokens: 0, today_spend: 0, today_success: 0, today_failure: 0 },
    ...overrides,
  }
}

function batchRow(overrides: Partial<ScreeningBatch>): ScreeningBatch {
  return {
    trade_date: '2026-10-13',
    status: 'ok',
    error: null,
    started_at: '2026-10-13T15:40:00',
    finished_at: '2026-10-13T15:43:00',
    l1_passed: 20,
    l2_top: 9,
    l3_keep: 2,
    pool_size: 2,
    ...overrides,
  }
}

function mockFetch(
  opts: {
    jobs?: OpsJobsResponse[]
    batches?: ScreeningBatch[]
    batchesStatus?: number
    run?: { status: number; body?: unknown }
  } = {}
) {
  const respond = (body: unknown, status = 200) =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })
    )
  const jobsBodies = opts.jobs ?? [jobsBody([])]
  let jobCalls = 0
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/api/v1/ops/jobs' && (!init || !init.method || init.method === 'GET')) {
      const body = jobsBodies[Math.min(jobCalls, jobsBodies.length - 1)]
      jobCalls += 1
      return respond(body)
    }
    if (url.startsWith('/api/screening/batches')) {
      return respond(opts.batches ?? [], opts.batchesStatus ?? 200)
    }
    if (/^\/api\/v1\/ops\/jobs\/[^/]+\/run$/.test(url)) {
      const run = opts.run ?? { status: 202, body: { run_id: 9 } }
      return respond(run.body ?? { run_id: 9 }, run.status)
    }
    if (url === '/api/sessions') return respond({ sessions: [] })
    if (url === '/api/snapshot/status') {
      return respond({ latest_date: null, stale: false, message: null, last_error: null, last_error_date: null })
    }
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function timelineOrder(): string[] {
  return screen
    .getAllByTestId(/^page-shell-batch-/)
    .map((node) => (node.getAttribute('data-testid') ?? '').replace('page-shell-batch-', ''))
}

describe('日批运维页（delta add-daily-ops-page T2.2）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    // 「今日」钉死为 2026-10-14 16:00（本机时区）：五态判定依赖运行行的日期
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date(2026, 9, 14, 16, 0, 0))
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  it('时间线按排程时刻升序渲染，任务清单数据驱动（含注册表外任务）', async () => {
    mockFetch({
      jobs: [
        jobsBody([
          opsJob('daily_marking', 16, 30, { label: '盯市' }),
          opsJob('market_snapshot_daily', 15, 30, { label: '行情快照' }),
          // 注册表外任务：页面必须照渲染（不硬编码 10 项清单）
          opsJob('zzz_custom', 21, 5, { label: '假任务' }),
          opsJob('screening_daily', 15, 40, { label: '选股' }),
          opsJob('factor_decay_monitor', 15, 45, { label: '因子衰减监控' }),
        ]),
      ],
    })
    render(<DailyOpsPage onBack={() => {}} onOpenEvalOps={() => {}} />)

    await screen.findByTestId('ops-job-title-market_snapshot_daily')
    expect(timelineOrder()).toEqual([
      'market_snapshot_daily', // 15:30
      'screening_daily', // 15:40
      'factor_decay_monitor', // 15:45
      'daily_marking', // 16:30
      'zzz_custom', // 21:05（非注册表任务同样渲染）
    ])
    // 每项展示排程时刻与任务名（均来自载荷）
    expect(screen.getByTestId('ops-job-title-screening_daily').textContent).toContain('15:40')
    expect(screen.getByTestId('ops-job-title-screening_daily').textContent).toContain('选股')
    expect(screen.getByTestId('ops-job-title-zzz_custom').textContent).toContain('假任务')
  })

  it('五态徽章映射：ok→active、failed→failed、未跑→retired、running→running、skipped-disabled→degraded', async () => {
    mockFetch({
      jobs: [
        jobsBody([
          opsJob('a_ok', 15, 30, { last_run: opsRun({ job_id: 'a_ok', status: 'ok' }) }),
          opsJob('b_failed', 15, 35, {
            last_run: opsRun({
              job_id: 'b_failed',
              status: 'failed',
              error: 'RuntimeError: LLM 网关整体不可用',
            }),
          }),
          opsJob('c_running', 15, 40, { last_run: opsRun({ job_id: 'c_running', status: 'running', finished_at: null }) }),
          opsJob('d_skipped', 15, 45, {
            last_run: opsRun({ job_id: 'd_skipped', status: 'skipped-disabled', summary: { reason: 'cohort_disabled' } }),
          }),
          // 未跑 ①：最近运行是昨天（今日无运行行）②：从未跑过且未到排程时刻
          opsJob('e_stale', 16, 0, {
            last_run: opsRun({ job_id: 'e_stale', status: 'ok', started_at: '2026-10-13T16:00:00', finished_at: '2026-10-13T16:01:00' }),
          }),
          opsJob('f_future', 18, 0),
        ]),
      ],
    })
    render(<DailyOpsPage onBack={() => {}} onOpenEvalOps={() => {}} />)
    await screen.findByTestId('ops-job-title-a_ok')

    const badgeStatus = (jobId: string) =>
      within(screen.getByTestId(`page-shell-batch-${jobId}`))
        .getByTestId('status-badge')
        .getAttribute('data-status')
    const badgeText = (jobId: string) =>
      within(screen.getByTestId(`page-shell-batch-${jobId}`)).getByTestId('status-badge').textContent

    expect(badgeStatus('a_ok')).toBe('active')
    expect(badgeStatus('b_failed')).toBe('failed')
    expect(badgeStatus('c_running')).toBe('running')
    expect(badgeStatus('d_skipped')).toBe('degraded')
    expect(badgeStatus('e_stale')).toBe('retired')
    expect(badgeStatus('f_future')).toBe('retired')
    // 五态文案逐字（徽章 label 覆盖：成功/失败/未跑/运行中/跳过）
    expect(badgeText('a_ok')).toBe('成功')
    expect(badgeText('b_failed')).toBe('失败')
    expect(badgeText('c_running')).toBe('运行中')
    expect(badgeText('d_skipped')).toBe('跳过')
    expect(badgeText('e_stale')).toBe('未跑')
    expect(badgeText('f_future')).toBe('未跑')

    // 摘要：成功行含状态与时刻；失败行错误逐字；未跑行如实区分「今日未跑」与「未到排程时刻」
    expect(screen.getByTestId('ops-job-summary-a_ok').textContent).toContain('成功')
    expect(screen.getByTestId('ops-job-summary-a_ok').textContent).toContain('2026-10-14T15:43:00')
    expect(screen.getByTestId('ops-job-error-b_failed').textContent).toBe('RuntimeError: LLM 网关整体不可用')
    expect(screen.getByTestId('ops-job-summary-e_stale').textContent).toContain('今日未跑')
    expect(screen.getByTestId('ops-job-summary-e_stale').textContent).toContain('2026-10-13')
    expect(screen.getByTestId('ops-job-summary-f_future').textContent).toContain('尚未到排程时刻')
  })

  it('一键补跑：POST 补跑端点 → 成功后刷新该任务最近运行', async () => {
    const fetchMock = mockFetch({
      jobs: [
        jobsBody([
          opsJob('screening_daily', 15, 40, {
            label: '选股',
            last_run: opsRun({ job_id: 'screening_daily', status: 'failed', error: 'LLM 网关整体不可用' }),
          }),
        ]),
        // 补跑后刷新：新的成功运行行
        jobsBody([
          opsJob('screening_daily', 15, 40, {
            label: '选股',
            last_run: opsRun({ job_id: 'screening_daily', run_id: 2, status: 'ok', started_at: `${TODAY}T16:01:00`, finished_at: `${TODAY}T16:02:00` }),
          }),
        ]),
      ],
    })
    render(<DailyOpsPage onBack={() => {}} onOpenEvalOps={() => {}} />)
    await screen.findByTestId('ops-job-title-screening_daily')
    expect(
      within(screen.getByTestId('page-shell-batch-screening_daily')).getByTestId('status-badge')
    ).toHaveAttribute('data-status', 'failed')

    fireEvent.click(screen.getByTestId('ops-job-run-screening_daily'))

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/v1/ops/jobs/screening_daily/run', expect.objectContaining({ method: 'POST' }))
    )
    await waitFor(() =>
      expect(
        within(screen.getByTestId('page-shell-batch-screening_daily')).getByTestId('status-badge')
      ).toHaveAttribute('data-status', 'active')
    )
    expect(screen.getByTestId('ops-job-summary-screening_daily').textContent).toContain('2026-10-14T16:02:00')
    expect(screen.queryByTestId('ops-job-error-screening_daily')).toBeNull()
  })

  it('补跑被拒（409 已在运行 / cohort 关闭 / 500 运行失败）按端点返回如实提示', async () => {
    const fetchMock = mockFetch({
      jobs: [
        jobsBody([
          opsJob('screening_daily', 15, 40, { label: '选股', last_run: opsRun({ job_id: 'screening_daily', status: 'running', finished_at: null }) }),
          opsJob('cohort_batch', 18, 0, { label: 'cohort 跑批' }),
        ]),
      ],
    })
    render(<DailyOpsPage onBack={() => {}} onOpenEvalOps={() => {}} />)
    await screen.findByTestId('ops-job-title-screening_daily')

    // 409 already_running：文案逐字说明「已在运行」，不得伪装成已触发
    ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce(() =>
      Promise.resolve(new Response(JSON.stringify({ detail: 'already_running' }), { status: 409 }))
    )
    fireEvent.click(screen.getByTestId('ops-job-run-screening_daily'))
    await waitFor(() =>
      expect(screen.getByTestId('ops-job-notice-screening_daily').textContent).toContain('该任务已在运行')
    )

    // 409 cohort_disabled：关闭语义如实（零 LLM 调用）
    ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce(() =>
      Promise.resolve(new Response(JSON.stringify({ detail: 'cohort_disabled' }), { status: 409 }))
    )
    fireEvent.click(screen.getByTestId('ops-job-run-cohort_batch'))
    await waitFor(() =>
      expect(screen.getByTestId('ops-job-notice-cohort_batch').textContent).toContain('cohort 开关未开启')
    )

    // 500 运行失败：原因逐字透传（detail）
    ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce(() =>
      Promise.resolve(new Response(JSON.stringify({ detail: 'run_failed（run_id=7）：RuntimeError: 快照缺失' }), { status: 500 }))
    )
    fireEvent.click(screen.getByTestId('ops-job-run-screening_daily'))
    await waitFor(() =>
      expect(screen.getByTestId('ops-job-notice-screening_daily').textContent).toContain('run_failed（run_id=7）：RuntimeError: 快照缺失')
    )
    expect(fetchMock).toHaveBeenCalled()
  })

  it('选股批次历史表：日期倒序 + 各层计数/池数 + 失败原因逐字', async () => {
    mockFetch({
      jobs: [jobsBody([opsJob('screening_daily', 15, 40, { label: '选股' })])],
      batches: [
        batchRow({ trade_date: '2026-10-14', status: 'failed', error: 'RuntimeError: LLM 网关整体不可用', l1_passed: 0, l2_top: 0, l3_keep: 0, pool_size: 0 }),
        batchRow({ trade_date: '2026-10-13' }),
      ],
    })
    render(<DailyOpsPage onBack={() => {}} onOpenEvalOps={() => {}} />)

    const table = await screen.findByTestId('ops-batches-table')
    const rows = within(table).getAllByTestId(/^ops-batch-row-/)
    expect(rows.map((row) => row.getAttribute('data-testid'))).toEqual([
      'ops-batch-row-2026-10-14',
      'ops-batch-row-2026-10-13',
    ])
    // 成功行：整行单元格逐字（日期 / 状态徽章文案 / 各层计数 / 池数 / 错误占位）
    const okRowEl = screen.getByTestId('ops-batch-row-2026-10-13')
    expect(Array.from(okRowEl.querySelectorAll('td')).map((td) => td.textContent)).toEqual([
      '2026-10-13',
      '成功',
      '20',
      '9',
      '2',
      '2',
      '—',
    ])
    expect(within(okRowEl).getByTestId('status-badge')).toHaveAttribute('data-status', 'active')
    // 失败行：原因逐字 + failed 徽章
    const failedRow = within(screen.getByTestId('ops-batch-row-2026-10-14'))
    expect(failedRow.getByTestId('ops-batch-error-2026-10-14').textContent).toBe('RuntimeError: LLM 网关整体不可用')
    expect(failedRow.getByTestId('status-badge')).toHaveAttribute('data-status', 'failed')
  })

  it('空态如实：无排程任务与无批次历史各自空态', async () => {
    mockFetch({ jobs: [jobsBody([])], batches: [] })
    render(<DailyOpsPage onBack={() => {}} onOpenEvalOps={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('page-shell-empty')).toBeTruthy())
    expect(screen.getByTestId('page-shell-empty-message').textContent).toContain('无排程任务')
    expect(screen.getByTestId('ops-batches-empty').textContent).toContain('暂无选股批次历史')
    expect(screen.queryByTestId('ops-batches-table')).toBeNull()
  })

  it('批次历史取数失败 → 显式报错（与「暂无历史」空态严格区分）', async () => {
    mockFetch({ jobs: [jobsBody([opsJob('screening_daily', 15, 40, { label: '选股' })])], batchesStatus: 500 })
    render(<DailyOpsPage onBack={() => {}} onOpenEvalOps={() => {}} />)

    expect(await screen.findByTestId('ops-batches-error')).toBeTruthy()
    expect(screen.queryByTestId('ops-batches-empty')).toBeNull()
    // 时间线不受批次通道故障拖垮（区块独立）
    expect(screen.getByTestId('ops-job-title-screening_daily')).toBeTruthy()
  })

  it('评估运维跳转：点击链接回调进设置页（全量控制台）', async () => {
    const onOpenEvalOps = vi.fn()
    mockFetch({ jobs: [jobsBody([])] })
    render(<DailyOpsPage onBack={() => {}} onOpenEvalOps={onOpenEvalOps} />)

    await screen.findByTestId('daily-ops-page')
    fireEvent.click(screen.getByTestId('ops-open-eval-ops'))
    expect(onOpenEvalOps).toHaveBeenCalledTimes(1)
  })

  it('直达 /ops 渲染日批运维页（刷新保持路由语义）', async () => {
    mockFetch()
    window.history.pushState({}, '', '/ops')
    render(<App />)

    expect(await screen.findByTestId('daily-ops-page')).toBeTruthy()
  })

  it('侧边栏收起态入口点击进入 /ops', async () => {
    localStorage.setItem('fa_api_key', 'test-key')
    mockFetch()
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('sidebar-trigger')).toBeTruthy())
    fireEvent.click(screen.getByTestId('sidebar-trigger'))
    const entry = await screen.findByTestId('sidebar-ops-collapsed')
    expect(entry.getAttribute('aria-label')).toBe('日批运维')
    fireEvent.click(entry)

    expect(window.location.pathname).toBe('/ops')
    expect(await screen.findByTestId('daily-ops-page')).toBeTruthy()
  })
})
