import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * 批量分析并发设置 E2E（delta add-analysis-concurrency-setting）。
 *
 * 红线：不 mock 被测系统——设置经真实 PUT /api/settings/analysis-concurrency 落盘，
 * 页面经真实 GET 读取；刷新后从同一后端设置文件回读。fixture 仅把真实设置归一到 10。
 *
 * 后端 ANALYSIS_CONCURRENCY_PATH 由 playwright config 注入独立测试文件，
 * 不读写生产 data/settings/analysis_concurrency.json。
 */
function apiBase(): string {
  const base = test.info().project.use.baseURL ?? 'http://localhost:5173'
  return base.replace('//localhost', '//127.0.0.1')
}

async function setConcurrency(request: APIRequestContext, value: number) {
  const resp = await request.put(`${apiBase()}/api/settings/analysis-concurrency`, {
    data: { value },
  })
  expect(resp.ok()).toBeTruthy()
}

test('会话管理并发设置：读取默认 10、保存 7、刷新后仍为 7', async ({ page, request }) => {
  // fixture 归一（真实 API，不是 mock）：把测试后端设置恢复到默认 10
  await setConcurrency(request, 10)

  await page.goto('/settings')
  await page.getByTestId('settings-nav-sessions').click()

  const input = page.getByTestId('sessions-concurrency-input')
  await expect(input).toHaveValue('10')

  await input.fill('7')
  await page.getByTestId('sessions-concurrency-save').click()
  await expect(page.getByTestId('sessions-concurrency-saved')).toContainText('已保存')
  await expect(input).toHaveValue('7')

  // 刷新后从后端设置文件回读（持久化生效）
  await page.reload()
  await page.getByTestId('settings-nav-sessions').click()
  await expect(page.getByTestId('sessions-concurrency-input')).toHaveValue('7')

  // 收尾恢复默认，避免影响同套件其它 spec
  await setConcurrency(request, 10)
})
