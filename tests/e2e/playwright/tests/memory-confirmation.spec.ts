import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * add-memory-confirmation T2.3：记忆确认页 E2E 门禁（默认 config，8000/5173）。
 *
 * 红线：不 mock 业务接口——条款经 TESTING=1 的 /api/test/seed 真实写入记忆库
 * （库路径 SESSIONS_DB_PATH=data/test-e2e-sessions.db，与开发库隔离），前端经真实
 * GET /api/memory/clauses 读取渲染；确认/驳回经真实 POST 端点落库。
 *
 * 用例串行（serial）：共享同一记忆库，seed/reset 互相覆盖，并行会互踩；
 * 每例前后均 reset，避免遗留条款让其它用例的空态/单条前提失效（共享库泄漏）。
 */
test.describe.configure({ mode: 'serial', timeout: 60_000 })

// API 造数显式走 IPv4：Windows 上 localhost 可能先解析到 ::1，而 vite dev server
// 绑 0.0.0.0（IPv4），APIRequestContext 只试 ::1 时会 ECONNREFUSED（首轮实测偶发）。
// 默认 5173（默认 config 端口对）；E2E_API_BASE 可覆盖（隔离端口临时验证用，
// 默认 config 下不设置，行为与既有 spec 的 5173 硬编码一致）。
const API_BASE = process.env.E2E_API_BASE ?? 'http://127.0.0.1:5173'

const PENDING_TEXT = '看多方向已结算读数 1 条：胜率 100%（命中 1 / 未中 0）'
const CONFIRMED_TEXT = '空头方向历史决策 2 条：结算未中（占该方向 100%）'
const REJECTED_TEXT = '中性方向历史决策 2 条：结算带内中性（占该方向 100%）'

async function resetMemory(request: APIRequestContext) {
  const resp = await request.post(`${API_BASE}/api/test/seed`, { data: { memory_reset: true } })
  expect(resp.ok()).toBeTruthy()
}

async function seedClauses(request: APIRequestContext) {
  const resp = await request.post(`${API_BASE}/api/test/seed`, {
    data: {
      memory: {
        clauses: [
          { text: PENDING_TEXT, status: 'pending' },
          { text: CONFIRMED_TEXT, status: 'confirmed' },
          { text: REJECTED_TEXT, status: 'rejected' },
        ],
      },
    },
  })
  expect(resp.ok()).toBeTruthy()
}

// 列表项定位：条款 id 为落库时生成的 uuid，E2E 不可预知 → 按正文文本过滤列表项
const listItems = '[data-testid^="page-shell-list-item-"]'

test.beforeEach(async ({ request }) => {
  await resetMemory(request)
})

test.afterEach(async ({ request }) => {
  await resetMemory(request)
})

test.afterAll(async ({ request }) => {
  await resetMemory(request)
})

test('三态过滤：默认待确认只显示 pending 条款', async ({ page, request }) => {
  await seedClauses(request)

  await page.goto('/memory')
  // 首屏取数在 8 并发 worker 的默认套件下偶见 >5s：显式放宽首屏断言超时
  await expect(page.locator(listItems)).toHaveCount(1, { timeout: 15_000 })
  await expect(page.locator(listItems).filter({ hasText: PENDING_TEXT })).toBeVisible()
  await expect(page.locator(listItems).filter({ hasText: CONFIRMED_TEXT })).toHaveCount(0)
  await expect(page.getByTestId('memory-filter-pending')).toHaveAttribute('aria-pressed', 'true')

  await page.getByTestId('memory-filter-confirmed').click()
  await expect(page.locator(listItems).filter({ hasText: CONFIRMED_TEXT })).toBeVisible({ timeout: 15_000 })
  await expect(page.locator(listItems).filter({ hasText: PENDING_TEXT })).toHaveCount(0)

  await page.getByTestId('memory-filter-rejected').click()
  await expect(page.locator(listItems).filter({ hasText: REJECTED_TEXT })).toBeVisible({ timeout: 15_000 })
})

test('确认流：详情（AiBadge + 后果提示）→ 确认 → 迁出待确认并出现在已确认（徽章已确认）', async ({
  page,
  request,
}) => {
  await seedClauses(request)

  await page.goto('/memory')
  const pendingItem = page.locator(listItems).filter({ hasText: PENDING_TEXT })
  await expect(pendingItem).toBeVisible({ timeout: 15_000 })
  await expect(pendingItem.getByTestId('status-badge')).toHaveText('待确认')

  await pendingItem.click()
  // 详情：完整正文 + AiBadge（LLM 提炼产物）+ 来源/提炼时间 + 确认后果提示
  const detail = page.getByTestId('memory-detail-text')
  await expect(detail).toContainText(PENDING_TEXT)
  await expect(detail.getByTestId('ai-badge')).toBeVisible()
  await expect(page.getByTestId('memory-detail-source')).toContainText('月批提炼')
  await expect(page.getByTestId('memory-detail-created-at')).toContainText('提炼时间')
  await expect(page.getByTestId('memory-confirm-effect-note')).toHaveText('确认后即刻生效注入风控辩论')

  await page.getByTestId('page-shell-confirm').click()

  // 待确认列表移除 C；无页面级报错
  await expect(page.locator(listItems).filter({ hasText: PENDING_TEXT })).toHaveCount(0, { timeout: 15_000 })
  await expect(page.getByTestId('memory-error')).toBeHidden()

  // 已确认列表出现 C（状态徽章为已确认）
  await page.getByTestId('memory-filter-confirmed').click()
  const confirmedItem = page.locator(listItems).filter({ hasText: PENDING_TEXT })
  await expect(confirmedItem).toBeVisible({ timeout: 15_000 })
  await expect(confirmedItem.getByTestId('status-badge')).toHaveText('已确认')
})

test('驳回流：驳回 → 迁出待确认并出现在已驳回（徽章已驳回）', async ({ page, request }) => {
  await seedClauses(request)

  await page.goto('/memory')
  const pendingItem = page.locator(listItems).filter({ hasText: PENDING_TEXT })
  await expect(pendingItem).toBeVisible({ timeout: 15_000 })

  await pendingItem.click()
  await page.getByTestId('page-shell-reject').click()

  await expect(page.locator(listItems).filter({ hasText: PENDING_TEXT })).toHaveCount(0, { timeout: 15_000 })

  await page.getByTestId('memory-filter-rejected').click()
  const rejectedItem = page.locator(listItems).filter({ hasText: PENDING_TEXT })
  await expect(rejectedItem).toBeVisible({ timeout: 15_000 })
  await expect(rejectedItem.getByTestId('status-badge')).toHaveText('已驳回')
})

test('空态如实：无 pending 条款 → 说明等待月批提炼（非骨架屏/错误态）', async ({ page }) => {
  await page.goto('/memory')

  const empty = page.getByTestId('memory-empty')
  await expect(empty).toBeVisible({ timeout: 15_000 })
  await expect(empty).toContainText('等待月批提炼')
  await expect(empty).toContainText('#327')
  // MUST NOT 以加载中或错误态冒充（spec 场景：空态如实）
  await expect(page.getByTestId('page-shell-skeleton')).toBeHidden()
  await expect(page.getByTestId('memory-error')).toBeHidden()
  await expect(page.locator(listItems)).toHaveCount(0)
})
