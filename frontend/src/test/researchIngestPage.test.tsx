import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ResearchIngestPage } from '../pages/research/ResearchIngestPage'

/**
 * 研报投喂页单测（add-research-report-ingest T4；spec research-report-ingest
 * 「前端研报投喂页」）。
 *
 * 契约：
 * - 上传 → POST /api/research/reports/ingest（multipart）；回执三态
 *   ingested / skipped / HTTP 错误显式报错，不互相冒充；
 * - 最近投喂文档列表 GET /api/research/reports/documents：空态
 *   research-docs-empty / 失败 research-docs-error 与空态严格区分；
 * - 提交成功后列表刷新；页面不展示文档正文。
 */

const DOCS_BODY = {
  documents: [
    {
      doc_key: 'd1',
      filename: '报告.txt',
      title: '深度报告',
      org: '某券商',
      publish_date: '2026-10-24',
      char_count: 128,
      depth_class: 'depth_1',
      source_type: 'manual_feed',
      ingested_at: '2026-10-25T10:00:00',
    },
  ],
}

function stubFetch(responses: Array<{ match: (url: string, method: string) => boolean; body: unknown; status?: number }>) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const hit = responses.find(r => r.match(url, method))
    if (!hit) throw new Error(`unexpected fetch ${method} ${url}`)
    return new Response(JSON.stringify(hit.body), {
      status: hit.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('研报投喂页（add-research-report-ingest）', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('空列表显示空态并区分加载失败', async () => {
    stubFetch([
      { match: url => url.includes('/api/research/reports/documents'), body: { documents: [] } },
    ])
    render(<ResearchIngestPage onBack={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('research-docs-empty')).toBeInTheDocument())
    expect(screen.queryByTestId('research-doc-item')).not.toBeInTheDocument()
  })

  it('列表接口失败显示错误态而非空态', async () => {
    stubFetch([
      {
        match: url => url.includes('/api/research/reports/documents'),
        body: { detail: 'x' },
        status: 500,
      },
    ])
    render(<ResearchIngestPage onBack={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('research-docs-error')).toBeInTheDocument())
  })

  it('上传成功：回执 ingested + 列表刷新 + 不展示正文', async () => {
    const fetchMock = stubFetch([
      {
        match: (url, method) => url.includes('/api/research/reports/documents') && method === 'GET',
        body: DOCS_BODY,
      },
      {
        match: (url, method) => url.includes('/api/research/reports/ingest') && method === 'POST',
        body: { status: 'ingested', doc_key: 'abc123def456', char_count: 128, depth_class: 'depth_1' },
      },
    ])
    render(<ResearchIngestPage onBack={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('research-doc-item')).toBeInTheDocument())

    const input = screen.getByTestId('research-file-input') as HTMLInputElement
    await waitFor(() => expect(input).not.toBeDisabled())
    fireEvent.change(input, { target: { files: [new File(['pdf'], '报告.pdf')] } })
    fireEvent.click(screen.getByTestId('research-submit'))

    await waitFor(() =>
      expect(screen.getByTestId('research-receipt')).toHaveTextContent('入库成功'),
    )
    expect(screen.getByTestId('research-receipt')).toHaveTextContent('abc123def456')
    // 列表刷新后仍为 1 条，且正文不出现在页面
    expect(screen.getAllByTestId('research-doc-item')).toHaveLength(1)
    expect(screen.queryByText('不应出现在列表响应里')).not.toBeInTheDocument()
    const posts = fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0][0]).toBe('/api/research/reports/ingest')
  })

  it('重复投喂：skipped 幂等提示，不误报失败', async () => {
    stubFetch([
      { match: url => url.includes('/api/research/reports/documents'), body: { documents: [] } },
      {
        match: (url, method) => url.includes('/api/research/reports/ingest') && method === 'POST',
        body: { status: 'skipped', doc_key: 'abc123def456' },
      },
    ])
    render(<ResearchIngestPage onBack={() => {}} />)
    const input = await screen.findByTestId('research-file-input')
    fireEvent.change(input, { target: { files: [new File(['pdf'], '报告.pdf')] } })
    fireEvent.click(screen.getByTestId('research-submit'))
    await waitFor(() => expect(screen.getByTestId('research-receipt')).toHaveTextContent('已入库'))
    expect(screen.getByTestId('research-receipt')).toHaveTextContent('幂等')
  })

  it('上传失败：显式错误文案含原因', async () => {
    stubFetch([
      { match: url => url.includes('/api/research/reports/documents'), body: { documents: [] } },
      {
        match: (url, method) => url.includes('/api/research/reports/ingest') && method === 'POST',
        body: { detail: '仅支持 pdf/txt/md 文件' },
        status: 400,
      },
    ])
    render(<ResearchIngestPage onBack={() => {}} />)
    const input = await screen.findByTestId('research-file-input')
    fireEvent.change(input, { target: { files: [new File(['MZ'], '程序.exe')] } })
    fireEvent.click(screen.getByTestId('research-submit'))
    await waitFor(() => expect(screen.getByTestId('research-receipt')).toHaveTextContent('入库失败'))
    expect(screen.getByTestId('research-receipt')).toHaveTextContent('pdf/txt/md')
  })
})
