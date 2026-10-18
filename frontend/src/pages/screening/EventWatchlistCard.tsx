import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button } from '../../components/ui/button'
import type {
  EventAnalyzeResponse,
  EventEvidenceRef,
  EventVerdict,
  EventWatchlistItem,
  EventWatchlistResponse,
  ScreeningQueueEntry,
} from '../../types'

/**
 * 选股页「事件观察」区块（delta add-event-driven-lane T7；spec frontend
 * 「选股页事件观察区块」）。
 *
 * - 取数 ``GET /api/events/watchlist?source=``（组件内 fetch + alive 清理，同
 *   MarketRegimeCard）：清单条目全要素——来源标识 / 标的与名称 / 来源相关要素
 *   （业绩预告：报告期、预告类型、净利润变动幅度区间；条款事件 cbb：现价、转股
 *   溢价率、强赎倒计时，缺字段一律 ``—`` 不折算）/ 分诊结论三态徽章 / 理由与证据
 *   引用 / 公告日期 / 清单状态；
 * - 三态徽章为组件内本地实现（outperform/in_line/risk 不在 StatusKey 内，不扩展
 *   设计令牌），结论附口径注释「研究标记，非投资建议」（徽章 title 同源）；
 * - 「发起深度分析」→ ``POST /api/events/watchlist/{id}/analyze``（复用既有分析
 *   队列，MUST NOT 另建并发通道）：响应 ``queue_state`` 即刻渲染排队/运行态，
 *   已发起条目按钮禁用（后端幂等，前端不重复提交）；
 * - 队列态单一真源 = 页面既有队列快照（``queue`` prop，来自 ``/api/screening/pool``
 *   ``queue`` 字段）：本组件不另起定时器，随 ScreeningPage 的 ``pollIntervalMs``
 *   轮询推进（排队中 → 分析中 → 已完成）；``onAnalysisStarted`` 供页面在事件入队
 *   后启动该轮询。快照无该会话（后端重启）→ 如实「已发起」，不猜测排队态；
 * - 空清单 → ``event-watchlist-empty`` 空态文案（按来源筛选区分文案，避免把
 *   筛选为空误读为清单为空）；请求失败/载荷畸形 → ``event-watchlist-error`` +
 *   重试；两者严格区分，MUST NOT 骨架屏冒充（``event-watchlist-loading`` 仅为
 *   取数中提示，非空态）；
 * - ``?source=`` 过滤：默认不带参数（后端 ``source is not None`` 才过滤，空串会被
 *   当成 ``WHERE source=''`` 误过滤），选择来源后带参数重取。
 */

// 队列四态展示文案（口径与 ScreeningPage 队列徽标一致）
const QUEUE_STATE_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '分析中',
  done: '已完成',
  failed: '失败',
}

const VERDICT_META: Record<EventVerdict, { label: string; color: string }> = {
  outperform: { label: '超预期', color: 'var(--status-success-default)' },
  in_line: { label: '符合预期', color: 'var(--status-warning-default)' },
  risk: { label: '风险', color: 'var(--status-error-default)' },
}

// 来源标识（v1 真实数据为 earnings；cbb 为条款事件未来增量，同表同端点）
const SOURCE_LABEL: Record<string, string> = {
  earnings: '业绩预告',
  cbb: '条款事件',
}

const SOURCE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部来源' },
  { value: 'earnings', label: '业绩预告' },
  { value: 'cbb', label: '条款事件' },
]

const CALIBER_NOTE = '研究标记，非投资建议'

// 稳定空引用（避免默认值每次渲染换新数组，击穿 useMemo）
const NO_QUEUE: ScreeningQueueEntry[] = []

interface StartedMeta {
  sessionId: string | null
  queueState: string | null
}

export interface EventWatchlistCardProps {
  /** 页面既有队列快照（``/api/screening/pool`` 的 ``queue``）；按 session 匹配条目队列态 */
  queue?: ScreeningQueueEntry[]
  /** 事件入队后通知页面启动队列快照轮询（节奏 = 页面 pollIntervalMs，本组件不另建定时器） */
  onAnalysisStarted?: () => void
}

function fmtText(value: string | null | undefined): string {
  return value === null || value === undefined || value === '' ? '—' : value
}

function fmtNumber(value: number | null | undefined, digits: number, suffix = ''): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(digits)}${suffix}`
    : '—'
}

function fmtDays(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value)} 个交易日` : '—'
}

/** 净利润变动幅度区间（% 口径原样展示；单边缺失按 ≥/≤ 如实标注，不折算不补零） */
function fmtChangeRange(low: number | null, high: number | null): string {
  const hasLow = typeof low === 'number' && Number.isFinite(low)
  const hasHigh = typeof high === 'number' && Number.isFinite(high)
  if (hasLow && hasHigh) return `${(low as number).toFixed(1)}% ~ ${(high as number).toFixed(1)}%`
  if (hasLow) return `≥ ${(low as number).toFixed(1)}%`
  if (hasHigh) return `≤ ${(high as number).toFixed(1)}%`
  return '—'
}

/** 证据引用（field=value）；JSON 损坏后端给 null → —，不伪装成空数组也不编造 */
function fmtEvidence(evidence: EventEvidenceRef[] | null): string {
  if (!Array.isArray(evidence) || evidence.length === 0) return '—'
  return evidence
    .map(ref => `${ref.field}=${fmtEvidenceValue(ref.value)}`)
    .join('；')
}

function fmtEvidenceValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

interface ElementCard {
  key: string
  label: string
  value: string
}

/** 来源相关要素（按条目来源渲染；条款事件要素为预留读取位，缺失显示 —） */
function elementCards(item: EventWatchlistItem): ElementCard[] {
  if (item.source === 'cbb') {
    return [
      { key: 'price', label: '现价', value: fmtNumber(item.price, 2) },
      { key: 'premium_rate', label: '转股溢价率', value: fmtNumber(item.premium_rate, 1, '%') },
      { key: 'countdown', label: '强赎倒计时', value: fmtDays(item.call_countdown_days) },
    ]
  }
  return [
    { key: 'report_period', label: '报告期', value: fmtText(item.report_period) },
    { key: 'forecast_type', label: '预告类型', value: fmtText(item.forecast_type) },
    { key: 'change', label: '净利润变动幅度', value: fmtChangeRange(item.change_low, item.change_high) },
  ]
}

export function EventWatchlistCard({ queue = NO_QUEUE, onAnalysisStarted }: EventWatchlistCardProps) {
  // null = 取数中；failed = 接口失败/载荷畸形（与空清单严格区分）
  const [data, setData] = useState<EventWatchlistItem[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [source, setSource] = useState('')
  const [reloadToken, setReloadToken] = useState(0)
  // 发起响应（session + queue_state）：即时徽标，随后由页面队列快照接管
  const [startedMeta, setStartedMeta] = useState<Record<number, StartedMeta>>({})
  const [busy, setBusy] = useState<Record<number, boolean>>({})
  const [analyzeError, setAnalyzeError] = useState<Record<number, string>>({})

  useEffect(() => {
    let alive = true
    setFailed(false)
    setData(null)
    const query = source === '' ? '' : `?source=${encodeURIComponent(source)}`
    fetch(`/api/events/watchlist${query}`)
      .then(resp => (resp.ok ? resp.json() : Promise.reject(new Error(String(resp.status)))))
      .then((payload: EventWatchlistResponse) => {
        if (!alive) return
        // 畸形 200 体守卫：items 非数组 → 失败态，不炸整页（不同款于空清单）
        if (!payload || !Array.isArray(payload.items)) {
          setFailed(true)
          return
        }
        setData(payload.items)
      })
      .catch(() => {
        if (!alive) return
        setFailed(true)
        setData(null)
      })
    return () => {
      alive = false
    }
  }, [source, reloadToken])

  const queueBySession = useMemo(
    () => new Map(queue.map(entry => [entry.session_id, entry.state])),
    [queue]
  )

  const triggerAnalyze = useCallback(
    async (id: number) => {
      setBusy(prev => ({ ...prev, [id]: true }))
      setAnalyzeError(prev => {
        if (!(id in prev)) return prev
        const next = { ...prev }
        delete next[id]
        return next
      })
      try {
        const resp = await fetch(`/api/events/watchlist/${id}/analyze`, { method: 'POST' })
        if (!resp.ok) throw new Error(String(resp.status))
        const body = (await resp.json().catch(() => null)) as Partial<EventAnalyzeResponse> | null
        setStartedMeta(prev => ({
          ...prev,
          [id]: {
            sessionId: body?.session_id ?? null,
            queueState: body?.queue_state ?? null,
          },
        }))
        onAnalysisStarted?.()
      } catch {
        // 如实条目级提示（不伪装成已入队）；按钮保持可重试
        setAnalyzeError(prev => ({ ...prev, [id]: '发起深度分析失败' }))
      } finally {
        setBusy(prev => ({ ...prev, [id]: false }))
      }
    },
    [onAnalysisStarted]
  )

  const items = data ?? []
  const activeSourceLabel = SOURCE_LABEL[source] ?? source
  const emptyText =
    source === ''
      ? '事件观察清单为空：暂无通过分诊的事件（等待日批抓取与分诊）'
      : `事件观察清单为空：当前来源筛选（${activeSourceLabel}）下无条目`

  return (
    <div
      data-testid="event-watchlist-card"
      className="rounded-lg border p-4"
      style={{ borderColor: 'var(--border-neutral-l1)' }}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <i
            className="fas fa-bell text-sm"
            style={{ color: 'var(--primary)' }}
            aria-hidden="true"
          ></i>
          <span className="text-sm font-medium text-foreground">事件观察</span>
          <span
            data-testid="event-verdict-caliber-note"
            className="text-[10px]"
            style={{ color: 'var(--text-tertiary)' }}
          >
            {CALIBER_NOTE}
          </span>
        </div>
        <select
          data-testid="event-source-filter"
          aria-label="事件来源筛选"
          value={source}
          onChange={event => setSource(event.target.value)}
          className="rounded border bg-transparent px-2 py-1 text-xs"
          style={{ borderColor: 'var(--border-neutral-l1)', color: 'var(--text-secondary)' }}
        >
          {SOURCE_OPTIONS.map(option => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      {failed && (
        <div className="flex items-center gap-2 text-xs">
          <span data-testid="event-watchlist-error" style={{ color: 'var(--status-error-default)' }}>
            事件观察清单加载失败
          </span>
          <Button
            data-testid="event-watchlist-retry"
            variant="ghost"
            size="sm"
            onClick={() => setReloadToken(token => token + 1)}
          >
            重试
          </Button>
        </div>
      )}

      {!failed && data === null && (
        <div
          data-testid="event-watchlist-loading"
          className="text-xs"
          style={{ color: 'var(--text-tertiary)' }}
        >
          加载中…
        </div>
      )}

      {!failed && data !== null && items.length === 0 && (
        <div
          data-testid="event-watchlist-empty"
          className="text-xs"
          style={{ color: 'var(--text-tertiary)' }}
        >
          {emptyText}
        </div>
      )}

      {!failed && items.length > 0 && (
        <div className="space-y-2">
          {items.map(item => {
            const local = startedMeta[item.id]
            const sessionId = item.session_id ?? local?.sessionId ?? null
            const started = sessionId !== null || item.status === 'analyzing'
            const queueState =
              (sessionId !== null ? queueBySession.get(sessionId) : undefined) ??
              local?.queueState ??
              null
            const queueLabel = queueState !== null ? QUEUE_STATE_LABEL[queueState] ?? queueState : null
            const verdictMeta =
              item.verdict !== null && item.verdict in VERDICT_META
                ? VERDICT_META[item.verdict as EventVerdict]
                : { label: fmtText(item.verdict), color: 'var(--text-tertiary)' }
            const analyzeMessage = analyzeError[item.id]
            return (
              <div
                key={item.id}
                data-testid={`event-watchlist-row-${item.id}`}
                className="rounded border p-2"
                style={{ borderColor: 'var(--border-neutral-l1)' }}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      data-testid={`event-source-badge-${item.id}`}
                      className="rounded-full border px-2 py-0.5 text-[10px]"
                      style={{
                        borderColor: 'var(--border-neutral-l1)',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {SOURCE_LABEL[item.source ?? ''] ?? fmtText(item.source)}
                    </span>
                    <span className="text-sm font-medium text-foreground">
                      {fmtText(item.code)} {fmtText(item.name)}
                    </span>
                    <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                      公告日 {fmtText(item.announce_date)}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      data-testid={`event-verdict-badge-${item.id}`}
                      data-verdict={item.verdict ?? ''}
                      title={CALIBER_NOTE}
                      className="rounded-full border px-2 py-0.5 text-[10px] font-medium"
                      style={{ color: verdictMeta.color, borderColor: verdictMeta.color }}
                    >
                      {verdictMeta.label}
                    </span>
                    {started && (
                      <span
                        data-testid={`event-queue-badge-${item.id}`}
                        className="rounded-full px-2 py-0.5 text-[10px]"
                        style={{
                          background: 'var(--bg-overlay-l2)',
                          color: 'var(--text-secondary)',
                        }}
                      >
                        {queueLabel ?? '已发起'}
                      </span>
                    )}
                    <Button
                      data-testid={`event-analyze-btn-${item.id}`}
                      variant="outline"
                      size="sm"
                      disabled={started || busy[item.id] === true}
                      onClick={() => {
                        void triggerAnalyze(item.id)
                      }}
                    >
                      <i className="fas fa-magnifying-glass-chart text-[10px]"></i>
                      发起深度分析
                    </Button>
                  </div>
                </div>

                <div className="mt-1 grid grid-cols-1 gap-1 md:grid-cols-3">
                  {elementCards(item).map(element => (
                    <div
                      key={element.key}
                      data-testid={`event-element-${item.id}-${element.key}`}
                      className="rounded border px-2 py-1"
                      style={{ borderColor: 'var(--border-neutral-l1)' }}
                    >
                      <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                        {element.label}
                      </div>
                      <div className="text-xs tabular-nums text-foreground">{element.value}</div>
                    </div>
                  ))}
                </div>

                <div className="mt-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
                  理由：{fmtText(item.reason)}
                </div>
                <div
                  data-testid={`event-evidence-${item.id}`}
                  className="text-[10px]"
                  style={{ color: 'var(--text-tertiary)' }}
                >
                  证据：{fmtEvidence(item.evidence)}
                </div>

                {sessionId !== null && (
                  <div
                    data-testid={`event-session-${item.id}`}
                    className="text-[10px]"
                    style={{ color: 'var(--text-tertiary)' }}
                  >
                    已发起深度分析 · 会话 {sessionId}
                  </div>
                )}

                {analyzeMessage && (
                  <div
                    data-testid={`event-analyze-message-${item.id}`}
                    className="text-[10px]"
                    style={{ color: 'var(--status-error-default)' }}
                  >
                    {analyzeMessage}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
