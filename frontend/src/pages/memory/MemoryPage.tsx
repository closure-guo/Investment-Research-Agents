import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { AiBadge } from '../../components/AiBadge'
import { PageShellC } from '../../components/shells/PageShellC'
import { StatusBadge } from '../../components/StatusBadge'
import { Button } from '../../components/ui/button'
import type { StatusKey } from '../../design/tokens'
import { formatSessionTime } from '../../lib/format'
import type { MemoryClause, MemoryClauseStatus, MemoryClauseListResponse } from '../../types'

/**
 * 记忆确认页（delta add-memory-confirmation T2.1；spec frontend「记忆确认页」）。
 *
 * 审批队列形态（设计系统 PageShellC：左列表 + 右详情 + 确认/驳回）：
 * - 三态筛选（待确认/已确认/已驳回，默认待确认）走 ``GET /api/memory/clauses?status=``；
 * - 右详情：完整正文（AiBadge 标注 LLM 提炼产物）+ 来源 + 提炼时间 + 结算样本；
 * - 确认/驳回 ``POST /api/memory/clauses/{id}/confirm|reject`` 后重取当前筛选列表
 *   （C 从待确认视图迁出，切到目标态即可见）；
 * - 确认动作旁明示「确认后即刻生效注入风控辩论」（注入门禁为后端既有语义）；
 * - 加载中（骨架屏）/ 取数失败（错误 + 重试）/ 空态（如实说明）三态严格区分：
 *   空库是「月批未接线」的常态，MUST NOT 以加载中或错误态冒充。
 *
 * 不做编辑（设计决策）：条款是「确认或驳回」的二元决策，编辑会让
 * 「LLM 提炼 → 人工把关」的溯源语义模糊。
 */

/** 条款三态 → StatusBadge 五态枚举（设计系统：状态展示必须复用 StatusBadge，禁止自绘） */
const STATUS_BADGE: Record<MemoryClauseStatus, { key: StatusKey; label: string }> = {
  pending: { key: 'degraded', label: '待确认' },
  confirmed: { key: 'active', label: '已确认' },
  rejected: { key: 'retired', label: '已驳回' },
}

const FILTERS: ReadonlyArray<{ value: MemoryClauseStatus; label: string }> = [
  { value: 'pending', label: '待确认' },
  { value: 'confirmed', label: '已确认' },
  { value: 'rejected', label: '已驳回' },
]

const EMPTY_ICONS: Record<MemoryClauseStatus, string> = {
  pending: 'fa-hourglass-half',
  confirmed: 'fa-circle-check',
  rejected: 'fa-ban',
}

// 空态文案如实：待确认空是「等待月批提炼」的常态（月批提炼尚未接线，见 issue #327）
const EMPTY_MESSAGES: Record<MemoryClauseStatus, string> = {
  pending: '暂无待确认条款：等待月批提炼（当前未接线，见 issue #327）',
  confirmed: '暂无已确认条款',
  rejected: '暂无已驳回条款',
}

// 来源友好名（未知来源原样展示，不臆造映射）
const SOURCE_LABELS: Record<string, string> = {
  monthly_batch: '月批提炼',
  llm: 'LLM 提炼',
}

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source
}

/** 结算样本（后端 evidence 为 JSON 文本或空）：对象逐键成行；非 JSON 历史行原样一行。 */
function evidenceLines(evidence: string): string[] {
  try {
    const parsed: unknown = JSON.parse(evidence)
    if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return Object.entries(parsed as Record<string, unknown>).map(([key, value]) => {
        const text = value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value)
        return `${key}: ${text}`
      })
    }
  } catch {
    // 非 JSON（历史行容错）→ 原样展示
  }
  return [evidence]
}

export function MemoryPage({ onBack }: { onBack: () => void }) {
  const [filter, setFilter] = useState<MemoryClauseStatus>('pending')
  // null = 加载中；fetch 失败置 error（错误态与空态严格区分，不互相冒充）
  const [clauses, setClauses] = useState<MemoryClause[] | null>(null)
  const [error, setError] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const load = useCallback(async (status: MemoryClauseStatus) => {
    setError(false)
    setClauses(null)
    try {
      const resp = await fetch(`/api/memory/clauses?status=${status}`)
      if (!resp.ok) throw new Error(String(resp.status))
      const body = (await resp.json()) as MemoryClauseListResponse
      setClauses(body.clauses ?? [])
    } catch {
      setError(true)
    }
  }, [])

  useEffect(() => {
    void load(filter)
  }, [filter, load])

  const switchFilter = (next: MemoryClauseStatus) => {
    setFilter(next)
    // 详情属旧态列表：清空选择，避免跨态残留选中项
    setSelectedId(null)
  }

  const mutate = useCallback(
    async (clause: MemoryClause, action: 'confirm' | 'reject') => {
      try {
        const resp = await fetch(`/api/memory/clauses/${encodeURIComponent(clause.id)}/${action}`, {
          method: 'POST',
        })
        if (!resp.ok) throw new Error(String(resp.status))
      } catch {
        toast.error(action === 'confirm' ? '确认失败' : '驳回失败')
      } finally {
        // 成功重取（条款迁出当前视图）；失败也重取，令本地视图与服务端对齐
        // （404 = 条款已被处理/不存在，重取后幽灵条目消失）
        await load(filter)
      }
    },
    [filter, load]
  )

  return (
    <div data-testid="memory-page" className="flex h-screen flex-col">
      {/* 标题栏（固定）：返回入口 + 三态筛选 */}
      <div className="shrink-0 border-b border-border px-6 pt-5 pb-4">
        <div className="flex items-center gap-2">
          <Button
            onClick={onBack}
            data-testid="memory-back"
            variant="ghost"
            size="icon"
            aria-label="返回对话"
            className="h-7 w-7"
          >
            <i className="fas fa-arrow-left text-sm"></i>
          </Button>
          <i className="fas fa-brain text-sm" style={{ color: 'var(--primary)' }}></i>
          <h1 className="text-base font-semibold text-foreground">记忆确认</h1>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          语义记忆条款由模型从已结算决策提炼，仅供参考；经人工确认后方可注入风控辩论。
        </p>
        <div className="mt-3 flex items-center gap-1.5" role="group" aria-label="条款状态筛选">
          {FILTERS.map(option => (
            <Button
              key={option.value}
              data-testid={`memory-filter-${option.value}`}
              aria-pressed={filter === option.value}
              variant={filter === option.value ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => switchFilter(option.value)}
              className="motion-reduce:transition-none"
            >
              {option.label}
            </Button>
          ))}
          {clauses !== null && !error && (
            <span data-testid="memory-count" className="ml-1 text-xs" style={{ color: 'var(--text-tertiary)' }}>
              共 {clauses.length} 条
            </span>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1">
        {error ? (
          <div data-testid="memory-error" className="flex items-center gap-2 px-6 py-8 text-sm text-foreground">
            条款加载失败
            <Button variant="ghost" size="sm" data-testid="memory-retry" onClick={() => { void load(filter) }}>
              重试
            </Button>
          </div>
        ) : (
          <PageShellC<MemoryClause>
            items={clauses ?? []}
            loading={clauses === null}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onConfirm={item => { void mutate(item, 'confirm') }}
            onReject={item => { void mutate(item, 'reject') }}
            emptyMessage={<span data-testid="memory-empty">{EMPTY_MESSAGES[filter]}</span>}
            emptyIcon={EMPTY_ICONS[filter]}
            renderItem={item => {
              const badge = STATUS_BADGE[item.status]
              return (
                <div className="space-y-1">
                  {/* 列表摘要也带 AiBadge：正文即 LLM 提炼产物 */}
                  <div className="flex items-start gap-1.5">
                    <p className="line-clamp-2 text-sm text-foreground">{item.text}</p>
                    <AiBadge />
                  </div>
                  <div className="flex items-center gap-2 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                    <span data-testid={`memory-status-${item.id}`}>
                      <StatusBadge status={badge.key} label={badge.label} />
                    </span>
                    <span>{sourceLabel(item.source)}</span>
                    <span>{formatSessionTime(item.created_at)}</span>
                  </div>
                </div>
              )
            }}
            renderDetail={item => {
              const badge = STATUS_BADGE[item.status]
              return (
                <div className="space-y-4">
                  <div className="flex items-center gap-2">
                    <span data-testid="memory-detail-status">
                      <StatusBadge status={badge.key} label={badge.label} />
                    </span>
                    <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                      条款 {item.id}
                    </span>
                  </div>
                  <div data-testid="memory-detail-text" className="flex items-start gap-1.5">
                    <p className="text-sm leading-relaxed text-foreground">{item.text}</p>
                    <AiBadge />
                  </div>
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <div data-testid="memory-detail-source">来源：{sourceLabel(item.source)}</div>
                    <div data-testid="memory-detail-created-at">
                      提炼时间：{formatSessionTime(item.created_at)}
                    </div>
                  </div>
                  {item.evidence && (
                    <div data-testid="memory-detail-evidence" className="rounded-lg border border-border px-3 py-2 text-xs">
                      <div className="mb-1 font-medium text-foreground">结算样本</div>
                      {evidenceLines(item.evidence).map((line, index) => (
                        <div key={index} className="tabular-nums text-muted-foreground">
                          {line}
                        </div>
                      ))}
                    </div>
                  )}
                  {/* 确认动作旁明示后果（spec 文案逐字）：按钮区在壳底部，本提示置于详情末尾紧邻操作区 */}
                  <p
                    data-testid="memory-confirm-effect-note"
                    className="text-xs"
                    style={{ color: 'var(--status-warning-default)' }}
                  >
                    确认后即刻生效注入风控辩论
                  </p>
                </div>
              )
            }}
          />
        )}
      </div>
    </div>
  )
}
