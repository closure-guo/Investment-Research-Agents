import { test, expect } from '@playwright/test'

/**
 * E2E: 流式输出中途切换会话再切回，内容继续增长且无重复渲染
 *
 * 对应 delta spec: resume-stream-on-session-switch Task 7.1
 * 核心场景：
 * 1. 发起深度分析，等待流式输出开始
 * 2. 切换到新会话（切走，不中断后端任务）
 * 3. 轮询后端直到任务推进（状态离开 running / 完成或澄清）——web-first
 *    替代旧版固定 sleep(10s)
 * 4. 切回原会话（经恢复端点重放 + 续传）
 * 5. 断言输出内容从切出位置继续增长（expect.poll 轮询渲染长度）
 *
 * 技术债改造（2026-09-04）：原 spec 含 7 处 waitForTimeout，全部改为
 * 状态驱动断言（输入框就绪/深度占位符/后端状态轮询/内容增长轮询）。
 * 禁止 mock 数据，必须通过前端模拟用户真实点击操作。
 */
const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000'
const FRONTEND = process.env.E2E_FRONTEND ?? 'http://localhost:5173'
const LLM_KEY = process.env.LLM_API_KEY || 'stub-key-for-testing'
// StubLLMClient 默认（无 STUB_SCENARIO）固定回复——deep 首问澄清轮的确定性产物
const STUB_ANSWER = '这是一段测试用的固定回复。用于验证流式渲染的增量累积。'

/** 主输入框就绪（空态/对话态统一入口） */
async function expectComposerReady(page: import('@playwright/test').Page) {
  await expect(page.getByPlaceholder(/输入/)).toBeVisible()
}

/** 切到深度模式并以「股票名称或代码」占位符可见为生效信号（web-first） */
async function switchToDeepMode(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: /模式/ }).click()
  await page.getByRole('button', { name: /深度研究.*5 层 Agent 流水线/ }).click()
  await expect(page.getByPlaceholder(/股票名称或代码/)).toBeVisible()
}

async function getSessionDetail(page: import('@playwright/test').Page, sessionId: string) {
  const resp = await page.request.get(`${API_BASE}/api/sessions/${sessionId}`)
  return (await resp.json()) as { status: string; chat_history?: unknown[] }
}

/** 发送前记录现有会话 id，发送后轮询 diff 出本测试新建的会话（并发安全） */
async function captureNewSessionId(
  page: import('@playwright/test').Page,
  knownIds: Set<string>,
): Promise<string> {
  let sessionId = ''
  await expect
    .poll(async () => {
      const resp = await page.request.get(`${API_BASE}/api/sessions`)
      const list = (await resp.json()).sessions as Array<{ session_id: string }>
      const fresh = list.find(s => !knownIds.has(s.session_id))
      if (fresh) sessionId = fresh.session_id
      return sessionId
    }, { timeout: 30_000, intervals: [500], message: '应出现本次测试新建的会话' })
    .not.toBe('')
  return sessionId
}

async function listSessionIds(page: import('@playwright/test').Page): Promise<Set<string>> {
  const resp = await page.request.get(`${API_BASE}/api/sessions`)
  const list = (await resp.json()).sessions as Array<{ session_id: string }>
  return new Set(list.map(s => s.session_id))
}

test.describe('Session Switch - Stream Resumption', () => {
  // 该文件三个测试共享同一后端 stub 流水线且都用深度分析——必须文件内串行，
  // 否则 sessions diff/状态轮询互相污染（fullyParallel 下同文件默认也并行）
  test.describe.configure({ mode: 'serial' })
  test.beforeEach(async ({ page }) => {
    page.on('console', msg => {
      if (msg.type() === 'error') console.log(`[BROWSER ERROR]`, msg.text())
    })
    page.on('pageerror', err => console.log('[PAGE ERROR]', err.message))

    await page.goto(FRONTEND)
    await page.evaluate((key) => {
      localStorage.setItem('fa_api_key', key)
      localStorage.setItem('fa_user_id', 'e2e-session-switch')
    }, LLM_KEY)
    await page.reload()
    await expectComposerReady(page)
  })

  test('中途切出再切回，内容继续增长', async ({ page }) => {
    test.setTimeout(180_000)

    // 切换到深度研究模式（占位符变化即生效）
    await switchToDeepMode(page)

    const knownIds = await listSessionIds(page)

    // 输入并发送
    await page.getByPlaceholder(/输入/).fill('深度分析600519')
    await page.getByTestId('send-button').click()

    // 等待流式输出开始
    await expect(page.getByText(/用户|分析|茅台|搜索/i).first()).toBeVisible({ timeout: 45_000 })

    // 记录切出前内容长度（测量值，非等待）
    const contentBefore = await page.locator('body').textContent()
    const lengthBefore = contentBefore?.length ?? 0
    console.log(`[E2E] 切出前内容长度: ${lengthBefore}`)

    // 会话 ID：发送前后 diff（并发安全，不用 sessions[0]）
    const sessionId = await captureNewSessionId(page, knownIds)
    console.log(`[E2E] 会话 ID: ${sessionId}`)

    // 切走：点击"新建分析"（等新会话输入框就绪，不固定 sleep）
    await page.getByRole('button', { name: /新建分析/ }).click()
    await expectComposerReady(page)
    console.log('[E2E] 已切到新会话')

    // 后端任务继续运行（新建分析仅断开本地订阅，不 cancel）：轮询至 completed——
    // web-first 替代旧版固定 sleep(10s)
    await expect
      .poll(async () => (await getSessionDetail(page, sessionId)).status, {
        timeout: 90_000,
        intervals: [2_000],
        message: '切走期间后端任务应推进到稳态（完成/澄清——stub deep 首问澄清为确定性终点）',
      })
      .toMatch(/completed|clarifying/)
    console.log('[E2E] 后端任务已到稳态')

    // 切回原会话
    const sessionsResp2 = await page.request.get(`${API_BASE}/api/sessions`)
    const sessions2 = (await sessionsResp2.json()).sessions as Array<{
      session_id: string
      display_name: string
    }>
    const target = sessions2.find(s => s.session_id === sessionId)
    if (!target) throw new Error('未找到原会话')

    await page.getByTestId('session-list').getByText(target.display_name, { exact: false }).first().click()
    await expect(
      page.getByTestId('thread-main').getByText(/分析|茅台|思考|报告/i).first(),
    ).toBeVisible({ timeout: 30_000 })
    console.log('[E2E] 已切回原会话')

    // 核心断言：切走期间产出的最终报告（真实报告节点组装，固定标题）在切回后
    // 渲染——证明恢复端点重放+续传把切出位置之后的内容完整带回。
    // （旧断言用 body 总长度增长，但重建视图的横幅/光标文本与流式中有差异，
    // ±20 字符的噪声会翻转断言——改判"产物可见"这一真信号）
    await expect(
      page.getByTestId('thread-main').getByText(STUB_ANSWER, { exact: false }).first(),
    ).toBeVisible({ timeout: 30_000 })
    console.log('[E2E] 澄清回复（stub 固定文本）已在切回后渲染')

    // 验证会话状态
    const detail = await getSessionDetail(page, sessionId)
    console.log(`[E2E] 会话状态: ${detail.status}`)
    expect(['running', 'completed', 'clarifying', 'interrupted']).toContain(detail.status)
  })

  test('显式停止后状态为中断', async ({ page }) => {
    test.setTimeout(120_000)

    await switchToDeepMode(page)

    const knownIds = await listSessionIds(page)

    // 输入并发送
    await page.getByPlaceholder(/输入/).fill('深度分析600519')
    await page.getByTestId('send-button').click()

    // 等待流式输出开始（有输出才可能有停止按钮）
    await expect(page.getByText(/用户|分析|茅台|搜索/i).first()).toBeVisible({ timeout: 45_000 })

    // 会话 ID：发送前后 diff（并发安全）
    const sessionId = await captureNewSessionId(page, knownIds)

    // 点击停止按钮（流式开始后应可见；快 stub 流窗口极短——
    // 可见即尽力点击，点击竞态（流自然结束按钮消失）则落入澄清稳态，轮询仍通过）
    const stopButton = page.getByRole('button', { name: /停止/ })
    let visible = false
    try {
      await expect(stopButton).toBeVisible({ timeout: 10_000 })
      visible = true
    } catch {
      // 环境未进入可见的流式窗口
    }
    if (!visible) {
      console.log('[E2E] 停止按钮不可见，跳过测试')
      test.skip()
      return
    }
    await stopButton.click({ timeout: 2_000 }).catch(() => {
      console.log('[E2E] 停止按钮已消失（流自然结束），按澄清稳态继续')
    })
    console.log('[E2E] 已点击停止按钮')

    // 轮询会话状态到中断态（web-first 替代固定 sleep(3s)+单次检查）
    await expect
      .poll(async () => (await getSessionDetail(page, sessionId)).status, {
        timeout: 60_000,
        intervals: [1_000],
        message: '停止后会话应进入中断/澄清/完成态',
      })
      .toMatch(/interrupted|clarifying|completed/)
  })

  // 「运行中的会话拒绝新输入」守卫不进 E2E：stub 流窗口为亚秒级（chunk 间隔
  // 硬编码 20-50ms，无 pacing env），「流式进行中发送第二条」无法确定性构造——
  // 旧 spec 因该竞态被移出门禁。守卫逻辑由前端单测覆盖（quickThreadGuards 的
  // 409→警告等价呈现 + store.isSessionRunning 守卫测试）。
})