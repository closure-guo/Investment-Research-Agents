import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../../components/ui/button'
import type {
  ScreeningAnalyzeResponse,
  ScreeningFactorContrib,
  ScreeningPoolResponse,
  ScreeningQueueEntry,
  ScreeningRemoval,
} from '../../types'

/**
 * 选股页（add-screening-pipeline T4；S5 + spec frontend delta）。
 *
 * - 取数 ``GET /api/screening/pool``（loading/error/empty 三态与列表严格区分）：
 *   最近成功批次的候选池表格（标的/综合分/连续在榜天数/L3 判定/批次日期）；
 * - 综合分 hover → 浮层展示 ``factor_contribs``（factor_id + contribution，
 *   按 |contribution| 降序）；
 * - 最近一次批次非 ok（``stale``）→ 横幅标注最近成功批次日期（spec：仍展示
 *   最近成功批次池）；
 * - 「对精选池跑深度分析」→ ``POST /api/screening/analyze`` → 立即渲染
 *   queued/running 徽标 + ``removed`` 移除原因，随后按 ``pollIntervalMs``
 *   （默认 2s）轮询池端点刷新徽标，队列无活动条目时自动停止轮询。
 *
 * 批量分析的进度本体仍走既有 SSE 会话通道（本页只消费队列徽标态）。
 */

// 队列四态展示文案（done/failed 为终态，不渲染徽标）
const QUEUE_STATE_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '分析中',
  done: '已完成',
  failed: '失败',
}
// 徽标可见态：仅排队/运行中（S5：终态不占位）
const ACTIVE_QUEUE_STATES = new Set(['queued', 'running'])

const DEFAULT_POLL_INTERVAL_MS = 2000

function sortedContribs(contribs: ScreeningFactorContrib[]): ScreeningFactorContrib[] {
  // 按 |contribution| 降序（spec 场景：浮层顺序即需求）
  return [...contribs].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
}

function groupRemovals(removals: ScreeningRemoval[]): Array<{ symbol: string; items: ScreeningRemoval[] }> {
  // 同一标的可能多因（S2 一次返回全部原因）→ 按标的分组，避免重复 testid/多行噪声
  const grouped = new Map<string, ScreeningRemoval[]>()
  for (const item of removals) {
    grouped.set(item.symbol, [...(grouped.get(item.symbol) ?? []), item])
  }
  return [...grouped.entries()].map(([symbol, items]) => ({ symbol, items }))
}

export function ScreeningPage({
  onBack,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
}: {
  onBack: () => void
  pollIntervalMs?: number
}) {
  // null = 加载中；error = 接口失败（与「无成功批次」空态严格区分）
  const [data, setData] = useState<ScreeningPoolResponse | null>(null)
  const [error, setError] = useState(false)
  const [hoverSymbol, setHoverSymbol] = useState<string | null>(null)
  const [queue, setQueue] = useState<ScreeningQueueEntry[]>([])
  const [removed, setRemoved] = useState<ScreeningRemoval[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeMessage, setAnalyzeMessage] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const load = useCallback(async () => {
    setError(false)
    try {
      const resp = await fetch('/api/screening/pool')
      if (!resp.ok) throw new Error(String(resp.status))
      const body = (await resp.json()) as ScreeningPoolResponse
      setData(body)
      setQueue(body.queue ?? [])
    } catch {
      setError(true)
      toast.error('选股池加载失败')
    }
  }, [])

  // 轮询只刷新队列徽标态（池表格以首屏/手动重试为准，避免高频整表重绘）
  const poll = useCallback(async () => {
    try {
      const resp = await fetch('/api/screening/pool')
      if (!resp.ok) return
      const body = (await resp.json()) as ScreeningPoolResponse
      const entries = body.queue ?? []
      setQueue(entries)
      if (!entries.some(item => ACTIVE_QUEUE_STATES.has(item.state))) stopPolling()
    } catch {
      // 单轮失败不打断轮询：下一轮重试（终态由后端队列驱动，徽标错一轮可自愈）
    }
  }, [stopPolling])

  const startPolling = useCallback(() => {
    if (timerRef.current !== null) return
    timerRef.current = window.setInterval(() => {
      void poll()
    }, pollIntervalMs)
  }, [poll, pollIntervalMs])

  useEffect(() => {
    void load()
  }, [load])
  useEffect(() => stopPolling, [stopPolling])

  const triggerAnalyze = useCallback(async () => {
    setAnalyzing(true)
    setAnalyzeMessage(null)
    try {
      const resp = await fetch('/api/screening/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      const body = (await resp.json().catch(() => ({}))) as Partial<ScreeningAnalyzeResponse> & {
        message?: string
      }
      if (!resp.ok) {
        setAnalyzeMessage(body.message ?? '批量分析触发失败')
        return
      }
      const entries = [...(body.started ?? []), ...(body.queued ?? [])]
      setQueue(entries)
      setRemoved(body.removed ?? [])
      if (entries.length > 0) startPolling()
    } catch {
      setAnalyzeMessage('批量分析触发失败')
      toast.error('批量分析触发失败')
    } finally {
      setAnalyzing(false)
    }
  }, [startPolling])

  const pool = data?.pool ?? null
  const batchDate = data?.batch_date ?? null
  const queueBySymbol = useMemo(
    () => new Map(queue.map(item => [item.symbol, item.state])),
    [queue]
  )
  const removedGroups = useMemo(() => groupRemovals(removed), [removed])
  const poolEmpty = pool !== null && pool.length === 0
  // 降级模式（add-screening-empty-library-fallback）：L2 跳过、按成交额排序——
  // 横幅披露 + 综合分列切换为成交额语义（标题与数值口径同步切换）
  const fallback = data?.selection_mode === 'fallback'
  const scoreHeader = fallback ? '近 20 日日均成交额（亿元）' : '综合分'
  const formatScore = (score: number) =>
    fallback ? (score / 1e8).toFixed(2) : score.toFixed(3)

  return (
    <div className="flex flex-col h-screen" data-testid="screening-page">
      {/* 标题栏（固定）：返回入口在所有状态下可见（空态/加载失败均可靠退回） */}
      <div
        className="shrink-0 px-6 pt-5 pb-4 border-b border-border"
        style={{ background: 'var(--background)' }}
      >
        <div className="flex items-center gap-2">
          <Button
            onClick={onBack}
            data-testid="screening-back"
            variant="ghost"
            size="icon"
            aria-label="返回对话"
            className="h-7 w-7"
          >
            <i className="fas fa-arrow-left text-sm"></i>
          </Button>
          <i className="fas fa-filter text-sm" style={{ color: 'var(--primary)' }}></i>
          <h1 className="text-base font-semibold text-foreground">选股池</h1>
          {batchDate && (
            <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              批次日期 {batchDate}
            </span>
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          最近成功选股批次的候选池（L3 粗筛 keep ∩ 连续在榜门槛达标）
        </p>
        <div className="flex items-center gap-3 mt-3">
          <Button
            data-testid="screening-analyze-btn"
            onClick={() => { void triggerAnalyze() }}
            disabled={analyzing || pool === null || pool.length === 0}
            size="sm"
          >
            <i className="fas fa-play text-[10px]"></i>
            对精选池跑深度分析
          </Button>
          {analyzeMessage && (
            <span
              data-testid="screening-analyze-message"
              className="text-xs"
              style={{ color: 'var(--status-error-default)' }}
            >
              {analyzeMessage}
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
        {data?.stale && (
          // spec：当日批次失败 → 页面展示最近成功批次并标注其日期
          <div
            data-testid="screening-batch-stale-banner"
            className="rounded-lg border px-3 py-2 text-xs flex items-center gap-2"
            style={{ borderColor: 'var(--status-warning-default)', color: 'var(--status-warning-default)' }}
          >
            <i className="fas fa-triangle-exclamation text-xs"></i>
            选股批次失败（最近成功: {batchDate ?? '无'}）——以下为最近成功批次池
          </div>
        )}

        {fallback && (
          // spec「无可打分因子降级通道」：降级披露横幅（文案单源在后端 notice）
          <div
            data-testid="screening-fallback-banner"
            className="rounded-lg border px-3 py-2 text-xs flex items-center gap-2"
            style={{ borderColor: 'var(--status-warning-default)', color: 'var(--status-warning-default)' }}
          >
            <i className="fas fa-triangle-exclamation text-xs"></i>
            {data?.notice ?? '因子库无可打分因子：本次为降级模式，按近 20 日日均成交额排序（非因子信号）'}
          </div>
        )}

        {removedGroups.length > 0 && (
          <div
            data-testid="screening-removed-list"
            className="rounded-lg border px-3 py-2 text-xs space-y-1"
            style={{ borderColor: 'var(--status-warning-default)' }}
          >
            <div className="font-medium text-foreground">时效性校验移除（未进入本次深度分析）</div>
            {removedGroups.map(group => (
              <div key={group.symbol} data-testid={`screening-removed-${group.symbol}`} className="text-muted-foreground">
                <span className="font-medium text-foreground">{group.symbol}</span>
                （{group.items.map(item => item.reason).join('、')}）
                {group.items
                  .map(item => (item.detail ? `：${item.detail}` : ''))
                  .join('')}
              </div>
            ))}
          </div>
        )}

        {error ? (
          <div data-testid="screening-error" className="flex items-center gap-2 text-sm text-foreground">
            选股池加载失败
            <Button variant="ghost" size="sm" onClick={() => { void load() }}>
              重试
            </Button>
          </div>
        ) : pool === null ? (
          <div data-testid="screening-loading" className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
            加载中…
          </div>
        ) : poolEmpty ? (
          <div data-testid="screening-empty" className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
            暂无可分析的精选池：等待下一批选股日批（或批次失败后重跑）
          </div>
        ) : (
          <table data-testid="screening-pool-table" className="w-full text-xs">
            <thead>
              <tr className="text-left" style={{ color: 'var(--text-tertiary)' }}>
                <th className="py-2 pr-3 font-medium">标的</th>
                <th className="py-2 pr-3 font-medium">{scoreHeader}</th>
                <th className="py-2 pr-3 font-medium">连续在榜天数</th>
                <th className="py-2 pr-3 font-medium">L3 判定</th>
                <th className="py-2 font-medium">批次日期</th>
              </tr>
            </thead>
            <tbody>
              {(pool ?? []).map(row => {
                const state = queueBySymbol.get(row.symbol)
                const badge = state !== undefined && ACTIVE_QUEUE_STATES.has(state) ? state : null
                return (
                  <tr
                    key={row.symbol}
                    data-testid={`screening-row-${row.symbol}`}
                    className="border-t"
                    style={{ borderColor: 'var(--border)' }}
                  >
                    <td className="py-2 pr-3">
                      <span className="font-medium text-foreground">{row.symbol}</span>
                      {badge && (
                        <span
                          data-testid={`screening-queue-badge-${row.symbol}`}
                          className="ml-2 rounded-full px-2 py-0.5 text-[10px]"
                          style={{ background: 'var(--bg-overlay-l2)', color: 'var(--text-secondary)' }}
                        >
                          {QUEUE_STATE_LABEL[badge] ?? badge}
                        </span>
                      )}
                    </td>
                    <td
                      data-testid={`screening-score-${row.symbol}`}
                      className="py-2 pr-3 relative align-top"
                      onMouseEnter={() => setHoverSymbol(row.symbol)}
                      onMouseLeave={() => setHoverSymbol(null)}
                    >
                      <span className="cursor-help underline decoration-dotted tabular-nums">
                        {formatScore(row.composite_score)}
                      </span>
                      {hoverSymbol === row.symbol && (
                        <div
                          data-testid="screening-contribs-tooltip"
                          className="absolute left-0 top-full z-20 mt-1 w-56 rounded-lg border p-2 shadow-lg"
                          style={{ borderColor: 'var(--border)', background: 'var(--background)' }}
                        >
                          <div className="text-[10px] mb-1" style={{ color: 'var(--text-tertiary)' }}>
                            因子贡献（|contribution| 降序）
                          </div>
                          {sortedContribs(row.factor_contribs ?? []).map(contrib => (
                            <div
                              key={contrib.factor_id}
                              data-testid={`screening-contrib-${contrib.factor_id}`}
                              className="flex justify-between gap-3 py-0.5"
                            >
                              <span>因子 {contrib.factor_id}</span>
                              <span className="tabular-nums">{contrib.contribution.toFixed(4)}</span>
                            </div>
                          ))}
                          {(row.factor_contribs ?? []).length === 0 && (
                            <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                              无贡献分解
                            </div>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="py-2 pr-3 tabular-nums">{row.consecutive_days}</td>
                    <td className="py-2 pr-3">
                      <span>{row.l3_verdict}</span>
                      <span className="mx-1">·</span>
                      <span>{row.l3_reason}</span>
                    </td>
                    <td className="py-2 tabular-nums">{batchDate ?? '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
