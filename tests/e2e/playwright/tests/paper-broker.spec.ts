import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * add-paper-broker T4：战绩页「虚拟结算 vs 仿真成交」双曲线 + 执行损耗标注 E2E 门禁。
 *
 * 落位说明（与计划「默认 config」的偏差，如实记录）：本 spec 属战绩页造数套件
 * （seed 写 equity_curve + paper_equity），而默认 config 的共享测试库
 * （data/test-e2e-sessions.db）同时承载 decisions.spec.ts 的「track-record-curve
 * count 0」空态断言——沿用既有 review fix 的隔离纪律（见
 * playwright.track-record.config.ts 头注释），本 spec 运行于该专属 config：
 * 端口对 8004/5177，后端 SESSIONS_DB_PATH=data/test-e2e-track-record.db +
 * EXECUTION_DB_PATH=data/test-e2e-track-record-execution.db 双独立库；
 * 默认 config 已 testIgnore 本文件（见其 testIgnore 注释）。
 * 运行命令：
 *   npx playwright test -c playwright.track-record.config.ts tests/paper-broker.spec.ts
 *
 * 红线：不 mock 业务接口（无 route.fulfill / MSW）——种子经 TESTING=1 的
 * /api/test/seed 真实落库，页面经真实 /api/v1/track-record/overview 取数渲染。
 *
 * 用例串行（serial）：共享同一测试库；每例前后 track_record_reset 复位
 * （清 equity_curve + paper_equity），种子不跨例/跨 spec 残留。
 */
test.describe.configure({ mode: 'serial' })

// 双曲线种子：虚拟 1.043 / 仿真 1.031（spec 场景数值）→ loss_pct = -1.2
const DUAL_CURVE_SEED = {
  track_record: {
    equity_curve: [
      { curve_date: '2026-09-01', agent_nav: 1.0, benchmark_nav: 1.0 },
      { curve_date: '2026-09-30', agent_nav: 1.043, benchmark_nav: 1.01 },
    ],
    paper_equity: [
      { trade_date: '2026-09-01', cash: 1_000_000.0, positions_value: 0.0, equity: 1_000_000.0 },
      { trade_date: '2026-09-30', cash: 998_000.0, positions_value: 33_000.0, equity: 1_031_000.0 },
    ],
  },
}

// 无 paper 数据种子：只有虚拟曲线（后端 paper: null → 前端单曲线）
const VIRTUAL_ONLY_SEED = {
  track_record: {
    equity_curve: [
      { curve_date: '2026-09-01', agent_nav: 1.0, benchmark_nav: 1.0 },
      { curve_date: '2026-09-30', agent_nav: 1.043, benchmark_nav: 1.01 },
    ],
  },
}

async function resetTrackRecord(request: APIRequestContext) {
  const resp = await request.post('/api/test/seed', { data: { track_record_reset: true } })
  expect(resp.ok()).toBeTruthy()
}

test.describe('战绩页:虚拟 vs 仿真双曲线（add-paper-broker）', () => {
  test.beforeEach(async ({ request }) => {
    await resetTrackRecord(request)
  })

  test.afterEach(async ({ request }) => {
    await resetTrackRecord(request)
  })

  test('paper 数据就位 → 双曲线图例「虚拟结算」「仿真成交」与执行损耗标注可见', async ({ page, request }) => {
    const resp = await request.post('/api/test/seed', { data: DUAL_CURVE_SEED })
    expect(resp.ok()).toBeTruthy()

    await page.goto('/track-record')
    await expect(page.getByTestId('track-record')).toBeVisible()
    await expect(page.getByTestId('track-record-curve')).toBeVisible()

    // spec 场景：图例区分两条曲线（DOM 图例区——ECharts 图例画在 canvas 内不可断言）
    await expect(page.getByTestId('legend-virtual')).toBeVisible()
    await expect(page.getByTestId('legend-virtual')).toHaveText('虚拟结算')
    await expect(page.getByTestId('legend-paper')).toBeVisible()
    await expect(page.getByTestId('legend-paper')).toHaveText('仿真成交')
    // 执行损耗 -1.2%（虚拟 1.043 / 仿真 1.031，(1.031/1.043−1)×100 → 保留 1 位）
    await expect(page.getByTestId('exec-loss')).toHaveText('执行损耗: -1.2%')
    // 口径说明（接口 note 透传）
    await expect(page.getByTestId('paper-note')).toContainText('差值为执行损耗')
  })

  test('无 paper 数据 → 单曲线渲染不报错，无损耗标注', async ({ page, request }) => {
    const resp = await request.post('/api/test/seed', { data: VIRTUAL_ONLY_SEED })
    expect(resp.ok()).toBeTruthy()

    await page.goto('/track-record')
    await expect(page.getByTestId('track-record-curve')).toBeVisible()
    await expect(page.getByTestId('exec-loss')).toHaveCount(0)
    await expect(page.getByTestId('curve-legend')).toHaveCount(0)
  })
})
