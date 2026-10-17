import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

/**
 * delta add-factor-research-monitor T4：因子研发监控页 E2E 门禁（默认 config，8000/5173）。
 *
 * 红线：不 mock 业务接口——fixture 快照文件由本 spec 以真实文件写入测试隔离路径
 * （playwright.config.ts webServer env FACTOR_RESEARCH_STATUS_PATH=
 * data/test-e2e-factor-research-status.json），后端真实 GET /api/factor-research/status
 * 读文件 + 派生 stale，前端经真实接口渲染。缺失/损坏态直接操作文件（删除/写垃圾）。
 *
 * 用例串行（serial）：共享同一快照文件，setup/teardown 互相覆盖，并行会互踩。
 */

test.describe.configure({ mode: 'serial', timeout: 60_000 })

// tests/e2e/playwright/tests → 仓库根为四层上；与 config webServer env 的相对路径同源。
// （ESM 项目：经 import.meta.url 解析本文件目录，无 __dirname。）
const STATUS_PATH = path.resolve(
  fileURLToPath(new URL('.', import.meta.url)),
  '../../../../data/test-e2e-factor-research-status.json'
)

// 防御：fixture 路径必须测试隔离，绝不落到生产 data/factor_research/status.json
if (!STATUS_PATH.includes('test-e2e')) {
  throw new Error(`fixture 路径未隔离: ${STATUS_PATH}`)
}

function isoAt(epoch: number): string {
  return new Date(epoch * 1000).toISOString()
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

test.afterAll(() => {
  removeSnapshot()
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
