import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * delta add-ops-missed-today-badge（issue #453）：日批运维页「今日漏跑」告警位 E2E。
 *
 * 红线：不 mock 业务接口——漏跑/失败态经 TESTING=1 的 /api/test/seed ``ops`` 块
 * 真实写入 job_runs 并冻结 ops 状态时钟（真实时钟下全部日批排程在 15:35 后，
 * 上午跑 E2E 无法自然构造「已过点未跑」态；时钟冻结与后端单测 _now patch 同语义），
 * 前端经真实 GET /api/v1/ops/jobs 读取渲染。
 *
 * 运行命令（isolated config：8001/5174 + 独立测试库，SESSIONS_DB_PATH 承载 job_runs）：
 *   npx playwright test -c playwright.isolated.config.ts tests/ops-missed-badge.spec.ts
 */

test.describe.configure({ mode: 'serial' })

/** 周四 16:10：全部 15:35+ 排程已过点（漏跑态的确定性时刻） */
const FROZEN_THURSDAY_1610 = '2026-10-22T16:10:00'

function apiBase(): string {
  const base = test.info().project.use.baseURL ?? 'http://localhost:5174'
  return base.replace('//localhost', '//127.0.0.1')
}

async function seedOps(request: APIRequestContext, body: Record<string, unknown>) {
  const resp = await request.post(`${apiBase()}/api/test/seed`, { data: body })
  expect(resp.ok()).toBeTruthy()
}

test.beforeAll(async ({ request }) => {
  await seedOps(request, { ops_runs_reset: true })
})

test.afterAll(async ({ request }) => {
  // 复位：清运行行 + 解除时钟冻结（同 isolated config 的其它 spec 不受残留影响）
  await seedOps(request, { ops_runs_reset: true, ops: { now: null } })
})

test('missed_today=true → 「今日漏跑」告警位（degraded，非 failed 样式）', async ({ page }) => {
  await seedOps(page.request, { ops: { now: FROZEN_THURSDAY_1610 } })
  await page.goto('/ops')

  const badge = page.getByTestId('page-shell-batch-factor_decay_monitor').getByTestId('status-badge')
  await expect(badge).toHaveAttribute('data-status', 'degraded')
  await expect(badge).toHaveText('今日漏跑')
  // 摘要明示漏跑语义 + 上次运行信息（当天无行、历史停在昨天）
  const summary = page.getByTestId('ops-job-summary-factor_decay_monitor')
  await expect(summary).toContainText('今日漏跑')
  await expect(summary).toContainText('15:45')
  // 与 failed 语义区分：该卡无失败错误位
  await expect(page.getByTestId('page-shell-batch-factor_decay_monitor').getByTestId(/^ops-job-error/)).toHaveCount(0)
})

test('当天已失败 → failed 徽章 + 错误逐字，不误报为漏跑', async ({ page }) => {
  // 失败行日期取浏览器真实今天：describeJob 的「今日有运行行」判定用浏览器时钟
  // （后端冻结时钟只作用于 missed_today 字段）；failed 分支先于漏跑分支判定。
  const pad = (n: number) => String(n).padStart(2, '0')
  const now = new Date()
  const browserToday = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
  await seedOps(page.request, {
    ops: {
      now: FROZEN_THURSDAY_1610,
      runs: [
        {
          job_id: 'screening_daily',
          status: 'failed',
          error: 'RuntimeError: LLM 网关整体不可用',
          started_at: `${browserToday}T15:41:00`,
          finished_at: `${browserToday}T15:43:00`,
        },
      ],
    },
  })
  await page.goto('/ops')

  const badge = page.getByTestId('page-shell-batch-screening_daily').getByTestId('status-badge')
  await expect(badge).toHaveAttribute('data-status', 'failed')
  await expect(badge).toHaveText('失败')
  await expect(page.getByTestId('ops-job-error-screening_daily')).toHaveText('RuntimeError: LLM 网关整体不可用')
  // 失败行在场 → 后端 missed_today=false → 界面不得出现漏跑告警
  await expect(page.getByTestId('page-shell-batch-screening_daily').getByText('今日漏跑')).toHaveCount(0)
  // 同屏对照：无运行行的任务仍是漏跑告警（两种故障各自可见）
  await expect(
    page.getByTestId('page-shell-batch-factor_decay_monitor').getByTestId('status-badge')
  ).toHaveText('今日漏跑')
})

test('未到点与周末不告警（后端 missed_today=false）', async ({ page }) => {
  // 浏览器时钟与后端冻结时钟对齐到同一时刻：describeJob 的排程日/未到点判定
  // 用浏览器时钟。本仓 Playwright 版本无 page.clock API → addInitScript 覆盖
  // Date（只 fake 「现在」，不劫持定时器）。
  const freezeBrowserClock = (iso: string) =>
    page.addInitScript(
      `(() => {
        const fixed = new Date(${JSON.stringify(iso)}).getTime()
        const Real = Date
        class FakeDate extends Real {
          constructor(...args) { super(...(args.length ? args : [fixed])) }
          static now() { return fixed }
        }
        window.Date = FakeDate
      })()`
    )

  // 未到点：周四 15:00（全部排程之前）
  await seedOps(page.request, { ops_runs_reset: true, ops: { now: '2026-10-22T15:00:00' } })
  await freezeBrowserClock('2026-10-22T15:00:00')
  await page.goto('/ops')
  await expect(page.getByTestId('daily-ops-page')).toBeVisible()
  await expect(page.getByText('今日漏跑')).toHaveCount(0)
  await expect(page.getByTestId('ops-job-summary-screening_daily')).toContainText('尚未到排程时刻')

  // 周末：周六 17:00（mon-fri 任务的排程日之外）。注意 day_of_week="*" 的任务
  // （factor_harvest_daily 15:55）周末漏跑是正确语义，断言只收窄到 mon-fri 任务卡
  await seedOps(page.request, { ops: { now: '2026-10-24T17:00:00' } })
  await freezeBrowserClock('2026-10-24T17:00:00')
  await page.reload()
  await expect(page.getByTestId('daily-ops-page')).toBeVisible()
  await expect(page.getByTestId('ops-job-summary-screening_daily')).toContainText('未到本期排程日')
  await expect(page.getByTestId('page-shell-batch-screening_daily').getByText('今日漏跑')).toHaveCount(0)
})
