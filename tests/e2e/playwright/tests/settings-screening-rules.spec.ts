import { test, expect } from '@playwright/test'
import { rmSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * add-screening-rules-config-ui：设置中心「选股规则」分区端到端验证。
 *
 * 走真实 stub 后端（TESTING=1）；SCREENING_RULES_CONFIG_PATH 隔离在
 * data/test-e2e-screening-rules.json（backend webServer cwd = 仓库根 = playwright
 * 运行目录上三级）。spec 内 serial：保存用例会写设置文件，默认显示用例依赖无文件状态。
 */

const RULES_FILE = resolve(process.cwd(), '../../../data/test-e2e-screening-rules.json')

test.describe.configure({ mode: 'serial' })

test.beforeAll(() => {
  rmSync(RULES_FILE, { force: true })
})

test.afterAll(() => {
  rmSync(RULES_FILE, { force: true })
})

async function openPane(page: import('@playwright/test').Page) {
  await page.goto('/settings')
  await page.getByRole('button', { name: '选股规则' }).click()
  await expect(page.getByTestId('screening-rules-pane')).toBeVisible()
}

test('分区入口与默认显示：三项默认值、来源「默认值」、生效时机', async ({ page }) => {
  await openPane(page)
  await expect(page.getByTestId('rules-input-min-trading-days')).toHaveValue('60')
  await expect(page.getByTestId('rules-input-min-avg-amount-wan')).toHaveValue('5000')
  await expect(page.getByTestId('rules-input-l2-top-n')).toHaveValue('100')
  await expect(page.getByText('默认值')).toHaveCount(3)
  await expect(page.getByText(/下一次选股日批或手动补跑开始时生效/)).toBeVisible()
})

test('非法输入前端拦截：不发 PUT、展示错误', async ({ page }) => {
  await openPane(page)
  const putUrls: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'PUT' && req.url().includes('/api/settings/screening-rules')) {
      putUrls.push(req.url())
    }
  })
  await page.getByTestId('rules-input-l2-top-n').fill('0')
  await page.getByTestId('rules-save').click()
  await expect(page.getByTestId('rules-save-error')).toBeVisible()
  expect(putUrls).toHaveLength(0)
})

test('保存成功回显并持久化：刷新后为 8000 万元、来源「配置文件」', async ({ page }) => {
  await openPane(page)
  await page.getByTestId('rules-input-min-avg-amount-wan').fill('8000')
  await page.getByTestId('rules-save').click()
  await expect(page.getByTestId('rules-saved')).toBeVisible()

  await page.reload()
  await page.getByRole('button', { name: '选股规则' }).click()
  await expect(page.getByTestId('rules-input-min-avg-amount-wan')).toHaveValue('8000')
  await expect(page.getByText('配置文件').first()).toBeVisible()
})
