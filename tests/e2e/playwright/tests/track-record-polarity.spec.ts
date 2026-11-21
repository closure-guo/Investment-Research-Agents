import { expect, request, test } from '@playwright/test'

/**
 * add-price-polarity-contract（#606）：观点详情三价极性标注 E2E（track-record 专属套件）。
 *
 * seed 通道造两类数据态（快照透传见 /api/test/seed，#606）：
 * - 南方航空 600029：exit 极性声明行（B 型叙事价位）→ 止损/目标带惯例语义标注
 * - 广誉远 600771：存量无声明行（A 型错置价位原样）→ 「口径未声明」，不虚构惯例语义
 *
 * 红线：不 mock 业务接口——数据经 TESTING seed 端点落入专属测试库。
 */
test.describe.configure({ mode: 'serial' })

const BASE = 'http://127.0.0.1:8004'

async function seedPolarityRows(): Promise<void> {
  const ctx = await request.newContext()
  try {
    const resp = await ctx.post(`${BASE}/api/test/seed`, {
      data: {
        track_record_reset: true,
        track_record: {
          predictions: [
            {
              symbol: '600029.SH',
              symbol_name: '南方航空',
              direction: 'short',
              status: 'open',
              entry_price: 5.12,
              target_price: 5.3971,
              created_at: '2026-11-20T15:00:00',
              rationale_snapshot: {
                action: 'sell',
                sell_type: 'exit',
                declared_prices: {
                  entry_price: 5.12,
                  stop_loss: 4.9,
                  target_price: 5.3971,
                  polarity: 'exit',
                },
              },
            },
            {
              symbol: '600771.SH',
              symbol_name: '广誉远',
              direction: 'short',
              status: 'open',
              entry_price: 11.8,
              created_at: '2026-11-20T15:05:00',
              // 存量形态对齐 #606 实证：有 declared_prices（A 型错置价位）但无
              // polarity 声明——「有价可误读」才需要口径未声明标注
              rationale_snapshot: {
                action: 'sell',
                declared_prices: { entry_price: 11.8, stop_loss: 12.223, target_price: 10.954 },
              },
            },
          ],
        },
      },
    })
    expect(resp.ok()).toBeTruthy()
  } finally {
    await ctx.dispose()
  }
}

/** 经 API 取 prediction_id 后直达详情路由（列表页「当前观点区」含同名行，
 *  行点击选择器有歧义；直达 URL 与用户分享链接同路径，更稳）。 */
async function gotoDetail(page: import('@playwright/test').Page, symbol: string): Promise<void> {
  const ctx = await request.newContext()
  try {
    const resp = await ctx.get(`${BASE}/api/v1/track-record/predictions?symbol=${symbol}`)
    expect(resp.ok()).toBeTruthy()
    const rows = (await resp.json()) as { predictions: Array<{ prediction_id: string; symbol: string }> }
    const row = rows.predictions.find((r) => r.symbol === symbol)
    expect(row, `seed 行未找到：${symbol}`).toBeTruthy()
    await page.goto(`/track-record/predictions/${row!.prediction_id}`)
  } finally {
    await ctx.dispose()
  }
}

test.describe('观点详情：三价极性标注（#606）', () => {
  // 本 describe 造的 seed 行会破坏套件内「库内只有自己造的行」类空态前提
  // （pred-tabs 空态断言等）——套件 globalSetup 只复位一次，收尾自行复位。
  test.afterAll(async () => {
    const ctx = await request.newContext()
    try {
      await ctx.post(`${BASE}/api/test/seed`, { data: { track_record_reset: true } })
    } finally {
      await ctx.dispose()
    }
  })

  test('exit 极性行：清仓位/反弹减仓带语义标注', async ({ page }) => {
    await seedPolarityRows()
    await gotoDetail(page, '600029.SH')
    const card = page.getByTestId('prediction-decision')
    await expect(card).toBeVisible()
    await expect(card).toContainText('止损价（清仓位·跌破清出）')
    await expect(card).toContainText('目标价（反弹减仓带）')
    await expect(card).toContainText('4.90')
    await expect(card).toContainText('5.40')
    await expect(card).not.toContainText('口径未声明')
  })

  test('存量无声明行：口径未声明标注，不虚构惯例语义', async ({ page }) => {
    await gotoDetail(page, '600771.SH')
    const card = page.getByTestId('prediction-decision')
    await expect(card).toBeVisible()
    await expect(card).toContainText('口径未声明')
    await expect(card).toContainText('12.22')
    await expect(card).not.toContainText('清仓位')
    await expect(card).not.toContainText('反弹减仓带')
  })
})
