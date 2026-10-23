import { test, expect } from '@playwright/test'

/**
 * add-quick-mode-operator：快速模式系统操作 E2E。
 *
 * 真实链路（不 mock 业务接口）：
 *   QuickThread → POST /api/agui/quick（TESTING=1 + STUB_SCENARIO=operator）
 *   → 模型真实调用系统工具（start_stock_analysis / get_daily_ops_status）
 *   → 工具回执经 AG-UI TOOL_CALL_RESULT 返回 → 快速对话复述。
 */

async function openQuickAndSend(
  page: import('@playwright/test').Page,
  message: string,
) {
  await page.goto('/')
  await page.evaluate(() => {
    localStorage.setItem('fa_api_key', 'stub-key-for-testing')
    localStorage.setItem('fa_user_id', 'user-operator-e2e')
  })
  await page.reload()

  await page.getByRole('button', { name: /模式/ }).click()
  await page.getByRole('button', { name: /快速模式/ }).click()

  await page.getByPlaceholder(/输入问题/).fill(message)
  await page.getByTestId('send-button').click()
}

test.describe('快速模式系统操作（add-quick-mode-operator）', () => {
  test('分析请求派单到独立 analysis 会话', async ({ page }) => {
    await openQuickAndSend(page, '分析茅台')

    const assistantMessage = page.getByTestId('agui-assistant-message')

    // 工具横幅：模型真实发起 start_stock_analysis（非预注入/伪造）
    await expect(assistantMessage).toContainText('start_stock_analysis', { timeout: 30_000 })
    // 回执复述：不声称分析已完成
    await expect(assistantMessage).toContainText('已启动贵州茅台', { timeout: 30_000 })
    // 结构化回执卡片：可一键打开派生分析会话
    await expect(page.getByTestId('agui-open-analysis')).toBeVisible({ timeout: 30_000 })
    // 双轨隔离：快速对话不出现管线节点事件
    await expect(assistantMessage).not.toContainText('node_start')
    await expect(assistantMessage).not.toContainText('node_complete')

    // 真实后端核验：独立 analysis 会话已创建
    await expect
      .poll(
        async () => {
          const resp = await page.request.get('/api/sessions')
          if (!resp.ok()) return null
          const body = (await resp.json()) as {
            sessions: Array<{ session_id: string; display_name?: string }>
          }
          const target = body.sessions.find((s) =>
            (s.display_name ?? '').includes('贵州茅台 深度分析'),
          )
          return target?.session_id ?? null
        },
        { timeout: 30_000 },
      )
      .not.toBeNull()

    const sessionsResp = await page.request.get('/api/sessions')
    const { sessions } = (await sessionsResp.json()) as {
      sessions: Array<{ session_id: string; display_name?: string }>
    }
    const sessionId = sessions.find((s) =>
      (s.display_name ?? '').includes('贵州茅台 深度分析'),
    )!.session_id

    const detailResp = await page.request.get(`/api/sessions/${sessionId}`)
    expect(detailResp.ok()).toBeTruthy()
    const detail = (await detailResp.json()) as { session_type?: string; status?: string }
    expect(detail.session_type).toBe('analysis')
  })

  test('日批状态查询走只读工具', async ({ page }) => {
    await openQuickAndSend(page, '日批现在什么状态')

    const assistantMessage = page.getByTestId('agui-assistant-message')
    await expect(assistantMessage).toContainText('get_daily_ops_status', {
      timeout: 30_000,
    })
    await expect(assistantMessage).toContainText('日批状态已读取', { timeout: 30_000 })
  })
})
