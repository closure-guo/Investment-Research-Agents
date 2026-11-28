import { expect, request, test } from '@playwright/test'

/**
 * update-outcome-band-volatility-scaled（2026-11-28 拍板）：战绩页连续超额读数条 E2E。
 *
 * 验收点（只读展示，不改判定状态机）：
 * 1. 连续超额主读数（n / 去重标的 / 均值 / 中位 / 超额胜率 / t）渲染；
 * 2. 同窗随机基准不可用时**如实披露原因**，不折算 0（本套件专属库无指数收盘序列，
 *    故基线必然不可用——这是设计内的降级态）；
 * 3. 波动率自适应带宽披露（切点 + 新口径已结算条数）。
 *
 * 红线：不 mock 业务接口——数据经 TESTING seed 端点落入专属测试库
 * （playwright.track-record.config.ts：SESSIONS_DB_PATH 与快照库均隔离）。
 */
test.describe.configure({ mode: 'serial' })

const BASE = 'http://127.0.0.1:8004'

/** 12 条已结算 long 行：7 正 5 负（超额胜率 58.3%），10 个去重标的（两个标的各重复一次）。 */
async function seedSettledRows(): Promise<void> {
  const ctx = await request.newContext()
  try {
    const excesses = [0.05, 0.04, 0.03, 0.02, 0.01, 0.06, 0.02, -0.02, -0.01, -0.03, -0.02, -0.01]
    const symbols = [
      '600001.SH', '600001.SH', '600002.SH', '600003.SH', '600004.SH', '600005.SH',
      '600006.SH', '600007.SH', '600007.SH', '600008.SH', '600009.SH', '600010.SH',
    ]
    const resp = await ctx.post(`${BASE}/api/test/seed`, {
      data: {
        track_record_reset: true,
        track_record: {
          predictions: excesses.map((excess, i) => ({
            symbol: symbols[i],
            symbol_name: `示例${i + 1}`,
            direction: 'long',
            status: excess > 0 ? 'resolved_win' : 'resolved_loss',
            entry_price: 10,
            created_at: '2026-09-01T10:00:00',
            resolved_at: '2026-09-03',
            settle_entry_price: 10,
            exit_price: 10 * (1 + excess),
            raw_return: excess,
            excess_return: excess,
            neutral_band_used: 0.03,
          })),
        },
      },
    })
    expect(resp.ok()).toBeTruthy()
  } finally {
    await ctx.dispose()
  }
}

test('连续超额 / 去重标的 / 带宽读数条与基准降级披露', async ({ page }) => {
  await seedSettledRows()
  await page.goto('/track-record')
  const strip = page.getByTestId('track-record-continuous')
  await expect(strip).toBeVisible()
  // n=12 且去重标的 10 只（标签同时披露两者：重复计数不能冒充样本量）
  await expect(strip.getByTestId('continuous-label')).toContainText('n=12')
  await expect(strip.getByTestId('continuous-label')).toContainText('去重标的 10 只')
  // 7/12 = 58.3%
  await expect(strip).toContainText('超额胜率 58.3%')
  // 均值 0.011667 → +1.17%
  await expect(strip).toContainText('均值 +1.17%')
  // 带宽披露：切点后 12 条带带宽落库
  const band = strip.getByTestId('track-record-band')
  await expect(band).toContainText('2026-11-28 切点起')
  await expect(band).toContainText('新口径已结算 12 条')
  // 专属测试库无指数收盘序列 → 基线不可用必须如实披露（不绘图、不折算 0）
  await expect(strip.getByTestId('track-record-baseline-unavailable')).toBeVisible()
})
