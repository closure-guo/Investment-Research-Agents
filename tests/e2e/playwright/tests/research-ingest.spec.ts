import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * 研报投喂页 E2E（add-research-report-ingest）。
 *
 * 不 mock 被测系统：真实前端 + 真实后端 + 真实 SQLite（隔离库）。
 * 投喂用无文本层 PDF（解析降级 → 文档入库、parse_error 披露、不触发 LLM）。
 * 场景：空态 → 上传 ingested（含降级披露）→ 列表出现 → 重复上传 skipped。
 *
 * 复位（issue #519）：「空态（隔离库首启）」断言只在 research 库零行时成立，
 * 上传行持久化在 data/test-e2e-research-reports.db——第二次本地重跑必失败。
 * beforeEach 经 TESTING=1 /api/test/seed ``research_reset`` 清直投面三表，
 * 库回到首启等价态（与 screening-page 前后 reset 同款纪律）。
 */

// 无文本层的伪 PDF：pypdf 解析失败 → 降级入库（与目录扫描降级语义一致）
const CORRUPT_PDF = Buffer.from('not a real pdf')

function apiBase(): string {
  const base = test.info().project.use.baseURL ?? 'http://localhost:5173'
  return base.replace('//localhost', '//127.0.0.1')
}

async function resetResearchDocs(request: APIRequestContext) {
  const resp = await request.post(`${apiBase()}/api/test/seed`, { data: { research_reset: true } })
  if (!resp.ok()) throw new Error(`research_reset 失败: ${resp.status()}`)
}

test.beforeEach(async ({ request }) => {
  await resetResearchDocs(request)
})

test('研报投喂：上传→回执→列表→幂等', async ({ page }) => {
  await page.goto('/research')

  // 空态（隔离库首启）：空态文案而非错误
  await expect(page.getByTestId('research-docs-empty')).toBeVisible()

  // 上传一份未入库的（无文本层）PDF → ingested + 降级披露
  await page.getByTestId('research-file-input').setInputFiles({
    name: '无文本层报告.pdf',
    mimeType: 'application/pdf',
    buffer: CORRUPT_PDF,
  })
  await page.getByTestId('research-submit').click()
  await expect(page.getByTestId('research-receipt')).toContainText('入库成功')
  await expect(page.getByTestId('research-receipt')).toContainText('解析降级')

  // 列表刷新出现该文档（不含正文字段）
  await expect(page.getByTestId('research-doc-item')).toHaveCount(1)
  await expect(page.getByTestId('research-doc-item')).toContainText('无文本层报告')

  // 同一文件再传 → skipped 幂等提示
  await page.getByTestId('research-file-input').setInputFiles({
    name: '无文本层报告.pdf',
    mimeType: 'application/pdf',
    buffer: CORRUPT_PDF,
  })
  await page.getByTestId('research-submit').click()
  await expect(page.getByTestId('research-receipt')).toContainText('已入库')
  await expect(page.getByTestId('research-receipt')).toContainText('幂等')
  await expect(page.getByTestId('research-doc-item')).toHaveCount(1)
})
