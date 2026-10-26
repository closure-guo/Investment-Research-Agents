import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'

/**
 * 研报投喂页（add-research-report-ingest T4）。
 *
 * 直投入库（POST /api/research/reports/ingest，multipart）+ 最近投喂文档列表
 * （GET /api/research/reports/documents）。列表与回执均不含文档正文（后端契约）。
 * 提交回执三态：ingested 成功 / skipped 幂等 / HTTP 错误显式报错，不互相冒充。
 */

interface IngestDocItem {
  doc_key: string
  filename: string
  title: string | null
  org: string | null
  publish_date: string | null
  char_count: number | null
  depth_class: string | null
  source_type: string | null
  ingested_at: string
}

type IngestReceipt =
  | { kind: 'ingested'; docKey: string; charCount: number | null; depthClass: string | null; parseError?: string }
  | { kind: 'skipped'; docKey: string }
  | { kind: 'error'; message: string }

const ACCEPT = '.pdf,.txt,.md'

export function ResearchIngestPage({ onBack }: { onBack: () => void }) {
  // null = 加载中；error = 接口失败（与空态严格区分）
  const [docs, setDocs] = useState<IngestDocItem[] | null>(null)
  const [listError, setListError] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [org, setOrg] = useState('')
  const [publishDate, setPublishDate] = useState('')
  const [title, setTitle] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [receipt, setReceipt] = useState<IngestReceipt | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadDocs = useCallback(async () => {
    setListError(false)
    try {
      const resp = await fetch('/api/research/reports/documents?limit=20')
      if (!resp.ok) throw new Error(String(resp.status))
      const body = (await resp.json()) as { documents: IngestDocItem[] }
      setDocs(body.documents ?? [])
    } catch {
      setListError(true)
    }
  }, [])

  useEffect(() => {
    void loadDocs()
  }, [loadDocs])

  const submit = useCallback(async () => {
    if (!file || submitting) return
    setSubmitting(true)
    setReceipt(null)
    try {
      const form = new FormData()
      form.append('file', file)
      if (org.trim()) form.append('org', org.trim())
      if (publishDate.trim()) form.append('publish_date', publishDate.trim())
      if (title.trim()) form.append('title', title.trim())
      const resp = await fetch('/api/research/reports/ingest', { method: 'POST', body: form })
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`
        try {
          const body = (await resp.json()) as { detail?: string }
          if (body.detail) detail = String(body.detail)
        } catch {
          // 非 JSON 错误体：保留状态码文案
        }
        setReceipt({ kind: 'error', message: `入库失败（${detail}）` })
        return
      }
      const body = (await resp.json()) as {
        status: 'ingested' | 'skipped'
        doc_key: string
        char_count?: number | null
        depth_class?: string | null
        parse_error?: string | null
      }
      if (body.status === 'skipped') {
        setReceipt({ kind: 'skipped', docKey: body.doc_key })
      } else {
        setReceipt({
          kind: 'ingested',
          docKey: body.doc_key,
          charCount: body.char_count ?? null,
          depthClass: body.depth_class ?? null,
          parseError: body.parse_error ?? undefined,
        })
      }
      setFile(null)
      setOrg('')
      setPublishDate('')
      setTitle('')
      if (fileInputRef.current) fileInputRef.current.value = ''
      await loadDocs()
    } catch {
      setReceipt({ kind: 'error', message: '入库失败（网络错误）' })
    } finally {
      setSubmitting(false)
    }
  }, [file, org, publishDate, title, submitting, loadDocs])

  return (
    <div data-testid="research-page" className="flex h-screen flex-col">
      {/* 标题栏 */}
      <div className="shrink-0 border-b border-border px-6 pt-5 pb-4">
        <div className="flex items-center gap-2">
          <Button
            onClick={onBack}
            data-testid="research-back"
            variant="ghost"
            size="icon"
            aria-label="返回对话"
            className="h-7 w-7"
          >
            <i className="fas fa-arrow-left text-sm"></i>
          </Button>
          <h1 className="text-base font-semibold" style={{ color: 'var(--text-default)' }}>
            研报投喂
          </h1>
          <span className="text-xs ml-2" style={{ color: 'var(--text-tertiary)' }}>
            全文仅个人研究自用；重复投喂按内容哈希幂等
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        <div className="max-w-3xl mx-auto space-y-5">
          {/* 上传表单 */}
          <div className="rounded-xl border p-4 space-y-3" style={{ borderColor: 'var(--border-neutral-l1)', background: 'var(--bg-surface-l1)' }}>
            <div className="flex items-center gap-3">
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPT}
                data-testid="research-file-input"
                onChange={e => setFile(e.target.files?.[0] ?? null)}
                className="text-xs flex-1"
                style={{ color: 'var(--text-default)' }}
              />
              <Button
                onClick={() => void submit()}
                disabled={!file || submitting}
                data-testid="research-submit"
                size="sm"
                className="rounded-lg"
              >
                {submitting ? '入库中…' : '入库'}
              </Button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <Input
                value={org}
                onChange={e => setOrg(e.target.value)}
                placeholder="机构（可选，覆盖文件名推断）"
                data-testid="research-org-input"
                className="h-8 text-xs"
              />
              <Input
                value={publishDate}
                onChange={e => setPublishDate(e.target.value)}
                placeholder="发布日期 YYYY-MM-DD（可选）"
                data-testid="research-date-input"
                className="h-8 text-xs"
              />
              <Input
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="标题（可选）"
                data-testid="research-title-input"
                className="h-8 text-xs"
              />
            </div>
          </div>

          {/* 回执区（三态） */}
          {receipt && (
            <div
              data-testid="research-receipt"
              className="rounded-lg border px-4 py-3 text-xs"
              style={{
                borderColor:
                  receipt.kind === 'error' ? 'var(--status-danger-default)' : 'var(--border-neutral-l1)',
                color: receipt.kind === 'error' ? 'var(--status-danger-default)' : 'var(--text-default)',
              }}
            >
              {receipt.kind === 'ingested' && (
                <>
                  <p className="font-medium">入库成功</p>
                  <p className="mt-1" style={{ color: 'var(--text-tertiary)' }}>
                    doc_key {receipt.docKey.slice(0, 12)}…
                    {receipt.charCount != null && ` · ${receipt.charCount} 字`}
                    {receipt.depthClass && receipt.depthClass !== 'unknown' && ` · ${receipt.depthClass}`}
                  </p>
                  {receipt.parseError && (
                    <p className="mt-1" style={{ color: 'var(--status-warning-default)' }}>
                      解析降级：{receipt.parseError}
                    </p>
                  )}
                </>
              )}
              {receipt.kind === 'skipped' && (
                <>
                  <p className="font-medium">内容此前已入库（幂等跳过）</p>
                  <p className="mt-1" style={{ color: 'var(--text-tertiary)' }}>
                    doc_key {receipt.docKey.slice(0, 12)}… 未产生重复行
                  </p>
                </>
              )}
              {receipt.kind === 'error' && <p>{receipt.message}</p>}
            </div>
          )}

          {/* 最近投喂文档 */}
          <div>
            <h2 className="text-sm font-medium mb-2" style={{ color: 'var(--text-default)' }}>
              最近投喂文档
            </h2>
            {docs === null && !listError && (
              <p className="text-xs py-3" style={{ color: 'var(--text-tertiary)' }}>
                加载中…
              </p>
            )}
            {listError && (
              <p className="text-xs py-3" data-testid="research-docs-error" style={{ color: 'var(--status-danger-default)' }}>
                列表加载失败
              </p>
            )}
            {docs !== null && !listError && docs.length === 0 && (
              <p className="text-xs py-3" data-testid="research-docs-empty" style={{ color: 'var(--text-tertiary)' }}>
                暂无投喂文档
              </p>
            )}
            {docs !== null && !listError && docs.length > 0 && (
              <ul data-testid="research-docs-list" className="space-y-1">
                {docs.map(d => (
                  <li
                    key={d.doc_key}
                    data-testid="research-doc-item"
                    className="flex items-center gap-3 rounded-lg px-3 py-2 text-xs"
                    style={{ background: 'var(--bg-surface-l1)' }}
                  >
                    <span className="flex-1 truncate" style={{ color: 'var(--text-default)' }}>
                      {d.title || d.filename}
                    </span>
                    <span style={{ color: 'var(--text-tertiary)' }}>{d.org || '—'}</span>
                    <span style={{ color: 'var(--text-tertiary)' }}>{d.publish_date || '—'}</span>
                    {d.char_count != null && (
                      <span style={{ color: 'var(--text-tertiary)' }}>{d.char_count} 字</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
