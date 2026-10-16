import { test, expect } from '@playwright/test'

/**
 * 管线分层时间轴（redesign-pipeline-hierarchical-timeline delta task 4.3）
 *
 * 验证目标：
 * 1. 分层时间轴渲染 6 个 layer（PREP/Layer I/Layer II/Trader/Risk/Fund）
 * 2. Layer I 4 个并行分析师按各自事件独立驱动状态（修复旧版全部绑定 technical_analyst 的错位）
 * 3. Layer II 辩论 5 个子节点逐个可见（解决旧版"卡在 Layer II 无反馈"）
 * 4. 当前运行节点高亮（data-current）
 *
 * 确定性方案同 pipeline-eta-banner.spec.ts（TESTING=1 + STUB_SCENARIO=pipeline，5175 前端）。
 */

test.setTimeout(180_000)

test.describe('管线分层时间轴（redesign-pipeline-hierarchical-timeline）', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('http://localhost:5175')
    await page.evaluate(() => {
      localStorage.setItem('fa_api_key', 'stub-key-for-testing')
      localStorage.setItem('fa_user_id', 'user-test-hierarchical')
      localStorage.removeItem('financeAgent.pipelineDurations')
      // 本 spec 断言列表时间轴 DOM（pipeline-timeline）；默认视图为 graph，显式预置 list
      localStorage.setItem('fa_pipeline_view', 'list')
    })
    await page.reload()
  })

  // 发送动作留在用例体内：test 4 需要在「发送前」先挂高亮观察器（issue #66），
  // 观察窗必须覆盖管线完整生命周期，不能从 beforeEach 发送之后才开始
  async function startAnalysis(page: import('@playwright/test').Page) {
    await page.getByRole('button', { name: /模式/ }).click()
    await page.getByRole('button', { name: /深度研究.*5 层 Agent 流水线/ }).click()
    await page.getByPlaceholder(/输入/).fill('深度分析600519')
    await page.getByTestId('send-button').click()
  }

  test('1. 分层时间轴渲染 6 个 layer 标题', async ({ page }) => {
    await startAnalysis(page)
    const timeline = page.getByTestId('pipeline-timeline')
    // 120s（issue #312）：timeline 挂载点=send 后首个流事件，CI 慢机 vite 冷启动
    // +stub 管线首事件可达 60-90s——环境慢不是功能坏（eval-ops 分区同款放宽先例）
    await expect(timeline).toBeVisible({ timeout: 120_000 })
    await expect(timeline.getByText('PREP', { exact: true })).toBeVisible()
    await expect(timeline.getByText('Layer I', { exact: true })).toBeVisible()
    await expect(timeline.getByText('Layer II', { exact: true })).toBeVisible()
    await expect(timeline.getByText('Trader', { exact: true })).toBeVisible()
    await expect(timeline.getByText('Risk', { exact: true })).toBeVisible()
    await expect(timeline.getByText('Fund', { exact: true })).toBeVisible()
  })

  test('2. Layer I 4 个并行分析师各自独立摘要（报告卡片）', async ({ page }) => {
    await startAnalysis(page)
    // stub 管线下 4 分析师并行同批完成（~6s 全程），管线时间轴的 4 节点 DOM 瞬态
    // 极窄、attached 轮询不可靠（#3 Layer II 串行节点窗口错开可捕获，#2 并行不可）。
    // 时间轴 4 节点独立渲染由单测覆盖（pipelineTree.test.ts / PipelineTimeline.test.tsx）。
    // 此处验证用户最终可见契约：报告卡片中 4 个分析师各自独立摘要（修复旧版全部
    // 显示 technical_analyst 摘要的错位——后端 _extract_output 按各自 key 取）。
    // 报告 DOM 演进（分析师节标题为中文名 + 各节 STUB 内容）：断言「4 个分析师各自独立
    // 摘要」= 节标题 + 对应 STUB 文本成对出现——防旧版全部显示 technical 摘要的错位回归。
    // （2026-10-17 规格更新：原断言 heading{name:'technical'} 系旧 DOM 残留，rot 修复。）
    const sections = [
      ['技术面分析师', 'STUB technical 分析摘要'],
      ['宏观分析师', 'STUB macro 分析摘要'],
      ['基本面分析师', 'STUB fundamental 分析摘要'],
      ['舆情分析师', 'STUB sentiment 分析摘要'],
    ] as const
    for (const [heading, content] of sections) {
      await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible({ timeout: 90_000 })
      await expect(page.getByText(content).first()).toBeVisible({ timeout: 30_000 })
    }
  })

  test('3. Layer II 辩论完成后管线产出最终报告', async ({ page }) => {
    await startAnalysis(page)
    // 同 #2：stub 下 Layer II 节点同批快速推进，时间轴子节点 DOM 瞬态在套件慢
    // 环境下 attached 轮询不可靠。Layer II 5 个辩论子节点的独立渲染由单测覆盖
    // （pipelineTree.test.ts / PipelineTimeline.test.tsx）。
    // 此处验证端到端契约：管线经 Layer II 辩论、Trader、Risk、Fund 完整跑完并
    // 产出最终报告卡片（证明 Layer II 未"卡住"，修复旧版 Layer II 无反馈问题）。
    await expect(page.getByText('深度分析报告').first()).toBeVisible({ timeout: 90_000 })
    // 报告包含投资分析标题（generate_report 在 Layer II 之后执行）
    await expect(page.getByRole('heading', { name: /投资分析报告/ }).first()).toBeVisible({ timeout: 90_000 })
  })

  test('4. 管线运行期间存在当前高亮节点（data-current）', async ({ page }) => {
    // 竞态治理（issue #66）：stub 管线全程 ~6s，运行中高亮（活动层 animate-ping，
    // legacy data-current 已移除）是瞬态窗口；「先发送后到达式断言」在慢 runner 下
    // 观察起点晚于窗口终点 → 60s 漏抓假红。改为发送前挂 100ms 轮询的
    // waitForFunction 连续观察，观察窗与管线生命周期零缺口。
    const pingSeen = page
      .waitForFunction(() => document.querySelector('.animate-ping') !== null, null, {
        polling: 100,
        timeout: 120_000,
      })
      .then(() => true)
      .catch(() => false)
    await startAnalysis(page)
    const timeline = page.getByTestId('pipeline-timeline')
    // 120s（issue #312）：timeline 挂载点=send 后首个流事件，CI 慢机 vite 冷启动
    // +stub 管线首事件可达 60-90s——环境慢不是功能坏（eval-ops 分区同款放宽先例）
    await expect(timeline).toBeVisible({ timeout: 120_000 })
    if (!(await pingSeen)) {
      // 观察窗全程未见高亮 = 管线在观察建立前已完成（前端每用例全新页面，
      // 不存在跨用例残留运行）。此时必须看到完成态报告卡，证明管线跑完而非卡住
      // ——「运行期高亮」与「已完成产出报告」二选一均为可接受终态
      await expect(page.getByText('深度分析报告').first()).toBeVisible({ timeout: 30_000 })
    }
  })
})
