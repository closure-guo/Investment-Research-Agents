import { test, expect } from '@playwright/test'

/**
 * watch 报告触发位链路 E2E（add-watch-trigger-tracking 任务 12 收口）。
 *
 * 覆盖：stub 管线产出 watch 决策（含结构化触发位 181/178，见
 * `_llm_utils._STUB_TRADE_DECISION`——取值以 stub K 线最新收盘 180 为基准，
 * 过价位门禁方向校验）→ 报告「交易决策」节渲染「上破触发位 / 下破触发位」
 * 行（值 = 申报值）→ 财务图表区 K 线卡渲染（触发参考线随 decision_levels
 * 进 chart_data；markLine 结构由前端 vitest `stockPriceKline.test.tsx`
 * 触发位正反用例钉死，canvas 内容不可 DOM 断言，此处钉链路可达性）。
 *
 * 环境：与 report-kline-chart.spec.ts 同一管线端口对（5175 → 8002
 * STUB_SCENARIO=pipeline，playwright.timeline.config.ts 拉起）。
 */

test.setTimeout(240_000)

test('深度分析完成后报告渲染 watch 触发位（交易决策节 + K 线卡）', async ({ page }) => {
  // 1. 进入应用并注入测试 API Key（管线端口对 5175 → 8002）
  await page.goto('http://localhost:5175')
  await page.evaluate(() => {
    localStorage.setItem('fa_api_key', 'stub-key-for-testing')
    localStorage.setItem('fa_user_id', 'user-watch-trigger')
  })
  await page.reload()

  // 2. 显式选中深度研究模式（EmptyState 两步下拉）
  await page.getByRole('button', { name: /模式/ }).click()
  await page.getByRole('button', { name: /深度研究.*5 层 Agent 流水线/ }).click()

  // 3. 发送深度分析请求（stub 确定性触发 5 层管线，trader/risk 产出 watch 决策）
  await page.getByPlaceholder(/输入/).fill('深度分析600519')
  await page.getByTestId('send-button').click()

  // 4. 报告完成终态：报告卡标题出现
  await expect(page.getByRole('heading', { name: '贵州茅台（600519）' })).toBeVisible({
    timeout: 150_000,
  })

  // 5. 交易决策节：watch 专属触发位行渲染申报值（stub 181 / 178）
  const report = page.getByTestId('report-full-section')
  const highLine = report.locator('li').filter({ hasText: '上破触发位' })
  await expect(highLine).toContainText('181', { timeout: 30_000 })
  const lowLine = report.locator('li').filter({ hasText: '下破触发位' })
  await expect(lowLine).toContainText('178')

  // 6. 财务图表区 K 线卡（触发参考线随 decision_levels 进 chart_data；
  //    卡标题已含「点线为触发位」语义，canvas 挂载证明 ECharts 渲染成功）
  const klineCard = page.getByTestId('chart-stock-price')
  await expect(klineCard).toBeVisible({ timeout: 30_000 })
  await expect(klineCard.getByRole('heading', { name: /股价 K 线/ })).toBeVisible()
  await expect(klineCard.locator('canvas').first()).toBeVisible()
})
