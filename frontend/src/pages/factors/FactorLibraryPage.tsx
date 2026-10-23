import { useCallback, useEffect, useMemo, useState } from 'react'
import { AiBadge } from '../../components/AiBadge'
import { PageShellB, type ShellTableColumn } from '../../components/shells/PageShellB'
import { StatusBadge } from '../../components/StatusBadge'
import { Button } from '../../components/ui/button'
import type { StatusKey } from '../../design/tokens'
import { formatSessionTime } from '../../lib/format'
import type { FactorDetail, FactorLastEvent, FactorListResponse, FactorRow } from '../../types'
import { IcSeriesChart } from './IcSeriesChart'

/**
 * 因子库页（delta add-factor-library-page T2.1；spec frontend「因子库页」）。
 *
 * 列表页形态（设计系统 PageShellB：筛选栏 + 数据表 + 行展开）：
 * - 取数 ``GET /api/factors``（只读；空库 → 空数组），筛选栏按 状态（全部/active/
 *   retired）+ 来源（全部/builtin/llm）双维过滤（十位级数据，客户端过滤足够）；
 * - 状态列复用 StatusBadge（active→active、retired→retired；candidate 无对应五态
 *   枚举，原值如实展示——生产里 candidate 只在准入瞬间存在）；
 * - `llm` 来源（含生产默认 `proposal`，见 validate.admit_candidates）携带 AiBadge
 *   ——LLM 提案产物标识；builtin 为确定性注册，不带角标；
 * - ICIR 为近 60 个交易日口径（后端 recent_ic_stats）；无 IC 记录（ic_points=0）
 *   显示「—」，MUST NOT 把口径值 0.0 冒充为真实读数；
 * - 衰减标记：事件历史出现过 degraded（衰减监控降级史，退役后标记仍在）→
 *   StatusBadge degraded；否则「—」；
 * - 行展开懒取 ``GET /api/factors/{id}``：完整表达式 + IC 序列 + 事件历史
 *   （准入/降级/退役及时间）+ **风格暴露披露**（add-factor-style-exposure：与 builtin
 *   风格代理的 IC 相关；仅披露不裁决——风格是合法收益源，页面须同时展示读数与
 *   「非独立 alpha」标注；面板不可得 → 如实「数据不足」）；
 * - 空态如实：库空是「等待 builtin 注册或提案过闸」的常态，MUST NOT 以加载中或
 *   错误态冒充；筛掉全部行是另一回事（无匹配文案，不冒充库空）。
 *
 * 只读页：不提供任何写入口（因子入库/状态变更仍只经五道闸与衰减监控）。
 */

type StatusFilter = 'all' | 'active' | 'retired'
type SourceFilter = 'all' | 'builtin' | 'llm'

/** 库内状态 → StatusBadge 五态枚举（label 用库内原值：active/retired/degraded） */
const STATUS_BADGE: Record<string, { key: StatusKey; label: string }> = {
  active: { key: 'active', label: 'active' },
  degraded: { key: 'degraded', label: 'degraded' },
  retired: { key: 'retired', label: 'retired' },
}

/** LLM 提案来源：显式 'llm' + 生产默认 'proposal'（validate.admit_candidates），均带 AiBadge */
const LLM_SOURCES = new Set(['llm', 'proposal'])

/** 闸门/状态变更结论（detail.reason）→ 展示短语；未知原因码原值如实（不臆造映射） */
const GATE_REASON_LABELS: Record<string, string> = {
  ADMITTED: '准入通过',
  BUCKET_QUOTA: '桶内保种淘汰',
  DECAY: '衰减降级',
}

const STATUS_FILTERS: ReadonlyArray<{ value: StatusFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'active', label: 'active' },
  { value: 'retired', label: 'retired' },
]

const SOURCE_FILTERS: ReadonlyArray<{ value: SourceFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'builtin', label: 'builtin' },
  { value: 'llm', label: 'llm' },
]

/** 表格行：PageShellB 要求 ``id: string``（数值 id 转字符串），factorId 保留数值键 */
interface FactorTableRow extends Omit<FactorRow, 'id'> {
  id: string
  factorId: number
}

function isLlmSource(source: string): boolean {
  return LLM_SOURCES.has(source)
}

function gateConclusion(event: FactorLastEvent | null): string {
  if (!event || !event.reason) return '—'
  return GATE_REASON_LABELS[event.reason] ?? event.reason
}

function eventDescription(event: FactorDetail['events'][number]): string {
  if (event.event === 'created') return `创建 · ${event.to_status ?? '—'}`
  return `状态变更 ${event.from_status ?? '—'} → ${event.to_status ?? '—'}`
}

function matchesStatus(row: FactorTableRow, filter: StatusFilter): boolean {
  return filter === 'all' || row.status === filter
}

function matchesSource(row: FactorTableRow, filter: SourceFilter): boolean {
  if (filter === 'all') return true
  return filter === 'llm' ? isLlmSource(row.source) : row.source === 'builtin'
}

const COLUMNS: readonly ShellTableColumn<FactorTableRow>[] = [
  {
    key: 'name',
    header: '名称',
    render: row => (
      <span data-testid={`factor-name-${row.id}`} className="text-foreground">
        {row.name ?? `因子 ${row.id}`}
      </span>
    ),
  },
  {
    key: 'expression',
    header: '表达式',
    // 截断展示（title 悬停看全量）；完整表达式由行展开给出
    render: row => (
      <span
        data-testid={`factor-expression-${row.id}`}
        title={row.expression}
        className="block max-w-[240px] truncate font-mono text-[11px]"
      >
        {row.expression}
      </span>
    ),
  },
  {
    key: 'status',
    header: '状态',
    render: row => {
      const badge = STATUS_BADGE[row.status]
      return (
        <span data-testid={`factor-status-${row.id}`}>
          {badge ? <StatusBadge status={badge.key} label={badge.label} /> : row.status}
        </span>
      )
    },
  },
  {
    key: 'source',
    header: '来源',
    render: row => (
      <span data-testid={`factor-source-${row.id}`} className="inline-flex items-center gap-1.5">
        {row.source}
        {isLlmSource(row.source) && <AiBadge />}
      </span>
    ),
  },
  {
    key: 'icir',
    header: 'ICIR',
    align: 'right',
    tabular: true,
    render: row => (
      // ic_points=0 → 「—」：库内无 IC 记录，不把口径值 0.0 冒充为读数
      <span data-testid={`factor-icir-${row.id}`}>
        {row.ic_points > 0 ? row.icir_60d.toFixed(3) : '—'}
        {row.ic_points > 0 && (
          <span
            data-testid={`factor-icir-samples-${row.id}`}
            className="ml-1 text-[10px]"
            style={{ color: 'var(--text-tertiary)' }}
          >
            样本 {row.ic_points}
          </span>
        )}
      </span>
    ),
  },
  {
    key: 'gate',
    header: '最近闸门结论',
    render: row => <span data-testid={`factor-gate-${row.id}`}>{gateConclusion(row.last_event)}</span>,
  },
  {
    key: 'decay',
    header: '衰减标记',
    render: row => (
      <span data-testid={`factor-decay-${row.id}`}>
        {row.decayed ? <StatusBadge status="degraded" label="衰减降级" /> : '—'}
      </span>
    ),
  },
]

/** 行展开详情：懒取 GET /api/factors/{id}（完整表达式 / IC 序列 / 事件历史）。 */
/** builtin 风格代理 → 展示短语（未知名原值如实，不臆造映射） */
const STYLE_LABELS: Record<string, string> = {
  momentum_20: '动量',
  reversal_5: '反转',
  turnover_proxy: '量比（换手代理）',
  volatility_20: '波动率',
  size_proxy: '规模代理（成交额）',
}

function FactorStyleExposureBlock({ factorId, exposure }: {
  factorId: number
  exposure: FactorDetail['style_exposure']
}) {
  return (
    <div data-testid={`factor-detail-style-${factorId}`}>
      <div className="mb-1 font-medium text-foreground">
        风格暴露（与 builtin 风格代理的 IC 相关）
      </div>
      {!exposure || exposure.dominant === null || exposure.dominant_correlation === null ? (
        <div className="text-muted-foreground" data-testid={`factor-style-unavailable-${factorId}`}>
          数据不足，无法计算（需与风格代理的共同 IC ≥ 30 日）
        </div>
      ) : (
        <>
          <div className="text-muted-foreground">
            主导：
            <span className="text-foreground">
              {STYLE_LABELS[exposure.dominant] ?? exposure.dominant}
            </span>{' '}
            ρ={exposure.dominant_correlation.toFixed(2)}（{exposure.n_days} 日）
            {exposure.flagged && (
              <span
                className="ml-1"
                style={{ color: 'var(--warning, #b45309)' }}
                data-testid={`factor-style-flag-${factorId}`}
              >
                · 与标准风格高度重合
              </span>
            )}
          </div>
          <div className="tabular-nums text-muted-foreground">
            {Object.entries(exposure.correlations).map(([name, rho]) => (
              <span key={name} className="mr-3 inline-block">
                {STYLE_LABELS[name] ?? name} {rho === null ? '—' : rho.toFixed(2)}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function FactorDetailPanel({ factorId }: { factorId: number }) {
  // null = 加载中；error = 取数失败（与「无 IC 记录」的空序列严格区分）
  const [detail, setDetail] = useState<FactorDetail | null>(null)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    setError(false)
    setDetail(null)
    try {
      const resp = await fetch(`/api/factors/${factorId}`)
      if (!resp.ok) throw new Error(String(resp.status))
      setDetail((await resp.json()) as FactorDetail)
    } catch {
      setError(true)
    }
  }, [factorId])

  useEffect(() => {
    void load()
  }, [load])

  if (error) {
    return (
      <div data-testid={`factor-detail-error-${factorId}`} className="flex items-center gap-2 text-sm text-foreground">
        详情加载失败
        <Button variant="ghost" size="sm" data-testid={`factor-detail-retry-${factorId}`} onClick={() => { void load() }}>
          重试
        </Button>
      </div>
    )
  }
  if (detail === null) {
    return (
      <div data-testid={`factor-detail-loading-${factorId}`} className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
        详情加载中…
      </div>
    )
  }

  return (
    <div data-testid={`factor-detail-${factorId}`} className="space-y-3 text-xs">
      <div data-testid={`factor-detail-expression-${factorId}`}>
        <div className="mb-1 font-medium text-foreground">完整表达式</div>
        <div className="break-all font-mono text-[11px] text-foreground">{detail.expression}</div>
        {detail.ast_json && detail.ast_json !== detail.expression && (
          <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">规范式：{detail.ast_json}</div>
        )}
      </div>
      <div data-testid={`factor-detail-ic-${factorId}`}>
        <div className="mb-1 font-medium text-foreground">
          IC 序列（近 {detail.ic_series.length} 个交易日，升序）
        </div>
        <IcSeriesChart points={detail.ic_series} />
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1" style={{ color: 'var(--text-tertiary)' }}>
          <span data-testid={`factor-ic-samples-${factorId}`}>
            样本 {detail.ic_points}/{detail.ic_window}
          </span>
          <span data-testid={`factor-ic-threshold-${factorId}`}>
            打分门槛 {detail.ic_points}/{detail.min_samples_for_scoring}
          </span>
          {detail.expected_factor_mode_date ? (
            <span data-testid={`factor-ic-expected-${factorId}`}>
              预计 {detail.expected_factor_mode_date} 恢复因子打分（按工作日推算、不含节假日）
            </span>
          ) : (
            <span data-testid={`factor-ic-expected-${factorId}`}>
              {detail.ic_points >= detail.min_samples_for_scoring ? '已达标' : '暂无 IC 记录'}
            </span>
          )}
        </div>
        {detail.ic_points < detail.min_samples_for_scoring && (
          <div
            data-testid={`factor-ic-insufficient-${factorId}`}
            className="mt-1"
            style={{ color: 'var(--status-warning-default)' }}
          >
            样本不足（{detail.ic_points}/{detail.min_samples_for_scoring}），暂不参与 L2 打分
          </div>
        )}
      </div>
      <FactorStyleExposureBlock factorId={factorId} exposure={detail.style_exposure} />
      <div data-testid={`factor-detail-events-${factorId}`}>
        <div className="mb-1 font-medium text-foreground">事件历史</div>
        {detail.events.length === 0 ? (
          <div className="text-muted-foreground">暂无事件</div>
        ) : (
          detail.events.map(event => (
            <div key={event.id} className="text-muted-foreground">
              <span className="text-foreground">{eventDescription(event)}</span>
              {event.reason && <span> · {GATE_REASON_LABELS[event.reason] ?? event.reason}</span>}
              <span> · {formatSessionTime(event.created_at)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export function FactorLibraryPage({ onBack }: { onBack: () => void }) {
  // null = 加载中；error = 取数失败（与「库空」空态严格区分）
  const [rows, setRows] = useState<FactorRow[] | null>(null)
  const [error, setError] = useState(false)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all')

  const load = useCallback(async () => {
    setError(false)
    setRows(null)
    try {
      const resp = await fetch('/api/factors')
      if (!resp.ok) throw new Error(String(resp.status))
      const body = (await resp.json()) as FactorListResponse
      setRows(body.factors ?? [])
    } catch {
      setError(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const tableRows = useMemo<FactorTableRow[]>(
    () => (rows ?? []).map(row => ({ ...row, id: String(row.id), factorId: row.id })),
    [rows]
  )
  const filtered = useMemo(
    () =>
      tableRows.filter(row => matchesStatus(row, statusFilter) && matchesSource(row, sourceFilter)),
    [tableRows, statusFilter, sourceFilter]
  )

  return (
    <div data-testid="factors-page" className="flex h-screen flex-col">
      {/* 标题栏（固定）：返回入口 + 说明；只读页无写操作入口 */}
      <div className="shrink-0 border-b border-border px-6 pt-5 pb-4">
        <div className="flex items-center gap-2">
          <Button
            onClick={onBack}
            data-testid="factors-back"
            variant="ghost"
            size="icon"
            aria-label="返回对话"
            className="h-7 w-7"
          >
            <i className="fas fa-arrow-left text-sm"></i>
          </Button>
          <i className="fas fa-flask text-sm" style={{ color: 'var(--primary)' }}></i>
          <h1 className="text-base font-semibold text-foreground">因子库</h1>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          因子资产目录（只读）：五道闸准入结果、近 60 日 ICIR 与衰减降级史；入库与状态变更仍只经准入管线与衰减监控。
        </p>
      </div>

      <div className="min-h-0 flex-1">
        {error ? (
          <div data-testid="factors-error" className="flex items-center gap-2 px-6 py-8 text-sm text-foreground">
            因子库加载失败
            <Button variant="ghost" size="sm" data-testid="factors-retry" onClick={() => { void load() }}>
              重试
            </Button>
          </div>
        ) : (
          <PageShellB<FactorTableRow>
            columns={COLUMNS}
            rows={filtered}
            loading={rows === null}
            // 空态如实：库空是「等待 builtin 注册 / 提案过闸」的常态；筛空另作文案
            emptyMessage={
              tableRows.length > 0 ? (
                <span data-testid="factors-filter-empty">
                  当前筛选无匹配因子（库内共 {tableRows.length} 条）
                </span>
              ) : (
                <span data-testid="factors-empty">因子库为空——等待 builtin 注册或提案通过五道闸</span>
              )
            }
            emptyIcon="fa-flask"
            // 只读页不提供主操作（统一空态的三要素降级为只读说明，见 ShellParts 契约）
            renderExpanded={row => <FactorDetailPanel factorId={row.factorId} />}
            expandLabel="详情"
            filterBar={
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <div className="flex items-center gap-1.5" role="group" aria-label="状态筛选">
                  <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                    状态
                  </span>
                  {STATUS_FILTERS.map(option => (
                    <Button
                      key={option.value}
                      data-testid={`factors-filter-status-${option.value}`}
                      aria-pressed={statusFilter === option.value}
                      variant={statusFilter === option.value ? 'secondary' : 'ghost'}
                      size="sm"
                      onClick={() => setStatusFilter(option.value)}
                      className="motion-reduce:transition-none"
                    >
                      {option.label}
                    </Button>
                  ))}
                </div>
                <div className="flex items-center gap-1.5" role="group" aria-label="来源筛选">
                  <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                    来源
                  </span>
                  {SOURCE_FILTERS.map(option => (
                    <Button
                      key={option.value}
                      data-testid={`factors-filter-source-${option.value}`}
                      aria-pressed={sourceFilter === option.value}
                      variant={sourceFilter === option.value ? 'secondary' : 'ghost'}
                      size="sm"
                      onClick={() => setSourceFilter(option.value)}
                      className="motion-reduce:transition-none"
                    >
                      {option.label}
                    </Button>
                  ))}
                </div>
                {rows !== null && (
                  <span data-testid="factors-count" className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                    共 {filtered.length} 条
                  </span>
                )}
              </div>
            }
          />
        )}
      </div>
    </div>
  )
}
