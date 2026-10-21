import { test, expect } from '@playwright/test'
import * as path from 'path'

/**
 * 重构前视觉基线截图采集（refactor-ui-design-system Task 2）
 *
 * 一次性采集工具，不属于常规 E2E 门禁断言：
 * - 仅在设置 BASELINE_DIR 环境变量时运行，否则整组跳过（避免污染日常门禁）
 * - 前端源码须处于基线提交 cc00bc0（main）状态运行本 spec，
 *   产出存档至 tests/validation/ui-baseline/（git 跟踪）
 *
 * 用法：
 *   BASELINE_DIR=<abs path>/tests/validation/ui-baseline npx playwright test visual-baseline.spec.ts
 *
 * 场景：
 * 1. 空态页 fullPage 截图 → empty-state.png
 * 2. 打开 LLM 设置弹窗后截图 → settings-modal.png
 *    注：基线 cc00bc0 的 header 仅在非空态渲染（App.tsx 空态走 EmptyState 分支），
 *    空态下设置入口为 EmptyState 内「去配置/修改」文本按钮（onClick=setShowSettings(true)），
 *    与 header 齿轮按钮打开同一 SettingsModal。
 */
const BASELINE_DIR = process.env.BASELINE_DIR ?? ''

test.describe('视觉基线截图采集（refactor-ui-design-system Task 2）', () => {
  // 未指定输出目录时跳过：本 spec 是一次性采集工具，不进常规门禁
  test.skip(!BASELINE_DIR, '需要 BASELINE_DIR 环境变量指定截图输出目录')

  test('采集空态页与设置弹窗基线截图', async ({ page }) => {
    await page.goto('/')

    // 空态页就绪：标题 + 输入框可见
    await expect(page.getByRole('heading', { name: 'Investment Research Agents' })).toBeVisible()
    await expect(page.locator('textarea').first()).toBeVisible()

    await page.screenshot({ fullPage: true, path: path.join(BASELINE_DIR, 'empty-state.png') })

    // 打开设置弹窗：空态下优先 header 齿轮按钮（fa-cog），否则 EmptyState 内「去配置/修改」入口
    const headerCog = page.locator('header button:has(i.fa-cog)')
    if ((await headerCog.count()) > 0) {
      await headerCog.first().click()
    } else {
      await page.getByRole('button', { name: /去配置|修改/ }).click()
    }

    // SettingsModal 就绪：标题「LLM 配置」可见，再留 300ms 让过渡动画收尾
    await expect(page.getByRole('heading', { name: 'LLM 配置' })).toBeVisible()
    await page.waitForTimeout(300)

    await page.screenshot({ fullPage: true, path: path.join(BASELINE_DIR, 'settings-modal.png') })
  })
})
