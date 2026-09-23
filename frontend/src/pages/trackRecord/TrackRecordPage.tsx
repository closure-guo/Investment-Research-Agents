import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import ReactECharts from 'echarts-for-react'
import type { EquityCurvePoint, PredictionRecord, PredictionsResponse, SegmentDimension, TrackRecordOverview } from '../../types'
import { Button } from '../../components/ui/button'
import { navigate } from '../../route'
import { cssVar } from '../../Charts'
import { loadTrackPrefs, type TrackTimeSpan } from '../../lib/trackPrefs'
import { PREDICTION_STATUS_CLS as STATUS_CLS, PREDICTION_STATUS_LABEL as STATUS_LABEL } from './predictionStatus'

const DIRECTION_LABEL: Record<string, string> = {
  long: '看多',
  short: '看空',
  neutral: '中性',
}

// 可排序列（add-track-record-sort-filter）：与后端 _SORT_WHITELIST 对齐
const COLUMNS: Array<{ key: string; label: string; numeric?: boolean }> = [
  { key: 'created_at', label: '建立日期' },
  { key: 'symbol', label: '标的' },
  { key: 'direction', label: '方向' },
  { key: 'status', label: '状态' },
  { key: 'entry_price', label: '入场价', numeric: true },
  { key: 'exit_price', label: '结算价', numeric: true },
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
  const [version, setVersion] = useState<number | null>(null)
  const [error, setError] = useState(false)
  const [sortBy, setSortBy] = useState<string>('created_at')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [keywordInput, setKeywordInput] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [applied, setApplied] = useState({ keyword: '', dateFrom: '', dateTo: '' })
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)

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
  }, [])

  useEffect(() => { void load(version) }, [load, version])

  // 观点日志按服务端排序/过滤/分页重新拉取（add-track-record-sort-filter）
  const predictionsUrl = useCallback(() => {
    const p = new URLSearchParams()
    if (sortBy !== 'created_at') p.set('sort_by', sortBy)
    if (sortDir !== 'desc') p.set('sort_dir', sortDir)
    if (applied.keyword) p.set('keyword', applied.keyword)
    if (applied.dateFrom) p.set('date_from', applied.dateFrom)
    if (applied.dateTo) p.set('date_to', applied.dateTo)
    if (page > 1) p.set('page', String(page))
    p.set('page_size', String(PAGE_SIZE))
    const q = p.toString()
    return `/api/v1/track-record/predictions${q ? `?${q}` : ''}`
  }, [sortBy, sortDir, applied, page])

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

  const chartOption = {
    color: [cssVar('--chart-sky', '#228EBF'), cssVar('--chart-amber', '#CBB54C')],
    tooltip: { trigger: 'axis' as const, valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(4) : String(v)) },
    grid: { left: 48, right: 16, top: 24, bottom: 28 },
    xAxis: { type: 'category' as const, data: (curve ?? []).map(p => p.date), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value' as const, axisLabel: { fontSize: 10 } },
    series: [
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
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6" data-testid="track-record-summary">
            <div className="rounded-xl p-4" style={{ background: 'var(--bg-overlay-l1)' }}>
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>观点总数</div>
              <div className="text-2xl font-semibold" style={{ color: 'var(--text-default)' }}>{overview.total}</div>
            </div>
            <div className="rounded-xl p-4" style={{ background: 'var(--bg-overlay-l1)' }}>
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>胜率（已判定）</div>
              <div className="text-2xl font-semibold" style={{ color: 'var(--text-default)' }}>
                {showWinRate ? `${(overview.win_rate! * 100).toFixed(1)}%` : '—'}
              </div>
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

          {/* 样本积累提示 */}
          {overview.insufficient_sample && (
            <div className="mb-4 rounded-lg px-4 py-3 text-xs" style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-secondary)' }} data-testid="track-record-insufficient">
              样本积累中（已判定 {overview.settled} 条，满 10 条解锁胜率）
            </div>
          )}

          {/* 组合风险指标（add-track-record-stage-b：P4 收益与风险成对） */}
          <div data-testid="track-record-risk" className="rounded-xl p-4 mb-6" style={{ background: 'var(--bg-overlay-l1)' }}>
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>组合风险指标 <span className="ml-1 text-[10px]">年化/波动/夏普/最大回撤/风险分</span></div>
              {portfolio?.as_of && (
                <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>快照截至 {portfolio.as_of}</div>
              )}
            </div>
            {portfolio && portfolio.available ? (
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
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
              </div>
            ) : (
              <div className="text-xs py-2" style={{ color: 'var(--text-tertiary)' }} data-testid="track-record-risk-empty">
                暂无净值快照——每个交易日收盘后自动生成组合净值与风险指标
              </div>
            )}
          </div>

          {/* 切片指标（stage-c：持有期/行业/市值/市场环境，n<10 标样本不足） */}
          {segments !== null && segments.length > 0 && (
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
          )}

          {/* 净值曲线（stage-b：agent vs 沪深300，起点归一 1.0；数据缺口断点不插值） */}
          {showCurve && (
            <div className="rounded-xl p-4 mb-6" style={{ background: 'var(--bg-overlay-l1)' }} data-testid="track-record-curve">
              <div className="text-xs mb-2" style={{ color: 'var(--text-tertiary)' }}>
                {showBenchmark ? '组合净值 vs 沪深300' : '组合净值'}（起点归一 1.0）
              </div>
              <ReactECharts option={chartOption} style={{ height: 240 }} notMerge />
            </div>
          )}

          {/* 观点日志 */}
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
              <div className="rounded-xl overflow-hidden border" style={{ borderColor: 'var(--border-neutral-l1)' }}>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs" style={{ color: 'var(--text-tertiary)' }}>
                      {COLUMNS.map(c => (
                        <th key={c.key} className={`px-4 py-2 font-normal ${c.numeric ? 'text-right' : ''}`}>
                          <button
                            type="button"
                            data-testid={`sort-${c.key}`}
                            onClick={() => onSort(c.key)}
                            className="inline-flex items-center gap-0.5 hover:opacity-80"
                            style={{ color: 'var(--text-tertiary)' }}
                          >
                            {c.label}{arrow(c.key)}
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(r => (
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
                        <td className="px-4 py-3 text-right">{fmt(r.entry_price)}</td>
                        <td className="px-4 py-3 text-right">{fmt(r.exit_price)}</td>
                        <td className="px-4 py-3 text-right"><Delta value={r.raw_return} /></td>
                        <td className="px-4 py-3 text-right"><Delta value={r.excess_return} /></td>
                      </tr>
                    ))}
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
