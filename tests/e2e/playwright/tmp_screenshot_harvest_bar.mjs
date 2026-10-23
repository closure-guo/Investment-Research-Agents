// 一次性取证脚本（T8 完成态进度条修正）：真实栈截图 + 进度条存在性读数。
// 用法：node tmp_screenshot_harvest_bar.mjs
import { chromium } from 'playwright'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

await page.goto('http://localhost:5173/factor-research', { waitUntil: 'networkidle' })

const section = page.getByTestId('factor-research-harvest')
await section.waitFor({ state: 'visible', timeout: 20000 })

const status = (await page.getByTestId('factor-research-harvest-status').textContent()) ?? ''
const progress = (await page.getByTestId('factor-research-harvest-progress').textContent()) ?? ''
const barCount = await page.getByTestId('factor-research-harvest-bar').count()

console.log('status:', status)
console.log('progress:', progress.replace(/\s+/g, ' ').trim())
console.log('harvest-bar count:', barCount)

await section.screenshot({ path: 'tmp/harvest-bar-after-fix.png' })
await page.screenshot({ path: 'tmp/harvest-section-after-fix.png' })

await browser.close()
