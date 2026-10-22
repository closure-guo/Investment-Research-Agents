import { test, expect } from '@playwright/test'

/**
 * 因子收割链路（add-factor-harvest-ui）只读 E2E。
 *
 * 铁律：不 mock 业务接口——直接打真实后端；数据随收割任务变化，因此只断言结构，
 * 不断言具体数字。区块只有两种合法形态：有数据（进度+漏斗+台账）或空态（未接入）。
 */
test.describe('因子收割链路（只读）', () => {
  test('区块渲染：进度/漏斗/分布/台账 或 未接入空态', async ({ page }) => {
    await page.goto('/factor-research')

    const section = page.getByTestId('factor-research-harvest')
    await expect(section).toBeVisible()

    const progress = page.getByTestId('factor-research-harvest-progress')
    const empty = page.getByTestId('factor-research-harvest-empty')
    await expect(progress.or(empty)).toBeVisible()

    if (await progress.isVisible()) {
      const text = (await progress.textContent()) ?? ''
      expect(text).toContain('批次')
      expect(text).toContain('剩余')

      for (const key of ['candidates', 'processed', 'gates', 'admitted']) {
        const card = page.getByTestId(`factor-research-harvest-step-${key}`)
        await expect(card).toBeVisible()
        await expect(card).toContainText(/\d/)
      }

      await expect(page.getByTestId('factor-research-harvest-gates')).toContainText('拒收分布')
      await expect(page.getByTestId('factor-research-harvest-admitted')).toBeVisible()
      await expect(page.getByTestId('factor-research-harvest-rejected')).toBeVisible()

      // 只读：区块内不得出现提交/启停类控件
      await expect(section.locator('button')).toHaveCount(0)
    } else {
      await expect(empty).toContainText('未接入')
    }

    // 主监控区块不受影响
    await expect(page.getByTestId('factor-research-supervisor')).toBeVisible()
  })
})
