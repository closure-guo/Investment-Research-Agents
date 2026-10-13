import { Fragment, useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import ReactECharts from 'echarts-for-react'
import type { CurrentStanceResponse, EquityCurvePoint, PredictionRecord, PredictionsResponse, SegmentDimension, TrackRecordOverview } from '../../types'
import { Button } from '../../components/ui/button'
import { navigate } from '../../route'
import { cssVar } from '../../Charts'
import { loadTrackPrefs, type TrackTimeSpan } from '../../lib/trackPrefs'
import { PREDICTION_STATUS_CLS as STATUS_CLS, PREDICTION_STATUS_LABEL as STATUS_LABEL } from './predictionStatus'
import { DIRECTION_LABEL } from './predictionDisplay'
import { IndexCompareCard } from './IndexCompareCard'

// 可排序列（add-track-record-sort-filter）：与后端 _SORT_WHITELIST 对齐；
// sortable: false 为纯展示列（不渲染排序按钮）。
// 价格三列口径标注：参考价=决策时点盘面口径，结算两列=hfq 后复权（update-track-record-settle-price-display）
const COLUMNS: Array<{ key: string; label: string; numeric?: boolean; sortable?: boolean }> = [
  { key: 'created_at', label: '建立日期' },
  { key: 'symbol', label: '标的' },
  { key: 'direction', label: '方向' },
  { key: 'status', label: '状态' },
  // update-track-record-display-clarity:窗口列——混合口径显式可见（T+20/T+252）；展示列不排序
  { key: 'horizon_days', label: '窗口', sortable: false },
  { key: 'entry_price', label: '参考价（盘面）', numeric: true },
  { key: 'settle_entry_price', label: '结算入场价（后复权）', numeric: true, sortable: false },
  { key: 'exit_price', label: '结算价（后复权）', numeric: true },
  { key: 'raw_return', label: '区间收益', numeric: true },
  { key: 'excess_return', label: '基准超额', numeric: true },
]

const PAGE_SIZE = 50

// 时间跨度窗口（add-agent-settings-center Task 12 审查修复）：
// 后端 GET /api/v1/track-record/equity-curve 无区间参数（src/finance_agent/api.py:2117），
// timeSpan 由前端在拉取全量曲线后按日期窗口裁剪。口径：timeSpan 仅作用于净值曲线展示
// 窗口（'3m'/'6m'/'1y' = 截至曲线最新点的最近 3/6/12 个月），总览指标（胜率/超额/
// 风险卡/切片）仍为全期口径不受影响；'all' 不过滤。
const TIME_SPAN_MONTHS: Record<Exclude<TrackTimeSpan, 'all'>, number> = {
  '3m': 3,
  '6m': 6,
  '1y': 12,
}

// ISO 日期（YYYY-MM-DD）平移 deltaMonths 个月；日超目标月末时按月末钳制，
// 规避 Date#setMonth 的溢出进位（如 05-31 减 6 个月应落 11-30，而非 12-01）。
function isoDateShiftMonths(iso: string, deltaMonths: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  const monthIndex = y * 12 + (m - 1) + deltaMonths
  const ny = Math.floor(monthIndex / 12)
  const nm = ((monthIndex % 12) + 12) % 12
  const nd = Math.min(d, new Date(ny, nm + 1, 0).getDate())
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${ny}-${pad(nm + 1)}-${pad(nd)}`
}

// 按时间跨度裁剪曲线点：'all' 原样返回；否则以数据内最新日期为右边界保留最近
// N 个月（含边界日）。后端按 curve_date ASC 返回，过滤后仍保持原顺序。
function windowCurveByTimeSpan(points: EquityCurvePoint[], timeSpan: TrackTimeSpan): EquityCurvePoint[] {
  if (points.length === 0 || timeSpan === 'all') return points
  const lastDate = points.reduce<string>((mx, p) => (p.date > mx ? p.date : mx), points[0].date)
  const cutoff = isoDateShiftMonths(lastDate, -TIME_SPAN_MONTHS[timeSpan])
  return points.filter(p => p.date >= cutoff)
}

function Delta({ value }: { value: number | null }) {
  if (value === null) return <span className="text-txt-tertiary">—</span>
  const pct = value * 100
  const up = value >= 0
  return <span className={`${up ? 'text-[color:var(--status-error-default)]' : 'text-[color:var(--status-success-default)]'} font-medium`}>{up ? '+' : ''}{pct.toFixed(2)}%</span>
}

function fmt(value: number | null, digits = 2) {
  return value === null ? '—' : value.toFixed(digits)
}

function pct(value: number | null, digits = 1) {
  return value === null ? '—' : `${(value * 100).toFixed(digits)}%`
}

// 风险分 → 展示色（stage-b：分数越高风险越高）
function riskColor(score: number | null) {
  if (score === null) return ''
  if (score >= 8) return 'text-[color:var(--status-error-default)]'
  if (score >= 5) return 'text-[color:var(--status-warning-default)]'
  return 'text-[color:var(--status-success-default)]'
}

export function TrackRecordPage({ onBack }: { onBack: () => void }) {
  const [overview, setOverview] = useState<TrackRecordOverview | null>(null)
  const [records, setRecords] = useState<PredictionRecord[] | null>(null)
  const [curve, setCurve] = useState<EquityCurvePoint[] | null>(null)
  const [segments, setSegments] = useState<SegmentDimension[] | null>(null)
  const [currentStance, setCurrentStance] = useState<PredictionRecord[] | null>(null)
  const [currentStanceError, setCurrentStanceError] = useState(false)
  const [version, setVersion] = useState<number | null>(null)
  const [error, setError] = useState(false)
  const [sortBy, setSortBy] = useState<string>('created_at')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [keywordInput, setKeywordInput] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [applied, setApplied] = useState({ keyword: '', dateFrom: '', dateTo: '' })
  // update-prediction-log-tabs:状态 tab——open=当前持有(与总览卡同口径)/resolved=已判定/''=全部
  const [statusFilter, setStatusFilter] = useState<'open' | 'resolved' | ''>('open')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  // update-track-record-display-clarity:同日重复折叠——展开组 key 集合(当前页前端作用域)
  const [expandedDups, setExpandedDups] = useState<Set<string>>(new Set())

  const load = useCallback(async (ver: number | null) => {
    setError(false)
    try {
      const [ovResp, cvResp, sgResp] = await Promise.all([
        fetch(`/api/v1/track-record/overview${ver !== null ? `?version=${ver}` : ''}`),
        fetch('/api/v1/track-record/equity-curve'),
        fetch('/api/v1/track-record/segments'),
      ])
      if (!ovResp.ok || !cvResp.ok || !sgResp.ok) throw new Error(String(ovResp.status))
      setOverview((await ovResp.json()) as TrackRecordOverview)
      const cv = (await cvResp.json()) as { points: EquityCurvePoint[] }
      // 时间跨度窗口在拉取后裁剪（见 windowCurveByTimeSpan；默认 'all' 不过滤）
      setCurve(windowCurveByTimeSpan(cv.points, loadTrackPrefs().timeSpan))
      const sg = (await sgResp.json()) as { dimensions: SegmentDimension[] }
      setSegments(sg.dimensions)
    } catch {
      setError(true)
      toast.error('战绩数据加载失败')
    }

    // 当前立场视图独立加载(add-current-stance-view):失败显示显式失败文案,
    // 不静默降级为空态冒充「无观点」
    try {
      const csResp = await fetch('/api/v1/track-record/current')
      if (!csResp.ok) throw new Error(String(csResp.status))
      const cs = (await csResp.json()) as CurrentStanceResponse
      // 200 但缺 current 数组 = 契约违约：按失败处理（显式失败文案），
      // 不静默降级为空态冒充「无观点」（setCurrentStance(undefined) 还会炸渲染）
      if (!Array.isArray(cs.current)) throw new Error('malformed current stance payload')
      setCurrentStance(cs.current)
      setCurrentStanceError(false)
    } catch {
      setCurrentStanceError(true)
    }
  }, [])

  useEffect(() => { void load(version) }, [load, version])

  // 观点日志按服务端排序/过滤/分页重新拉取（add-track-record-sort-filter）
  const predictionsUrl = useCallback(() => {
    const p = new URLSearchParams()
    if (sortBy !== 'created_at') p.set('sort_by', sortBy)
    if (sortDir !== 'desc') p.set('sort_dir', sortDir)
    if (statusFilter) p.set('status', statusFilter)
    if (applied.keyword) p.set('keyword', applied.keyword)
    if (applied.dateFrom) p.set('date_from', applied.dateFrom)
    if (applied.dateTo) p.set('date_to', applied.dateTo)
    if (page > 1) p.set('page', String(page))
    p.set('page_size', String(PAGE_SIZE))
    const q = p.toString()
    return `/api/v1/track-record/predictions${q ? `?${q}` : ''}`
  }, [sortBy, sortDir, applied, page, statusFilter])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const resp = await fetch(predictionsUrl())
        if (!resp.ok) throw new Error(String(resp.status))
        const data = (await resp.json()) as PredictionsResponse
        if (cancelled) return
        setRecords(data.predictions)
        setTotal(data.total)
      } catch {
        if (!cancelled) setError(true)
      }
    })()
    return () => { cancelled = true }
  }, [predictionsUrl])

  const onStatusTab = (tab: 'open' | 'resolved' | '') => {
    if (tab !== statusFilter) {
      setStatusFilter(tab)
      setPage(1)
    }
  }

  const onSort = (col: string) => {
    if (sortBy === col) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(col)
      setSortDir(col === 'created_at' ? 'desc' : 'asc')
    }
    setPage(1)
  }
  const applyFilters = () => {
    setApplied({ keyword: keywordInput.trim(), dateFrom, dateTo })
    setPage(1)
  }
  const resetFilters = () => {
    setKeywordInput(''); setDateFrom(''); setDateTo('')
    setApplied({ keyword: '', dateFrom: '', dateTo: '' })
    setSortBy('created_at'); setSortDir('desc'); setPage(1)
  }
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const arrow = (col: string) => (sortBy === col ? (sortDir === 'asc' ? ' ↑' : ' ↓') : '')

  const disclaimer = overview?.disclaimer ?? '历史业绩不代表未来表现'
  const rows = records ?? []

  // update-track-record-display-clarity:同日重复折叠——渲染前把 rows 组装为显示序列。
  // 当前页内 consecutive (symbol, 建立日期) 的 duplicate_of_day 行合并为一个 dup-group
  // 汇总行（前端展示作用域，分页 total 与服务端数据零改动）；无 dup 行时退化为全 single
  type DisplayRow =
    | { kind: 'single'; row: PredictionRecord }
    | { kind: 'dup-group'; key: string; rows: PredictionRecord[] }
  const displayRows: DisplayRow[] = []
  {
    let i = 0
    while (i < rows.length) {
      const r = rows[i]
      const date = r.created_at.slice(0, 10)
      if (
        r.status === 'duplicate_of_day' &&
        i + 1 < rows.length &&
        rows[i + 1].status === 'duplicate_of_day' &&
        rows[i + 1].symbol === r.symbol &&
        rows[i + 1].created_at.slice(0, 10) === date
      ) {
        const group: PredictionRecord[] = []
        while (
          i < rows.length &&
          rows[i].status === 'duplicate_of_day' &&
          rows[i].symbol === r.symbol &&
          rows[i].created_at.slice(0, 10) === date
        ) {
          group.push(rows[i])
          i += 1
        }
        // key 加首行 prediction_id：同股同日可能有多段 consecutive dup 区（段间被日主行
        // 隔开，created_at DESC 下不连续；生产 688072 2026-10-02 实证两段）。仅 symbol|date
        // 会撞 key——两段共享 expandedDups 条目与 testid，点一组两组同开同收
        displayRows.push({ kind: 'dup-group', key: `${r.symbol}|${date}|${r.prediction_id}`, rows: group })
      } else {
        displayRows.push({ kind: 'single', row: r })
        i += 1
      }
    }
  }

  // 观点日志单行 JSX（update-track-record-display-clarity:同日重复折叠抽出自 tbody，
  // single 行与 dup-group 展开明细行共用；onClick 导航逻辑不变）
  const renderPredictionRow = (r: PredictionRecord) => (
    <tr
      key={r.prediction_id}
      className="border-t cursor-pointer hover:opacity-80"
      style={{ borderColor: 'var(--border-neutral-l1)' }}
      onClick={() => navigate(`/track-record/predictions/${r.prediction_id}`)}
      data-testid={`prediction-row-${r.prediction_id}`}
    >
      <td className="px-4 py-3" style={{ color: 'var(--text-secondary)' }}>{r.created_at.slice(0, 10)}</td>
      <td className="px-4 py-3">
        <div className="font-medium" style={{ color: 'var(--text-default)' }}>{r.symbol_name ?? r.symbol}</div>
        <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>{r.symbol}</div>
      </td>
      <td className="px-4 py-3" style={{ color: 'var(--text-secondary)' }}>{DIRECTION_LABEL[r.direction]}</td>
      <td className="px-4 py-3">
        <span className={STATUS_CLS[r.status]}>{STATUS_LABEL[r.status]}</span>
        {r.status === 'open' && (
          <span className="ml-1 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>未结算</span>
        )}
      </td>
      <td className="px-4 py-3" style={{ color: 'var(--text-secondary)' }}>T+{r.horizon_days}</td>
      <td className="px-4 py-3 text-right">{fmt(r.entry_price)}</td>
      <td className="px-4 py-3 text-right">{fmt(r.settle_entry_price)}</td>
      <td className="px-4 py-3 text-right">{fmt(r.exit_price)}</td>
      {(() => {
        // update-track-record-display-clarity:open 行展示最新盯市浮动
        // (latest_mark),已结算行展示结算读数;title 区分语义
        const floating = r.status === 'open' ? r.latest_mark : undefined
        const markTitle = floating ? `盯市 ${floating.mark_date}（未结算浮动）` : undefined
        return (
          <>
            <td className="px-4 py-3 text-right" title={markTitle}>
              <Delta value={floating ? floating.cum_return : r.raw_return} />
            </td>
            <td className="px-4 py-3 text-right" title={markTitle}>
              <Delta value={floating ? floating.cum_excess : r.excess_return} />
            </td>
          </>
        )
      })()}
    </tr>
  )

  const showWinRate = overview !== null && !overview.insufficient_sample && overview.win_rate !== null
  const portfolio = overview?.portfolio
  const showCurve = curve !== null && curve.length >= 2
  const versions = overview?.versions ?? []

  // 战绩展示偏好（add-agent-settings-center Task 12）：渲染期读取本地偏好并应用到
  // 回撤警示阈值 / 基准线开关 / 净值图形态；时间跨度在 load() 拉取 equity-curve 后
  // 按 windowCurveByTimeSpan 前端窗口裁剪（'all' 不过滤），仅作用于曲线窗口。
  const prefs = loadTrackPrefs()
  const showBenchmark = prefs.benchmark !== 'none'
  const isAreaForm = prefs.navChartForm === 'interval'

  // add-paper-broker（B4）：overview.paper 有数据时净值图切换为「虚拟结算 vs 仿真成交」
  // 双曲线——数据为接口对齐结果（共同日期、同一基准期、基期重合），前端不重算不插值；
  // null/缺省（无 paper 数据或旧后端）保持既有单曲线渲染，绝不伪造第二条线。
  // 基准叠加偏好沿用：沪深300 按共同日期取既有曲线序列（缺日断点，connectNulls:false）。
  const paper = overview?.paper ?? null
  const paperDates = paper !== null ? paper.series.map(p => p.date) : []
  const benchmarkByDate = new Map((curve ?? []).map(p => [p.date, p.benchmark_nav]))
  const lossText = paper !== null ? `${paper.loss_pct > 0 ? '+' : ''}${paper.loss_pct.toFixed(1)}%` : null

  const chartOption = {
    color: [cssVar('--chart-sky', '#228EBF'), cssVar('--chart-amber', '#CBB54C')],
    tooltip: { trigger: 'axis' as const, valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(4) : String(v)) },
    grid: { left: 48, right: 16, top: 24, bottom: 28 },
    xAxis: {
      type: 'category' as const,
      data: paper !== null ? paperDates : (curve ?? []).map(p => p.date),
      axisLabel: { fontSize: 10 },
    },
    yAxis: { type: 'value' as const, axisLabel: { fontSize: 10 } },
    series: paper !== null
      ? [
          {
            name: '虚拟结算',
            type: 'line' as const,
            data: paper.series.map(p => p.virtual_equity),
            showSymbol: false,
            connectNulls: false,
            ...(isAreaForm ? { areaStyle: { opacity: 0.18 } } : {}),
          },
          {
            name: '仿真成交',
            type: 'line' as const,
            data: paper.series.map(p => p.paper_equity),
            showSymbol: false,
            connectNulls: false,
          },
          // 基准偏好：'none' 不叠加；其它值按共同日期映射沪深300（缺日留空断点）
          ...(showBenchmark
            ? [{
                name: '沪深300',
                type: 'line' as const,
                data: paperDates.map(d => benchmarkByDate.get(d) ?? null),
                showSymbol: false,
                connectNulls: false,
              }]
            : []),
        ]
      : [
          {
            name: '组合净值',
            type: 'line' as const,
            data: (curve ?? []).map(p => p.agent_nav),
            showSymbol: false,
            connectNulls: false,
            // 净值图形态偏好：'interval'（区间收益）以面积填充突出区间变动；'cumulative'（累计净值）为纯折线
            ...(isAreaForm ? { areaStyle: { opacity: 0.18 } } : {}),
          },
          // 基准偏好：'none' 不叠加基准线；其它值叠加现有沪深300序列（zz500/zz1000 序列待接入，见 trackPrefs.ts）
          ...(showBenchmark
            ? [{
                name: '沪深300',
                type: 'line' as const,
                data: (curve ?? []).map(p => p.benchmark_nav),
                showSymbol: false,
                connectNulls: false,
              }]
            : []),
        ],
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-8" data-testid="track-record">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold" style={{ color: 'var(--text-default)' }}>历史战绩</h1>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => navigate('/track-record/calibration')} data-testid="track-record-calibration-link">校准查看</Button>
          <Button variant="ghost" size="sm" onClick={onBack} data-testid="track-record-back">返回聊天</Button>
        </div>
      </div>

      {/* 版本切换（P6 分段封存：统计按版本分段查看） */}
      {versions.length > 1 && (
        <div data-testid="track-record-version-selector" className="flex items-center gap-2 mb-4">
          <label className="text-xs" style={{ color: 'var(--text-tertiary)' }}>版本</label>
          <select
            value={version ?? overview?.version_seq ?? ''}
            onChange={e => setVersion(e.target.value === '' ? null : Number(e.target.value))}
            className="text-xs rounded-lg px-2 py-1 border"
            style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }}
            data-testid="track-record-version-select"
          >
            {versions.map(v => (
              <option key={v.agent_id} value={v.version_seq}>
                v{v.version_seq} {v.model_version}{v.retired_at ? '（已封存）' : '（当前）'}
              </option>
            ))}
          </select>
        </div>
      )}

      {error ? (
        <div className="text-sm py-16 text-center" style={{ color: 'var(--text-tertiary)' }}>数据加载失败，请刷新重试</div>
      ) : overview === null || records === null ? (
        <div className="py-16 text-center text-sm" style={{ color: 'var(--text-tertiary)' }}>加载中…</div>
      ) : (
        <>
          {/* 总览区 */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-3" data-testid="track-record-summary">
            <div className="rounded-xl p-4" style={{ background: 'var(--bg-overlay-l1)' }}>
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>观点总数</div>
              <div className="text-2xl font-semibold" style={{ color: 'var(--text-default)' }}>{overview.total}</div>
            </div>
            <div className="rounded-xl p-4" style={{ background: 'var(--bg-overlay-l1)' }}>
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>胜率（已结算）</div>
              <div className="text-2xl font-semibold" style={{ color: 'var(--text-default)' }}>
                {showWinRate ? `${(overview.win_rate! * 100).toFixed(1)}%` : '—'}
              </div>
            </div>
            {/* 回避正确率（Δ2 披露）：门槛真源在后端——GET /track-record/overview 在
                settled < MIN_SETTLED_FOR_WINRATE 时把 avoidance_rate 置 null（见
                src/finance_agent/api.py 的 avoidance_rate 组装），前端只认 null，
                不再复制「10」这个阈值。null → 「样本积累中」，绝不折算 0% */}
            <div className="rounded-xl p-4" data-testid="track-record-avoidance" style={{ background: 'var(--bg-overlay-l1)' }}>
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>回避正确率（中性观点已结算）</div>
              {overview.avoidance === undefined ? (
                <div className="text-2xl font-semibold" style={{ color: 'var(--text-default)' }}>—</div>
              ) : overview.avoidance.avoidance_rate === null ? (
                <div className="text-sm font-medium mt-1" style={{ color: 'var(--text-secondary)' }}>
                  样本积累中（已结算 {overview.avoidance.settled} 条，满 10 条解锁）
                </div>
              ) : (
                <>
                  <div className="text-2xl font-semibold" style={{ color: 'var(--text-default)' }}>
                    {pct(overview.avoidance.avoidance_rate, 1)}
                  </div>
                  <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                    样本 {overview.avoidance.settled} 条（回避命中 {overview.avoidance.avoidance_win} / 未命中 {overview.avoidance.avoidance_loss}）
                  </div>
                </>
              )}
            </div>
            <div className="rounded-xl p-4" style={{ background: 'var(--bg-overlay-l1)' }}>
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>平均超额</div>
              <div className="text-2xl font-semibold"><Delta value={overview.avg_excess} /></div>
            </div>
            <div className="rounded-xl p-4" style={{ background: 'var(--bg-overlay-l1)' }}>
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>截至</div>
              <div className="text-2xl font-semibold text-base mt-1" style={{ color: 'var(--text-default)' }}>{overview.as_of}</div>
            </div>
          </div>

          {/* 口径披露（Δ2）：判定口径与存量旧口径计数常驻，不受样本门槛限制；存量 0 明示「无存量」。
              update-track-record-display-clarity：双计数——legacy_settled（已结算）与
              legacy_open（进行中，不计入头条口径）分列；legacy_open=0 或缺省不渲染该分句 */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-4 text-xs"
            style={{ color: 'var(--text-tertiary)' }} data-testid="track-record-caliber-row">
            <span data-testid="track-record-caliber">
              当前判定口径 T+{overview.caliber_horizon ?? '（未提供）'} 交易日
            </span>
            <span data-testid="track-record-legacy">
              {overview.legacy_settled === undefined
                ? '存量旧口径：（未提供）'
                : overview.legacy_settled === 0
                  ? '存量旧口径：无存量'
                  : `另有 ${overview.legacy_settled} 条旧口径已结算`}
            </span>
            {overview.legacy_open !== undefined && overview.legacy_open > 0 && (
              <span data-testid="track-record-legacy-open">
                另有 {overview.legacy_open} 条旧口径进行中，不计入头条口径
              </span>
            )}
          </div>

          {/* 样本积累提示 */}
          {overview.insufficient_sample && (
            <div className="mb-4 rounded-lg px-4 py-3 text-xs" style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-secondary)' }} data-testid="track-record-insufficient">
              样本积累中（已结算 {overview.settled} 条，满 10 条解锁胜率）
            </div>
          )}

          {/* 组合风险指标（add-track-record-stage-b：P4 收益与风险成对） */}
          <div data-testid="track-record-risk" className="rounded-xl p-4 mb-6" style={{ background: 'var(--bg-overlay-l1)' }}>
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>组合风险指标 <span className="ml-1 text-[10px]">年化/波动/夏普/最大回撤/风险分/β/α</span></div>
              {portfolio?.as_of && (
                <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>快照截至 {portfolio.as_of}</div>
              )}
            </div>
            {portfolio && portfolio.available ? (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div>
                  <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>年化收益</div>
                  <div className="text-lg font-semibold" style={{ color: portfolio.annual_return !== null && portfolio.annual_return >= 0 ? 'var(--text-default)' : 'var(--text-default)' }}>
                    <Delta value={portfolio.annual_return} />
                  </div>
                </div>
                <div>
                  <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>波动率</div>
                  <div className="text-lg font-semibold" style={{ color: 'var(--text-default)' }}>{pct(portfolio.volatility)}</div>
                </div>
                <div>
                  <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>夏普比率</div>
                  <div className="text-lg font-semibold" style={{ color: 'var(--text-default)' }}>{fmt(portfolio.sharpe)}</div>
                </div>
                <div>
                  <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>最大回撤</div>
                  <div className="text-lg font-semibold" style={{ color: portfolio.max_drawdown !== null && portfolio.max_drawdown >= prefs.drawdownThreshold ? 'var(--status-error-default)' : 'var(--text-default)' }}>{pct(portfolio.max_drawdown)}</div>
                </div>
                <div>
                  <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>风险分（{portfolio.risk_label ?? '—'}）</div>
                  <div className={`text-lg font-semibold ${riskColor(portfolio.risk_score)}`}>{portfolio.risk_score ?? '—'}</div>
                </div>
                {/* β/α（add-portfolio-beta-alpha）：!= null 同时兜住旧快照缺字段（undefined），如实占位「—」 */}
                <div>
                  <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>β 市场敞口</div>
                  <div className="text-lg font-semibold" data-testid="portfolio-beta" style={{ color: 'var(--text-default)' }}>
                    {portfolio.beta != null ? portfolio.beta.toFixed(2) : '—'}
                  </div>
                  <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>≈1 满仓跟随大盘,负值=反向敞口</div>
                </div>
                <div>
                  <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>α 年化超额</div>
                  <div className="text-lg font-semibold" data-testid="portfolio-alpha" style={{ color: 'var(--text-default)' }}>
                    {portfolio.jensen_alpha != null ? <Delta value={portfolio.jensen_alpha} /> : '—'}
                  </div>
                  <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>剔除大盘影响后的独立判断收益</div>
                </div>
              </div>
            ) : (
              <div className="text-xs py-2" style={{ color: 'var(--text-tertiary)' }} data-testid="track-record-risk-empty">
                暂无净值快照——每个交易日收盘后自动生成组合净值与风险指标
              </div>
            )}
          </div>

          {/* 切片指标（stage-c：持有期/行业/市值/市场环境，n<10 标样本不足）。
              update-track-record-display-clarity:settled=0 时折叠为一行说明——
              首批观点结算前分桶表全是空桶，展示空表徒增噪音；settled>0 时
              沿用原区块（segments 加载守卫保留，避免 overview 先到时 map 空指针） */}
          {overview.settled === 0 ? (
            <div className="rounded-xl p-4 mb-6" data-testid="track-record-segments-empty" style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-tertiary)' }}>
              切片指标将在首批观点结算后可用
            </div>
          ) : segments !== null && segments.length > 0 ? (
            <div className="rounded-xl p-4 mb-6" style={{ background: 'var(--bg-overlay-l1)' }} data-testid="track-record-segments">
              <div className="text-xs mb-3" style={{ color: 'var(--text-tertiary)' }}>切片指标（n&lt;10 标注「样本不足」）</div>
              <div className="grid md:grid-cols-2 gap-4">
                {segments.map(d => (
                  <div key={d.dimension}>
                    <div className="text-xs font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>{d.dimension}</div>
                    <table className="w-full text-xs">
                      <thead>
                        <tr style={{ color: 'var(--text-tertiary)' }}>
                          <th className="py-1 pr-2 text-left font-normal">分桶</th>
                          <th className="py-1 pr-2 text-right font-normal">样本</th>
                          <th className="py-1 pr-2 text-right font-normal">胜率</th>
                          <th className="py-1 text-right font-normal">平均超额</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.buckets.filter(b => b.sample_size > 0).map(b => (
                          <tr key={b.name} className="border-t" style={{ borderColor: 'var(--border-neutral-l1)' }}>
                            <td className="py-1 pr-2" style={{ color: 'var(--text-default)' }}>
                              {b.name}
                              {b.insufficient && <span className="ml-1 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>样本不足</span>}
                            </td>
                            <td className="py-1 pr-2 text-right" style={{ color: 'var(--text-secondary)' }}>{b.sample_size}</td>
                            <td className="py-1 pr-2 text-right" style={{ color: 'var(--text-secondary)' }}>{b.win_rate === null ? '—' : `${(b.win_rate * 100).toFixed(1)}%`}</td>
                            <td className="py-1 text-right"><Delta value={b.avg_excess} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* 净值曲线（stage-b：agent vs 沪深300，起点归一 1.0；数据缺口断点不插值）。
              add-paper-broker（B4）：paper 数据存在时切换为「虚拟结算 vs 仿真成交」双曲线
              + 执行损耗标注（图例为 DOM 文本——ECharts 图例画在 canvas 内，DOM 图例区
              供 E2E/组件测试断言，spec 场景 1.043/1.031 → -1.2%） */}
          {showCurve && (
            <div className="rounded-xl p-4 mb-6" style={{ background: 'var(--bg-overlay-l1)' }} data-testid="track-record-curve">
              <div className="text-xs mb-2" style={{ color: 'var(--text-tertiary)' }}>
                {paper !== null
                  ? '虚拟结算 vs 仿真成交（同一批决策、同一基准期）'
                  : showBenchmark ? '组合净值 vs 沪深300（起点归一 1.0）' : '组合净值（起点归一 1.0）'}
              </div>
              {paper !== null && (
                <div data-testid="curve-legend" className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                  <span data-testid="legend-virtual" className="inline-flex items-center gap-1">
                    <i aria-hidden="true" className="inline-block h-2 w-2 rounded-full" style={{ background: cssVar('--chart-sky', '#228EBF') }} />
                    虚拟结算
                  </span>
                  <span data-testid="legend-paper" className="inline-flex items-center gap-1">
                    <i aria-hidden="true" className="inline-block h-2 w-2 rounded-full" style={{ background: cssVar('--chart-amber', '#CBB54C') }} />
                    仿真成交
                  </span>
                  <span data-testid="exec-loss" style={{ color: 'var(--status-warning-default)' }}>执行损耗: {lossText}</span>
                  <span data-testid="paper-note">{paper.note}</span>
                </div>
              )}
              <ReactECharts option={chartOption} style={{ height: 240 }} notMerge />
            </div>
          )}

          {/* 跑赢指数对比（add-index-performance-compare）：组合区间收益 vs 主要指数同期收益；
              span 与净值图窗口同源（页面战绩展示偏好 prefs.timeSpan） */}
          <IndexCompareCard span={prefs.timeSpan} />

          {/* 当前观点区（add-current-stance-view）：每股最新一条 open 的立场视图。
              纯展示收敛：台账不受影响，统计口径零改动（delta spec 战绩页面 MODIFIED） */}
          <div data-testid="current-stance" className="rounded-xl p-4 mb-6" style={{ background: 'var(--bg-overlay-l1)' }}>
            <div className="flex flex-wrap items-baseline justify-between gap-1 mb-2">
              <div className="text-sm font-medium">当前观点</div>
              <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                每股仅显示最新一条进行中观点;历史逐条记录见下方观点日志
              </div>
            </div>
            {currentStanceError ? (
              <div className="text-xs py-2" style={{ color: 'var(--text-secondary)' }}>当前观点加载失败</div>
            ) : currentStance === null ? (
              <div className="text-xs py-2" style={{ color: 'var(--text-tertiary)' }}>加载中…</div>
            ) : currentStance.length === 0 ? (
              <div className="text-xs py-2" data-testid="current-stance-empty" style={{ color: 'var(--text-tertiary)' }}>暂无进行中观点</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs" style={{ color: 'var(--text-tertiary)' }}>
                    <th className="px-2 py-2 font-normal">建立日期</th>
                    <th className="px-2 py-2 font-normal">标的</th>
                    <th className="px-2 py-2 font-normal">方向</th>
                    <th className="px-2 py-2 font-normal text-right">入场价</th>
                    <th className="px-2 py-2 font-normal">判定窗口</th>
                    <th className="px-2 py-2 font-normal">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {currentStance.map(r => (
                    <tr
                      key={r.prediction_id}
                      data-testid={`current-stance-row-${r.prediction_id}`}
                      className="border-t cursor-pointer hover:opacity-80"
                      style={{ borderColor: 'var(--border-neutral-l1)' }}
                      onClick={() => navigate(`/track-record/predictions/${r.prediction_id}`)}
                    >
                      <td className="px-2 py-3" style={{ color: 'var(--text-secondary)' }}>{r.created_at.slice(0, 10)}</td>
                      <td className="px-2 py-3">
                        <div className="font-medium" style={{ color: 'var(--text-default)' }}>{r.symbol_name ?? r.symbol}</div>
                        <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>{r.symbol}</div>
                      </td>
                      <td className="px-2 py-3" style={{ color: 'var(--text-secondary)' }}>{DIRECTION_LABEL[r.direction]}</td>
                      <td className="px-2 py-3 text-right">{fmt(r.entry_price)}</td>
                      <td className="px-2 py-3" style={{ color: 'var(--text-secondary)' }}>T+{r.horizon_days}</td>
                      <td className="px-2 py-3">
                        <span className={STATUS_CLS[r.status]}>{STATUS_LABEL[r.status]}</span>
                        <span className="ml-1 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>未结算</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* 观点日志（update-prediction-log-tabs）：标题 + 状态 tab */}
          <div className="flex items-center justify-between mb-3" data-testid="prediction-log-header">
            <div>
              <div className="text-sm font-medium">观点日志</div>
              <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                每条 = 一次分析结论;当前持有 = 仍在判定窗口内(长短见「窗口」列)
              </div>
            </div>
            <div className="flex gap-1" role="tablist" data-testid="prediction-log-tabs">
              {([['open', '当前持有'], ['resolved', '已判定'], ['', '全部']] as const).map(([value, label]) => (
                <button
                  key={label}
                  type="button"
                  role="tab"
                  aria-selected={statusFilter === value}
                  onClick={() => onStatusTab(value)}
                  data-testid={`prediction-tab-${value || 'all'}`}
                  className="text-xs rounded-lg px-3 py-1 border"
                  style={
                    statusFilter === value
                      ? { background: 'var(--bg-overlay-l2)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l2)' }
                      : { color: 'var(--text-secondary)', borderColor: 'var(--border-neutral-l1)' }
                  }
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {/* 过滤工具栏（add-track-record-sort-filter） */}
          <div className="flex flex-wrap items-center gap-2 mb-3" data-testid="track-record-filters">
            <input
              data-testid="track-record-keyword"
              value={keywordInput}
              onChange={e => setKeywordInput(e.target.value)}
              placeholder="输入代码/名称/方向/状态"
              className="text-xs rounded-lg px-2 py-1 border"
              style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }}
            />
            <input type="date" data-testid="track-record-date-from" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="text-xs rounded-lg px-2 py-1 border" style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }} />
            <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>至</span>
            <input type="date" data-testid="track-record-date-to" value={dateTo} onChange={e => setDateTo(e.target.value)} className="text-xs rounded-lg px-2 py-1 border" style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)', borderColor: 'var(--border-neutral-l1)' }} />
            <Button size="sm" onClick={applyFilters} data-testid="track-record-apply">查询</Button>
            <Button size="sm" variant="ghost" onClick={resetFilters} data-testid="track-record-reset">重置</Button>
          </div>

          {rows.length === 0 ? (
            <div className="py-16 text-center" data-testid="track-record-empty">
              <p className="text-sm" style={{ color: 'var(--text-tertiary)' }}>暂无观点记录。完成深度分析产生交易建议后，判定结果会出现在这里。</p>
            </div>
          ) : (
            <>
              {/* prediction-log = 观点日志表格容器测试钩子（add-prediction-pool-integrity
                  Task 8，纯属性不改布局）；空态时本元素不渲染（走 track-record-empty） */}
              <div className="rounded-xl overflow-hidden border" data-testid="prediction-log" style={{ borderColor: 'var(--border-neutral-l1)' }}>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs" style={{ color: 'var(--text-tertiary)' }}>
                      {COLUMNS.map(c => (
                        <th key={c.key} className={`px-4 py-2 font-normal ${c.numeric ? 'text-right' : ''}`}>
                          {c.sortable === false ? (
                            c.label
                          ) : (
                            <button
                              type="button"
                              data-testid={`sort-${c.key}`}
                              onClick={() => onSort(c.key)}
                              className="inline-flex items-center gap-0.5 hover:opacity-80"
                              style={{ color: 'var(--text-tertiary)' }}
                            >
                              {c.label}{arrow(c.key)}
                            </button>
                          )}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {displayRows.map(d => {
                      if (d.kind === 'single') return renderPredictionRow(d.row)
                      const expanded = expandedDups.has(d.key)
                      const first = d.rows[0]
                      return (
                        <Fragment key={d.key}>
                          {/* testid 全局替换 | → -：key 现含两个 |，字符串单次 replace 只换
                              第一个，会残留 `日期|pid` 破坏 testid 三段格式 */}
                          <tr
                            data-testid={`dup-group-${d.key.replace(/\|/g, '-')}`}
                            className="border-t cursor-pointer hover:opacity-80"
                            style={{ borderColor: 'var(--border-neutral-l1)' }}
                            onClick={() =>
                              setExpandedDups(prev => {
                                const next = new Set(prev)
                                if (next.has(d.key)) next.delete(d.key)
                                else next.add(d.key)
                                return next
                              })
                            }
                          >
                            <td className="px-4 py-3" style={{ color: 'var(--text-secondary)' }}>{first.created_at.slice(0, 10)}</td>
                            <td className="px-4 py-3">
                              <div className="font-medium" style={{ color: 'var(--text-default)' }}>{first.symbol_name ?? first.symbol}</div>
                              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>{first.symbol}</div>
                            </td>
                            <td className="px-4 py-3" style={{ color: 'var(--text-secondary)' }}>—</td>
                            <td className="px-4 py-3"><span className={STATUS_CLS.duplicate_of_day}>同日重复 ×{d.rows.length}</span></td>
                            <td className="px-4 py-3" style={{ color: 'var(--text-secondary)' }}>T+{first.horizon_days}</td>
                            <td className="px-4 py-3 text-right">—</td>
                            <td className="px-4 py-3 text-right">—</td>
                            <td className="px-4 py-3 text-right">—</td>
                            <td className="px-4 py-3 text-right">—</td>
                            <td className="px-4 py-3 text-right">—</td>
                          </tr>
                          {expanded && d.rows.map(r => renderPredictionRow(r))}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <div className="flex items-center justify-end gap-3 mt-3 text-xs" data-testid="track-record-pagination">
                <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(p => p - 1)} data-testid="track-record-prev">上一页</Button>
                <span style={{ color: 'var(--text-tertiary)' }}>第 {page} / {pageCount} 页 · 共 {total} 条</span>
                <Button size="sm" variant="ghost" disabled={page >= pageCount} onClick={() => setPage(p => p + 1)} data-testid="track-record-next">下一页</Button>
              </div>
            </>
          )}
        </>
      )}

      {/* 风险提示：常驻，不可关闭 */}
      <div data-testid="track-record-disclaimer" className="mt-6 rounded-lg px-4 py-3 text-xs" style={{ background: 'rgba(250, 204, 21, 0.12)', color: 'var(--text-secondary)' }}>
        {disclaimer}，投资需谨慎
      </div>
    </div>
  )
}
