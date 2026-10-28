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
const RULES_URL = '/api/settings/screening-rules'

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

test('分区入口与默认显示：三项默认值、逐项来源「默认值」、生效时机', async ({ page }) => {
  await openPane(page)
  await expect(page.getByTestId('rules-input-min-trading-days')).toHaveValue('60')
  await expect(page.getByTestId('rules-input-min-avg-amount-wan')).toHaveValue('5000')
  await expect(page.getByTestId('rules-input-l2-top-n')).toHaveValue('100')
  await expect(page.getByTestId('rules-input-min-trading-days-source')).toHaveText('默认值')
  await expect(page.getByTestId('rules-input-min-avg-amount-wan-source')).toHaveText('默认值')
  await expect(page.getByTestId('rules-input-l2-top-n-source')).toHaveText('默认值')
  await expect(page.getByText(/下一次选股日批或手动补跑开始时生效/)).toBeVisible()
})

test('非法输入前端拦截：不发 PUT、展示错误', async ({ page }) => {
  await openPane(page)
  const putUrls: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'PUT' && req.url().includes(RULES_URL)) {
      putUrls.push(req.url())
    }
  })
  await page.getByTestId('rules-input-l2-top-n').fill('0')
  await page.getByTestId('rules-save').click()
  await expect(page.getByTestId('rules-save-error')).toBeVisible()
  expect(putUrls).toHaveLength(0)
})

test('保存成功回显并持久化：PUT 体万元换算为元、刷新后 8000 万元来源「配置文件」', async ({ page }) => {
  await openPane(page)
  const putBodies: Array<Record<string, number>> = []
  page.on('request', (req) => {
    if (req.method() === 'PUT' && req.url().includes(RULES_URL)) {
      putBodies.push(JSON.parse(req.postData() ?? '{}') as Record<string, number>)
    }
  })
  await page.getByTestId('rules-input-min-avg-amount-wan').fill('8000')
  await page.getByTestId('rules-save').click()
  await expect(page.getByTestId('rules-saved')).toBeVisible()
  // 核心数值不变量：1 万元 = 10⁴ 元，双向无舍入（delta「万元口径展示与保存」场景）
  expect(putBodies).toHaveLength(1)
  expect(putBodies[0]).toEqual({ min_trading_days: 60, min_avg_amount_20d: 80000000, l2_top_n: 100 })
  await expect(page.getByTestId('rules-input-min-avg-amount-wan-source')).toHaveText('配置文件')

  await page.reload()
  await page.getByRole('button', { name: '选股规则' }).click()
  await expect(page.getByTestId('rules-input-min-avg-amount-wan')).toHaveValue('8000')
  await expect(page.getByTestId('rules-input-min-avg-amount-wan-source')).toHaveText('配置文件')
})

test('加载失败隔离：GET 故障 → 原位错误提示，不渲染保存面', async ({ page }) => {
  // route.abort 故障注入（红线明示合法且推荐）；不影响其他用例与后端本身
  await page.route(`**${RULES_URL}`, (route) => route.abort())
  await page.goto('/settings')
  await page.getByRole('button', { name: '选股规则' }).click()
  await expect(page.getByTestId('rules-load-error')).toBeVisible()
  await expect(page.getByTestId('rules-save')).not.toBeVisible()
})
