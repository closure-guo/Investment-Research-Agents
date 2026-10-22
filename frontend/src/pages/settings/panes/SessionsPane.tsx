// SessionsPane 会话管理分区（add-agent-settings-center Task 9）
// 挂载时拉取 GET /api/sessions 展示会话总数；
// 「清空全部会话」为高危操作，弹二次确认框，确认后 POST /api/sessions/clear-all 并回调 onCleared。
// delta add-analysis-concurrency-setting：新增「批量分析最大并发数」设置项
// （GET/PUT /api/settings/analysis-concurrency；保存即作用于 AnalyzeQueue）。
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../../../components/ui/button'

interface SessionsPaneProps {
  // 清空成功后回调（父组件可用于刷新会话列表 / 提示）
  onCleared?: () => void
}

interface AnalysisConcurrency {
  value: number
  source: 'file' | 'env' | 'default'
  default: number
  min: number
  max: number
  env_value?: number | null
  file_status?: string
}

const CONCURRENCY_URL = '/api/settings/analysis-concurrency'

const SOURCE_LABEL: Record<string, string> = {
  file: '已保存设置',
  env: '环境变量',
  default: '默认值',
}

export function SessionsPane({ onCleared }: SessionsPaneProps) {
  const [count, setCount] = useState<number | null>(null)
  // 加载状态机：loading 首次加载中 / ready 已就绪（含刷新失败保留旧数据）/ error 首次加载失败
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  // 并发设置（独立加载：失败只影响本区块，不阻塞会话列表）
  const [concurrency, setConcurrency] = useState<AnalysisConcurrency | null>(null)
  const [concurrencyInput, setConcurrencyInput] = useState('')
  const [concurrencyStatus, setConcurrencyStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [concurrencySaving, setConcurrencySaving] = useState(false)
  const [concurrencySaved, setConcurrencySaved] = useState(false)
  const [concurrencyError, setConcurrencyError] = useState<string | null>(null)

  // 拉取会话总数：
  // - mode='full'（初次挂载 / 失败后点重试）：进入 loading，失败置 error 渲染错误态
  // - mode='refresh'（清空操作后刷新）：失败保留旧 count 不清空、不回落加载态，仅 toast
  const loadSessions = useCallback(async (mode: 'full' | 'refresh' = 'full') => {
    if (mode === 'full') setStatus('loading')
    try {
      const res = await fetch('/api/sessions')
      if (!res.ok) throw new Error(String(res.status))
      const data = (await res.json()) as { sessions?: unknown }
      // 契约：GET /api/sessions 返回 { sessions: [...] }；缺字段或非数组视为失败
      if (!Array.isArray(data.sessions)) throw new Error('invalid sessions payload')
      setCount(data.sessions.length)
      setStatus('ready')
    } catch {
      if (mode === 'full') setStatus('error')
      toast.error('会话列表加载失败')
    }
  }, [])

  const loadConcurrency = useCallback(async () => {
    setConcurrencyStatus('loading')
    try {
      const res = await fetch(CONCURRENCY_URL)
      if (!res.ok) throw new Error(String(res.status))
      const data = (await res.json()) as AnalysisConcurrency
      // 契约：值/区间必须是数字；source ∈ file/env/default（缺字段视为失败，不猜默认值）
      if (
        typeof data.value !== 'number' ||
        typeof data.min !== 'number' ||
        typeof data.max !== 'number' ||
        !['file', 'env', 'default'].includes(data.source)
      ) {
        throw new Error('invalid concurrency payload')
      }
      setConcurrency(data)
      setConcurrencyInput(String(data.value))
      setConcurrencyStatus('ready')
    } catch {
      setConcurrencyStatus('error')
    }
  }, [])

  useEffect(() => { void loadSessions() }, [loadSessions])
  useEffect(() => { void loadConcurrency() }, [loadConcurrency])

  // 确认清空：POST clear-all 成功回调 onCleared，失败仅 toast。
  // 提交期间 submitting 禁用确认按钮防重复点击；结束后走 refresh 刷新（失败保留旧数据）。
  const clearAll = async () => {
    setSubmitting(true)
    try {
      const res = await fetch('/api/sessions/clear-all', { method: 'POST' })
      if (!res.ok) throw new Error(String(res.status))
      onCleared?.()
    } catch {
      toast.error('清空会话失败')
    } finally {
      setSubmitting(false)
      setConfirmOpen(false)
      void loadSessions('refresh')
    }
  }

  const saveConcurrency = async () => {
    const raw = concurrencyInput.trim()
    const parsed = Number(raw)
    if (!/^\d+$/.test(raw) || !Number.isInteger(parsed)) {
      setConcurrencyError('请输入整数')
      setConcurrencySaved(false)
      return
    }
    if (concurrency && (parsed < concurrency.min || parsed > concurrency.max)) {
      setConcurrencyError(`取值范围 ${concurrency.min}–${concurrency.max}`)
      setConcurrencySaved(false)
      return
    }
    setConcurrencySaving(true)
    setConcurrencyError(null)
    setConcurrencySaved(false)
    try {
      const res = await fetch(CONCURRENCY_URL, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: parsed }),
      })
      if (!res.ok) throw new Error(String(res.status))
      const data = (await res.json()) as AnalysisConcurrency
      setConcurrency(data)
      setConcurrencyInput(String(data.value))
      setConcurrencySaved(true)
    } catch {
      setConcurrencyError('保存失败')
    } finally {
      setConcurrencySaving(false)
    }
  }

  // 首次加载失败：错误文案 + 重试
  if (status === 'error') {
    return (
      <div data-testid="sessions-pane-error" className="py-6 text-sm space-y-3" style={{ color: 'var(--text-tertiary)' }}>
        <p>会话列表加载失败</p>
        <Button data-testid="sessions-retry" size="sm" variant="outline" onClick={() => void loadSessions()}>
          重试
        </Button>
      </div>
    )
  }

  if (status === 'loading') {
    return <div className="py-6 text-sm" style={{ color: 'var(--text-tertiary)' }}>会话列表加载中…</div>
  }

  return (
    <div data-testid="sessions-pane" className="space-y-6">
      {/* 会话总数统计卡 */}
      <div className="rounded-xl p-4" style={{ background: 'var(--bg-overlay-l1)' }}>
        <p className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>会话总数</p>
        <p className="text-lg font-medium">{count}</p>
      </div>

      {/* 批量分析并发上限（delta add-analysis-concurrency-setting） */}
      <div data-testid="sessions-concurrency" className="rounded-xl p-4 space-y-3" style={{ background: 'var(--bg-overlay-l1)' }}>
        <h3 className="text-sm font-medium">批量分析最大并发数</h3>
        <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          选股批量、自动池、研报触发与事件条目共用此上限；超出部分排队。保存后立即生效。
        </p>

        {concurrencyStatus === 'loading' && (
          <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>并发设置加载中…</p>
        )}

        {concurrencyStatus === 'error' && (
          <div
            data-testid="sessions-concurrency-load-error"
            className="flex items-center gap-2 text-xs"
            style={{ color: 'var(--status-error-default)' }}
          >
            <span>并发设置加载失败</span>
            <Button size="sm" variant="outline" data-testid="sessions-concurrency-retry" onClick={() => void loadConcurrency()}>
              重试
            </Button>
          </div>
        )}

        {concurrencyStatus === 'ready' && concurrency && (
          <>
            <div className="flex flex-wrap items-end gap-2">
              <label className="space-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
                <span>并发上限（{concurrency.min}–{concurrency.max}）</span>
                <input
                  type="number"
                  inputMode="numeric"
                  min={concurrency.min}
                  max={concurrency.max}
                  step={1}
                  value={concurrencyInput}
                  data-testid="sessions-concurrency-input"
                  onChange={(e) => {
                    setConcurrencyInput(e.target.value)
                    setConcurrencySaved(false)
                    setConcurrencyError(null)
                  }}
                  className="w-28 rounded-md border border-border px-2 py-1 text-sm"
                  style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)' }}
                />
              </label>
              <Button
                size="sm"
                data-testid="sessions-concurrency-save"
                disabled={concurrencySaving}
                onClick={() => void saveConcurrency()}
              >
                {concurrencySaving ? '保存中…' : '保存'}
              </Button>
              {concurrencySaved && (
                <span data-testid="sessions-concurrency-saved" className="text-xs" style={{ color: 'var(--status-success-default)' }}>
                  已保存
                </span>
              )}
              {concurrencyError !== null && (
                <span data-testid="sessions-concurrency-save-error" className="text-xs" style={{ color: 'var(--status-error-default)' }}>
                  {concurrencyError}
                </span>
              )}
            </div>
            <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
              当前生效：{concurrency.value}（来源：{SOURCE_LABEL[concurrency.source] ?? concurrency.source}
              {concurrency.env_value != null
                ? `；环境变量 SCREENING_ANALYZE_CONCURRENCY=${concurrency.env_value}`
                : ''}）
              {concurrency.file_status === 'corrupt' || concurrency.file_status === 'invalid'
                ? ' · 配置文件异常，已回退'
                : ''}
            </p>
          </>
        )}
      </div>

      {/* 清空全部会话（高危） */}
      <div className="rounded-xl p-4 space-y-3" style={{ background: 'var(--bg-overlay-l1)' }}>
        <h3 className="text-sm font-medium">全部会话管理</h3>
        <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>高危操作：将删除全部会话记录。</p>
        <Button data-testid="sessions-clear-all" size="sm" variant="destructive" onClick={() => setConfirmOpen(true)}>
          清空全部会话
        </Button>
      </div>

      {/* 二次确认框 */}
      {confirmOpen && (
        <div
          data-testid="sessions-clear-all-confirm"
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.45)' }}
        >
          <div
            className="rounded-xl p-5 w-full max-w-sm space-y-3"
            style={{ background: 'var(--bg-overlay-l1)', border: '1px solid var(--border-neutral-l1)' }}
          >
            <p className="text-sm font-medium">清空全部会话？</p>
            <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>此操作不可恢复，删除后无法找回。</p>
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                data-testid="sessions-clear-all-cancel"
                onClick={() => setConfirmOpen(false)}
              >
                取消
              </Button>
              <Button
                size="sm"
                variant="destructive"
                data-testid="sessions-clear-all-submit"
                disabled={submitting}
                onClick={() => void clearAll()}
              >
                {submitting ? '清空中…' : '确认清空'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
