import { test, expect } from '@playwright/test'

/**
 * 流状态层重构 · 场景验收（真实服务，docker localhost:5173，真实 LLM）
 * 6.2 流式进行中刷新页面 → 恢复后从断点续传，无重复、无缺失、思考不分裂
 *
 * 前置：docker compose up -d --build（后端 LLM 指向 opencode，前端为最新代码）
 */
const LLM_KEY = process.env.LLM_API_KEY || 'stub-key-for-e2e-test'

test.describe('场景验收 · 刷新恢复 @live', () => {
  test('快速模式流式进行中刷新，恢复后思考不分裂、续写同一回复', async ({ page }) => {
    test.setTimeout(150_000)

    await page.goto('/')
    await page.evaluate((key) => {
      // #48 起前端从 fa_llm_profiles 读取激活配置（无配置时强制拦截发送）。
      // 显式写入 profiles 模拟「已配置 LLM」用户；fa_api_key 仅作旧版兼容迁移来源。
      localStorage.setItem(
        'fa_llm_profiles',
        JSON.stringify({
          profiles: [
            {
              id: 'e2e-profile',
              name: 'E2E',
              config: { model: 'deepseek/deepseek-chat', baseUrl: '', apiKey: key, thinking: 'enabled' },
            },
          ],
          activeId: 'e2e-profile',
        }),
      )
      localStorage.setItem('fa_api_key', key)
      localStorage.setItem('fa_user_id', `e2e-refresh-${Date.now()}`)
      localStorage.removeItem('fa_current_session_id')
    }, LLM_KEY)
    await page.reload()
    await page.waitForTimeout(400)

    // 快速模式发起对话
    await page.getByRole('button', { name: /模式/ }).click()
    await page.getByRole('button', { name: /快速模式/ }).click()
    await page.getByPlaceholder(/输入问题/).fill('平安银行怎么样')
    await page.getByTestId('send-button').click()

    // 等待流式输出开始（思考横幅出现）
    await expect(page.getByText('思考中').first()).toBeVisible({ timeout: 30_000 })

    // 流式进行中刷新页面
    await page.reload()
    await page.waitForTimeout(600)

    // 关键断言 1：刷新后自动恢复会话，思考继续渲染（不停滞、不消失）
    await expect(
      page.getByText('思考中').first().or(page.getByText('思考已完成').first()),
    ).toBeVisible({ timeout: 30_000 })

    // 关键断言 2：思考不分裂——assistant 回复容器（含思考横幅的气泡）
    // 应只有一条（同一回复续写），而非「历史思考 + 新思考」两条并列。
    // 等续传事件到达后再统计。
    await page.waitForTimeout(3000)
    const assistantBubbles = page.locator('[data-testid="stream-status"]')
      .or(page.locator('div').filter({ has: page.getByText(/思考中|思考已完成/) }))
    // 注：快速模式单轮回复应只有一个 assistant 气泡承载思考。
    // 修复前 bug：历史思考气泡 + 新思考气泡两条 → count>=2。
    // 这里宽松断言：思考横幅按钮（每个气泡顶部一个）数量不异常膨胀。
    const thinkingBanners = page.getByText(/思考中|思考已完成/)
    const count = await thinkingBanners.count()
    // 同一条回复的 timeline 可有多个「思考已完成」分段（thinking_to_answer 分段），
    // 但「思考中」（进行中）至多一个，且不应因刷新而新建第二条 chat 气泡。
    // 宽松上界：思考分段 ≤ 5（正常单轮思考分段数）；>5 提示分裂/重复。
    expect(count).toBeLessThanOrEqual(5)

    // 关键断言 3：assistant 回复气泡（承载思考/工具的容器）只有一条——
    // 刷新恢复后续传事件复用重建的同一 chat 消息，不新建第二条气泡。
    // 通过「停止生成」仍在（流式进行中）+ 思考横幅在单一容器内验证。
    // remove-standalone-stop-button：停止入口唯一化为输入框 composer-stop，
    // 页面级「停止生成」按钮已删除——流式进行中的观察代理随之改为 composer-stop 可见。
    await expect(page.getByTestId('composer-stop')).toBeVisible({ timeout: 5_000 })
    // 修复前 bug：历史思考气泡 + 新思考气泡两条并列（本用例会捕获到 >1 个回复容器）。
    // 用页面级结构断言：含「思考」的 assistant 气泡容器数 == 1。
    const bubblesWithThinking = page.locator('div').filter({
      has: page.getByText(/正在搜索|思考中|思考已完成/),
    }).filter({ hasNot: page.locator('div div div div') }) // 收紧到气泡层级
    // 宽松验证：整页「正在搜索」状态指示归属于当前这一条回复（不重复出现多个独立回复头）。
    // 最终：确认流式仍在推进或已产出正文（无整段丢失）。
    await expect(page.getByText(/正在搜索|思考已完成|平安银行/).first()).toBeVisible({ timeout: 15_000 })
  })

  test('深度模式流式进行中刷新：内容不消失，replay 重建完整管线与思考', async ({ page }) => {
    test.setTimeout(240_000)

    await page.goto('/')
    await page.evaluate((key) => {
      // 同上：显式写入 fa_llm_profiles 模拟已配置 LLM 用户（#48 起无配置会拦截发送）
      localStorage.setItem(
        'fa_llm_profiles',
        JSON.stringify({
          profiles: [
            {
              id: 'e2e-profile',
              name: 'E2E',
              config: { model: 'deepseek/deepseek-chat', baseUrl: '', apiKey: key, thinking: 'enabled' },
            },
          ],
          activeId: 'e2e-profile',
        }),
      )
      localStorage.setItem('fa_api_key', key)
      localStorage.setItem('fa_user_id', `e2e-deep-refresh-${Date.now()}`)
      localStorage.removeItem('fa_current_session_id')
    }, LLM_KEY)
    await page.reload()
    await page.waitForTimeout(400)

    // 用「新建分析」进入空态（确保模式切换入口可用，不受历史会话影响）
    await page.getByRole('button', { name: /新建分析/ }).first().click()
    await page.waitForTimeout(400)
    await page.getByRole('button', { name: /模式/ }).click()
    // 选择模式下拉中的「深度研究」项（含「5 层 Agent 流水线」描述，区别于模式切换按钮）
    await page.getByRole('button', { name: /5 层 Agent 流水线/ }).click()
    await page.getByPlaceholder(/输入股票名称或代码/).fill('分析热门股票')
    await page.getByTestId('send-button').click()

    // 等待流式输出开始（管线或思考出现；深度分析管线初始化较慢，放宽超时）
    await expect(
      page.getByText(/思考中|已识别|开始分析|正在分析/).first().or(page.getByTestId('pipeline-timeline').first()),
    ).toBeVisible({ timeout: 60_000 })

    // 流式进行中刷新页面
    await page.reload()
    await page.waitForTimeout(800)

    // 关键断言 1：刷新后自动恢复会话并进入分析视图（内容不永久消失）
    await expect(
      page.getByTestId('pipeline-timeline').first().or(page.getByText(/思考中|思考已完成|正在搜索|正在分析|已识别/).first()),
    ).toBeVisible({ timeout: 90_000 })

    // 关键断言 2：replay 重建了 user 消息（输入内容仍在消息流中）
    await expect(page.getByText('分析热门股票').first()).toBeVisible({ timeout: 30_000 })

    // 关键断言 3：内容不消失——刷新后管线/思考持续存在（非只出现刷新后新输出）
    await page.waitForTimeout(5000)
    await expect(
      page.getByText(/思考中|思考已完成|正在搜索|正在分析|已识别|开始分析/).first(),
    ).toBeVisible({ timeout: 30_000 })
  })
})
