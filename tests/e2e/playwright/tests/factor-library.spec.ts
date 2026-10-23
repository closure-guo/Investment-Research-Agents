import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * delta add-factor-library-page T2.3：因子库页 E2E 门禁（默认 config，8000/5173）。
 *
 * 红线：不 mock 业务接口——两因子经 TESTING=1 的 /api/test/seed 真实写入因子库
 * （playwright.config.ts webServer env FACTOR_LIBRARY_DB_PATH=
 * data/test-e2e-factor-library.db，与开发库 data/factor_library.db 物理隔离），
 * 前端经真实 GET /api/factors 读取渲染；行展开的 IC 序列/事件历史经真实
 * GET /api/factors/{id} 取数。
 *
 * 用例串行（serial）：共享同一因子库，seed/reset 互相覆盖，并行会互踩；
 * 每例前后均 reset，避免遗留因子让其它用例的空态/双因子前提失效（共享库泄漏）。
 */
test.describe.configure({ mode: 'serial', timeout: 60_000 })

// API 造数显式走 IPv4：Windows 上 localhost 可能先解析到 ::1，而 vite dev server
// 绑 0.0.0.0（IPv4），APIRequestContext 只试 ::1 时会 ECONNREFUSED（首轮实测偶发）。
// 默认 5173（默认 config 端口对）；E2E_API_BASE 可覆盖（隔离端口临时验证用，
// 默认 config 下不设置，行为与既有 spec 的 5173 硬编码一致）。
const API_BASE = process.env.E2E_API_BASE ?? 'http://127.0.0.1:5173'

// LLM 提案因子（active+llm）：IC 序列 4 点，手算 ICIR = 0.0125 / 0.017078 = 0.732
// （mean 0.0125、样本标准差 ddof=1 → recent_ic_stats 口径）
const LLM_EXPRESSION = 'ts_rank(close,20)'
// builtin 因子（retired+builtin）：表达式取自注册词表（momentum_20），IC 无记录
const BUILTIN_EXPRESSION = 'delta(close,20)'

async function resetFactors(request: APIRequestContext) {
  const resp = await request.post(`${API_BASE}/api/test/seed`, { data: { factors_reset: true } })
  expect(resp.ok()).toBeTruthy()
}

async function seedFactors(request: APIRequestContext) {
  const resp = await request.post(`${API_BASE}/api/test/seed`, {
    data: {
      factors: {
        factors: [
          {
            expression: LLM_EXPRESSION,
            hypothesis: '截面排名动量：排名抬升的标的短期收益分布偏正。',
            category: '动量',
            status: 'active',
            source: 'llm',
            alignment_score: 0.72,
            ic: [
              ['2026-10-08', 0.02],
              ['2026-10-09', -0.01],
              ['2026-10-12', 0.03],
              ['2026-10-13', 0.01],
            ],
          },
          {
            expression: BUILTIN_EXPRESSION,
            hypothesis: '过去 20 个交易日涨幅高的标的短期内倾向继续跑赢。',
            category: 'builtin',
            status: 'retired',
            source: 'builtin',
            pool_increment: 0.05,
            // 衰减后退役（真实路径 degraded → retired）：造「衰减标记」列的确定性状态
            via_degraded: true,
            detail: { reason: 'BUCKET_QUOTA' },
          },
        ],
      },
    },
  })
  expect(resp.ok()).toBeTruthy()
}

/** 因子 id 由库自增生成（seed 不可预知）→ 经真实 API 反查，供行 testid 定位。 */
async function factorIds(request: APIRequestContext) {
  const resp = await request.get(`${API_BASE}/api/factors`)
  expect(resp.ok()).toBeTruthy()
  const body = (await resp.json()) as { factors: { id: number; expression: string }[] }
  const llm = body.factors.find(factor => factor.expression === LLM_EXPRESSION)
  const builtin = body.factors.find(factor => factor.expression === BUILTIN_EXPRESSION)
  expect(llm, 'seed 后应能查到 LLM 因子').toBeTruthy()
  expect(builtin, 'seed 后应能查到 builtin 因子').toBeTruthy()
  return { llmId: llm!.id, builtinId: builtin!.id }
}

test.beforeEach(async ({ request }) => {
  await resetFactors(request)
})

test.afterEach(async ({ request }) => {
  await resetFactors(request)
})

test.afterAll(async ({ request }) => {
  await resetFactors(request)
})

test('种子两因子 → 列表可见（状态/来源徽章、ICIR、衰减标记）', async ({ page, request }) => {
  await seedFactors(request)
  const { llmId, builtinId } = await factorIds(request)

  await page.goto('/factors')
  // 首屏取数在 8 并发 worker 的默认套件下偶见 >5s（单进程后端 + 全量并行）：
  // 显式放宽首屏断言超时（与 snapshot-banner.spec 同款处置），不掩盖真实故障
  await expect(page.getByTestId('page-shell-table')).toBeVisible({ timeout: 15_000 })

  const llmRow = page.getByTestId(`page-shell-row-${llmId}`)
  await expect(llmRow).toContainText(LLM_EXPRESSION)
  await expect(llmRow).toContainText(`因子 ${llmId}`) // 无注册名 → 回退显示因子 id
  await expect(llmRow.getByTestId(`factor-status-${llmId}`).getByTestId('status-badge')).toHaveAttribute(
    'data-status',
    'active',
  )
  await expect(llmRow.getByTestId(`factor-source-${llmId}`)).toContainText('llm')
  await expect(llmRow.getByTestId(`factor-source-${llmId}`).getByTestId('ai-badge')).toBeVisible()
  await expect(llmRow.getByTestId(`factor-icir-${llmId}`)).toContainText('0.732')
  await expect(llmRow.getByTestId(`factor-icir-samples-${llmId}`)).toContainText('样本 4')
  await expect(llmRow.getByTestId(`factor-gate-${llmId}`)).toContainText('准入通过')
  await expect(llmRow.getByTestId(`factor-decay-${llmId}`)).toHaveText('—')

  const builtinRow = page.getByTestId(`page-shell-row-${builtinId}`)
  await expect(builtinRow).toContainText('momentum_20') // builtin 注册词表作品名
  await expect(
    builtinRow.getByTestId(`factor-status-${builtinId}`).getByTestId('status-badge'),
  ).toHaveAttribute('data-status', 'retired')
  await expect(builtinRow.getByTestId(`factor-source-${builtinId}`).getByTestId('ai-badge')).toHaveCount(0)
  await expect(builtinRow.getByTestId(`factor-icir-${builtinId}`)).toHaveText('—') // 无 IC 记录不冒充 0.000
  await expect(builtinRow.getByTestId(`factor-gate-${builtinId}`)).toContainText('桶内保种淘汰')
  await expect(
    builtinRow.getByTestId(`factor-decay-${builtinId}`).getByTestId('status-badge'),
  ).toHaveAttribute('data-status', 'degraded')
})

test('筛选：来源 llm → 仅 LLM 因子；状态 retired → 仅内置退役因子', async ({ page, request }) => {
  await seedFactors(request)
  const { llmId, builtinId } = await factorIds(request)

  await page.goto('/factors')
  await expect(page.getByTestId('page-shell-table')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId(`page-shell-row-${llmId}`)).toBeVisible()
  await expect(page.getByTestId(`page-shell-row-${builtinId}`)).toBeVisible()

  await page.getByTestId('factors-filter-source-llm').click()
  await expect(page.getByTestId(`page-shell-row-${llmId}`)).toBeVisible()
  await expect(page.getByTestId(`page-shell-row-${builtinId}`)).toBeHidden()
  await expect(page.getByTestId('factors-count')).toContainText('1')

  await page.getByTestId('factors-filter-source-all').click()
  await page.getByTestId('factors-filter-status-retired').click()
  await expect(page.getByTestId(`page-shell-row-${builtinId}`)).toBeVisible()
  await expect(page.getByTestId(`page-shell-row-${llmId}`)).toBeHidden()
})

test('行展开 → 完整表达式 + IC 序列 + 事件历史（含时间）', async ({ page, request }) => {
  await seedFactors(request)
  const { llmId } = await factorIds(request)

  await page.goto('/factors')
  await expect(page.getByTestId('page-shell-table')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId(`page-shell-row-detail-${llmId}`)).toBeHidden()

  await page.getByTestId(`page-shell-row-expand-${llmId}`).click()

  const detail = page.getByTestId(`page-shell-row-detail-${llmId}`)
  await expect(detail).toBeVisible()
  await expect(page.getByTestId(`factor-detail-expression-${llmId}`)).toContainText(LLM_EXPRESSION)

  // IC 序列（delta add-factor-ic-visibility）：折线图 + 样本/门槛/预计恢复日
  const icSeries = page.getByTestId(`factor-detail-ic-${llmId}`)
  await expect(icSeries.getByTestId('factor-ic-chart')).toBeVisible()
  await expect(page.getByTestId(`factor-ic-samples-${llmId}`)).toContainText('4/60')
  await expect(page.getByTestId(`factor-ic-threshold-${llmId}`)).toContainText('4/10')
  await expect(page.getByTestId(`factor-ic-expected-${llmId}`)).toContainText('2026-10-23')
  await expect(page.getByTestId(`factor-ic-insufficient-${llmId}`)).toContainText('样本不足（4/10）')

  const events = page.getByTestId(`factor-detail-events-${llmId}`)
  await expect(events).toContainText('创建') // created 事件
  await expect(events).toContainText('状态变更 candidate → active')
  await expect(events).toContainText('准入通过') // 闸门结论（ADMITTED）
  await expect(events).toContainText('2026') // 事件时间

  // 风格暴露披露（add-factor-style-exposure）：块恒在；快照库可得 → 主导风格读数，
  // 不可得 → 如实「数据不足」（两种诚实状态都接受，MUST NOT 臆造读数）
  const style = page.getByTestId(`factor-detail-style-${llmId}`)
  await expect(style).toBeVisible()
  await expect(style).toContainText('风格暴露（与 builtin 风格代理的 IC 相关）')
  await expect(style).toContainText(/数据不足|主导/)
})

test('空库 → 空态如实（无表格 / 无骨架屏）', async ({ page }) => {
  await page.goto('/factors')

  const empty = page.getByTestId('factors-empty')
  await expect(empty).toBeVisible({ timeout: 15_000 })
  await expect(empty).toContainText('因子库为空')
  await expect(page.getByTestId('page-shell-table')).toBeHidden()
  await expect(page.getByTestId('page-shell-skeleton')).toBeHidden()
})
