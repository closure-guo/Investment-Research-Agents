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
import {
  ACTUAL_STATE_LABEL,
  FACTOR_RESEARCH_POLL_MS,
  controlErrorMessage,
  deriveControlView,
  fetchFactorResearchStatus,
  fetchHarvestChain,
  formatDurationS,
  submitFactorResearchControl,
  submitFactorResearchRestart,
} from '../../lib/factorResearch'
import type {
  FactorResearchCurrentSession,
  FactorResearchSession,
  FactorResearchStatusResponse,
  HarvestChainResponse,
} from '../../types'

/**
 * 因子研发监控页（delta add-factor-research-monitor T3；spec frontend「因子研发监控页」；
 * delta add-factor-research-control：看护启停开关；
 * delta add-factor-research-settings：实态语义 + 重启管线）。
 *
 * 展示外部因子研发（RD-Agent 持续循环看护）的运行状态 + 控制：
 * - 取数 ``GET /api/factor-research/status``（端点透传采集器快照 + 派生 stale + control 块）；
 * - 看护状态区：实际态徽章（运行中/已停止/状态未知）+ pid + 启动时刻 + 已运行时长 +
 *   累计会话/候选实现数；
 * - 控制条（常驻）：启停开关 + 重启按钮 + 状态文案。开关位置反映**验证过的实际状态**
 *   （``actual_state``）；快照失联时显示「状态未知」并披露持久化意图，MUST NOT 用意图冒充
 *   运行中。停止/重启经二次确认后调用端点；``pending`` / ``restart_pending`` 期间显示
 *   切换中/重启中并禁用重复操作；
 * - 当前会话进度区：循环序号 · 当前步骤（中文映射，未知原值如实）· 步骤进度 x/5 ·
 *   本步已用/预计剩余 · 本会话因子列表 · 日志尾（等宽字体）；
 * - 会话历史表：开始时刻 / 时长 / 状态（新→旧）；
 * - 陈旧（端点 stale=true）→ 顶部明示「数据已陈旧」，内容照常展示（陈旧 ≠ 不可用）；
 * - 缺失/损坏（available=false）→ 如实空态二分（「采集器未接入」/「快照损坏」）；
 * - 轮询自动刷新（默认 10s，≤ 15s 契约；测试可注入间隔）；单轮失败静默重试。
 *
 * 数据面只读；控制面仅启停 + 重启两项（MUST NOT 增设其它控制入口）。
 */

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

/** 五道闸名称（收割漏斗用；与后端 gate 编号一致）。 */
const GATE_LABELS: Record<string, string> = {
  '0': '结构重复',
  '1': '覆盖率',
  '2': 'ICIR',
  '3': 'FDR',
  '4': '池冗余',
  '5': '样本外',
}

function pctText(part: number, whole: number): string {
  if (whole <= 0) return '—'
  return `${Math.round((part / whole) * 100)}%`
}

function pct(part: number, whole: number): number {
  if (whole <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((part / whole) * 100)))
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
  // 控制面（delta add-factor-research-control / settings）：确认弹窗 / 提交中 / 失败提示
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [restartConfirmOpen, setRestartConfirmOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [restartSubmitting, setRestartSubmitting] = useState(false)
  const [controlError, setControlError] = useState<string | null>(null)
  // 409 control_unavailable 后的通道封禁（快照刷新成功即恢复；spec「409 时开关禁用」）
  const [channelBlocked, setChannelBlocked] = useState(false)
  // 收割链路（add-factor-harvest-ui）：独立取数，失败只降级本区块
  const [harvest, setHarvest] = useState<HarvestChainResponse | null>(null)
  const [harvestError, setHarvestError] = useState(false)
  const timerRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await fetchFactorResearchStatus())
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

  // 收割链路取数排在主快照之后：保证首屏两路请求顺序稳定（主监控优先）且互不影响
  const loadHarvest = useCallback(async () => {
    try {
      setHarvest(await fetchHarvestChain())
      setHarvestError(false)
    } catch {
      // 轮询失败保留旧数据；首屏失败显示区块级错误
      setHarvestError(true)
    }
  }, [])

  useEffect(() => {
    void loadHarvest()
    const timer = window.setInterval(() => {
      void loadHarvest()
    }, pollIntervalMs)
    return () => window.clearInterval(timer)
  }, [loadHarvest, pollIntervalMs])

  // 控制提交：只发布尔期望态（零命令面）；失败如实提示、位置不翻转（不乐观改状态）
  const submitControl = useCallback(
    async (desired: boolean) => {
      setSubmitting(true)
      setControlError(null)
      try {
        await submitFactorResearchControl(desired)
        await load() // 刷新 → control.pending 决定「切换中」
      } catch (err) {
        const { text, unavailable } = controlErrorMessage(err)
        if (unavailable) setChannelBlocked(true) // 禁用至下一轮刷新给出通道恢复证据
        setControlError(text)
      } finally {
        setSubmitting(false)
      }
    },
    [load]
  )

  // 重启提交（无请求体；代理按 request_id 去重执行）
  const submitRestart = useCallback(async () => {
    setRestartSubmitting(true)
    setControlError(null)
    try {
      await submitFactorResearchRestart()
      await load() // 刷新 → control.restart_pending 决定「重启中」
    } catch (err) {
      const { text, unavailable } = controlErrorMessage(err)
      if (unavailable) setChannelBlocked(true)
      setControlError(text)
    } finally {
      setRestartSubmitting(false)
    }
  }, [load])

  const supervisor = data?.supervisor ?? null
  const launcher = data?.launcher ?? null
  const current = data?.current_session ?? null
  const sessions = data?.sessions ?? []
  const totals = data?.totals ?? null
  const stale = data?.stale === true
  // 僵死披露（delta add-factor-research-stall-detection）：采集正常但会话日志停更。
  // 未上报（旧采集器）→ stalled=false 且 logAgeS=null，只能表现为「不可判定」。
  const stalled = data?.progress?.stalled === true
  const logAgeS = data?.progress?.log_age_seconds ?? null
  const collectedAt = data?.collected_at ? formatSessionTime(data.collected_at) : '—'
  // distro 探活披露（delta add-factor-research-distro-liveness-probe）：
  // 仅「快照陈旧 + 探活可用且新鲜 + distro 执行失败」同时成立才判定 distro 不可用；
  // 未探活（available=false）/ 探活不新鲜（fresh=false）/ 探活成功（ok=true）→ 均不冒充结论。
  const distroDown =
    data?.stale === true &&
    data.distro?.available === true &&
    data.distro?.fresh === true &&
    data.distro?.ok === false
  const distroProbe = distroDown ? (data?.distro ?? null) : null
  const distroName = distroProbe?.distro ?? null
  const distroFailures = distroProbe?.consecutive_failures ?? null
  // 收割台账：后端契约保证数组；防御式兜底避免单次畸形响应拖垮整页
  const recentAdmitted = harvest?.recent_admitted ?? []
  const recentRejected = harvest?.recent_rejected ?? []

  // 控制面派生（单一共享源：开关位置只取验证过的 actual_state，意图仅作离线披露）
  const view = deriveControlView(data, { submitting, restartSubmitting, channelBlocked })
  const {
    actualState,
    running,
    honor,
    snapshotLive,
    switchDisabled,
    restartDisabled,
    stateText: controlStateText,
  } = view

  const recentDurations = (data?.sessions ?? [])
    .filter((s) => s.duration_s !== null)
    .slice(0, 3)
    .map((s) => s.duration_s as number)
  const shortSessions = recentDurations.length === 3 && recentDurations.every((d) => d < 120)

  const onSwitchClick = () => {
    if (switchDisabled) return
    if (running) {
      setConfirmOpen(true) // 停止为破坏性方向（终止当前会话）→ 二次确认
      return
    }
    void submitControl(true)
  }

  const onRestartClick = () => {
    if (restartDisabled) return
    setRestartConfirmOpen(true)
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

      {/* 控制条（常驻：开关 + 重启 + 状态文案 + 失败提示） */}
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
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid="factor-research-restart-button"
            disabled={restartDisabled}
            onClick={onRestartClick}
            className="h-6 px-2 text-xs"
          >
            <i className="fas fa-rotate-right mr-1"></i>
            重启管线
          </Button>
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

      {/* 重启二次确认（终止当前会话并重新拉起；delta add-factor-research-settings） */}
      <Dialog open={restartConfirmOpen} onOpenChange={setRestartConfirmOpen}>
        <DialogContent data-testid="factor-research-restart-confirm-dialog">
          <DialogHeader>
            <DialogTitle>重启研发管线？</DialogTitle>
            <DialogDescription>
              将终止当前研发会话（进行中的实验作废）并重新拉起看护；工作区与候选产物保留，重启后从既有工作区继续。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="secondary"
              size="sm"
              data-testid="factor-research-restart-cancel"
              onClick={() => setRestartConfirmOpen(false)}
            >
              取消
            </Button>
            <Button
              size="sm"
              data-testid="factor-research-restart-confirm"
              onClick={() => {
                setRestartConfirmOpen(false)
                void submitRestart()
              }}
            >
              确认重启
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
                {distroDown ? (
                  <>
                    数据已陈旧（最近采集 {collectedAt}）——研究侧 distro 探活失败（疑似 WSL
                    子系统不可用，采集器并未停止），以下为最后一次快照
                  </>
                ) : (
                  <>数据已陈旧（最近采集 {collectedAt}）——采集器可能已停止，以下为最后一次快照</>
                )}
                {honor === 'launcher' && launcher?.checked_at != null && (
                  <>；启动器正在按节拍尝试恢复（最近检查 {formatSessionTime(launcher.checked_at)}）</>
                )}
              </div>
            )}

            {/* distro 探活失败披露：只陈述事实与人工恢复动作，不提供可点击处置入口（零命令面） */}
            {distroDown && (
              <div
                data-testid="factor-research-distro-probe-banner"
                className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
                style={{ background: 'var(--status-danger-default, #b91c1c)', color: 'white' }}
              >
                <i className="fas fa-plug-circle-xmark"></i>
                研究侧 distro{distroName ? `「${distroName}」` : ''} 探活
                {distroFailures !== null ? `连续 ${distroFailures} 次失败` : '失败'}
                （最近探活 {formatSessionTime(distroProbe?.probed_at)}）——采集器并未停止；需由人执行
                wsl --terminate rdagent，下一次节拍会自动重新拉起
              </div>
            )}

            {stalled && (
              <div
                data-testid="factor-research-stall-banner"
                className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
                style={{ background: 'var(--status-danger-default, #b91c1c)', color: 'white' }}
              >
                <i className="fas fa-exclamation-triangle"></i>
                当前会话无进展 {formatDurationS(logAgeS)}——进程仍存活但日志已停止更新，
                看护不会自动重拉；需经「重启管线」由人发起
              </div>
            )}

            {shortSessions && (
              <div
                data-testid="factor-research-short-sessions"
                className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
                style={{ background: 'var(--status-warning-default)', color: 'white' }}
              >
                <i className="fas fa-exclamation-triangle"></i>
                连续短命会话：最近 3 次均在 {recentDurations.join(' / ')} 秒内结束——疑似凭证或配额问题（研发侧将退避重试）
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
                  status={
                    actualState === 'running'
                      ? 'running'
                      : actualState === 'stopped'
                        ? 'retired'
                        : 'degraded'
                  }
                  label={ACTUAL_STATE_LABEL[actualState]}
                  className="text-[11px]"
                />
                <span data-testid="factor-research-supervisor-pid" className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  pid {snapshotLive ? (supervisor?.pid ?? '—') : '—'}
                </span>
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  启动 {snapshotLive && supervisor?.started_at ? formatSessionTime(supervisor.started_at) : '—'}
                </span>
                <span data-testid="factor-research-supervisor-uptime" className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  已运行 {actualState === 'running' ? formatDurationS(uptimeS) : '—'}
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
                  {stalled ? (
                    <div
                      data-testid="factor-research-progress-stale"
                      className="text-xs"
                      style={{ color: 'var(--text-tertiary)' }}
                    >
                      进度读数来自 {formatDurationS(logAgeS)} 前的日志（会话无进展，读数已定格）
                    </div>
                  ) : (
                    (current.step_elapsed_s !== null || current.step_eta_s !== null) && (
                      <div data-testid="factor-research-step-timing" className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                        本步已用 {formatDurationS(current.step_elapsed_s)}
                        {current.step_eta_s !== null && <> · 预计剩余 {formatDurationS(current.step_eta_s)}</>}
                      </div>
                    )
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

            {/* 收割链路（add-factor-harvest-ui）：实时进度 + 漏斗 + 台账 */}
            <section
              data-testid="factor-research-harvest"
              className="rounded-xl border border-border p-4"
              style={{ background: 'var(--bg-base-secondary)' }}
            >
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs font-medium text-foreground">
                因子收割链路
                {harvest?.job?.schedule && (
                  <span className="font-normal" style={{ color: 'var(--text-tertiary)' }}>
                    每天 {String(harvest.job.schedule.hour).padStart(2, '0')}:{String(harvest.job.schedule.minute).padStart(2, '0')} 自动跑
                  </span>
                )}
                {harvest?.job?.status && (
                  <span
                    data-testid="factor-research-harvest-status"
                    className="rounded-full border border-border px-2 py-0.5 text-[11px] font-normal"
                  >
                    {harvest.job.status === 'running' ? '运行中' : harvest.job.status === 'ok' ? '已完成' : harvest.job.status}
                  </span>
                )}
              </div>

              {harvestError && harvest === null && (
                <div
                  data-testid="factor-research-harvest-error"
                  className="flex items-center gap-2 text-xs"
                  style={{ color: 'var(--status-error-default)' }}
                >
                  收割数据加载失败
                  <Button variant="ghost" size="sm" onClick={() => { void loadHarvest() }}>
                    重试
                  </Button>
                </div>
              )}

              {harvest !== null && !harvest.available && (
                <p data-testid="factor-research-harvest-empty" className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                  收割数据未接入（状态文件缺失或损坏）
                </p>
              )}

              {harvest !== null && harvest.available && (
                <div className="space-y-4">
                  <div data-testid="factor-research-harvest-progress" className="space-y-2">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
                      <span>批次 {harvest.progress?.batches_done ?? 0}/{harvest.progress?.batch_limit ?? 0}</span>
                      <span>已处理 {harvest.progress?.processed ?? 0}</span>
                      <span>剩余 {harvest.progress?.pending ?? 0}</span>
                      <span>
                        本轮入库 {harvest.progress?.admitted_in_run ?? 0} · 拒收 {harvest.progress?.rejected_in_run ?? 0}
                      </span>
                      {harvest.progress?.eta_s != null && <span>预计还需 {formatDurationS(harvest.progress.eta_s)}</span>}
                    </div>
                    <div className="h-2 w-full overflow-hidden rounded-full" style={{ background: 'var(--bg-overlay-l1)' }}>
                      <div
                        data-testid="factor-research-harvest-bar"
                        className="h-full rounded-full transition-all"
                        style={{
                          width: `${pct(harvest.progress?.processed ?? 0, harvest.funnel?.candidates ?? 0)}%`,
                          background: 'var(--status-success-default)',
                        }}
                      />
                    </div>
                  </div>

                  <div data-testid="factor-research-harvest-funnel" className="grid grid-cols-2 gap-2 md:grid-cols-4">
                    {[
                      { key: 'candidates', label: '候选', value: harvest.funnel?.candidates ?? 0 },
                      { key: 'processed', label: '已处理', value: harvest.funnel?.processed ?? 0 },
                      { key: 'gates', label: '到闸', value: harvest.funnel?.reached_gates ?? 0 },
                      { key: 'admitted', label: '入库', value: harvest.funnel?.admitted ?? 0 },
                    ].map((item) => (
                      <div
                        key={item.key}
                        data-testid={`factor-research-harvest-step-${item.key}`}
                        className="rounded-lg border border-border px-3 py-2"
                        style={{ background: 'var(--bg-overlay-l1)' }}
                      >
                        <div className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>{item.label}</div>
                        <div className="text-lg font-semibold text-foreground">{item.value}</div>
                        <div className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
                          {pctText(item.value, harvest.funnel?.candidates ?? 0)}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div
                    data-testid="factor-research-harvest-gates"
                    className="flex flex-wrap items-center gap-2 text-[11px]"
                    style={{ color: 'var(--text-tertiary)' }}
                  >
                    拒收分布：
                    {Object.entries(harvest.funnel?.rejected_by_gate ?? {}).map(([gate, count]) => (
                      <span key={gate} className="rounded-full border border-border px-2 py-0.5">
                        {GATE_LABELS[gate] ?? `闸${gate}`} {count}
                      </span>
                    ))}
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div data-testid="factor-research-harvest-admitted">
                      <div className="mb-1 text-xs font-medium text-foreground">最近入库</div>
                      {recentAdmitted.length === 0 ? (
                        <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>暂无</p>
                      ) : (
                        <ul className="space-y-1">
                          {recentAdmitted.map((item) => (
                            <li key={item.factor_id} className="flex items-center justify-between gap-2 text-[11px]">
                              <code className="truncate font-mono" style={{ color: 'var(--text-secondary)' }} title={item.expression ?? ''}>
                                {item.expression}
                              </code>
                              <span className="shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                                ICIR {item.icir_60d.toFixed(3)}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                    <div data-testid="factor-research-harvest-rejected">
                      <div className="mb-1 text-xs font-medium text-foreground">最近拒收</div>
                      {recentRejected.length === 0 ? (
                        <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>暂无</p>
                      ) : (
                        <ul className="space-y-1">
                          {recentRejected.map((item, index) => (
                            <li
                              key={`${item.expression}-${index}`}
                              className="flex items-center justify-between gap-2 text-[11px]"
                            >
                              <code className="truncate font-mono" style={{ color: 'var(--text-secondary)' }} title={item.expression ?? ''}>
                                {item.expression}
                              </code>
                              <span className="shrink-0" style={{ color: 'var(--text-tertiary)' }}>
                                {item.gate != null ? (GATE_LABELS[String(item.gate)] ?? `闸${item.gate}`) : '转译'} {item.reason ?? ''}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
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
