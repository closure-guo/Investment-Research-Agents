import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

/**
 * 因子收割链路（add-factor-harvest-ui）只读 E2E。
 *
 * 铁律：不 mock 业务接口——直接打真实后端；数据随收割任务变化，因此只断言结构，
 * 不断言具体数字。区块只有两种合法形态：有数据（进度+漏斗+台账）或空态（未接入）。
 *
 * 形态判定走**端点读数**（不是 DOM 可见性探测）：前置不成立的用例显式 `test.skip`
 * 并给出理由，而不是在断言里条件绕过——绕过会让「区块坏成第三种形态」静默变成通过
 * （scan #5a P0，2026-10-23 T8 清理）。运行中 / 已收尾拆成两个各自命名的用例，
 * 保证「收尾不渲染进度条」这条回归断言在任何形态下都不会被悄悄挪走。
 */

type HarvestPayload = {
  available: boolean
  job: { status: string | null } | null
}

async function fetchHarvest(request: APIRequestContext): Promise<HarvestPayload> {
  const response = await request.get('/api/factor-research/harvest')
  expect(response.status()).toBe(200)
  return (await response.json()) as HarvestPayload
}

/** 两态共有的区块结构（漏斗四卡 / 拒收分布 / 台账 / 只读 / 不拖垮主监控）。 */
async function expectHarvestBlock(page: Page): Promise<void> {
  for (const key of ['candidates', 'processed', 'gates', 'admitted']) {
    const card = page.getByTestId(`factor-research-harvest-step-${key}`)
    await expect(card).toBeVisible()
    await expect(card).toContainText(/\d/)
  }

  await expect(page.getByTestId('factor-research-harvest-gates')).toContainText('拒收分布')
  await expect(page.getByTestId('factor-research-harvest-admitted')).toBeVisible()
  await expect(page.getByTestId('factor-research-harvest-rejected')).toBeVisible()

  // 只读：区块内不得出现提交/启停类控件
  await expect(page.getByTestId('factor-research-harvest').locator('button')).toHaveCount(0)

  // 主监控区块不受影响
  await expect(page.getByTestId('factor-research-supervisor')).toBeVisible()
}

test.describe('因子收割链路（只读）', () => {
  test('运行中：渲染队列消化进度条与批次进度', async ({ page, request }) => {
    await page.goto('/factor-research')
    const payload = await fetchHarvest(request)
    test.skip(payload.job?.status !== 'running', '收割未在运行 → 由「已收尾」用例覆盖')

    const progress = page.getByTestId('factor-research-harvest-progress')
    await expect(progress).toBeVisible()

    const text = (await progress.textContent()) ?? ''
    // 运行中口径：批次进度（含上限分母）+ 队列 + 本轮入库/拒收（ETA 视速率是否可算而出现）
    expect(text).toContain('批次')
    expect(text).toContain('剩余')
    await expect(page.getByTestId('factor-research-harvest-bar')).toBeVisible()

    await expectHarvestBlock(page)
  })

  test('已收尾：不渲染队列消化进度条', async ({ page, request }) => {
    await page.goto('/factor-research')
    const payload = await fetchHarvest(request)
    test.skip(payload.job?.status === 'running', '收割运行中 → 由「运行中」用例覆盖')

    const progress = page.getByTestId('factor-research-harvest-progress')
    await expect(progress).toBeVisible()

    // 已收尾（ok/failed）与状态不可得（job 块缺失）共有形态：本轮汇总 + 队列归属「下次自动跑」；
    // 批次上限分母、外推 ETA 与进度条都不得出现——候选文件被 RD-Agent 持续追加，全局消化率配
    // 「已完成」徽标只会读成「没跑完」（2026-10-23 实测卡在 94%）。
    // 注：「已完成」徽标 + 「本轮 N 批 · 入库 x · 拒收 y」仅 job.status=ok/failed 渲染，由单测覆盖。
    const text = (await progress.textContent()) ?? ''
    expect(text).toContain('已处理')
    expect(text).toContain('队列剩余')
    expect(text).not.toContain('预计还需')
    expect(text).not.toMatch(/批次 \d+\//)

    // 回归捕捉断言（T8 的 bug 就是它抓住的）：父容器可见已由上一段断言证明，
    // 因此 count=0 是「区块渲染了、进度条没有」，而不是「什么都没渲染」。
    await expect(page.getByTestId('factor-research-harvest-bar')).toHaveCount(0)

    await expectHarvestBlock(page)
  })

  test('未接入空态：不拖垮整页', async ({ page, request }) => {
    const payload = await fetchHarvest(request)
    test.skip(payload.available, '收割数据已接入（available=true）→ 由渲染用例覆盖')

    await page.goto('/factor-research')

    await expect(page.getByTestId('factor-research-harvest-empty')).toContainText('未接入')
    // 主监控区块不受影响
    await expect(page.getByTestId('factor-research-supervisor')).toBeVisible()
  })
})
