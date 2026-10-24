import { test, expect } from '@playwright/test'

/**
 * add-composer-stat-pills：输入框下方三枚计量控件的端到端验证。
 *
 * 数据链路全程真实：/api/test/sessions 造真库会话（含 usage）→ stats 端点聚合
 * → 前端控件渲染。唯一 stub 是 LLM（TESTING=1），与计量条无关。
 */

const PROFILE_STORE = {
  profiles: [
    {
      id: 'p-stats',
      name: 'E2E 配置',
      config: {
        apiKey: 'sk-e2e-saved',
        model: 'deepseek/deepseek-chat',
        baseUrl: 'https://api.deepseek.com/v1',
        thinking: 'enabled',
        apiForm: 'chat_completion',
        contextLength: 200000,
      },
    },
  ],
  activeId: 'p-stats',
}

async function seedSession(request: import('@playwright/test').APIRequestContext, name: string) {
  const resp = await request.post('/api/test/seed', {
    data: {
      session_type: 'chat',
      display_name: name,
      status: 'completed',
      chat_history: [
        { role: 'user', content: '茅台最近怎么样' },
        { role: 'assistant', content: '茅台近期震荡，估值处于历史中位。' },
        { role: 'user', content: '主要风险呢' },
        { role: 'assistant', content: '主要风险在需求端与批价回落。' },
      ],
      usage: { prompt: 120000, completion: 8000, total: 128000, context_peak: 45000 },
    },
  })
  expect(resp.ok()).toBeTruthy()
  const body = await resp.json()
  expect(body.session_id).toBeTruthy()
  return body.session_id as string
}

test('三枚计量控件展示真实轮次、token 与上下文占用', async ({ page, request }, testInfo) => {
  const name = `计量测试会话_${Date.now()}`
  await seedSession(request, name)

  await page.addInitScript((store) => {
    localStorage.setItem('fa_llm_profiles', JSON.stringify(store))
  }, PROFILE_STORE)
  await page.goto('/')

  // 侧栏会话 → 点击选中，触发 stats 拉取
  await page.getByText(name, { exact: true }).click()

  const stats = page.getByTestId('composer-stats')
  await expect(stats).toBeVisible()
  await expect(stats.getByText('2 轮 2 步')).toBeVisible()
  await expect(stats.getByText('12.8万')).toBeVisible()
  await expect(stats.getByText('23%')).toBeVisible()

  // 免责声明已从输入栏移除
  await expect(page.getByText('AI 生成仅供参考，不构成投资建议')).toHaveCount(0)

  // 会话统计面板
  await page.getByRole('button', { name: '会话统计' }).click()
  const activityDialog = page.getByRole('dialog')
  await expect(activityDialog).toBeVisible()
  await expect(activityDialog.getByText('轮次')).toBeVisible()
  await expect(activityDialog.getByText('步骤')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // Token 用量面板：互斥打开，展示精确值
  await page.getByRole('button', { name: 'Token 用量' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(1)
  await expect(page.getByRole('dialog').getByText('128,000')).toBeVisible()
  await expect(page.getByRole('dialog').getByText('120,000')).toBeVisible()
  await expect(page.getByRole('dialog').getByText('8,000', { exact: true })).toBeVisible()

  // 上下文面板：~ 估算前缀 + 峰值 / 窗口
  await page.getByRole('button', { name: '上下文已用' }).click()
  const contextPanel = page.locator('[data-composer-stat-panel="context"]')
  await expect(contextPanel).toBeVisible()
  await expect(contextPanel).toContainText('上下文已用')
  await expect(contextPanel).toContainText('~4.5万 / 20万')
  // 证据截图：三控件 + 上下文面板（test-results 下，不入库）
  await page.screenshot({ path: testInfo.outputPath('composer-stats.png') })
})
