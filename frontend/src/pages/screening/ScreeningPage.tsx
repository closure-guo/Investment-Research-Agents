import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '../../components/ui/button'
import { EventWatchlistCard } from './EventWatchlistCard'
import { FactorSignalCard } from './FactorSignalCard'
import { MarketRegimeCard } from './MarketRegimeCard'
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

// 池档位（add-microcap-pool-ui）：主档缺省；小市值档 v1 只展示（不触发深度分析）
type PoolTier = 'main' | 'microcap'

// 门禁缺失要点码 → 可读文案（口径 pipeline.FRONTIER_MISSING_*；未知码原样列出）
const FRONTIER_MISSING_LABEL: Record<string, string> = {
  delist_pool: '退市股池未就绪',
  regime_data: '拥挤度数据未就绪',
}
// pipeline.FRONTIER_GATE_OBSERVATION_PREFIX 逐字前缀（记录字段自由串，只做前缀分流；
// 漂移由 E2E 门禁用例钉住）
const FRONTIER_GATE_OBSERVATION_PREFIX = 'observation_only:missing='

function parseFrontierGate(gate: string | null): { ready: boolean; missing: string[] } | null {
  if (!gate) return null
  if (gate === 'ready') return { ready: true, missing: [] }
  if (gate.startsWith(FRONTIER_GATE_OBSERVATION_PREFIX)) {
    const missing = gate
      .slice(FRONTIER_GATE_OBSERVATION_PREFIX.length)
      .split(',')
      .map(code => code.trim())
      .filter(Boolean)
    return { ready: false, missing }
  }
  // 未知取值：保守按观察态处理并原样列出（spec：未知要点码原样列出）
  return { ready: false, missing: [gate] }
}

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
  const [poolTier, setPoolTier] = useState<PoolTier>('main')
  const [data, setData] = useState<ScreeningPoolResponse | null>(null)
  const [error, setError] = useState(false)
  const [hoverSymbol, setHoverSymbol] = useState<string | null>(null)
  const [queue, setQueue] = useState<ScreeningQueueEntry[]>([])
  const [removed, setRemoved] = useState<ScreeningRemoval[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeMessage, setAnalyzeMessage] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const loadSeqRef = useRef(0)

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const load = useCallback(async (tier: PoolTier) => {
    const seq = ++loadSeqRef.current
    setError(false)
    setData(null)
    try {
      // 主档不带参数（缺省即 main，与既有请求零漂移）；小市值档显式带档位。
      // init 显式传 undefined：测试以 toHaveBeenCalledWith(url, undefined) 钉住双参形态
      const resp = await fetch(
        tier === 'main' ? '/api/screening/pool' : `/api/screening/pool?pool=${tier}`,
        undefined
      )
      if (!resp.ok) throw new Error(String(resp.status))
      const body = (await resp.json()) as ScreeningPoolResponse
      if (seq !== loadSeqRef.current) return // 迟到的旧档响应丢弃
      setData(body)
      setQueue(body.queue ?? [])
    } catch {
      if (seq !== loadSeqRef.current) return
      setError(true)
      toast.error('选股池加载失败')
    }
  }, [])

  // 轮询只刷新队列徽标态（池表格以首屏/手动重试为准，避免高频整表重绘）
  const poll = useCallback(async () => {
    try {
      // 主档不带参数（与既有请求零漂移）；显式带当前档位只为防切档窗口误刷
      const resp = await fetch(
        poolTier === 'main' ? '/api/screening/pool' : `/api/screening/pool?pool=${poolTier}`,
        undefined
      )
      if (!resp.ok) return
      const body = (await resp.json()) as ScreeningPoolResponse
      const entries = body.queue ?? []
      setQueue(entries)
      if (!entries.some(item => ACTIVE_QUEUE_STATES.has(item.state))) stopPolling()
    } catch {
      // 单轮失败不打断轮询：下一轮重试（终态由后端队列驱动，徽标错一轮可自愈）
    }
  }, [stopPolling, poolTier])

  const startPolling = useCallback(() => {
    if (timerRef.current !== null) return
    timerRef.current = window.setInterval(() => {
      void poll()
    }, pollIntervalMs)
  }, [poll, pollIntervalMs])

  // 档位切换即重取；切回重取保证数据与对应档位严格对应
  useEffect(() => {
    void load(poolTier)
  }, [load, poolTier])
  useEffect(() => stopPolling, [stopPolling])

  const switchTier = useCallback(
    (tier: PoolTier) => {
      if (tier === poolTier) return
      stopPolling() // 队列徽标轮询属主档分析流，切档即停
      setPoolTier(tier)
    },
    [poolTier, stopPolling]
  )

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

  const isMicrocap = poolTier === 'microcap'
  // testid 档位中缀：main 保持既有逐字（既有 E2E 零漂移），microcap 加前缀防选择器歧义
  const tid = (base: string) => (isMicrocap ? `screening-microcap-${base}` : `screening-${base}`)
  const gate = parseFrontierGate(data?.frontier_gate ?? null)
  // 档位口径文案
  const caliberText = isMicrocap
    ? '口径：小票通道阈值（流动性下限 1000 万）· L3 keep'
    : '口径：L3 粗筛 keep ∩ 连续在榜门槛达标'

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
          {isMicrocap
            ? '小市值通道观察池（同 L1→L2→L3 链；仅观察，不产生订单）'
            : '最近成功选股批次的候选池（L3 粗筛 keep ∩ 连续在榜门槛达标）'}
        </p>
        {poolTier === 'main' && (
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
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
        {/* ① 精选池：页面主体，置顶（此前是裸 <table> 且排在温度计/事件清单之后，
            首屏看不到选出来的标的） */}
        <section
          data-testid="screening-pool-card"
          className="rounded-lg border p-4"
          style={{ borderColor: 'var(--border-neutral-l1)' }}
        >
          <div className="mb-2 flex items-center gap-1" role="tablist" aria-label="池档位">
            {(['main', 'microcap'] as const).map(tier => (
              <button
                key={tier}
                type="button"
                role="tab"
                aria-selected={poolTier === tier}
                data-testid={`screening-tier-${tier}`}
                onClick={() => switchTier(tier)}
                className="rounded-full px-2.5 py-1 text-xs"
                style={
                  poolTier === tier
                    ? { background: 'var(--primary)', color: '#fff' }
                    : { background: 'var(--bg-overlay-l2)', color: 'var(--text-secondary)' }
                }
              >
                {tier === 'main' ? '主档池' : '小市值池'}
              </button>
            ))}
          </div>
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-sm font-medium text-foreground">精选池</span>
              <span
                data-testid="screening-pool-count"
                className="text-xs tabular-nums"
                style={{ color: 'var(--text-secondary)' }}
              >
                共 {pool?.length ?? 0} 只
              </span>
              {batchDate && (
                <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                  批次 {batchDate}
                </span>
              )}
            </div>
            <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
              {caliberText}
            </span>
          </div>

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

        {isMicrocap && gate?.ready && (
          <div
            data-testid="screening-microcap-gate-ready"
            className="rounded-lg border px-3 py-2 text-xs flex items-center gap-2"
            style={{ borderColor: 'var(--status-success-default)', color: 'var(--status-success-default)' }}
          >
            <i className="fas fa-circle-check text-xs"></i>
            前置齐备，可执行候选（仍不自动产生订单）
          </div>
        )}

        {isMicrocap && gate && !gate.ready && (
          <div
            data-testid="screening-microcap-gate-observation"
            className="rounded-lg border px-3 py-2 text-xs flex items-center gap-2 flex-wrap"
            style={{ borderColor: 'var(--status-warning-default)', color: 'var(--status-warning-default)' }}
          >
            <i className="fas fa-triangle-exclamation text-xs"></i>
            <span>观察模式：以下前置未就绪 ——</span>
            <span className="font-medium text-foreground">
              {gate.missing.map(code => FRONTIER_MISSING_LABEL[code] ?? code).join('、')}
            </span>
            <span>（仍不产生订单）</span>
          </div>
        )}

        {isMicrocap && data?.exec_constraints && (
          <div
            data-testid="screening-microcap-constraints"
            className="rounded-lg border px-3 py-2 text-xs"
            style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
          >
            建议执行约束（记录字段，非生效约束）：
            {typeof data.exec_constraints.suggested_single_cap === 'number' &&
              ` 单票上限 ${Math.round(data.exec_constraints.suggested_single_cap * 100)}%`}
            {typeof data.exec_constraints.slippage_tier_bump === 'number' &&
              ` · 滑点档位提升 ${data.exec_constraints.slippage_tier_bump} 档`}
          </div>
        )}

        {isMicrocap && (
          <div
            data-testid="screening-microcap-observation-note"
            className="text-xs"
            style={{ color: 'var(--text-tertiary)' }}
          >
            小市值通道 v1 仅观察：批次与约束仅为记录字段，不向执行链产生订单
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
            <Button variant="ghost" size="sm" onClick={() => { void load(poolTier) }}>
              重试
            </Button>
          </div>
        ) : pool === null ? (
          <div data-testid="screening-loading" className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
            加载中…
          </div>
        ) : poolEmpty ? (
          <div
            data-testid={isMicrocap ? 'screening-microcap-empty' : 'screening-empty'}
            className="text-sm"
            style={{ color: 'var(--text-tertiary)' }}
          >
            {isMicrocap
              ? '尚无小市值批次：等待小票通道日批落库'
              : '暂无可分析的精选池：等待下一批选股日批（或批次失败后重跑）'}
          </div>
        ) : (
          <table data-testid="screening-pool-table" className="w-full text-xs">
            <thead>
              <tr className="text-left" style={{ color: 'var(--text-tertiary)' }}>
                <th className="py-2 pr-3 font-medium">标的</th>
                <th className="py-2 pr-3 font-medium">股票名称</th>
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
                    data-testid={`${tid('row')}-${row.symbol}`}
                    className="border-t"
                    style={{ borderColor: 'var(--border)' }}
                  >
                    <td className="py-2 pr-3">
                      <span className="font-medium text-foreground">{row.symbol}</span>
                      {badge && (
                        <span
                          data-testid={`${tid('queue-badge')}-${row.symbol}`}
                          className="ml-2 rounded-full px-2 py-0.5 text-[10px]"
                          style={{ background: 'var(--bg-overlay-l2)', color: 'var(--text-secondary)' }}
                        >
                          {QUEUE_STATE_LABEL[badge] ?? badge}
                        </span>
                      )}
                    </td>
                    <td
                      data-testid={`${tid('stock-name')}-${row.symbol}`}
                      className="py-2 pr-3 align-top"
                      style={{ color: 'var(--text-secondary)' }}
                    >
                      {row.stock_name || '—'}
                    </td>
                    <td
                      data-testid={`${tid('score')}-${row.symbol}`}
                      className="py-2 pr-3 relative align-top"
                      onMouseEnter={() => setHoverSymbol(row.symbol)}
                      onMouseLeave={() => setHoverSymbol(null)}
                    >
                      <span className="cursor-help underline decoration-dotted tabular-nums">
                        {formatScore(row.composite_score)}
                      </span>
                      {hoverSymbol === row.symbol && (
                        <div
                          data-testid={tid('contribs-tooltip')}
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
        </section>

        {/* ③ 上下文卡片：市场温度计 → 因子信号 → 事件观察。
            它们是解释池子的上下文，排在主体之下；事件清单自带固定高度滚动框。 */}
        <MarketRegimeCard />
        {poolTier === 'main' && data?.factor_signal && <FactorSignalCard signal={data.factor_signal} />}
        <EventWatchlistCard queue={queue} onAnalysisStarted={startPolling} />
      </div>
    </div>
  )
}
