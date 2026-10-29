import { test, expect, type APIRequestContext } from '@playwright/test'

/**
 * F2 冒烟测试：验证前后端全栈可达
 *
 * 不触发 /api/analyze 或 /api/chat（那些是 F3 的 streaming/contract spec 范围）
 *
 * API 走 vite 代理（apiBase = config use.baseURL）：硬编码默认端口 8000 会在隔离
 * config（8001/5174）下打到宿主机 8000 上可能存在的其它服务（incident 038 家族；
 * 与 screening-page/daily-ops/market-regime/event-watchlist 的 apiBase 处置同款）。
 * APIRequestContext 显式走 IPv4（Windows 上 localhost 可能先解析 ::1 致 ECONNREFUSED）。
 */
function apiBase(): string {
  const base = test.info().project.use.baseURL ?? 'http://localhost:5173'
  return base.replace('//localhost', '//127.0.0.1')
}

test.describe('F2 smoke: 全栈可达', () => {
  test('前端首页可达且标题正确', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/Investment Research Agents/i)
  })

  test('后端 /api/health 返回 200', async ({ request }: { request: APIRequestContext }) => {
    const resp = await request.get(`${apiBase()}/api/health`)
    expect(resp.status()).toBe(200)
    const body = await resp.json()
    expect(body.status).toBe('ok')
  })

  test('TESTING 模式下 /api/test/seed 端点可用', async ({
    request,
  }: {
    request: APIRequestContext
  }) => {
    // 验证后端以 TESTING=1 启动（webServer 配置已注入环境变量）
    const resp = await request.post(`${apiBase()}/api/test/seed`, {
      data: { symbol: '300308' },
    })
    expect(resp.status()).toBe(200)
    const body = await resp.json()
    expect(body.mode).toBe('testing')
  })
})
