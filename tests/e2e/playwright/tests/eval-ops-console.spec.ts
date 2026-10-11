import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

/**
 * 评估运维分区 E2E 门禁（delta add-eval-ops-console，spec R7「评估运维分区前端」）
 *
 * 环境：`playwright.eval-ops.config.ts` 专属套件（issue #252 物理隔离）——
 * TESTING=1 真后端（:8005）+ vite dev（:5178）+ 专属测试库
 * `data/test-e2e-eval-ops.db`。本套件手动补跑用例在服务端同步执行 20-60s，
 * 在默认套件并行运行会独占单进程后端致流式用例随机红，故拆独立端口对。
 * **不 mock 任何业务接口**：`page.request` 只做「读真实状态」的复核（cohort 配置、运行历史），
 * 断言文本一律取自真实 DOM / 真实响应，不使用 `route.fulfill`。
 *
 * 环境前提（本套件不自行设置，若漂移会红）：
 * 1. TESTING=1 → 后端不启动调度器，`GET /api/v1/ops/jobs` 的 `scheduler_running` 恒为 false。
 * 2. `data/test-e2e-eval-ops.db`（gitignored 专属测试库）里 cohort 开关为默认「未开启」——
 *    本套件全程不确认开启（取消路径），故不会自我污染；人工在测试库里点过「确认开启」后
 *    需删除该库再跑（cohort 已开启时「关闭时拒绝」用例自动 skip，见该用例注释）。
 *
 * 断言纪律：**只断言稳定终态**。`POST /api/v1/ops/jobs/{id}/run` 在服务端同步执行完才返回，
 * 「运行中」只在请求在途的亚秒窗口内可见，属过渡态（前端 reviewer 已标记为 flake 源），
 * 故本套件不断言该状态，只断言终态与拒绝态。
 *
 * 超时预算（describe 级 120s）：ops 端点本身很快（实测单发与 12 并发均 ≈0.25s），
 * 但专属套件仍可能与 CI 上其他 config 步骤共享机器资源，`page.goto`
 * 冷启动 vite dev 的 ESM 模块图可达 20–30s——默认 30s 用例预算会把「环境慢」误报成
 * 「功能坏」。断言强度不变，只放大时间预算。
 */
test.describe.configure({ timeout: 120_000 })

// 五任务 id（顺序 = 后端 JOB_IDS）
const JOB_IDS = [
  'decision_settle_daily',
  'daily_marking',
  'metrics_snapshot',
  'integrity_check',
  'cohort_batch',
] as const

const JOBS_URL = '/api/v1/ops/jobs'
const COHORT_URL = '/api/v1/ops/cohort'

// 依赖服务端往返的断言超时（默认 5s 在并行负载下过紧；实测端点本体 ≈0.25s，慢的是排队）
const NET_TIMEOUT = 30_000

type OpsRun = {
  run_id: number
  job_id: string
  kind: string
  status: string
  error: string | null
}

/**
 * 打开设置中心「评估运维」分区（设置入口 = `/settings` 页面，与 add-agent-settings-center 同款）
 *
 * 分区可见等待放宽到 60s：vite dev 冷启动模块图在 CI 共享资源下变慢，
 * `GET /api/v1/ops/jobs` 就绪可达 20–30s，此时分区停在
 * 「评估运维加载中…」占位态（该占位态无 testid）——不是功能缺陷，是默认 expect 超时过紧。
 */
async function openEvalOps(page: Page): Promise<void> {
  await page.goto('/settings')
  await page.getByTestId('settings-nav-eval-ops').click()
  await expect(page.getByTestId('eval-ops-pane')).toBeVisible({ timeout: 60_000 })
}

/** 读某任务的最近一次运行行（真实 API，非 mock）；无运行记录 → null */
async function latestRun(request: APIRequestContext, jobId: string): Promise<OpsRun | null> {
  const resp = await request.get(JOBS_URL)
  expect(resp.status()).toBe(200)
  const body = (await resp.json()) as { jobs: Array<{ job_id: string; last_run: OpsRun | null }> }
  const job = body.jobs.find((item) => item.job_id === jobId)
  expect(job, `GET /jobs 应含任务 ${jobId}`).toBeTruthy()
  return job?.last_run ?? null
}

/** 读 cohort 配置真源（服务端持久化状态） */
async function cohortState(request: APIRequestContext): Promise<{ enabled: boolean; hour: number; minute: number }> {
  const resp = await request.get(COHORT_URL)
  expect(resp.status()).toBe(200)
  const body = (await resp.json()) as { enabled: boolean; hour: number; minute: number }
  return { enabled: body.enabled, hour: body.hour, minute: body.minute }
}

/** 回测报告注册表的报告名集合（拒绝批次的「未落新报告」证据；只读真实目录扫描结果） */
async function reportNames(request: APIRequestContext): Promise<string[]> {
  const resp = await request.get('/api/v1/ops/reports')
  expect(resp.status()).toBe(200)
  const body = (await resp.json()) as Array<{ name: string }>
  return body.map((item) => item.name)
}

/** cohort_batch 的配置变更审计行数（PUT /cohort 是唯一来源 → 计数可判「是否写入过配置」） */
async function cohortAuditCount(request: APIRequestContext): Promise<number> {
  const resp = await request.get(JOBS_URL)
  expect(resp.status()).toBe(200)
  const body = (await resp.json()) as {
    jobs: Array<{ job_id: string; history: OpsRun[] }>
  }
  const job = body.jobs.find((item) => item.job_id === 'cohort_batch')
  return (job?.history ?? []).filter((row) => row.kind === 'config-change').length
}

test.describe('评估运维分区（设置中心）', () => {
  test('分区渲染五任务卡片与「调度器未运行」显式横幅', async ({ page }) => {
    await openEvalOps(page)

    // TESTING=1 → 调度器未启动：显式提示，不得空白、不得以空列表冒充「一切正常」
    const banner = page.getByTestId('eval-ops-not-running')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('调度器未运行')

    // 五任务卡片（`:not(...)` 排除 `eval-ops-job-audit-*`——审计行 testid 与本前缀重合）
    const cards = page.locator('[data-testid^="eval-ops-job-"]:not([data-testid^="eval-ops-job-audit-"])')
    await expect(cards).toHaveCount(JOB_IDS.length)
    for (const jobId of JOB_IDS) {
      const card = page.getByTestId(`eval-ops-job-${jobId}`)
      await expect(card).toBeVisible()
      // 每卡片三要素：排程 / 下次触发 / 最近一次运行
      await expect(card).toContainText('排程')
      await expect(card).toContainText('下次触发')
      await expect(card).toContainText('最近一次运行')
      // 调度器未运行 → 下次触发为显式占位，不冒充时间
      await expect(page.getByTestId(`eval-ops-next-${jobId}`)).toContainText('调度器未运行')
    }

    // cohort 开关 / 跑批时刻 / 今日花费与预算同屏（默认未开启 = env 无引导值时的内置默认）
    const summary = page.getByTestId('eval-ops-cohort-summary')
    await expect(summary).toContainText('跑批时刻')
    await expect(summary).toContainText('今日花费')
    await expect(summary).toContainText('预算')
    await expect(page.getByTestId('eval-ops-cohort-enabled')).toHaveText('未开启')
  })

  test('cohort 开关切换先确认（成本估算 + 预算上限），取消后状态不变且零写入', async ({ page, request }) => {
    const before = await cohortState(request)
    const auditsBefore = await cohortAuditCount(request)

    await openEvalOps(page)
    await page.getByTestId('eval-ops-tab-cohort').click()
    const toggle = page.getByTestId('eval-ops-cohort-toggle')
    await expect(toggle).not.toBeChecked()

    await toggle.click()

    // 烧钱动作必须带确认，且确认内容含成本估算与预算上限
    const dialog = page.getByTestId('eval-ops-confirm-dialog')
    await expect(dialog).toBeVisible({ timeout: NET_TIMEOUT })
    await expect(page.getByTestId('eval-ops-confirm-cost')).toContainText('tokens')
    await expect(page.getByTestId('eval-ops-confirm-budget')).toContainText('预算上限')

    await page.getByTestId('eval-ops-confirm-cancel').click()
    await expect(dialog).toBeHidden()
    await expect(toggle).not.toBeChecked()

    // 取消 = 零请求：服务端 cohort 状态与配置变更审计行数均不得变化
    expect(await cohortState(request)).toEqual(before)
    expect(await cohortAuditCount(request)).toBe(auditsBefore)
  })

  test('正式批发起被拒绝：展示服务端真实原因，且不启动任何回放', async ({ page, request }) => {
    // 离线环境下指数取数走 3 次重试退避（实测 ≈22s）；超时预算由 describe 级 120s 覆盖
    await openEvalOps(page)
    await page.getByTestId('eval-ops-tab-backtest').click()
    const reportsBefore = await reportNames(request)

    // 预登记门禁预检行如实渲染（本 worktree 预登记存在且字段齐备 → 不走前端本地拒绝分支，
    // 拒绝必须来自服务端真门禁/前置判定）
    await expect(page.getByTestId('eval-ops-backtest-prereg')).toContainText('预登记门禁预检')

    await page.getByTestId('eval-ops-backtest-codes').fill('600519')
    await page.getByTestId('eval-ops-backtest-formal').click()
    await page.getByTestId('eval-ops-confirm-ok').click()

    // 拒绝原因必须可见（非静默、非转圈）：1 只标的 < 每 regime 10 只 → 服务端 422 前置拒绝
    const error = page.getByTestId('eval-ops-error')
    await expect(error).toBeVisible({ timeout: 60_000 })
    await expect(error).toContainText('回测批发起被拒绝')
    await expect(error).toContainText('标的池不足')

    // 拒绝发生在任何回放之前：无结果卡、无「后台执行中」提示、报告注册表无新报告
    await expect(page.getByTestId('eval-ops-backtest-result')).toHaveCount(0)
    await expect(page.getByTestId('eval-ops-task-pending')).toHaveCount(0)
    expect(await reportNames(request)).toEqual(reportsBefore)
  })

  test('手动补跑完整性校验：落到终态结果并落运行历史（不断言瞬时「运行中」）', async ({ page, request }) => {
    const before = await latestRun(request, 'integrity_check')

    await openEvalOps(page)
    const cell = page.getByTestId('eval-ops-last-run-integrity_check')

    await page.getByTestId('eval-ops-run-integrity_check').click()

    // 终态断言：POST 服务端同步执行完才返回，回到前端时运行行已收尾；
    // 「运行中」只在请求在途的亚秒窗口可见，不作断言（flake 源）。
    await expect(cell).toContainText(/成功|失败/, { timeout: NET_TIMEOUT })

    // 落运行历史：新行 run_id 递增、kind=manual、终态（幂等补跑的可查证据）
    await expect
      .poll(async () => (await latestRun(request, 'integrity_check'))?.run_id ?? 0, { timeout: NET_TIMEOUT })
      .toBeGreaterThan(before?.run_id ?? 0)
    const after = await latestRun(request, 'integrity_check')
    expect(after?.kind).toBe('manual')
    expect(['ok', 'failed']).toContain(after?.status)

    // 卡片必须渲染**这一行**（精确到 finished_at 时间戳）：证明补跑结果进了界面，
    // 而非停留在上一条历史（`not.toHaveText` 一类的「文本变了」断言会因空白归一化假绿）
    expect(after?.finished_at).toBeTruthy()
    await expect(cell).toContainText(String(after?.finished_at), { timeout: NET_TIMEOUT })
  })

  test('cohort 关闭时手动跑批被拒绝：展示服务端原因且零 LLM 调用', async ({ page, request }) => {
    const cohort = await cohortState(request)
    // 前置不成立则跳过（开关已开时点「立即运行」会真的跑批，属烧钱动作，不得在门禁里误触）
    test.skip(cohort.enabled, 'cohort 开关已开启：本用例只验证「关闭时拒绝」语义')

    await openEvalOps(page)
    await page.getByTestId('eval-ops-run-cohort_batch').click()

    const error = page.getByTestId('eval-ops-error')
    await expect(error).toBeVisible({ timeout: NET_TIMEOUT })
    await expect(error).toContainText('cohort 开关未开启')

    // 零 LLM 调用的证据：运行历史留 skipped-disabled 行（而非 running/ok）
    const row = await latestRun(request, 'cohort_batch')
    expect(row?.status).toBe('skipped-disabled')
  })
})
