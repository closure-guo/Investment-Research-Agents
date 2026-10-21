import { test, expect } from '@playwright/test'

/**
 * F2 冒烟测试：验证前后端全栈可达
 *
 * 不触发 /api/analyze 或 /api/chat（那些是 F3 的 streaming/contract spec 范围）
 */
test.describe('F2 smoke: 全栈可达', () => {
  test('前端首页可达且标题正确', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/Investment Research Agents/i)
  })

  test('后端 /api/health 返回 200', async ({ request }) => {
    const resp = await request.get('http://localhost:8000/api/health')
    expect(resp.status()).toBe(200)
    const body = await resp.json()
    expect(body.status).toBe('ok')
  })

  test('TESTING 模式下 /api/test/seed 端点可用', async ({ request }) => {
    // 验证后端以 TESTING=1 启动（webServer 配置已注入环境变量）
    const resp = await request.post('http://localhost:8000/api/test/seed', {
      data: { symbol: '300308' },
    })
    expect(resp.status()).toBe(200)
    const body = await resp.json()
    expect(body.mode).toBe('testing')
  })
})
