import { test, expect } from '@playwright/test'

/**
 * add-assistant-ui-thread Task 4.2：quick 模式 AG-UI 通道对话流 E2E 门禁。
 *
 * 链路（真实链路，不 mock 业务接口——E2E 红线）：
 *   发送 → 前端 quickChat → QuickThread（assistant-ui Thread）
 *   → POST /api/agui/quick（TESTING=1 下 build_agent 走 StubLLMClient 确定性输出）
 *   → SSE AG-UI 事件流（RUN_STARTED → REASONING / TEXT_MESSAGE 事件族 → RUN_FINISHED）
 *   → Thread 流式渲染 → RUN_FINISHED 终止态 → 刷新恢复（rebuildSession 快照渲染历史）。
 *
 * 与旧 spec streaming.spec.ts 的区别：旧 spec 走 /api/chat 旧通道（streamStore 渲染），
 * 本 spec 走 assistant-ui 渲染路径（QuickThread / agui-* testid）；前端 quick 发送
 * 已改走 /api/agui/quick（双轨隔离：深度模式 /api/stream 零改动）。
 *
 * stub 确定性输出（StubLLMClient 默认场景）：
 *   reasoning = "## 分析思路\n用户询问了一个测试问题，我需要给出简短回答。"
 *   answer    = "这是一段测试用的固定回复。用于验证流式渲染的增量累积。"
 */

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000'

/** 配置测试 Key → 切快速模式 → 发送一条消息（走 AG-UI 通道） */
async function openAndSendQuickMessage(page: import('@playwright/test').Page, message: string) {
  await page.goto('/')
  await page.evaluate(() => {
    localStorage.setItem('fa_api_key', 'stub-key-for-testing')
    localStorage.setItem('fa_user_id', 'user-agui-e2e')
  })
  await page.reload()

  // 切换到快速模式（EmptyState「模式：」下拉菜单，同 streaming.spec.ts 调整说明）
  await page.getByRole('button', { name: /模式/ }).click()
  await page.getByRole('button', { name: /快速模式/ }).click()

  await page.getByPlaceholder(/输入问题/).fill(message)
  await page.getByTestId('send-button').click()
}

test.describe('AG-UI quick 通道对话流（assistant-ui Thread 渲染）', () => {
  // 全量门禁 8 并发 worker 下 vite/后端争用，page.reload 等 load 可超默认 30s
  // （环境性失败族，归因同 tests/validation/2026-08-29-* §E2E）——给足余量。
  test.setTimeout(90_000)

  test('发送 → 流式渲染 → RUN_FINISHED 终止态 + 落库全文一致', async ({ page }) => {
    const message = 'AGUI通道对话流测试'
    await openAndSendQuickMessage(page, message)

    // assistant-ui Thread 渲染路径接管新 run（挂载后以用户气泡进入 Thread 为准——
    // 空 Thread 零高度，toBeVisible 会误判 hidden）
    const thread = page.getByTestId('agui-thread')
    await expect(thread).toBeAttached({ timeout: 10_000 })
    await expect(page.getByTestId('agui-user-message')).toContainText(message, { timeout: 10_000 })

    // 流式指示器出现（RUN_STARTED 后 running=true）
    const streamStatus = page.getByTestId('agui-stream-status')
    await expect(streamStatus).toBeVisible({ timeout: 10_000 })

    // 流式增量渲染（stub 首 chunk 需等后端 ReAct Agent 初始化 + SSE 传输，给足 timeout）
    const assistantMessage = page.getByTestId('agui-assistant-message')
    await expect(assistantMessage).toContainText('这是', { timeout: 20_000 })

    // 思考段（REASONING_MESSAGE_* → Reasoning part）渲染 stub 思维链
    await expect(assistantMessage).toContainText('分析思路', { timeout: 10_000 })

    // RUN_FINISHED 终止态：全部增量累积完成 + 指示器消失（running=false）
    await expect(assistantMessage).toContainText('增量累积', { timeout: 15_000 })
    await expect(streamStatus).toBeHidden({ timeout: 15_000 })

    // 后端落库核验（经 API 只读校验，非 mock）：会话终止态 + 分块拼接 == 落库全文
    const sessionsResp = await page.request.get(`${API_BASE}/api/sessions`)
    expect(sessionsResp.ok()).toBeTruthy()
    const { sessions } = (await sessionsResp.json()) as {
      sessions: Array<{ session_id: string; display_name?: string }>
    }
    const target = sessions.find(s => (s.display_name ?? '').includes(message))
    expect(target, 'RUN_STARTED.thread_id 回传的会话应出现在会话列表').toBeTruthy()
    if (!target) return

    const detailResp = await page.request.get(`${API_BASE}/api/sessions/${target.session_id}`)
    expect(detailResp.ok()).toBeTruthy()
    const detail = (await detailResp.json()) as {
      status: string
      chat_history: Array<{ role: string; content: string }>
    }
    expect(detail.status).toBe('completed')
    const assistantEntry = [...detail.chat_history].reverse().find(m => m.role === 'assistant')
    expect(assistantEntry?.content).toContain('固定回复。')
    expect(assistantEntry?.content).toContain('增量累积。')
  })

  test('刷新恢复：快照渲染历史（MessageItem 一次呈现，Thread 无残留）', async ({ page }) => {
    const message = 'AGUI刷新恢复测试'
    await openAndSendQuickMessage(page, message)

    // 等待本轮 run 完成（终态先落库再下发，刷新时快照必已含全文）
    const assistantMessage = page.getByTestId('agui-assistant-message')
    await expect(assistantMessage).toContainText('增量累积', { timeout: 20_000 })
    await expect(page.getByTestId('agui-stream-status')).toBeHidden({ timeout: 15_000 })

    // 刷新 → fa_current_session_id 自动恢复 → rebuildSession 快照渲染历史
    await page.reload()
    const historyEntry = page.getByTestId('stream-output').filter({ hasText: '固定回复' })
    await expect(historyEntry).toBeVisible({ timeout: 20_000 })

    // 历史快照渲染恰好一次（不重复、不串流）
    await expect(historyEntry).toHaveCount(1)
    await expect(historyEntry).toContainText('增量累积')

    // user 历史气泡（MessageItem 路径，.msg-user）恰好一次
    const userHistory = page.locator('.msg-user', { hasText: message })
    await expect(userHistory).toHaveCount(1)

    // Thread 重挂载后为空壳：本 mount 无新 run，历史不经 Thread 二次渲染
    await expect(page.getByTestId('agui-user-message')).toHaveCount(0)
    await expect(page.getByTestId('agui-assistant-message')).toHaveCount(0)
  })
})
