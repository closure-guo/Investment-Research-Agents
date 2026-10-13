import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * add-market-snapshot T10：行情快照失败态全局横幅 E2E 门禁。
 *
 * 红线：不 mock 业务接口——告警态经 TESTING=1 的 /api/test/seed 真实写入快照库
 * （playwright.config.ts webServer env SNAPSHOT_DB_PATH=data/test-e2e-snapshot.db），
 * 前端经真实 GET /api/snapshot/status（60s 轮询，挂载即首轮）读取渲染。
 *
 * 用例串行（serial）：三例共享同一快照库 meta，seed/reset 互相覆盖，并行会互踩；
 * 每例前后均 reset，避免遗留 stale 态让其他 spec 的页面顶部出现横幅（共享库泄漏）。
 */
test.describe.configure({ mode: 'serial', timeout: 60_000 })

// 当天（北京时区）：D5「当日失败立即告警」判定基准（后端亦取北京时间）
const todayBeijing = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date())

// D5 逐字文案锚点（后端 compute_snapshot_status 给定）
const STALE_TEXT = '行情快照未更新（数据日期: 2026-10-09）'

// 清空快照三表：新库无 last_attempt_at → D5 fresh-DB 分支 → 无横幅
async function resetSnapshot(request: APIRequestContext) {
  const resp = await request.post('/api/test/seed', { data: { snapshot_reset: true } })
  expect(resp.ok()).toBeTruthy()
}

test.beforeEach(async ({ request }) => {
  await resetSnapshot(request)
})

test.afterEach(async ({ request }) => {
  await resetSnapshot(request)
})

test.afterAll(async ({ request }) => {
  await resetSnapshot(request)
})

test('快照失败态 → 全局横幅可见且文案含数据日期', async ({ page, request }) => {
  const seedResp = await request.post('/api/test/seed', {
    data: {
      snapshot_reset: true,
      snapshot: {
        last_success_date: '2026-10-09',
        last_error_date: todayBeijing,
        last_error: 'boom',
      },
    },
  })
  expect(seedResp.ok()).toBeTruthy()

  await page.goto('/')
  const banner = page.getByTestId('snapshot-stale-banner')
  await expect(banner).toBeVisible({ timeout: 15_000 })
  await expect(banner).toContainText(STALE_TEXT)
})

test('快照成功态（当天已更新）→ 无横幅', async ({ page, request }) => {
  const seedResp = await request.post('/api/test/seed', {
    data: { snapshot_reset: true, snapshot: { last_success_date: todayBeijing } },
  })
  expect(seedResp.ok()).toBeTruthy()

  // 锚定首轮轮询响应后再断言，确保「无横幅」是读到成功态而非请求未返回
  const statusResp = page.waitForResponse((r) => r.url().includes('/api/snapshot/status'))
  await page.goto('/')
  expect((await statusResp).ok()).toBeTruthy()
  await expect(page.getByTestId('snapshot-stale-banner')).toHaveCount(0)
})

test('告警不阻断：stale 态下新分析输入框保持可用', async ({ page, request }) => {
  const seedResp = await request.post('/api/test/seed', {
    data: {
      snapshot_reset: true,
      snapshot: {
        last_success_date: '2026-10-09',
        last_error_date: todayBeijing,
        last_error: 'boom',
      },
    },
  })
  expect(seedResp.ok()).toBeTruthy()

  await page.goto('/')
  await expect(page.getByTestId('snapshot-stale-banner')).toBeVisible({ timeout: 15_000 })
  // 首页空态主输入框（deep 模式 placeholder；既有 spec 同款定位）
  await expect(page.getByPlaceholder(/输入股票名称或代码/).first()).toBeEnabled()
})
