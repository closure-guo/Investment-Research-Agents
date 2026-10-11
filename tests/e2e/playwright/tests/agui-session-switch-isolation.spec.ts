import { test, expect } from '@playwright/test'

/**
 * issue #30 验收 E2E：快速模式会话切换内容隔离（AG-UI 通道现行架构）。
 *
 * 原 issue 六缺陷针对旧手写 SSE quickChat 架构，已被两次后续演进结构性消解：
 *   ① streamStore 重构（消息按会话键控、seq/去重/reader 归属校验入 store）
 *   ② add-assistant-ui-thread（quick 通道改 AG-UI HttpAgent，切走时 abort + 中断落库）
 * 本 spec 不复活旧断言，而是把「现行语义」钉进 E2E 门禁：
 *   - 会话 A（quick，已完成）内容不因后续会话的流式而串染
 *   - 会话 C（quick，流式中）被切走 → abort 中断落库（现行设计），切回内容不丢
 *   - 双向隔离：A 的追问不进 C，C 的中断内容不进 A
 *
 * 链路（真实链路，不 mock 业务接口——E2E 红线）：同 agui-chat.spec.ts
 *   quickChat → QuickThread → POST /api/agui/quick（TESTING=1 StubLLMClient 确定性输出）
 *   → SSE AG-UI 事件流 → Thread 流式渲染。
 *
 * 时序非脆弱性：切走动作在 stream-status 可见后立即执行——stub run 快于点击时
 * abort 守卫 no-op（isRunning=false，正常完成切换），慢于点击则走中断落库路径；
 * 两条路径满足同一组隔离断言，测试不因 stub 快慢翻转。
 */

const STUB_ANSWER = '这是一段测试用的固定回复。用于验证流式渲染的增量累积。'
const MSG_A1 = '会话A首问-苹果'
const MSG_C1 = '会话C独立问题-香蕉'
const MSG_A2 = '会话A追问-樱桃'

/** 配置测试 Key → 显式切快速模式（本地/CI 模式记忆态不定）→ 等输入框就绪 */
async function openFresh(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.evaluate(() => {
    localStorage.setItem('fa_api_key', 'stub-key-for-testing')
    localStorage.setItem('fa_user_id', 'user-agui-switch')
  })
  await page.reload()
  await expect(page.getByPlaceholder(/输入/).first()).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: /模式/ }).click()
  await page.getByRole('button', { name: /快速模式/ }).click()
}

/** 切换模式（会话中切换=开启新会话），以模式按钮文案变化为生效信号 */
async function switchMode(page: import('@playwright/test').Page, target: '快速模式' | '深度研究') {
  await page.getByRole('button', { name: /模式/ }).click()
  await page.getByRole('button', { name: new RegExp(target) }).first().click()
}

test.describe('快速模式会话切换内容隔离（issue #30 验收）', () => {
  test.setTimeout(180_000)
  test.describe.configure({ mode: 'serial' })

  test('A 完成后新建 C 流式中切走：双向无串染、C 中断落库、A 可续问', async ({ page }) => {
    await openFresh(page)

    // 1. 会话 A：quick 首问，等 RUN_FINISHED 终止态。
    // 不先断言 stream-status 可见——本地 stub 全程 <2s，visible 等待会错过
    // 在途窗口（终态 hidden + 内容断言才是真信号）
    await page.getByPlaceholder(/输入/).first().fill(MSG_A1)
    await page.getByTestId('send-button').click()
    await expect(page.getByTestId('agui-user-message').last()).toContainText(MSG_A1, { timeout: 15_000 })
    await expect(page.getByTestId('agui-stream-status')).toBeHidden({ timeout: 60_000 })
    await expect(page.getByTestId('agui-assistant-message').last()).toContainText(
      STUB_ANSWER,
      { timeout: 15_000 },
    )

    // 2. quick→deep→quick 两次模式切换铸造独立会话 C（现行 UI 建新会话路径）
    await switchMode(page, '深度研究')
    await expect(page.getByPlaceholder(/股票名称或代码/)).toBeVisible({ timeout: 15_000 })
    await switchMode(page, '快速模式')

    // 3. C 流式中（stream-status 可见即处于在途窗口）切回 A——侧边栏按标题点击
    await page.getByPlaceholder(/输入/).first().fill(MSG_C1)
    await page.getByTestId('send-button').click()
    await expect(page.getByTestId('agui-user-message').last()).toContainText(MSG_C1, { timeout: 15_000 })
    // 立即切走：stub 慢于点击则走 abort 中断落库路径，快于点击则正常完成——
    // 两路径满足同一组隔离断言（spec 头部「时序非脆弱性」）
    await page.getByTestId('session-list').getByText(MSG_A1, { exact: false }).first().click()
    // 切回 A：历史经 rebuildSession 快照走 MessageItem 渲染（stream-output），
    // agui-* testid 属挂载中的活 run——切回后恒为 0（agui-chat 刷新恢复同款语义）。
    // 现行设计：切走时 C 被 abort 中断落库；stub 快于点击则 C 正常完成——
    // 两路径下 A 内容同一断言。
    const histA = page.getByTestId('stream-output').filter({ hasText: STUB_ANSWER })
    await expect(histA.first()).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.msg-user', { hasText: MSG_A1 })).toHaveCount(1)
    // 隔离断言①：A 视图中 MUST NOT 出现 C 的消息
    await expect(page.getByTestId('thread-main')).not.toContainText(MSG_C1)

    // 4. A 续问：新一轮问答渲染在 A
    await page.getByPlaceholder(/输入/).first().fill(MSG_A2)
    await page.getByTestId('send-button').click()
    await expect(page.getByTestId('agui-user-message').last()).toContainText(MSG_A2, { timeout: 15_000 })
    await expect(page.getByTestId('agui-stream-status')).toBeHidden({ timeout: 60_000 })
    await expect(page.getByTestId('agui-user-message').last()).toContainText(MSG_A2)
    await expect(page.getByTestId('agui-assistant-message').last()).toContainText(
      STUB_ANSWER,
      { timeout: 15_000 },
    )

    // 5. 切到 C：C 的历史经 MessageItem 渲染（中断落库或完整回答均应可见）
    await page.getByTestId('session-list').getByText(MSG_C1, { exact: false }).first().click()
    await expect(page.locator('.msg-user', { hasText: MSG_C1 }).first()).toBeVisible({ timeout: 30_000 })
    // 隔离断言②：C 视图中 MUST NOT 出现 A 的追问内容
    await expect(page.getByTestId('thread-main')).not.toContainText(MSG_A2)

    // 6. 落库核验（真实 API 复核，非 mock）：A 与 C 的 chat_history 互不含对方消息
    const sessions = (await (
      await page.request.get('http://localhost:8000/api/sessions')
    ).json()).sessions as Array<{ session_id: string; display_name: string }>
    const sidA = sessions.find(s => s.display_name?.includes(MSG_A1))?.session_id
    const sidC = sessions.find(s => s.display_name?.includes(MSG_C1))?.session_id
    expect(sidA).toBeTruthy()
    expect(sidC).toBeTruthy()
    const apiHistA = (await (
      await page.request.get(`http://localhost:8000/api/sessions/${sidA}`)
    ).json()).chat_history as Array<{ role: string; content: string }>
    const apiHistC = (await (
      await page.request.get(`http://localhost:8000/api/sessions/${sidC}`)
    ).json()).chat_history as Array<{ role: string; content: string }>
    const textA = JSON.stringify(apiHistA)
    const textC = JSON.stringify(apiHistC)
    expect(textA).toContain(MSG_A1)
    expect(textA).toContain(MSG_A2)
    expect(textA).not.toContain(MSG_C1)
    expect(textC).toContain(MSG_C1)
    expect(textC).not.toContain(MSG_A2)
  })
})
