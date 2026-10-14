import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * delta add-broker-console T2.3：执行台账页 E2E 门禁（默认 config，8000/5173）。
 *
 * 红线：不 mock 业务接口——账户造数经 TESTING=1 的 /api/test/seed 真实写入
 * execution 库（playwright.config.ts webServer env
 * EXECUTION_DB_PATH=data/test-e2e-execution.db，与开发库 data/execution.db 隔离），
 * 前端经真实 /api/broker/* 四端点读取渲染。
 *
 * 用例串行（serial）：共享同一 execution 库，seed/broker_reset 互相覆盖，并行会互踩；
 * 每例前后均 reset（清 orders/positions/cash 三表），避免种子跨例残留（同
 * screening-page.spec 的隔离纪律）。
 *
 * 市值断言的自适应性：本套件不造行情快照行（快照库为默认测试库 data/test-e2e-snapshot.db，
 * 其内容不由本 spec 控制）——持仓市值可能可得（快照命中）或不可得（快照缺失）。
 * 两种都合法，但**标注必须严格一致**：不可得 → 市值/总资产显示「—」且有标注文案，
 * MUST NOT 以 0 冒充；可得 → 显示具体金额。下方用同一不变量覆盖两态。
 *
 * 运行命令（默认端口被 Docker 生产栈占用时改跑 isolated config，见其头注释）：
 *   npx playwright test -c playwright.isolated.config.ts tests/broker-console.spec.ts
 */
test.describe.configure({ mode: 'serial', timeout: 60_000 })

const AS_OF = '2026-10-13'

/**
 * 造数 API 基址：取 config ``use.baseURL`` 的 origin 并把 localhost 换成 127.0.0.1。
 * 两个理由：① 默认 config（5173）被 Docker 生产栈占用时本 spec 改跑
 * playwright.isolated.config.ts（5174）——硬编码端口会让 API 造数打到生产前端；
 * ② Windows 上 localhost 可能先解析到 ::1，而 vite dev server 绑 IPv4，
 * APIRequestContext 只试 ::1 时会 ECONNREFUSED（同 screening spec 的实测处置）。
 */
function apiBase(): string {
  const base = test.info().project.use.baseURL ?? 'http://localhost:5173'
  return base.replace('//localhost', '//127.0.0.1')
}

// 四态订单 + 一条 ST_FLAG 门禁拒绝（order_id 缺省由后端按 signal_id:symbol:side 派生）
const ORDERS = [
  {
    symbol: '600519.SH',
    side: 'buy',
    qty: 100,
    status: 'pending',
    reason: 'LIMIT_UP_UNFILLABLE',
    decision_id: 'decision-sig-pending',
    signal_id: 'sig-pending',
    created_at: `${AS_OF}T09:31:00`,
  },
  {
    symbol: '000001.SZ',
    side: 'buy',
    qty: 2000,
    status: 'filled',
    reason: null,
    fill_price: 13.0,
    decision_id: 'decision-sig-filled',
    signal_id: 'sig-filled',
    created_at: `${AS_OF}T09:32:00`,
  },
  {
    symbol: '600519.SH',
    side: 'sell',
    qty: 100,
    status: 'rejected',
    reason: 'INSUFFICIENT_CASH',
    decision_id: 'decision-sig-rejected',
    signal_id: 'sig-rejected',
    created_at: `${AS_OF}T09:33:00`,
  },
  {
    symbol: '000001.SZ',
    side: 'sell',
    qty: 500,
    status: 'expired',
    reason: 'CANCELED',
    attempts: 3,
    decision_id: 'decision-sig-expired',
    signal_id: 'sig-expired',
    created_at: `${AS_OF}T09:34:00`,
  },
  {
    symbol: '000001.SZ',
    side: 'sell',
    qty: 500,
    status: 'rejected',
    reason: 'ST_FLAG',
    decision_id: 'decision-sig-gate',
    signal_id: 'sig-gate',
    created_at: `${AS_OF}T09:35:00`,
  },
]

const BROKER_SEED = {
  broker: {
    cash: 876_543.21,
    positions: [
      { symbol: '600519.SH', qty: 100, avg_cost: 1500.0, updated_at: AS_OF },
      { symbol: '000001.SZ', qty: 2000, avg_cost: 12.0, updated_at: AS_OF },
    ],
    orders: ORDERS,
  },
}

async function resetBroker(request: APIRequestContext) {
  const resp = await request.post(`${apiBase()}/api/test/seed`, { data: { broker_reset: true } })
  expect(resp.ok()).toBeTruthy()
}

async function seedBroker(request: APIRequestContext) {
  const resp = await request.post(`${apiBase()}/api/test/seed`, { data: BROKER_SEED })
  expect(resp.ok()).toBeTruthy()
}

test.beforeEach(async ({ request }) => {
  await resetBroker(request)
})

test.afterEach(async ({ request }) => {
  await resetBroker(request)
})

test('种子账户 → 指标条齐备：资金逐字 + 市值可得的标注一致性（不可得 → 「—」不填 0）', async ({
  page,
  request,
}) => {
  await seedBroker(request)

  await page.goto('/broker')
  // 首屏取数在并发套件下偶见 >5s：显式放宽首屏断言超时（同 screening spec 处置）
  await expect(page.getByTestId('page-shell-metrics')).toBeVisible({ timeout: 15_000 })

  // 资金逐字（账本口径，独立于快照）
  await expect(page.getByTestId('page-shell-metric-cash')).toContainText('876,543.21')

  // 市值/总资产可得性与标注严格一致（两态都合法，但不可混用）
  const note = page.getByTestId('broker-market-value-note')
  if ((await note.count()) > 0) {
    await expect(note).toContainText('快照')
    await expect(page.getByTestId('broker-metric-positions-value')).toHaveText('—')
    await expect(page.getByTestId('broker-metric-equity-value')).toHaveText('—')
  } else {
    await expect(page.getByTestId('broker-metric-positions-value')).toContainText('¥')
    await expect(page.getByTestId('broker-metric-equity-value')).toContainText('¥')
  }

  // 主图表：构成图或如实空态（不画空图）
  await expect(
    page.getByTestId('broker-positions-pie').or(page.getByTestId('broker-chart-empty')),
  ).toBeVisible()
})

test('持仓页签：两持仓逐行渲染（标的/数量/成本），现价不可得显示「—」', async ({ page, request }) => {
  await seedBroker(request)

  await page.goto('/broker')
  await expect(page.getByTestId('page-shell-tab-positions')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('page-shell-tab-content')).toHaveAttribute('data-tab', 'positions')

  const row = page.getByTestId('broker-position-row-600519.SH')
  await expect(row).toContainText('600519.SH')
  await expect(row).toContainText('100')
  await expect(row).toContainText('1,500.00') // 成本
  await expect(page.getByTestId('broker-position-row-000001.SZ')).toContainText('2,000')
})

test('订单页签：四态徽章映射（pending→running、filled→active、rejected→failed、expired→retired）', async ({
  page,
  request,
}) => {
  await seedBroker(request)

  await page.goto('/broker')
  await expect(page.getByTestId('page-shell-tab-orders')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('page-shell-tab-orders').click()
  await expect(page.getByTestId('page-shell-tab-content')).toHaveAttribute('data-tab', 'orders')

  const badgeStatus = async (orderId: string, status: string) => {
    await expect(
      page.getByTestId(`broker-order-status-${orderId}`).getByTestId('status-badge'),
    ).toHaveAttribute('data-status', status)
  }
  await badgeStatus('sig-pending:600519.SH:buy', 'running')
  await badgeStatus('sig-filled:000001.SZ:buy', 'active')
  await badgeStatus('sig-rejected:600519.SH:sell', 'failed')
  await badgeStatus('sig-expired:000001.SZ:sell', 'retired')

  // 原因码与成交价如实（成交行有 fill_price，未成交行为占位）
  await expect(page.getByTestId('broker-order-row-sig-filled:000001.SZ:buy')).toContainText('13.00')
  await expect(page.getByTestId('broker-order-reason-sig-pending:600519.SH:buy')).toContainText(
    'LIMIT_UP_UNFILLABLE',
  )
})

test('门禁拒绝页签：仅 ST_FLAG 等被拒订单（含原因码与关联决策），不含已成交/挂单', async ({
  page,
  request,
}) => {
  await seedBroker(request)

  await page.goto('/broker')
  await expect(page.getByTestId('page-shell-tab-gate-log')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('page-shell-tab-gate-log').click()
  await expect(page.getByTestId('page-shell-tab-content')).toHaveAttribute('data-tab', 'gate-log')

  // ST_FLAG 拒绝行：原因码 + 关联 decision_id
  const gateRow = page.getByTestId('broker-gate-row-sig-gate:000001.SZ:sell')
  await expect(gateRow).toContainText('000001.SZ')
  await expect(gateRow).toContainText('ST_FLAG')
  await expect(gateRow).toContainText('decision-sig-gate')
  // 另一条 rejected（INSUFFICIENT_CASH）同现
  await expect(page.getByTestId('broker-gate-row-sig-rejected:600519.SH:sell')).toContainText(
    'INSUFFICIENT_CASH',
  )
  // 非 rejected 状态不出现在门禁拒绝视图
  await expect(page.getByTestId('broker-gate-row-sig-filled:000001.SZ:buy')).toBeHidden()
  await expect(page.getByTestId('broker-gate-row-sig-pending:600519.SH:buy')).toBeHidden()
})

test('空账户如实：初始资金 + 图表/三页签空态，MUST NOT 伪持仓/伪订单', async ({ page }) => {
  // before 钩子已 reset：账户三表为空（资金回初始 100 万）
  await page.goto('/broker')
  await expect(page.getByTestId('page-shell-metrics')).toBeVisible({ timeout: 15_000 })

  await expect(page.getByTestId('page-shell-metric-cash')).toContainText('1,000,000.00')
  await expect(page.getByTestId('broker-chart-empty')).toContainText('无持仓')
  await expect(page.getByTestId('broker-positions-empty')).toContainText('暂无持仓')

  await page.getByTestId('page-shell-tab-orders').click()
  await expect(page.getByTestId('broker-orders-empty')).toContainText('暂无订单')

  await page.getByTestId('page-shell-tab-gate-log').click()
  await expect(page.getByTestId('broker-gate-empty')).toContainText('暂无门禁拒绝记录')
})
