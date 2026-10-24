import { test, expect } from '@playwright/test'

/**
 * refine-llm-settings-pane：LLM 配置分区端到端验证。
 *
 * 只走 UI 与 localStorage（后端不存 profile），不触发真实 LLM 调用。
 */

const PROFILE_STORE = {
  profiles: [
    {
      id: 'p-settings',
      name: 'E2E 配置',
      config: {
        apiKey: 'sk-e2e-saved',
        model: 'deepseek/deepseek-chat',
        baseUrl: 'https://api.deepseek.com/v1',
        thinking: 'enabled',
        apiForm: 'chat_completion',
      },
    },
  ],
  activeId: 'p-settings',
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((store) => {
    localStorage.setItem('fa_llm_profiles', JSON.stringify(store))
  }, PROFILE_STORE)
  await page.goto('/settings')
})

test('三段分组、只写密钥与眼睛切换', async ({ page }, testInfo) => {
  await expect(page.getByRole('heading', { name: '连接' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '模型' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '高级' })).toBeVisible()

  const key = page.getByTestId('api-key-input')
  await expect(key).toHaveValue('')
  await expect(key).toHaveAttribute('placeholder', /已保存/)
  await expect(key).toHaveAttribute('type', 'password')

  await page.getByRole('button', { name: '显示密钥' }).click()
  await expect(key).toHaveAttribute('type', 'text')
  await page.getByRole('button', { name: '隐藏密钥' }).click()
  await expect(key).toHaveAttribute('type', 'password')
  await page.screenshot({ path: testInfo.outputPath('llm-settings.png') })
})

test('字段级校验阻止保存，合法后保留原密钥写入 profile', async ({ page }) => {
  // 上下文长度非法
  await page.getByTestId('context-length-input').fill('0')
  await page.getByRole('button', { name: '确认' }).click()
  await expect(page.getByTestId('context-length-error')).toBeVisible()

  // Base URL 协议非法
  await page.getByTestId('context-length-input').fill('200000')
  await page.getByTestId('base-url-input').fill('ftp://example.com')
  await page.getByRole('button', { name: '确认' }).click()
  await expect(page.getByTestId('base-url-error')).toBeVisible()

  // 合法后保存：未输入新密钥，profile 保留原 key
  await page.getByTestId('base-url-input').fill('https://api.deepseek.com/v1')
  await page.getByRole('button', { name: '确认' }).click()
  await expect(page.getByTestId('context-length-error')).toHaveCount(0)
  await expect(page.getByTestId('base-url-error')).toHaveCount(0)

  const saved = await page.evaluate(() => {
    const raw = localStorage.getItem('fa_llm_profiles')
    return raw ? JSON.parse(raw) : null
  })
  expect(saved?.profiles?.[0]?.config?.apiKey).toBe('sk-e2e-saved')
  expect(saved?.profiles?.[0]?.config?.contextLength).toBe(200000)
})
