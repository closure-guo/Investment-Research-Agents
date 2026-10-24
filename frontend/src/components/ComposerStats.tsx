// ComposerStats — 输入框下方计量条（add-composer-stat-pills）
//
// 视觉对齐 DeepSeek Harness（MIT）composer dock 的三枚控件：
//   会话统计「N 轮 M 步」/ Token 用量 / 上下文已用环形百分比。
// 数据来自 GET /api/sessions/{id}/stats；任一控件数据缺失时各自隐藏，
// 不以 0 冒充真值（spec composer-stat-pills「组成与显隐」）。
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export interface SessionStats {
  turns: number
  steps: number
  duration_ms: number
  tokens_prompt: number
  tokens_completion: number
  tokens_total: number
  context_peak_tokens: number
}

type StatId = 'activity' | 'usage' | 'context'

// 面板宽度与视口边距（对齐 DSH：300–440px / 12px margin）
const PANEL_WIDTH = 300
const VIEWPORT_MARGIN = 12
const PANEL_GAP = 8

/** 紧凑计数：1.2万 / 3.4亿（中文语境，避免 K/M 混排）。 */
export function formatCompactNumber(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0'
  if (value >= 1e8) return `${trimZero(value / 1e8)}亿`
  if (value >= 1e4) return `${trimZero(value / 1e4)}万`
  return String(Math.round(value))
}

function trimZero(value: number): string {
  const rounded = Math.round(value * 10) / 10
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}

/** 精确计数：千分位，用于面板明细。 */
export function formatExactNumber(value: number): string {
  return Math.round(value).toLocaleString('zh-CN')
}

/** 紧凑时长：45.2s / 2m42s。 */
export function formatCompactDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0s'
  const seconds = ms / 1000
  if (seconds < 60) return `${Math.round(seconds * 10) / 10}s`
  const whole = Math.round(seconds)
  return `${Math.floor(whole / 60)}m${String(whole % 60).padStart(2, '0')}s`
}

function normalizeStats(raw: unknown): SessionStats | null {
  if (!raw || typeof raw !== 'object') return null
  const data = raw as Record<string, unknown>
  const num = (key: string): number => {
    const value = data[key]
    return typeof value === 'number' && Number.isFinite(value) ? value : 0
  }
  return {
    turns: num('turns'),
    steps: num('steps'),
    duration_ms: num('duration_ms'),
    tokens_prompt: num('tokens_prompt'),
    tokens_completion: num('tokens_completion'),
    tokens_total: num('tokens_total'),
    context_peak_tokens: num('context_peak_tokens'),
  }
}

function useAnchoredPosition(open: boolean, anchorRef: React.RefObject<HTMLElement | null>) {
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null)

  useEffect(() => {
    if (!open || !anchorRef.current) {
      setPos(null)
      return
    }
    const rect = anchorRef.current.getBoundingClientRect()
    const left = Math.min(
      Math.max(VIEWPORT_MARGIN, rect.left),
      Math.max(VIEWPORT_MARGIN, window.innerWidth - PANEL_WIDTH - VIEWPORT_MARGIN),
    )
    setPos({ left, bottom: window.innerHeight - rect.top + PANEL_GAP })
  }, [open, anchorRef])

  return pos
}

function Ring({ percent }: { percent: number }) {
  const radius = 5.5
  const circumference = 2 * Math.PI * radius
  return (
    <svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" className="flex-none">
      <circle cx="7" cy="7" r={radius} fill="none" strokeWidth="2" style={{ stroke: 'var(--border-neutral-l1)' }} />
      <circle
        cx="7"
        cy="7"
        r={radius}
        fill="none"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={`${(circumference * percent) / 100} ${circumference}`}
        transform="rotate(-90 7 7)"
        style={{ stroke: 'var(--text-tertiary)' }}
      />
    </svg>
  )
}

function StatPill({
  id,
  open,
  onToggle,
  icon,
  label,
  title,
  onClose,
  children,
}: {
  id: StatId
  open: boolean
  onToggle: (id: StatId) => void
  icon: React.ReactNode
  label: React.ReactNode
  title: string
  onClose: () => void
  children: React.ReactNode
}) {
  const anchorRef = useRef<HTMLButtonElement>(null)
  const pos = useAnchoredPosition(open, anchorRef)

  return (
    <span data-composer-stat={id} className="inline-flex min-w-0">
      <button
        ref={anchorRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={title}
        onClick={() => onToggle(id)}
        className="inline-flex items-center gap-1.5 rounded-md px-2 py-[1px] text-[12px] leading-5 transition-colors hover:bg-[var(--bg-overlay-l1)]"
        style={{ color: open ? 'var(--text-secondary)' : 'var(--text-tertiary)' }}
      >
        {icon}
        <span className="truncate tabular-nums">{label}</span>
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            data-composer-stat-panel={id}
            role="dialog"
            aria-label={title}
            className="fixed z-[80] rounded-xl border p-4 text-[12px] leading-[18px] shadow-xl"
            style={{
              left: pos.left,
              bottom: pos.bottom,
              width: PANEL_WIDTH,
              background: 'var(--bg-overlay-l2)',
              borderColor: 'var(--border-neutral-l1)',
              color: 'var(--text-secondary)',
            }}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <div className="mb-2 flex items-center justify-between gap-3">
              <span className="inline-flex items-center gap-1.5 font-medium" style={{ color: 'var(--text-default)' }}>
                {icon}
                {title}
              </span>
            </div>
            <div className="mb-2.5 border-t" style={{ borderColor: 'var(--border-neutral-l2, var(--border-neutral-l1))' }} />
            {children}
            <button type="button" className="sr-only" onClick={onClose}>
              关闭
            </button>
          </div>,
          document.body,
        )}
    </span>
  )
}

export function ComposerStats({
  sessionId,
  running = false,
  contextWindow,
}: {
  sessionId: string | null
  running?: boolean
  contextWindow?: number | null
}) {
  const [stats, setStats] = useState<SessionStats | null>(null)
  const [open, setOpen] = useState<StatId | null>(null)

  // 会话切换先清空，避免上一会话的数字短暂挂在新会话上
  useEffect(() => {
    setStats(null)
    setOpen(null)
  }, [sessionId])

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false

    const load = async () => {
      try {
        const resp = await fetch(`/api/sessions/${sessionId}/stats`)
        if (!resp.ok) return
        const data = normalizeStats(await resp.json())
        if (!cancelled) setStats(data)
      } catch {
        // 端点不可用/网络抖动：保持隐藏，不阻断聊天（spec：缺失即隐藏）
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [sessionId, running])

  const close = useCallback(() => {
    if (open) {
      // Escape 关闭时焦点回到触发控件（无障碍契约）
      document
        .querySelector<HTMLElement>(`[data-composer-stat="${open}"] button`)
        ?.focus()
    }
    setOpen(null)
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null
      if (target?.closest('[data-composer-stat], [data-composer-stat-panel]')) return
      setOpen(null)
    }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('pointerdown', onPointerDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('pointerdown', onPointerDown, true)
    }
  }, [open, close])

  if (!stats) return null

  const showActivity = stats.steps > 0
  const showUsage = stats.tokens_total > 0
  const showContext = stats.context_peak_tokens > 0 && !!contextWindow && contextWindow > 0
  if (!showActivity && !showUsage && !showContext) return null

  const percent = showContext
    ? Math.min(100, Math.round((stats.context_peak_tokens / contextWindow!) * 100))
    : 0

  const toggle = (id: StatId) => setOpen((current) => (current === id ? null : id))

  const gaugeIcon = <i className="fas fa-gauge-high text-[12px]" aria-hidden="true" />
  const databaseIcon = <i className="fas fa-database text-[12px]" aria-hidden="true" />

  return (
    <div className="mt-1 flex items-center justify-center gap-1" data-testid="composer-stats">
      {showActivity && (
        <StatPill
          id="activity"
          open={open === 'activity'}
          onToggle={toggle}
          onClose={close}
          icon={gaugeIcon}
          label={`${stats.turns} 轮 ${stats.steps} 步`}
          title="会话统计"
        >
          <dl className="grid grid-cols-[minmax(76px,auto)_minmax(0,1fr)] gap-x-4 gap-y-1.5">
            <dt style={{ color: 'var(--text-tertiary)' }}>轮次</dt>
            <dd className="text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>{stats.turns}</dd>
            <dt style={{ color: 'var(--text-tertiary)' }}>步骤</dt>
            <dd className="text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>{stats.steps}</dd>
            {stats.duration_ms > 0 && (
              <>
                <dt style={{ color: 'var(--text-tertiary)' }}>管线用时</dt>
                <dd className="text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>{formatCompactDuration(stats.duration_ms)}</dd>
              </>
            )}
          </dl>
        </StatPill>
      )}

      {showUsage && (
        <StatPill
          id="usage"
          open={open === 'usage'}
          onToggle={toggle}
          onClose={close}
          icon={databaseIcon}
          label={formatCompactNumber(stats.tokens_total)}
          title="Token 用量"
        >
          <dl className="grid grid-cols-[minmax(76px,auto)_minmax(0,1fr)] gap-x-4 gap-y-1.5">
            <dt style={{ color: 'var(--text-tertiary)' }}>输入</dt>
            <dd className="text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>{formatExactNumber(stats.tokens_prompt)}</dd>
            <dt style={{ color: 'var(--text-tertiary)' }}>输出</dt>
            <dd className="text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>{formatExactNumber(stats.tokens_completion)}</dd>
            <dt style={{ color: 'var(--text-tertiary)' }}>合计</dt>
            <dd className="text-right tabular-nums" style={{ color: 'var(--text-secondary)' }}>{formatExactNumber(stats.tokens_total)}</dd>
          </dl>
        </StatPill>
      )}

      {showContext && (
        <StatPill
          id="context"
          open={open === 'context'}
          onToggle={toggle}
          onClose={close}
          icon={<Ring percent={percent} />}
          label={`${percent}%`}
          title="上下文已用"
        >
          <div className="flex items-baseline gap-1.5">
            <span style={{ color: 'var(--text-tertiary)' }}>上下文已用</span>
            <span className="font-medium" style={{ color: 'var(--text-default)' }}>{percent}%</span>
            <span className="ml-auto tabular-nums font-medium" style={{ color: 'var(--text-default)' }}>
              ~{formatCompactNumber(stats.context_peak_tokens)} / {formatCompactNumber(contextWindow!)}
            </span>
          </div>
          <div
            className="mt-2.5 mb-3 flex h-1 overflow-hidden rounded-full"
            style={{ background: 'var(--bg-overlay-l1)' }}
          >
            <div style={{ width: `${percent}%`, background: 'var(--text-tertiary)', borderRadius: 2 }} />
          </div>
          <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
            最近一次分析的单次请求峰值 prompt tokens；窗口取当前配置的上下文长度。
          </p>
        </StatPill>
      )}
    </div>
  )
}
