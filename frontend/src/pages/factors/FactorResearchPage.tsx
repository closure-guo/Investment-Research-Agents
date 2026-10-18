import { useCallback, useEffect, useRef, useState } from 'react'
import { StatusBadge } from '../../components/StatusBadge'
import { Button } from '../../components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../components/ui/dialog'
import { formatSessionTime } from '../../lib/format'
import type {
  FactorResearchCurrentSession,
  FactorResearchSession,
  FactorResearchStatusResponse,
} from '../../types'

/**
 * 因子研发监控页（delta add-factor-research-monitor T3；spec frontend「因子研发监控页」；
 * delta add-factor-research-control：看护启停开关）。
 *
 * 展示外部因子研发（RD-Agent 持续循环看护）的运行状态 + 看护启停控制：
 * - 取数 ``GET /api/factor-research/status``（端点透传采集器快照 + 派生 stale + control 块）；
 * - 看护状态区：运行中/已停止徽章（StatusBadge 五态）+ pid + 启动时刻 + 已运行时长 +
 *   累计会话/候选实现数；
 * - 控制条（常驻）：启停开关 + 状态文案。开关位置反映快照**实际**状态（MUST NOT 乐观翻转）；
 *   停止经二次确认（终止当前会话）后 ``PUT /api/factor-research/control``；``control.pending``
 *   期间显示「切换中」并禁用重复操作；快照缺失/损坏/陈旧（控制通道无存活证据）→ 开关禁用并
 *   明示理由；请求失败如实提示、位置不翻转；
 * - 当前会话进度区：循环序号 · 当前步骤（中文映射，未知原值如实）· 步骤进度 x/5 ·
 *   本步已用/预计剩余 · 本会话因子列表 · 日志尾（等宽字体）；
 * - 会话历史表：开始时刻 / 时长 / 状态（新→旧）；
 * - 陈旧（端点 stale=true）→ 顶部明示「数据已陈旧」，内容照常展示（陈旧 ≠ 不可用）；
 * - 缺失/损坏（available=false）→ 如实空态二分（「采集器未接入」/「快照损坏」），
 *   MUST NOT 以骨架屏或错误提示冒充；
 * - 轮询自动刷新（默认 10s，≤ 15s 契约；测试可注入间隔）；单轮失败静默重试。
 *
 * 数据面只读；控制面仅看护启停一项（MUST NOT 增设其它控制入口）。
 */

export const FACTOR_RESEARCH_POLL_MS = 10_000

/** RD-Agent 步骤名 → 中文（未知步骤名原值如实，不臆造映射） */
const STEP_LABELS: Record<string, string> = {
  direct_exp_gen: '实验生成',
  coding: '编码实现',
  running: '回测运行',
  feedback: '结果反馈',
  record: '记录入库',
}

function stepLabel(name: string | null): string {
  if (!name) return '—'
  return STEP_LABELS[name] ?? name
}

/** 时长秒 → 人类可读（长会话小时级不塌缩为 480:00） */
function formatDurationS(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return '—'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}小时${m}分`
  if (m > 0) return `${m}分${s}秒`
  return `${s}秒`
}

function progressText(cur: FactorResearchCurrentSession): string {
  const parts: string[] = []
  if (cur.loop_index !== null) parts.push(`循环 ${cur.loop_index + 1}`)
  if (cur.step_name) parts.push(`当前步骤 ${stepLabel(cur.step_name)}`)
  if (cur.step_index !== null && cur.steps_total !== null) {
    parts.push(`第 ${cur.step_index + 1}/${cur.steps_total} 步`)
  }
  return parts.length > 0 ? parts.join(' · ') : '暂无进度标记'
}

function sessionStatusText(s: FactorResearchSession, current: FactorResearchCurrentSession | null): string {
  if (s.ended_at) return '已结束'
  if (current && s.log_file === current.log_file) return current.alive ? '进行中' : '已停止'
  return '未知'
}

export function FactorResearchPage({
  onBack,
  pollIntervalMs = FACTOR_RESEARCH_POLL_MS,
}: {
  onBack: () => void
  /** 轮询间隔（默认 10s；测试注入短间隔） */
  pollIntervalMs?: number
}) {
  // null = 加载中；error = 首屏取数失败（与「未接入/损坏」空态严格区分）
  const [data, setData] = useState<FactorResearchStatusResponse | null>(null)
  const [error, setError] = useState(false)
  // 控制面（delta add-factor-research-control）：确认弹窗 / 提交中 / 失败提示
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [controlError, setControlError] = useState<string | null>(null)
  // 409 control_unavailable 后的通道封禁（快照刷新成功即恢复；spec「409 时开关禁用」）
  const [channelBlocked, setChannelBlocked] = useState(false)
  const timerRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    try {
      const resp = await fetch('/api/factor-research/status')
      if (!resp.ok) throw new Error(String(resp.status))
      setData((await resp.json()) as FactorResearchStatusResponse)
      setError(false)
      // 通道恢复证据：成功刷新即解封并清掉一次性失败提示（如实反映当前态）
      setChannelBlocked(false)
      setControlError(null)
    } catch {
      // 首屏失败 → 错误态（可重试）；轮询失败静默（保留旧数据，下一轮自愈）
      setError(true)
    }
  }, [])

  useEffect(() => {
    void load()
    timerRef.current = window.setInterval(() => {
      void load()
    }, pollIntervalMs)
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [load, pollIntervalMs])

  // 控制提交：只发布尔期望态（零命令面）；失败如实提示、位置不翻转（不乐观改状态）
  const submitControl = useCallback(
    async (desired: boolean) => {
      setSubmitting(true)
      setControlError(null)
      try {
        const resp = await fetch('/api/factor-research/control', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ desired_running: desired }),
        })
        if (!resp.ok) {
          let detail = ''
          try {
            detail = ((await resp.json()) as { detail?: string }).detail ?? ''
          } catch {
            // 非 JSON 响应：按状态码如实提示
          }
          if (resp.status === 409 && detail === 'control_unavailable') {
            setChannelBlocked(true) // 禁用至下一轮刷新给出通道恢复证据
            setControlError('控制通道不可用（快照未更新，请求未受理）')
          } else {
            setControlError(`请求失败（HTTP ${resp.status}）`)
          }
          return
        }
        await load() // 刷新 → control.pending 决定「切换中」
      } catch {
        setControlError('请求失败：网络错误')
      } finally {
        setSubmitting(false)
      }
    },
    [load]
  )

  const supervisor = data?.supervisor ?? null
  const current = data?.current_session ?? null
  const sessions = data?.sessions ?? []
  const totals = data?.totals ?? null
  const stale = data?.stale === true
  const collectedAt = data?.collected_at ? formatSessionTime(data.collected_at) : '—'

  // ── 控制面派生（开关位置 = 快照实态；通道存活证据 = 新鲜快照）──
  const control = data?.control ?? null
  const running = supervisor?.running === true
  const pending = control?.pending === true
  const channelReady = data !== null && data.available === true && !stale
  const switchDisabled = submitting || pending || channelBlocked || !channelReady

  let controlStateText: string
  if (data === null) {
    controlStateText = '—'
  } else if (data.available !== true) {
    controlStateText =
      data.reason === 'corrupt' ? '控制通道未接入（快照损坏）' : '控制通道未接入（采集器未接入）'
  } else if (stale) {
    controlStateText = '控制通道暂不可用（数据已陈旧）'
  } else if (pending) {
    controlStateText = control?.requested_at
      ? `切换中…（${formatSessionTime(control.requested_at)} 发起，等待研发侧受理）`
      : '切换中…（等待研发侧受理）'
  } else {
    controlStateText = running ? '已开启' : '已关闭'
  }

  const onSwitchClick = () => {
    if (switchDisabled) return
    if (running) {
      setConfirmOpen(true) // 停止为破坏性方向（终止当前会话）→ 二次确认
      return
    }
    void submitControl(true)
  }

  // 看护已运行时长：客户端按启动时刻实时推算（每次轮询重算）
  const uptimeS = supervisor?.started_at
    ? Math.max(0, Math.floor((Date.now() - Date.parse(supervisor.started_at)) / 1000))
    : null

  return (
    <div data-testid="factor-research-page" className="flex h-screen flex-col">
      {/* 标题栏（固定）：返回入口 + 说明；只读页无写操作入口 */}
      <div className="shrink-0 border-b border-border px-6 pt-5 pb-4">
        <div className="flex items-center gap-2">
          <Button
            onClick={onBack}
            data-testid="factor-research-back"
            variant="ghost"
            size="icon"
            aria-label="返回对话"
            className="h-7 w-7"
          >
            <i className="fas fa-arrow-left text-sm"></i>
          </Button>
          <i className="fas fa-microscope text-sm" style={{ color: 'var(--primary)' }}></i>
          <h1 className="text-base font-semibold text-foreground">因子研发监控</h1>
          <span className="ml-auto text-[10px]" style={{ color: 'var(--text-tertiary)' }} data-testid="factor-research-collected-at">
            最近采集 {collectedAt}
          </span>
        </div>
      </div>

      {/* 控制条（常驻：开关 + 状态文案 + 失败提示；delta add-factor-research-control） */}
      <div
        data-testid="factor-research-control"
        className="shrink-0 border-b border-border px-6 py-2.5"
      >
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
            研发看护
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={running}
            aria-label="研发看护开关"
            data-testid="factor-research-control-switch"
            disabled={switchDisabled}
            onClick={onSwitchClick}
            className="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              background: running ? 'var(--status-success-default)' : 'var(--bg-overlay-l1)',
            }}
          >
            <span
              aria-hidden="true"
              className="inline-block h-4 w-4 rounded-full bg-white shadow transition-transform"
              style={{ transform: running ? 'translateX(1.125rem)' : 'translateX(0.125rem)' }}
            />
          </button>
          <span
            data-testid="factor-research-control-state"
            className="text-xs"
            style={{ color: 'var(--text-secondary)' }}
          >
            {controlStateText}
          </span>
          {controlError !== null && (
            <span
              data-testid="factor-research-control-error"
              className="text-xs"
              style={{ color: 'var(--status-error-default)' }}
            >
              {controlError}
            </span>
          )}
        </div>
      </div>

      {/* 停止二次确认（指定「终止当前研发会话」语义；启动方向直接生效） */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent data-testid="factor-research-control-confirm-dialog">
          <DialogHeader>
            <DialogTitle>停止研发看护？</DialogTitle>
            <DialogDescription>
              将终止当前研发会话（进行中的实验作废）并停止后续循环；工作区与候选产物保留，重新开启后从既有工作区继续。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="secondary"
              size="sm"
              data-testid="factor-research-control-cancel"
              onClick={() => setConfirmOpen(false)}
            >
              取消
            </Button>
            <Button
              size="sm"
              data-testid="factor-research-control-confirm"
              onClick={() => {
                setConfirmOpen(false)
                void submitControl(false)
              }}
            >
              确认停止
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="flex-1 overflow-y-auto p-6">
        {data === null && !error && (
          <div data-testid="factor-research-loading" className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-24 rounded-xl animate-pulse" style={{ background: 'var(--bg-overlay-l1)' }} />
            ))}
          </div>
        )}

        {data === null && error && (
          <div data-testid="factor-research-error" className="flex items-center gap-2 text-sm text-foreground">
            监控数据加载失败
            <Button variant="ghost" size="sm" data-testid="factor-research-retry" onClick={() => { void load() }}>
              重试
            </Button>
          </div>
        )}

        {data !== null && !data.available && (
          <div
            data-testid={data.reason === 'corrupt' ? 'factor-research-empty-corrupt' : 'factor-research-empty-missing'}
            className="flex flex-col items-center justify-center gap-2 py-20 text-sm"
            style={{ color: 'var(--text-secondary)' }}
          >
            <i
              className={`fas ${data.reason === 'corrupt' ? 'fa-triangle-exclamation' : 'fa-satellite-dish'} text-xl`}
              style={{ color: 'var(--text-tertiary)' }}
            ></i>
            {data.reason === 'corrupt' ? (
              <p data-testid="factor-research-empty-text">快照损坏——采集器状态文件不可解析，请检查 distro 侧采集器</p>
            ) : (
              <p data-testid="factor-research-empty-text">采集器未接入——因子研发看护未运行或采集器未部署</p>
            )}
          </div>
        )}

        {data !== null && data.available && (
          <div className="space-y-4">
            {stale && (
              <div
                data-testid="factor-research-stale-banner"
                className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
                style={{ background: 'var(--status-warning-default)', color: 'white' }}
              >
                <i className="fas fa-exclamation-triangle"></i>
                数据已陈旧（最近采集 {collectedAt}）——采集器可能已停止，以下为最后一次快照
              </div>
            )}

            {/* 看护状态区 */}
            <section
              data-testid="factor-research-supervisor"
              className="rounded-xl border border-border p-4"
              style={{ background: 'var(--bg-base-secondary)' }}
            >
              <div className="flex flex-wrap items-center gap-3">
                <StatusBadge
                  status={supervisor?.running ? 'running' : 'retired'}
                  label={supervisor?.running ? '运行中' : '已停止'}
                  className="text-[11px]"
                />
                <span data-testid="factor-research-supervisor-pid" className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  pid {supervisor?.pid ?? '—'}
                </span>
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  启动 {supervisor?.started_at ? formatSessionTime(supervisor.started_at) : '—'}
                </span>
                <span data-testid="factor-research-supervisor-uptime" className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  已运行 {supervisor?.running ? formatDurationS(uptimeS) : '—'}
                </span>
                <span data-testid="factor-research-totals" className="ml-auto text-xs" style={{ color: 'var(--text-secondary)' }}>
                  累计会话 {totals?.sessions ?? 0} · 候选实现 {totals?.candidates ?? 0}
                </span>
              </div>
            </section>

            {/* 当前会话进度区 */}
            <section
              data-testid="factor-research-current"
              className="rounded-xl border border-border p-4"
              style={{ background: 'var(--bg-base-secondary)' }}
            >
              {current === null ? (
                <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>尚无会话记录</p>
              ) : (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
                    <span className="font-mono" data-testid="factor-research-log-file">{current.log_file}</span>
                    <span>开始 {current.started_at ? formatSessionTime(current.started_at) : '—'}</span>
                    <span data-testid="factor-research-duration">已运行 {formatDurationS(current.duration_s)}</span>
                    <span data-testid="factor-research-alive" style={{ color: current.alive ? 'var(--status-success-default)' : 'var(--text-tertiary)' }}>
                      {current.alive ? '会话进行中' : '会话已停止'}
                    </span>
                  </div>
                  <div data-testid="factor-research-progress" className="text-sm font-medium text-foreground">
                    {progressText(current)}
                  </div>
                  {(current.step_elapsed_s !== null || current.step_eta_s !== null) && (
                    <div data-testid="factor-research-step-timing" className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                      本步已用 {formatDurationS(current.step_elapsed_s)}
                      {current.step_eta_s !== null && <> · 预计剩余 {formatDurationS(current.step_eta_s)}</>}
                    </div>
                  )}
                  <div data-testid="factor-research-factors" className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>本会话因子：</span>
                    {current.factors.length === 0 ? (
                      <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>尚无</span>
                    ) : (
                      current.factors.map((name) => (
                        <span
                          key={name}
                          data-testid="factor-research-factor"
                          className="rounded-full border border-border px-2 py-0.5 font-mono text-[11px] text-foreground"
                        >
                          {name}
                        </span>
                      ))
                    )}
                  </div>
                  <div>
                    <div className="mb-1 text-xs" style={{ color: 'var(--text-tertiary)' }}>日志尾（最近 {current.log_tail.length} 行）</div>
                    <pre
                      data-testid="factor-research-log-tail"
                      className="max-h-56 overflow-y-auto rounded-lg p-3 font-mono text-[11px] leading-relaxed"
                      style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-secondary)' }}
                    >
                      {current.log_tail.join('\n')}
                    </pre>
                  </div>
                </div>
              )}
            </section>

            {/* 会话历史表 */}
            <section
              data-testid="factor-research-sessions"
              className="rounded-xl border border-border p-4"
              style={{ background: 'var(--bg-base-secondary)' }}
            >
              <div className="mb-2 text-xs font-medium text-foreground">会话历史（新→旧）</div>
              {sessions.length === 0 ? (
                <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>暂无会话记录</p>
              ) : (
                <table className="w-full text-xs">
                  <thead>
                    <tr style={{ color: 'var(--text-tertiary)' }}>
                      <th className="py-1 text-left font-normal">日志</th>
                      <th className="py-1 text-left font-normal">开始</th>
                      <th className="py-1 text-left font-normal">时长</th>
                      <th className="py-1 text-left font-normal">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.map((s) => (
                      <tr key={s.log_file} data-testid="factor-research-session-row" className="border-t border-border">
                        <td className="py-1.5 font-mono" style={{ color: 'var(--text-secondary)' }}>{s.log_file}</td>
                        <td className="py-1.5" style={{ color: 'var(--text-secondary)' }}>
                          {s.started_at ? formatSessionTime(s.started_at) : '—'}
                        </td>
                        <td className="py-1.5" style={{ color: 'var(--text-secondary)' }}>{formatDurationS(s.duration_s)}</td>
                        <td className="py-1.5" style={{ color: 'var(--text-secondary)' }}>{sessionStatusText(s, current)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
