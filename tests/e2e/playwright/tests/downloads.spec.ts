import { test, expect } from '@playwright/test'

/**
 * add-download-center Task 5：/downloads 下载管理页 E2E 门禁
 *
 * 覆盖（stub 后端，无 LLM 依赖）：
 * 1. 侧边栏「下载管理」入口 → URL 变为 /downloads 且渲染下载管理页
 * 2. 直达 /downloads（baseURL 5173，等价刷新语义）→ 下载管理页渲染
 * 3. 空态 → 「返回聊天」回跳会话页（EmptyState）
 *
 * 空态成立条件：默认 config 的后端 webServer 注入 REPORTS_DIR=tmp/e2e-reports-8000
 * （目录不存在 → GET /api/files 返回 []）。CI 全新 checkout 天然为空；本地重复运行
 * 或同套件其它 spec 会在共享目录留下导出文件 → 本用例先经**业务 DELETE 接口**
 * 清空（不 route.fulfill、不 mock），再断言空态。
 *
 * 红线约束：不 mock /api/files 等业务接口（E2E 禁止 route.fulfill 被测系统）；
 * 删除回滚、筛选等副作用路径由组件测试（frontend/src/test/downloads/）覆盖。
 */
const API_BASE = 'http://localhost:8000'

/** 经业务 DELETE 接口清空共享导出目录（本地测试隔离；CI 下通常已是空目录）。 */
async function clearReports(page: import('@playwright/test').Page): Promise<void> {
  const resp = await page.request.get(`${API_BASE}/api/files`)
  if (!resp.ok()) return
  const files = (await resp.json()) as Array<{ file_name?: string }>
  for (const file of files) {
    if (file.file_name) {
      await page.request.delete(`${API_BASE}/api/files/${encodeURIComponent(file.file_name)}`)
    }
  }
}

test.describe('add-download-center: 下载管理页', () => {
  // 全量套件 8 worker 并发时本机（Windows）后端事件循环与 vite 代理会被
  // stub 流水线压出无响应窗口（smoke /api/health 同类环境性失败，基线存在），
  // 给本 spec 文件级重试兜底；隔离运行时 3 例均 2-3s 稳定通过。
  test.describe.configure({ retries: 2 })

  test('侧边栏「下载管理」入口跳转 /downloads 并渲染下载管理页', async ({ page }) => {
    // 下载管理入口现仅在折叠态图标栏(add-collapsible-sidebar 移除展开态底部导航)
    await page.addInitScript(() => localStorage.setItem('fa_sidebar_collapsed', '1'))
    await page.goto('/')
    const entry = page.getByTestId('sidebar-downloads-collapsed')
    await expect(entry).toBeVisible()
    await entry.click()
    await expect(page).toHaveURL(/\/downloads$/)
    await expect(page.getByTestId('download-center')).toBeVisible()
  })

  test('直达 /downloads 渲染下载管理页（刷新保持路由语义）', async ({ page }) => {
    await page.goto('/downloads')
    await expect(page).toHaveURL(/\/downloads$/)
    await expect(page.getByTestId('download-center')).toBeVisible()
    // 刷新（vite dev 下由 SPA fallback 提供 index.html）后仍停留在下载管理页
    await page.reload()
    await expect(page).toHaveURL(/\/downloads$/)
    await expect(page.getByTestId('download-center')).toBeVisible()
  })

  test('空态显示提示与「返回聊天」，点击回跳会话页', async ({ page }) => {
    // 全量套件并发下 /api/files 与页面加载都可能出现 15s+ 无响应窗口
    // （后端同步 graph.stream 阻塞事件循环 + vite 代理排队）。轮询重载等待
    // 响应窗口；reload 自身可能 ERR_ABORTED，捕获后继续下一轮。
    test.setTimeout(240_000)
    await clearReports(page)  // 本地重复运行/同套件其它 spec 可能留下导出文件
    await page.goto('/downloads')
    await expect(page.getByTestId('download-center')).toBeVisible()
    const empty = page.getByTestId('downloads-empty')
    const maxAttempts = 6
    let visible = false
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      try {
        await expect(empty).toBeVisible({ timeout: 20_000 })
        visible = true
        break
      } catch {
        if (attempt < maxAttempts - 1) {
          await clearReports(page)
          await page.reload({ timeout: 20_000 }).catch(() => {})
        }
      }
    }
    expect(visible, '轮询 6 次后空态仍未出现：/api/files 无响应或共享报告目录被并发写入').toBe(true)
    await empty.getByRole('button', { name: '返回聊天' }).click()
    await expect(page).toHaveURL(/\/$/)
    // 会话页 EmptyState 特征：标题与输入框（Kimi 风格改版后 hero 标题为「今天想研究什么？」）
    await expect(page.getByRole('heading', { name: '今天想研究什么？' })).toBeVisible()
  })
})
