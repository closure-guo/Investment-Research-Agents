/* 人工验证驱动脚本（delta add-factor-research-control）：真实浏览器操作开关并截图。
 * 用法：node validate-control-live.cjs stop|start
 * 前置：真实栈在跑（后端 8000 读真实 data 桥 + 前端 5173 + distro 代理存活）。
 */
const { chromium } = require('playwright')
const fs = require('node:fs')

const OUT_DIR = 'D:/WorkSpace/finance_analysis_agent/.worktrees/factor-research-control/tests/validation'

async function state(page) {
  return {
    checked: await page.getByTestId('factor-research-control-switch').getAttribute('aria-checked'),
    disabled: await page.getByTestId('factor-research-control-switch').isDisabled(),
    text: (await page.getByTestId('factor-research-control-state').textContent()) ?? '',
  }
}

;(async () => {
  const phase = process.argv[2]
  fs.mkdirSync(OUT_DIR, { recursive: true })
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  await page.addInitScript(() => localStorage.setItem('fa_api_key', 'live-validation'))
  await page.goto('http://127.0.0.1:5175/', { waitUntil: 'domcontentloaded' })
  // 真实数据下 boot 会话恢复会抢路由（既有行为）；用应用内导航进页（与 E2E 入口用例同路径）
  await page.waitForTimeout(2500)
  await page.evaluate(() => {
    window.history.pushState({}, '', '/factor-research')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await page.getByTestId('factor-research-control-switch').waitFor({ timeout: 15_000 })

  if (phase === 'stop') {
    const s0 = await state(page)
    console.log('初始态:', JSON.stringify(s0))
    await page.screenshot({ path: `${OUT_DIR}/2026-10-18-frc-stop-1-running.png` })

    await page.getByTestId('factor-research-control-switch').click()
    await page.getByTestId('factor-research-control-confirm-dialog').waitFor({ timeout: 5_000 })
    await page.screenshot({ path: `${OUT_DIR}/2026-10-18-frc-stop-2-confirm.png` })
    console.log('确认弹窗:', await page.getByTestId('factor-research-control-confirm-dialog').textContent())

    await page.getByTestId('factor-research-control-confirm').click()
    // 待调和期：切换中 + 禁用
    await page.waitForFunction(
      () => document.querySelector('[data-testid="factor-research-control-state"]')?.textContent?.includes('切换中'),
      { timeout: 30_000 }
    )
    console.log('提交后(pending):', JSON.stringify(await state(page)))
    await page.screenshot({ path: `${OUT_DIR}/2026-10-18-frc-stop-3-pending.png` })

    // 等待研发侧受理：开关翻转为关
    await page.waitForFunction(
      () => document.querySelector('[data-testid="factor-research-control-switch"]')?.getAttribute('aria-checked') === 'false',
      { timeout: 90_000 }
    )
    await page.waitForFunction(
      () => !document.querySelector('[data-testid="factor-research-control-switch"]')?.hasAttribute('disabled'),
      { timeout: 30_000 }
    )
    console.log('调和完成:', JSON.stringify(await state(page)))
    await page.screenshot({ path: `${OUT_DIR}/2026-10-18-frc-stop-4-stopped.png` })
  } else if (phase === 'start') {
    console.log('启动前:', JSON.stringify(await state(page)))
    await page.getByTestId('factor-research-control-switch').click()
    await page.waitForFunction(
      () => document.querySelector('[data-testid="factor-research-control-switch"]')?.getAttribute('aria-checked') === 'true',
      { timeout: 90_000 }
    )
    await page.waitForFunction(
      () => !document.querySelector('[data-testid="factor-research-control-switch"]')?.hasAttribute('disabled'),
      { timeout: 30_000 }
    )
    console.log('重启完成:', JSON.stringify(await state(page)))
    await page.screenshot({ path: `${OUT_DIR}/2026-10-18-frc-start-1-running.png` })
  }
  await browser.close()
})().catch((e) => {
  console.error('FAILED:', e)
  process.exit(1)
})
