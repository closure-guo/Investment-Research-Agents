// polish-dark-mode-shortcuts Task 2.1/2.2：命令面板（Cmd/Ctrl+K）。
// 会话标题搜索跳转 + 快捷动作（新建会话/下载管理/切换主题）+ 底部快捷键清单。
// 轻量自建（Dialog 复用 + 输入过滤），不引入 cmdk 依赖；语义对齐 shadcn Command。
import { useEffect, useMemo, useRef, useState } from 'react'
import type { SessionMeta } from './types'
import { HOTKEY_LIST } from './hooks/useHotkeys'
import type { ThemeChoice } from './theme'
import { formatSessionTime } from './lib/format'

export interface CommandPaletteProps {
  open: boolean
  onClose: () => void
  sessions: SessionMeta[]
  onSelectSession: (id: string) => void
  onNewSession: () => void
  onOpenDownloads: () => void
  onOpenMemory: () => void
  /** 因子库页入口（delta add-factor-library-page：与侧边栏折叠图标同源 navigate） */
  onOpenFactors: () => void
  /** 因子研发监控页入口（delta add-factor-research-monitor：与侧边栏折叠图标同源 navigate） */
  onOpenFactorResearch: () => void
  /** 执行台账页入口（delta add-broker-console：与侧边栏折叠图标同源 navigate） */
  onOpenBroker: () => void
  /** 日批运维页入口（delta add-daily-ops-page：与侧边栏折叠图标同源 navigate） */
  onOpenOps: () => void
  /** 研报投喂页入口（add-research-report-ingest：与侧边栏折叠图标同源 navigate） */
  onOpenResearch: () => void
  onCycleTheme: () => void
  /** 当前主题（面板内展示动作副作用说明） */
  themeChoice: ThemeChoice
}

export function CommandPalette({ open, onClose, sessions, onSelectSession, onNewSession, onOpenDownloads, onOpenMemory, onOpenFactors, onOpenFactorResearch, onOpenBroker, onOpenOps, onOpenResearch, onCycleTheme, themeChoice }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // 打开时清空搜索并聚焦；Esc 关闭
  useEffect(() => {
    if (open) {
      setQuery('')
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const actions = useMemo(() => ([
    { id: 'action-new', icon: 'fa-plus', label: '新建会话', hint: 'Ctrl/⌘+⇧+N', run: onNewSession },
    { id: 'action-downloads', icon: 'fa-download', label: '打开下载管理', hint: '', run: onOpenDownloads },
    // add-memory-confirmation：记忆确认页入口（与侧边栏折叠图标同源 navigate）
    { id: 'action-memory', icon: 'fa-brain', label: '打开记忆确认', hint: '', run: onOpenMemory },
    // add-factor-library-page：因子库页入口（与侧边栏折叠图标同源 navigate）
    { id: 'action-factors', icon: 'fa-flask', label: '打开因子库', hint: '', run: onOpenFactors },
    // add-factor-research-monitor：因子研发监控页入口（与侧边栏折叠图标同源 navigate）
    { id: 'action-factor-research', icon: 'fa-microscope', label: '打开因子研发监控', hint: '', run: onOpenFactorResearch },
    // add-broker-console：执行台账页入口（与侧边栏折叠图标同源 navigate）
    { id: 'action-broker', icon: 'fa-book-open', label: '打开执行台账', hint: '', run: onOpenBroker },
    // add-daily-ops-page：日批运维页入口（与侧边栏折叠图标同源 navigate）
    { id: 'action-ops', icon: 'fa-list-check', label: '打开日批运维', hint: '', run: onOpenOps },
    // add-research-report-ingest：研报投喂页入口（与侧边栏折叠图标同源 navigate）
    { id: 'action-research', icon: 'fa-file-lines', label: '打开研报投喂', hint: '', run: onOpenResearch },
    { id: 'action-theme', icon: 'fa-moon', label: `切换主题（当前：${themeChoice === 'system' ? '跟随系统' : themeChoice === 'dark' ? '深色' : '浅色'}）`, hint: '', run: onCycleTheme },
  ]), [onNewSession, onOpenDownloads, onOpenMemory, onOpenFactors, onOpenFactorResearch, onOpenBroker, onOpenOps, onOpenResearch, onCycleTheme, themeChoice])

  const q = query.trim().toLowerCase()
  const matchedSessions = q
    ? sessions.filter((s) =>
        (s.display_name || '').toLowerCase().includes(q) ||
        (s.stock_name || '').toLowerCase().includes(q) ||
        (s.stock_code || '').includes(q),
      ).slice(0, 6)
    : []
  const matchedActions = q ? actions.filter((a) => a.label.toLowerCase().includes(q)) : actions

  if (!open) return null

  const runAndClose = (run: () => void) => { onClose(); run() }

  return (
    <div className="fixed inset-0 z-[90]" data-testid="command-palette">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} data-testid="palette-backdrop" />
      <div
        className="absolute left-1/2 top-[15%] -translate-x-1/2 w-[560px] max-w-[92vw] rounded-xl overflow-hidden shadow-2xl"
        style={{ background: 'var(--bg-base-default)', border: '1px solid var(--border-neutral-l1)' }}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索会话，或输入命令…"
          data-testid="palette-input"
          className="w-full px-4 py-3.5 text-sm outline-none border-0 bg-transparent"
          style={{ color: 'var(--text-default)', borderBottom: '1px solid var(--border-neutral-l1)' }}
        />
        <div className="max-h-[320px] overflow-y-auto px-2 py-2" data-testid="palette-results">
          {matchedActions.length > 0 && (
            <div className="mb-1">
              {matchedActions.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  data-testid={a.id}
                  onClick={() => runAndClose(a.run)}
                  className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left text-sm transition-colors hover:bg-muted"
                >
                  <i className={`fas ${a.icon} text-xs`} style={{ color: 'var(--text-brand)' }}></i>
                  <span className="flex-1" style={{ color: 'var(--text-secondary)' }}>{a.label}</span>
                  {a.hint && <span className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)' }}>{a.hint}</span>}
                </button>
              ))}
            </div>
          )}
          {matchedSessions.length > 0 && (
            <div>
              <p className="px-3 py-1 text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>历史会话</p>
              {matchedSessions.map((s) => (
                <button
                  key={s.session_id}
                  type="button"
                  data-testid={`palette-session-${s.session_id}`}
                  onClick={() => runAndClose(() => onSelectSession(s.session_id))}
                  className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left text-sm transition-colors hover:bg-muted"
                >
                  <i className="fas fa-comments text-xs" style={{ color: 'var(--text-tertiary)' }}></i>
                  <span className="flex-1 truncate" style={{ color: 'var(--text-secondary)' }}>{s.display_name}</span>
                  <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>{formatSessionTime(s.created_at)}</span>
                </button>
              ))}
            </div>
          )}
          {q && matchedActions.length === 0 && matchedSessions.length === 0 && (
            <p className="px-3 py-4 text-center text-xs" style={{ color: 'var(--text-tertiary)' }}>无匹配结果</p>
          )}
        </div>
        {/* 底部快捷键清单（Task 2.2） */}
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5"
          style={{ borderTop: '1px solid var(--border-neutral-l1)', background: 'var(--bg-base-secondary)' }}
          data-testid="palette-hotkeys"
        >
          {HOTKEY_LIST.map((h) => (
            <span key={h.combo} className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
              <kbd className="font-mono px-1 py-0.5 rounded" style={{ background: 'var(--bg-overlay-l2)' }}>{h.combo}</kbd>
              <span className="ml-1">{h.description}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
