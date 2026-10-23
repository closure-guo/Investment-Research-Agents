import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

/**
 * delta add-factor-research-monitor T4：因子研发监控页 E2E 门禁（默认 config，8000/5173）。
 * delta add-factor-research-control T4：看护启停开关用例。
 *
 * 红线：不 mock 业务接口——fixture 快照/控制文件由本 spec 以真实文件写入测试隔离路径
 * （playwright.config.ts webServer env FACTOR_RESEARCH_STATUS_PATH=…）：
 * 后端真实 GET /api/factor-research/status 读文件 + 派生 stale + control 块、
 * 真实 PUT /api/factor-research/control 写控制文件（断言其真值）。
 * 缺失/损坏态直接操作文件（删除/写垃圾）；「研发侧调和完成」用推进快照文件模拟。
 *
 * 用例串行（serial）：共享同一快照/控制文件，setup/teardown 互相覆盖，并行会互踩。
 */

test.describe.configure({ mode: 'serial', timeout: 60_000 })

// tests/e2e/playwright/tests → 仓库根为四层上；与 config webServer env 的相对路径同源。
// （ESM 项目：经 import.meta.url 解析本文件目录，无 __dirname。）
const STATUS_PATH = path.resolve(
  fileURLToPath(new URL('.', import.meta.url)),
  '../../../../data/test-e2e-factor-research-status.json'
)
const CONTROL_PATH = path.resolve(
  fileURLToPath(new URL('.', import.meta.url)),
  '../../../../data/test-e2e-factor-research-control.json'
)
const LAUNCHER_PATH = path.resolve(
  fileURLToPath(new URL('.', import.meta.url)),
  '../../../../data/test-e2e-factor-research-launcher.json'
)
const CONFIG_PATH = path.resolve(
  fileURLToPath(new URL('.', import.meta.url)),
  '../../../../data/test-e2e-factor-research-pipeline.json'
)
const DISTRO_PROBE_PATH = path.resolve(
  fileURLToPath(new URL('.', import.meta.url)),
  '../../../../data/test-e2e-factor-research-distro-probe.json'
)

// 防御：fixture 路径必须测试隔离，绝不落到生产 data/factor_research/status.json 与控制文件
if (!STATUS_PATH.includes('test-e2e') || !CONTROL_PATH.includes('test-e2e')) {
  throw new Error(`fixture 路径未隔离: ${STATUS_PATH} / ${CONTROL_PATH}`)
}
if (!LAUNCHER_PATH.includes('test-e2e')) {
  throw new Error(`launcher fixture 路径未隔离: ${LAUNCHER_PATH}`)
}
if (!CONFIG_PATH.includes('test-e2e')) {
  throw new Error(`config fixture 路径未隔离: ${CONFIG_PATH}`)
}
if (!DISTRO_PROBE_PATH.includes('test-e2e')) {
  throw new Error(`distro 探活 fixture 路径未隔离: ${DISTRO_PROBE_PATH}`)
}

function isoAt(epoch: number): string {
  return new Date(epoch * 1000).toISOString()
}

/** 控制文件读取（PUT 真实落盘后的真值断言；不存在/损坏 → null）。 */
function readControl(): { desired_running?: unknown; restart_request_id?: unknown } | null {
  try {
    return JSON.parse(readFileSync(CONTROL_PATH, 'utf-8')) as {
      desired_running?: unknown
      restart_request_id?: unknown
    }
  } catch {
    return null
  }
}

function removeControl() {
  rmSync(CONTROL_PATH, { force: true })
}

/** 控制文件写入（PUT 之外的人工前置态：如「已请求但尚未生效」）。 */
function writeControl(payload: Record<string, unknown>) {
  mkdirSync(path.dirname(CONTROL_PATH), { recursive: true })
  writeFileSync(CONTROL_PATH, JSON.stringify(payload), 'utf-8')
}

/** 合法快照 fixture：形状与采集器契约（factor-research-monitor spec）一致。 */
function snapshotBody(overrides: Record<string, unknown> = {}) {
  const epoch = Math.floor(Date.now() / 1000) - 40
  return {
    collected_at: isoAt(epoch),
    collected_at_epoch: epoch,
    supervisor: { running: true, pid: 7752, started_at: isoAt(epoch - 1800) },
    current_session: {
      log_file: 'session-20261017-113600.log',
      started_at: isoAt(epoch - 1440),
      ended_at: null,
      duration_s: 1440,
      alive: true,
      loop_index: 4,
      step_index: 0,
      step_name: 'direct_exp_gen',
      steps_total: 5,
      step_elapsed_s: 595,
      step_eta_s: 90,
      factors: ['ret_5d_reversal', 'max_ret_20d'],
      log_tail: ['Workflow Progress: 1/5 loop_index=4 step_name=direct_exp_gen', 'plain tail line'],
    },
    sessions: [
      { log_file: 'session-20261017-113600.log', started_at: isoAt(epoch - 1440), ended_at: null, duration_s: 1440 },
      {
        log_file: 'session-20261017-110000.log',
        started_at: isoAt(epoch - 3600),
        ended_at: isoAt(epoch - 2000),
        duration_s: 300,
      },
    ],
    totals: { sessions: 2, candidates: 135, workspace_tasks: 222 },
    ...overrides,
  }
}

function writeSnapshot(snapshot: unknown) {
  mkdirSync(path.dirname(STATUS_PATH), { recursive: true })
  writeFileSync(STATUS_PATH, JSON.stringify(snapshot), 'utf-8')
}

function removeSnapshot() {
  rmSync(STATUS_PATH, { force: true })
}

/** 启动器心跳 fixture（默认新鲜 + 代理就绪；delta add-factor-research-selfheal）。 */
function writeLauncher({ ageS = 30, agentOk = true }: { ageS?: number; agentOk?: boolean } = {}) {
  const epoch = Math.floor(Date.now() / 1000) - ageS
  mkdirSync(path.dirname(LAUNCHER_PATH), { recursive: true })
  writeFileSync(
    LAUNCHER_PATH,
    JSON.stringify({
      checked_at: isoAt(epoch),
      checked_at_epoch: epoch,
      agent_pid: 4321,
      agent_ok: agentOk,
      snapshot_age_s: 12,
      detail: null,
    })
  )
}

function removeLauncher() {
  rmSync(LAUNCHER_PATH, { force: true })
}

/** 节奏配置文件（delta add-factor-research-settings；与后端 FACTOR_RESEARCH_CONFIG_PATH 同源）。 */
function readConfig(): Record<string, unknown> | null {
  try {
    return JSON.parse(readFileSync(CONFIG_PATH, 'utf-8')) as Record<string, unknown>
  } catch {
    return null
  }
}

function writeConfig(payload: Record<string, unknown>) {
  mkdirSync(path.dirname(CONFIG_PATH), { recursive: true })
  writeFileSync(CONFIG_PATH, JSON.stringify(payload), 'utf-8')
}

function removeConfig() {
  rmSync(CONFIG_PATH, { force: true })
}

/** distro 探活 fixture（delta add-factor-research-distro-liveness-probe）。 */
function writeDistroProbe(overrides: Record<string, unknown> = {}) {
  const epoch = Math.floor(Date.now() / 1000) - 40
  mkdirSync(path.dirname(DISTRO_PROBE_PATH), { recursive: true })
  writeFileSync(
    DISTRO_PROBE_PATH,
    JSON.stringify({
      probed_at: isoAt(epoch),
      probed_at_epoch: epoch,
      distro: 'rdagent',
      ok: false,
      exit_code: -1,
      detail: 'Wsl/E_IO_ERROR',
      consecutive_failures: 8,
      snapshot_stale: true,
      terminated_at: null,
      terminated_at_epoch: 0,
      terminate_count: 0,
      ...overrides,
    })
  )
}

function removeDistroProbe() {
  rmSync(DISTRO_PROBE_PATH, { force: true })
}

test.beforeEach(() => {
  removeLauncher()
})

test.afterAll(() => {
  removeSnapshot()
  removeControl()
  removeLauncher()
  removeConfig()
  removeDistroProbe()
})

test('1. 运行中：看护徽章/pid/循环·步骤·进度/因子/日志尾/历史表/累计计数', async ({ page }) => {
  writeSnapshot(snapshotBody())
  await page.goto('/factor-research')

  const supervisor = page.getByTestId('factor-research-supervisor')
  await expect(supervisor).toBeVisible()
  await expect(page.getByTestId('status-badge')).toHaveAttribute('data-status', 'running')
  await expect(page.getByTestId('status-badge')).toHaveText('运行中')
  await expect(page.getByTestId('factor-research-supervisor-pid')).toContainText('7752')

  // 进度：循环 5（loop_index 0 基 +1）· 步骤中文映射 · 第 1/5 步
  await expect(page.getByTestId('factor-research-progress')).toHaveText(
    '循环 5 · 当前步骤 实验生成 · 第 1/5 步'
  )
  await expect(page.getByTestId('factor-research-step-timing')).toContainText('本步已用 9分55秒')

  await expect(page.getByTestId('factor-research-factor')).toHaveCount(2)
  await expect(page.getByTestId('factor-research-log-tail')).toContainText('Workflow Progress')

  await expect(page.getByTestId('factor-research-session-row')).toHaveCount(2)
  await expect(page.getByTestId('factor-research-session-row').first()).toContainText('进行中')
  await expect(page.getByTestId('factor-research-session-row').nth(1)).toContainText('已结束')

  await expect(page.getByTestId('factor-research-totals')).toContainText('候选实现 135')
  await expect(page.getByTestId('factor-research-stale-banner')).toHaveCount(0)
})

test('2. 陈旧快照：明示「数据已陈旧」且内容仍可见', async ({ page }) => {
  // 采集时刻回拨 400s（> 180s 阈值）→ 后端派生 stale=true
  const epoch = Math.floor(Date.now() / 1000) - 400
  writeSnapshot(snapshotBody({ collected_at: isoAt(epoch), collected_at_epoch: epoch }))
  await page.goto('/factor-research')

  const banner = page.getByTestId('factor-research-stale-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('数据已陈旧')
  // 内容不被隐藏（陈旧 ≠ 不可用）
  await expect(page.getByTestId('factor-research-supervisor')).toBeVisible()
  await expect(page.getByTestId('factor-research-progress')).toBeVisible()
})

test('3. 未接入：快照缺失 → 「采集器未接入」空态（非加载中/错误冒充）', async ({ page }) => {
  removeSnapshot()
  await page.goto('/factor-research')

  const empty = page.getByTestId('factor-research-empty-missing')
  await expect(empty).toBeVisible()
  await expect(empty).toContainText('采集器未接入')
  await expect(page.getByTestId('factor-research-supervisor')).toHaveCount(0)
  await expect(page.getByTestId('factor-research-loading')).toHaveCount(0)
})

test('4. 损坏：快照不可解析 → 「快照损坏」空态（与缺失文案区分）', async ({ page }) => {
  mkdirSync(path.dirname(STATUS_PATH), { recursive: true })
  writeFileSync(STATUS_PATH, 'not json at all', 'utf-8')
  await page.goto('/factor-research')

  const empty = page.getByTestId('factor-research-empty-corrupt')
  await expect(empty).toBeVisible()
  await expect(empty).toContainText('快照损坏')
  await expect(page.getByTestId('factor-research-empty-missing')).toHaveCount(0)
})

test('5. 入口：侧边栏收起态图标与命令面板动作均进入 /factor-research', async ({ page }) => {
  writeSnapshot(snapshotBody())
  await page.goto('/')

  // 侧边栏收起态图标入口
  await expect(page.getByTestId('sidebar-trigger')).toBeVisible()
  await page.getByTestId('sidebar-trigger').click()
  const entry = page.getByTestId('sidebar-factor-research-collapsed')
  await expect(entry).toBeVisible()
  await entry.click()
  await expect(page).toHaveURL(/\/factor-research$/)
  await expect(page.getByTestId('factor-research-page')).toBeVisible()

  // 命令面板动作入口（Ctrl+K → 打开因子研发监控）
  await page.keyboard.press('Control+k')
  const action = page.getByTestId('action-factor-research')
  await expect(action).toBeVisible()
  await expect(action).toContainText('打开因子研发监控')
  await action.click()
  await expect(page).toHaveURL(/\/factor-research$/)
  await expect(page.getByTestId('factor-research-page')).toBeVisible()
})

// ── 看护启停开关（delta add-factor-research-control T4）──

test('6. 看护开关：停止需确认 → 控制文件真写入 → 「切换中」→ 快照推进后翻转', async ({ page }) => {
  const fixture = snapshotBody()
  writeSnapshot(fixture) // running=true，新鲜
  removeControl()
  await page.goto('/factor-research')

  // 前置守卫（防复用生产栈把 PUT 落到真控制文件）：后端必须读的是本 spec 的隔离 fixture。
  // 快照的 collected_at 唯一，其余来源（生产默认路径/陈旧数据）不可能逐字一致。
  const probe = (await (
    await page.request.get('/api/factor-research/status')
  ).json()) as { collected_at?: string; available?: boolean }
  expect(
    probe.available === true && probe.collected_at === fixture.collected_at,
    '后端未读取隔离 fixture（疑似复用了生产栈）——拒绝继续，防控制文件写入生产'
  ).toBe(true)

  const sw = page.getByTestId('factor-research-control-switch')
  await expect(sw).toBeVisible()
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  await expect(sw).toBeEnabled()
  await expect(page.getByTestId('factor-research-control-state')).toContainText('已开启')

  // 点击停止 → 二次确认弹窗；未确认前 MUST NOT 发请求（控制文件不存在）
  await sw.click()
  const dialog = page.getByTestId('factor-research-control-confirm-dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('将终止当前研发会话')
  expect(existsSync(CONTROL_PATH)).toBe(false)

  // 确认 → 真实 PUT → 控制文件真值落地（非 mock）
  await page.getByTestId('factor-research-control-confirm').click()
  await expect.poll(() => readControl()?.desired_running).toBe(false)

  // pending（期望≠实态）→ 「切换中」+ 禁用；位置不翻转（仍显示实态开）
  await expect(page.getByTestId('factor-research-control-state')).toContainText('切换中', {
    timeout: 15_000,
  })
  await expect(sw).toBeDisabled()
  await expect(sw).toHaveAttribute('aria-checked', 'true')

  // 模拟 distro 代理调和完成：快照推进为 running=false → 开关翻转 + 恢复可操作
  writeSnapshot(snapshotBody({ supervisor: { running: false, pid: null, started_at: null } }))
  await expect(sw).toHaveAttribute('aria-checked', 'false', { timeout: 15_000 })
  await expect(sw).toBeEnabled()
  await expect(page.getByTestId('factor-research-control-state')).toContainText('已关闭')
})

test('7. 陈旧数据：开关禁用 + 明示理由（控制通道无存活证据）', async ({ page }) => {
  const epoch = Math.floor(Date.now() / 1000) - 400
  writeSnapshot(snapshotBody({ collected_at: isoAt(epoch), collected_at_epoch: epoch }))
  await page.goto('/factor-research')

  await expect(page.getByTestId('factor-research-stale-banner')).toBeVisible()
  const sw = page.getByTestId('factor-research-control-switch')
  await expect(sw).toBeDisabled()
  await expect(page.getByTestId('factor-research-control-state')).toContainText(
    '研究侧与启动器均离线'
  )
})

test('8. 快照缺失：空态照旧 + 控制条开关禁用「控制通道未接入」', async ({ page }) => {
  removeSnapshot()
  await page.goto('/factor-research')

  await expect(page.getByTestId('factor-research-empty-missing')).toBeVisible()
  const sw = page.getByTestId('factor-research-control-switch')
  await expect(sw).toBeDisabled()
  await expect(page.getByTestId('factor-research-control-state')).toContainText('控制通道未接入')
})

// ── 自愈启动器与离线可开（delta add-factor-research-selfheal）──

test('9. 离线可开：陈旧快照 + 启动器心跳 → 开关可点并明示自动拉起', async ({ page }) => {
  const epoch = Math.floor(Date.now() / 1000) - 400
  writeSnapshot(snapshotBody({ collected_at: isoAt(epoch), collected_at_epoch: epoch }))
  removeControl()
  writeLauncher()

  await page.goto('/factor-research')

  const sw = page.getByTestId('factor-research-control-switch')
  await expect(sw).toBeEnabled()
  // 快照陈旧 → 实态未知：开关 MUST NOT 显示为开（只有验证过运行中才为开）
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await expect(page.getByTestId('factor-research-control-state')).toContainText('≤3 分钟')
  await expect(page.getByTestId('factor-research-stale-banner')).toContainText(
    '启动器正在按节拍尝试恢复'
  )

  await sw.click() // 意图为 null → 位置为关 → 点击 = 开启
  await expect.poll(() => readControl()?.desired_running, { timeout: 10_000 }).toBe(true)
  await expect(page.getByTestId('factor-research-control-state')).toContainText('等待启动器拉起')
})

test('10. 双离线：陈旧快照 + 无心跳 → 开关禁用并明示理由', async ({ page }) => {
  const epoch = Math.floor(Date.now() / 1000) - 400
  writeSnapshot(snapshotBody({ collected_at: isoAt(epoch), collected_at_epoch: epoch }))
  removeControl()

  await page.goto('/factor-research')

  const sw = page.getByTestId('factor-research-control-switch')
  await expect(sw).toBeDisabled()
  await expect(page.getByTestId('factor-research-control-state')).toContainText(
    '研究侧与启动器均离线'
  )
})

test('11. 启动失败：pending + start-failed → 展示原因而非切换中', async ({ page }) => {
  writeSnapshot(
    snapshotBody({
      supervisor: { running: false, pid: null, started_at: null },
      agent: {
        last_action: 'start-failed',
        last_action_at: isoAt(Math.floor(Date.now() / 1000) - 30),
        last_error: '看护未在启动窗口内就位（检查 launch 脚本与环境）',
      },
    })
  )
  writeControl({ desired_running: true, requested_at: isoAt(Math.floor(Date.now() / 1000)) })

  await page.goto('/factor-research')

  const state = page.getByTestId('factor-research-control-state')
  await expect(state).toContainText('启动失败')
  await expect(state).toContainText('看护未在启动窗口内就位')
  await expect(state).not.toContainText('切换中')
})

test('12. 连续短命会话：最近 3 次均 <120 秒 → 告警', async ({ page }) => {
  const now = Math.floor(Date.now() / 1000)
  writeSnapshot(
    snapshotBody({
      sessions: [
        { log_file: 's3.log', started_at: isoAt(now - 3600), ended_at: isoAt(now - 3577), duration_s: 23 },
        { log_file: 's2.log', started_at: isoAt(now - 4200), ended_at: isoAt(now - 4176), duration_s: 24 },
        { log_file: 's1.log', started_at: isoAt(now - 4800), ended_at: isoAt(now - 4775), duration_s: 25 },
      ],
    })
  )

  await page.goto('/factor-research')

  await expect(page.getByTestId('factor-research-short-sessions')).toContainText('连续短命会话')
})

// ── 实态语义 + 重启 + 配置页（delta add-factor-research-settings）──

test('13. 快照陈旧 + 意图 true：开关不冒充运行中（关）且明示状态未知', async ({ page }) => {
  const epoch = Math.floor(Date.now() / 1000) - 400
  writeSnapshot(snapshotBody({ collected_at: isoAt(epoch), collected_at_epoch: epoch }))
  writeControl({ desired_running: true, requested_at: isoAt(epoch - 10) })
  writeLauncher()

  await page.goto('/factor-research')

  const sw = page.getByTestId('factor-research-control-switch')
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await expect(page.getByTestId('status-badge')).toHaveAttribute('data-status', 'degraded')
  await expect(page.getByTestId('status-badge')).toHaveText('状态未知')
  const state = page.getByTestId('factor-research-control-state')
  await expect(state).toContainText('状态未知')
  await expect(state).toContainText('已记录保持运行')
})

test('14. 重启管线：确认后控制文件真写入 restart 请求 → 「重启中」→ 研究侧回写后恢复', async ({
  page,
}) => {
  writeSnapshot(snapshotBody())
  removeControl()

  await page.goto('/factor-research')

  const restart = page.getByTestId('factor-research-restart-button')
  await expect(restart).toBeEnabled()
  await restart.click()
  await expect(page.getByTestId('factor-research-restart-confirm-dialog')).toBeVisible()
  expect(readControl()).toBeNull() // 未确认前零请求

  await page.getByTestId('factor-research-restart-confirm').click()
  await expect
    .poll(() => readControl()?.restart_request_id, { timeout: 10_000 })
    .toBeTruthy()
  const control = readControl() as { restart_request_id: string; desired_running: boolean }
  expect(control.desired_running).toBe(true)

  // 后端已看到请求但快照尚无 agent.restart → 「重启中」+ 按钮禁用
  await expect(page.getByTestId('factor-research-control-state')).toContainText('重启中')
  await expect(page.getByTestId('factor-research-restart-button')).toBeDisabled()

  // 模拟研究侧代理回写处理结果 → 刷新后恢复实态
  writeSnapshot(
    snapshotBody({
      agent: {
        last_action: 'started',
        last_action_at: isoAt(Math.floor(Date.now() / 1000)),
        last_action_epoch: Math.floor(Date.now() / 1000),
        last_error: null,
        restart: {
          request_id: control.restart_request_id,
          requested_at_epoch: Math.floor(Date.now() / 1000) - 5,
          result: 'restarted',
          handled_at: isoAt(Math.floor(Date.now() / 1000)),
          handled_at_epoch: Math.floor(Date.now() / 1000),
        },
      },
    })
  )
  await page.reload()
  await expect(page.getByTestId('factor-research-control-state')).toContainText('已开启')
  await expect(page.getByTestId('factor-research-restart-button')).toBeEnabled()
})

test('15. 设置中心因子研发配置：实态/节奏配置保存真写文件', async ({ page }) => {
  writeSnapshot(snapshotBody())
  // 前序重启用例会留下 restart 请求；本用例验证配置页实态，先清控制文件
  removeControl()
  writeConfig({
    snapshot_interval_s: 15,
    session_gap_s: 60,
    short_session_backoff_s: 600,
    short_session_threshold_s: 120,
  })

  await page.goto('/settings')
  await page.getByTestId('settings-nav-factor-research').click()
  await expect(page.getByTestId('factor-research-settings-pane')).toBeVisible()
  await expect(page.getByTestId('factor-research-settings-state')).toContainText('已开启')

  const input = page.getByTestId('factor-research-settings-config-snapshot_interval_s')
  await expect(input).toHaveValue('15')
  await input.fill('30')
  await page.getByTestId('factor-research-settings-config-save').click()

  await expect
    .poll(() => (readConfig()?.snapshot_interval_s as number | undefined), { timeout: 10_000 })
    .toBe(30)
  await expect(page.getByTestId('factor-research-settings-config-saved')).toContainText('已保存')
})

test('16. 会话僵死：明示无进展且不把定格读数当当前进度', async ({ page }) => {
  // 采集链路正常（快照 40s 前）+ 看护运行中 + 会话存活，但日志静默 3600s（> 900s 阈值）
  // → 后端派生 progress.stalled=true。fixture 只写**原始读数** log_age_s（阈值单源在后端）。
  const base = snapshotBody()
  writeSnapshot({
    ...base,
    current_session: { ...(base.current_session as Record<string, unknown>), log_age_s: 3600 },
  })
  await page.goto('/factor-research')

  const banner = page.getByTestId('factor-research-stall-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('无进展 1小时0分')
  // 横幅只披露，零命令面
  await expect(banner.locator('button, a')).toHaveCount(0)
  // 定格的步骤读数不得冒充当前进度
  await expect(page.getByTestId('factor-research-step-timing')).toHaveCount(0)
  await expect(page.getByTestId('factor-research-progress-stale')).toContainText('1小时0分')
  // 采集链路正常 → 陈旧横幅不出现（僵死与陈旧互斥，不互相遮蔽）
  await expect(page.getByTestId('factor-research-stale-banner')).toHaveCount(0)
})

test('17. 日志新鲜：不告警且照常显示步骤进度', async ({ page }) => {
  const base = snapshotBody()
  writeSnapshot({
    ...base,
    current_session: { ...(base.current_session as Record<string, unknown>), log_age_s: 42 },
  })
  await page.goto('/factor-research')

  await expect(page.getByTestId('factor-research-progress')).toBeVisible()
  await expect(page.getByTestId('factor-research-stall-banner')).toHaveCount(0)
  await expect(page.getByTestId('factor-research-step-timing')).toContainText('本步已用 9分55秒')
})

test('18. distro 探活失败 + 快照陈旧：明示 distro 不可用且不误指采集器', async ({ page }) => {
  const epoch = Math.floor(Date.now() / 1000) - 400
  writeSnapshot(snapshotBody({ collected_at: isoAt(epoch), collected_at_epoch: epoch }))
  writeDistroProbe({ ok: false, consecutive_failures: 8 })
  await page.goto('/factor-research')

  const banner = page.getByTestId('factor-research-distro-probe-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('连续 8 次')
  await expect(banner).toContainText('distro')
  await expect(banner).toContainText('采集器并未停止')
  // 零命令面：横幅内无处置入口
  await expect(banner.locator('button, a')).toHaveCount(0)

  // 陈旧横幅不得再声称「采集器可能已停止」（incident 049 实测该表述失实）
  const staleBanner = page.getByTestId('factor-research-stale-banner')
  await expect(staleBanner).toBeVisible()
  await expect(staleBanner).not.toContainText('采集器可能已停止')
  await expect(staleBanner).toContainText('distro 探活失败')
})

test('19. distro 探活正常：保持既有陈旧表述且无探活提示', async ({ page }) => {
  const epoch = Math.floor(Date.now() / 1000) - 400
  writeSnapshot(snapshotBody({ collected_at: isoAt(epoch), collected_at_epoch: epoch }))
  writeDistroProbe({
    ok: true,
    exit_code: 0,
    detail: null,
    consecutive_failures: 0,
    snapshot_stale: false,
  })
  await page.goto('/factor-research')

  const staleBanner = page.getByTestId('factor-research-stale-banner')
  await expect(staleBanner).toBeVisible()
  await expect(staleBanner).toContainText('采集器可能已停止')
  await expect(page.getByTestId('factor-research-distro-probe-banner')).toHaveCount(0)
  removeDistroProbe()
})
