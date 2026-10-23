import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

/**
 * 因子收割链路（add-factor-harvest-ui）只读 E2E。
 *
 * 铁律：不 mock 业务接口——数据面由本 spec 以**真实文件**写入测试隔离路径
 * （playwright.config.ts webServer env 的 FACTOR_RESEARCH_HARVEST_STATE_PATH /
 * _CANDIDATES_PATH / _HEARTBEAT_PATH），后端仍走真实读文件路径。
 * 为什么必须 seed：`data/factor_research/` 不入库，CI 全新检出没有状态/候选文件 →
 * 端点 available=false → 数据形态用例恒 skip，E2E 侧就抓不到「完成态不渲染进度条」
 * 这条回归（2026-10-23 T8 补）。
 *
 * 套件归属：本 spec 走**专属 config**（playwright.harvest.config.ts，端口 8007/5180）。
 * 因为它还需要一份监控快照 fixture——收割区块整块挂在主快照的 `data.available` 门控下
 * （FactorResearchPage.tsx），而默认套件里那份快照由 factor-research-monitor.spec.ts
 * 写/删，两个文件并行时胜负取决于调度时序（见该 config 头部说明）。
 *
 * 形态判定走**端点读数**（不是 DOM 可见性探测）：前置不成立的用例显式 `test.skip`
 * 并给出理由，而不是在断言里条件绕过——绕过会让「区块坏成第三种形态」静默变成通过
 * （scan #5a P0，T8 清理）。运行中 / 已收尾拆成两个各自命名的用例，保证「收尾不渲染
 * 进度条」这条回归断言在任何形态下都不会被悄悄挪走。
 *
 * 用例串行（serial）：共享同一份 fixture，空态用例要删掉它们（并行会互踩）。
 */

// tests/e2e/playwright/tests → 仓库根为四层上；与 config webServer env 的相对路径同源。
// （ESM 项目：经 import.meta.url 解析本文件目录，无 __dirname。）
const REPO_ROOT = fileURLToPath(new URL('../../../../', import.meta.url))
const STATUS_PATH = path.resolve(REPO_ROOT, 'data/test-e2e-factor-research-status.json')
const STATE_PATH = path.resolve(REPO_ROOT, 'data/test-e2e-factor-research-harvest-state.json')
const CANDIDATES_PATH = path.resolve(REPO_ROOT, 'data/test-e2e-factor-research-candidates.json')
const HEARTBEAT_PATH = path.resolve(REPO_ROOT, 'data/test-e2e-factor-research-heartbeat.json')

// 防御：fixture 路径必须测试隔离，绝不落到生产 data/factor_research/
for (const fixturePath of [STATUS_PATH, STATE_PATH, CANDIDATES_PATH, HEARTBEAT_PATH]) {
  if (!fixturePath.includes('test-e2e')) {
    throw new Error(`fixture 路径未隔离: ${fixturePath}`)
  }
}

/**
 * 监控快照 fixture：收割区块挂在主快照之下（`data.available` 门控），没有它页面只显示
 * 「采集器未接入」，收割区块根本不存在。形状与采集器契约一致（同 monitor spec 的 snapshotBody）。
 */
function seedStatusFixture(): void {
  const epoch = Math.floor(Date.now() / 1000)
  const isoAt = (offset: number) => new Date((epoch + offset) * 1000).toISOString()
  mkdirSync(path.dirname(STATUS_PATH), { recursive: true })
  writeFileSync(
    STATUS_PATH,
    JSON.stringify({
      collected_at: isoAt(0),
      collected_at_epoch: epoch,
      supervisor: { running: true, pid: 4242, started_at: isoAt(-900) },
      current_session: null,
      sessions: [],
      totals: { sessions: 0, candidates: 0, workspace_tasks: 0 },
    })
  )
}

/** 真实文件写入数据面：2 条候选未处理（候选 2 / 已处理 0 / 队列剩余 2）。 */
function seedHarvestFixtures(): void {
  mkdirSync(path.dirname(STATE_PATH), { recursive: true })
  writeFileSync(STATE_PATH, JSON.stringify({ updated_at: new Date().toISOString(), processed: {} }))
  writeFileSync(
    CANDIDATES_PATH,
    JSON.stringify({
      candidates: [
        { name: 'E2E_SEEDED_A', description: 'e2e fixture', formulation: 'close' },
        { name: 'E2E_SEEDED_B', description: 'e2e fixture', formulation: 'volume' },
      ],
    })
  )
  rmSync(HEARTBEAT_PATH, { force: true }) // 无批内心跳 → in_batch=null（缺失即如实降级）
}

function clearHarvestFixtures(): void {
  rmSync(STATE_PATH, { force: true })
  rmSync(CANDIDATES_PATH, { force: true })
}

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

test.describe.configure({ mode: 'serial', timeout: 60_000 })

test.describe('因子收割链路（只读）', () => {
  test.beforeAll(() => {
    seedStatusFixture()
    seedHarvestFixtures()
  })

  test.afterAll(() => {
    // 收尾清场：fixture 留在盘上会污染后续本地跑（尤其 monitor spec 的空态前提）
    rmSync(STATUS_PATH, { force: true })
    clearHarvestFixtures()
  })

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
    // 数据面缺失 = 真实降级路径（serial 保证本用例最后跑，不会与上面两例抢 fixture）
    clearHarvestFixtures()

    const payload = await fetchHarvest(request)
    test.skip(payload.available, '收割数据已接入（available=true）→ 由渲染用例覆盖')

    await page.goto('/factor-research')

    await expect(page.getByTestId('factor-research-harvest-empty')).toContainText('未接入')
    // 主监控区块不受影响
    await expect(page.getByTestId('factor-research-supervisor')).toBeVisible()
  })
})
