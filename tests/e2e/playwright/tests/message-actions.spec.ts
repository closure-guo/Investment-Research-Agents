import { test, expect } from '@playwright/test'

/**
 * 消息操作条 E2E（修复：布局位移 + 重试仅最后一段 + 图标化四按钮）
 *
 * 环境：操作条挂在 AnalysisThread 的 chat 容器(stream-output)下;quick 通道
 * 的 run 走 AG-UI QuickThread 不渲染该容器且不持久化会话,故本 spec 用
 * STUB_SCENARIO=pipeline 的 5 层深度管线后端(8002/前端 5175,由
 * playwright.timeline.config.ts 拉起)驱动深度分析——完成后的 agent 摘要
 * 以 chat 消息经 AnalysisThread 渲染为 stream-output,可 hover 断言操作条。
 *
 * 断言：
 * 1. 深度分析完成后摘要消息出现;操作条为 hover-only 悬浮层(hover 才挂载,不占布局高度)
 * 2. hover 后四图标按钮,顺序 复制/重试/点赞/点踩,均为 FontAwesome 图标(无文字)
 * 3. 重试只出现在最后一段 agent 输出(历史消息 hover 无重试)
 */
test.setTimeout(300_000)

async function startDeepAnalysis(page: import('@playwright/test').Page, query: string) {
  await page.getByPlaceholder(/输入/).fill(query)
  await page.getByTestId('send-button').click()
  // 深度分析完成:报告卡标题「名称（代码）」出现(run_deep_analysis 完成)
  await expect(
    page.getByRole('heading', { name: '贵州茅台（600519）' }).first(),
  ).toBeVisible({ timeout: 150_000 })
  // 整个 run 结束:输入框停止按钮消失(isRunning 退出——appState 退出 analyzing 且无
  // streaming 消息)——报告卡出现后 agent 的最终摘要轮仍在流式,isRunning 仍 true 会隐藏重试按钮
  // remove-standalone-stop-button:停止入口唯一化为 composer-stop,原页面级「停止生成」按钮已删
  await expect(page.getByTestId('composer-stop')).toHaveCount(0, { timeout: 60_000 })
}

test.describe('消息操作条（复制/重试/点赞/点踩）', () => {
  test('深度分析摘要可 hover 四图标按钮(顺序固定),重试仅最后一段', async ({ page }) => {
    // 1. 进入 pipeline 前端(5175 → 8002 STUB_SCENARIO=pipeline)并注入测试 Key
    await page.goto('http://localhost:5175')
    await page.evaluate(() => {
      localStorage.setItem('fa_api_key', 'stub-key-for-testing')
      localStorage.setItem('fa_user_id', 'user-message-actions')
    })
    await page.reload()

    // 2. 显式选「深度研究 … 5 层 Agent 流水线」模式
    await page.getByRole('button', { name: /模式/ }).click()
    await page.getByRole('button', { name: /深度研究.*5 层 Agent 流水线/ }).click()

    // 3. 第一轮深度分析:完成后 agent 摘要 chat 消息经 AnalysisThread 渲染。
    // 用 .last() 取最后一段 agent 输出(agent 可能先发开场消息,历史段无重试)。
    await startDeepAnalysis(page, '深度分析600519')
    const last = page.getByTestId('stream-output').last()
    await expect(last).toBeVisible({ timeout: 30_000 })

    // 操作条为 hover-only 悬浮层(b585921):未 hover 时不挂载(不占布局高度)。
    // 先移开指针,断言未 hover 无操作条——旧断言依赖「指针恰好压在最后一条消息上」,
    // 在 CI 布局下不稳定(timeline suite 两次红的历史根因之一)。
    await page.mouse.move(0, 0)
    await expect(last.getByTestId('message-actions')).toHaveCount(0)

    // hover → 挂载四图标按钮,顺序固定;这是最后一段 agent 输出 → 含重试
    // toPass 包裹 hover+断言（issue #312）：hover-only 悬浮层的挂载与鼠标位置
    // 存在竞态（CI 慢机渲染滞后/滚动补偿移走指针）——重试竞态而不掩真回归
    // （UI 真坏时 toPass 预算内恒不满足仍红）
    const lastActions = last.getByTestId('message-actions')
    await expect(async () => {
      await last.hover()
      await expect(lastActions.locator('button')).toHaveCount(4)
    }).toPass({ timeout: 15_000 })
    const labels = await lastActions.locator('button').evaluateAll((bs) =>
      bs.map((b) => b.getAttribute('aria-label')),
    )
    expect(labels).toEqual(['复制', '重试', '点赞', '点踩'])
    await expect(lastActions.locator('button').nth(0).locator('i.fa-copy')).toBeVisible()
    await expect(lastActions.locator('button').nth(1).locator('i.fa-redo')).toBeVisible()
    await expect(lastActions.locator('button').nth(2).locator('i.fa-thumbs-up')).toBeVisible()
    await expect(lastActions.locator('button').nth(3).locator('i.fa-thumbs-down')).toBeVisible()
    await expect(lastActions).not.toContainText('重新生成')

    // 4. 第二轮深度分析:最后一条仍含重试;更早的历史段 hover 无重试
    await startDeepAnalysis(page, '深度分析600519')
    const outputs = page.getByTestId('stream-output')
    await expect(outputs.count()).resolves.toBeGreaterThanOrEqual(2)

    const histActions = outputs.nth(0).getByTestId('message-actions')
    await expect(async () => {
      await outputs.nth(0).hover()
      await expect(histActions.locator('button')).toHaveCount(3)
    }).toPass({ timeout: 15_000 })
    const histLabels = await histActions.locator('button').evaluateAll((bs) =>
      bs.map((b) => b.getAttribute('aria-label')),
    )
    expect(histLabels).toEqual(['复制', '点赞', '点踩'])

    const finalActions = outputs.last().getByTestId('message-actions')
    await expect(async () => {
      await outputs.last().hover()
      await expect(finalActions.locator('button')).toHaveCount(4)
    }).toPass({ timeout: 15_000 })
    const finalLabels = await finalActions.locator('button').evaluateAll((bs) =>
      bs.map((b) => b.getAttribute('aria-label')),
    )
    expect(finalLabels).toEqual(['复制', '重试', '点赞', '点踩'])
  })
})
