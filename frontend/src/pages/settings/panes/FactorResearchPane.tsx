import { useCallback, useEffect, useRef, useState } from 'react'
import { StatusBadge } from '../../../components/StatusBadge'
import { Button } from '../../../components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../../components/ui/dialog'
import { formatSessionTime } from '../../../lib/format'
import {
  ACTUAL_STATE_LABEL,
  AGENT_ACTION_LABEL,
  CONFIG_SOURCE_LABEL,
  DEFAULT_FACTOR_RESEARCH_CONFIG,
  FACTOR_RESEARCH_CONFIG_FIELDS,
  FACTOR_RESEARCH_POLL_MS,
  controlErrorMessage,
  deriveControlView,
  fetchFactorResearchStatus,
  formatDurationS,
  submitFactorResearchConfig,
  submitFactorResearchControl,
  submitFactorResearchRestart,
  validateConfigInputs,
} from '../../../lib/factorResearch'
import type {
  FactorResearchConfigValues,
  FactorResearchStatusResponse,
} from '../../../types'

/**
 * 因子研发配置分区（delta add-factor-research-settings；spec frontend「因子研发配置页」）。
 *
 * 四个区块：服务实态 / 控制（启动·停止·重启）/ 研发节奏配置 / 恢复说明。
 * - 实态与开关语义共用 ``deriveControlView``（actual_state 为唯一依据，意图只披露不冒充）；
 * - 停止/重启经二次确认；pending / restart_pending 期间禁用重复操作；
 * - 配置仅四个节奏字段（零命令面），保存前前端按区间校验；
 * - 控制通道不可用（honor=unavailable）时动作与保存禁用，并给出宿主机恢复命令文案。
 */

type ConfirmAction = 'stop' | 'restart'

const EMPTY_CONFIG_RAW: Record<keyof FactorResearchConfigValues, string> = {
  snapshot_interval_s: String(DEFAULT_FACTOR_RESEARCH_CONFIG.snapshot_interval_s),
  session_gap_s: String(DEFAULT_FACTOR_RESEARCH_CONFIG.session_gap_s),
  short_session_backoff_s: String(DEFAULT_FACTOR_RESEARCH_CONFIG.short_session_backoff_s),
  short_session_threshold_s: String(DEFAULT_FACTOR_RESEARCH_CONFIG.short_session_threshold_s),
}

function toRaw(values: FactorResearchConfigValues): Record<keyof FactorResearchConfigValues, string> {
  return {
    snapshot_interval_s: String(values.snapshot_interval_s),
    session_gap_s: String(values.session_gap_s),
    short_session_backoff_s: String(values.short_session_backoff_s),
    short_session_threshold_s: String(values.short_session_threshold_s),
  }
}

const HONOR_LABEL: Record<string, string> = {
  live: '实时（研究侧在线）',
  launcher: '启动器可用（研究侧离线）',
  unavailable: '不可用（研究侧与启动器均离线）',
}

function agentActionText(data: FactorResearchStatusResponse | null): string {
  const agent = data?.agent
  if (!agent || !agent.last_action) return '—'
  const label = AGENT_ACTION_LABEL[agent.last_action] ?? agent.last_action
  const at = agent.last_action_at ? `（${formatSessionTime(agent.last_action_at)}）` : ''
  const err = agent.last_error ? `：${agent.last_error}` : ''
  return `${label}${at}${err}`
}

export function FactorResearchPane({
  onOpenMonitor,
  pollIntervalMs = FACTOR_RESEARCH_POLL_MS,
}: {
  onOpenMonitor?: () => void
  /** 轮询间隔（默认 10s；测试注入短间隔） */
  pollIntervalMs?: number
}) {
  const [data, setData] = useState<FactorResearchStatusResponse | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [restartSubmitting, setRestartSubmitting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [channelBlocked, setChannelBlocked] = useState(false)
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null)
  const [configRaw, setConfigRaw] = useState(EMPTY_CONFIG_RAW)
  const [configDirty, setConfigDirty] = useState(false)
  const [configSaving, setConfigSaving] = useState(false)
  const [configError, setConfigError] = useState<string | null>(null)
  const [configSaved, setConfigSaved] = useState(false)
  const timerRef = useRef<number | null>(null)
  const dirtyRef = useRef(false)

  useEffect(() => {
    dirtyRef.current = configDirty
  }, [configDirty])

  const load = useCallback(async () => {
    try {
      const resp = await fetchFactorResearchStatus()
      setData(resp)
      setLoadError(false)
      setChannelBlocked(false)
      setActionError(null)
      if (!dirtyRef.current) {
        setConfigRaw(toRaw(resp.config?.values ?? DEFAULT_FACTOR_RESEARCH_CONFIG))
      }
    } catch {
      // 首屏失败 → 可重试错误态；轮询失败静默（保留旧数据，下一轮自愈）
      setLoadError(true)
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

  const view = deriveControlView(data, { submitting, restartSubmitting, channelBlocked })
  const control = data?.control ?? null
  const launcher = data?.launcher ?? null
  const current = data?.current_session ?? null
  const collectedAt = data?.collected_at ? formatSessionTime(data.collected_at) : '—'
  const config = data?.config ?? null
  const agentConfig = data?.agent?.config ?? null
  const online = view.reachable && !channelBlocked

  const runControl = useCallback(
    async (desired: boolean) => {
      setSubmitting(true)
      setActionError(null)
      try {
        await submitFactorResearchControl(desired)
        await load()
      } catch (err) {
        const { text, unavailable } = controlErrorMessage(err)
        if (unavailable) setChannelBlocked(true)
        setActionError(text)
      } finally {
        setSubmitting(false)
      }
    },
    [load]
  )

  const runRestart = useCallback(async () => {
    setRestartSubmitting(true)
    setActionError(null)
    try {
      await submitFactorResearchRestart()
      await load()
    } catch (err) {
      const { text, unavailable } = controlErrorMessage(err)
      if (unavailable) setChannelBlocked(true)
      setActionError(text)
    } finally {
      setRestartSubmitting(false)
    }
  }, [load])

  const saveConfig = useCallback(async () => {
    const parsed = validateConfigInputs(configRaw)
    if ('error' in parsed) {
      setConfigError(parsed.error)
      setConfigSaved(false)
      return
    }
    setConfigSaving(true)
    setConfigError(null)
    setConfigSaved(false)
    try {
      await submitFactorResearchConfig(parsed.values)
      setConfigDirty(false)
      dirtyRef.current = false
      setConfigSaved(true)
      await load()
    } catch (err) {
      const { text, unavailable } = controlErrorMessage(err)
      if (unavailable) setChannelBlocked(true)
      setConfigError(text)
    } finally {
      setConfigSaving(false)
    }
  }, [configRaw, load])

  const onConfigChange = (key: keyof FactorResearchConfigValues, value: string) => {
    setConfigRaw((prev) => ({ ...prev, [key]: value }))
    setConfigDirty(true)
    dirtyRef.current = true
    setConfigSaved(false)
    setConfigError(null)
  }

  const startDisabled = !online || view.running || submitting || restartSubmitting
  const stopDisabled =
    !online ||
    submitting ||
    restartSubmitting ||
    view.pending ||
    (view.actualState === 'stopped' && control?.desired_running !== true)
  const restartDisabled = view.restartDisabled
  const saveDisabled = !online || configSaving

  return (
    <div data-testid="factor-research-settings-pane" className="space-y-4">
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <i className="fas fa-flask text-sm" style={{ color: 'var(--primary)' }}></i>
          <h2 className="text-base font-semibold text-foreground">因子研发配置</h2>
          <span className="ml-auto text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
            最近采集 {collectedAt}
          </span>
        </div>
        <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
          外部 RD-Agent 因子研发看护的服务实态、启停/重启与研发节奏；数据经宿主机
          data/factor_research 文件桥同步。
        </p>
      </header>

      {loadError && data === null && (
        <div data-testid="factor-research-settings-load-error" className="text-sm space-y-2">
          <span style={{ color: 'var(--text-secondary)' }}>配置数据加载失败</span>
          <Button size="sm" variant="outline" onClick={() => void load()}>
            重试
          </Button>
        </div>
      )}

      {/* 服务实态 */}
      <section
        data-testid="factor-research-settings-status"
        className="rounded-xl border border-border p-4 space-y-2"
        style={{ background: 'var(--bg-base-secondary)' }}
      >
        <div className="text-xs font-medium text-foreground">服务实态</div>
        <div className="flex flex-wrap items-center gap-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
          <StatusBadge
            status={
              view.actualState === 'running'
                ? 'running'
                : view.actualState === 'stopped'
                  ? 'retired'
                  : 'degraded'
            }
            label={ACTUAL_STATE_LABEL[view.actualState]}
            className="text-[11px]"
          />
          <span data-testid="factor-research-settings-state">{view.stateText}</span>
          {data?.stale === true && (
            <span style={{ color: 'var(--status-warning-default)' }}>快照已陈旧</span>
          )}
        </div>
        <dl className="grid grid-cols-1 gap-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
          <div className="flex gap-2">
            <dt style={{ color: 'var(--text-tertiary)' }}>最近采集：</dt>
            <dd data-testid="factor-research-settings-collected-at">
              {collectedAt}
              {typeof data?.age_seconds === 'number' && `（${formatDurationS(data.age_seconds)}前）`}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt style={{ color: 'var(--text-tertiary)' }}>控制通道：</dt>
            <dd data-testid="factor-research-settings-channel">{HONOR_LABEL[view.honor]}</dd>
          </div>
          <div className="flex gap-2">
            <dt style={{ color: 'var(--text-tertiary)' }}>期望态：</dt>
            <dd data-testid="factor-research-settings-desired">
              {control?.desired_running === true
                ? '保持运行'
                : control?.desired_running === false
                  ? '停止'
                  : '未记录'}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt style={{ color: 'var(--text-tertiary)' }}>代理最近动作：</dt>
            <dd data-testid="factor-research-settings-agent-action">{agentActionText(data)}</dd>
          </div>
          <div className="flex gap-2">
            <dt style={{ color: 'var(--text-tertiary)' }}>启动器心跳：</dt>
            <dd data-testid="factor-research-settings-launcher">
              {launcher?.available
                ? `${launcher.checked_at ? formatSessionTime(launcher.checked_at) : '—'}（${
                    launcher.fresh ? '新鲜' : '已过期'
                  }${launcher.agent_ok ? '，代理就绪' : '，代理未就绪'}）`
                : launcher?.reason === 'corrupt'
                  ? '心跳文件损坏'
                  : '未接入'}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt style={{ color: 'var(--text-tertiary)' }}>当前会话：</dt>
            <dd>
              {current
                ? `${current.log_file} · 已运行 ${formatDurationS(current.duration_s)}`
                : '尚无会话记录'}
            </dd>
          </div>
        </dl>
      </section>

      {/* 控制 */}
      <section
        data-testid="factor-research-settings-controls"
        className="rounded-xl border border-border p-4 space-y-2"
        style={{ background: 'var(--bg-base-secondary)' }}
      >
        <div className="text-xs font-medium text-foreground">控制</div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            data-testid="factor-research-settings-start"
            disabled={startDisabled}
            onClick={() => void runControl(true)}
          >
            启动
          </Button>
          <Button
            size="sm"
            variant="outline"
            data-testid="factor-research-settings-stop"
            disabled={stopDisabled}
            onClick={() => setConfirmAction('stop')}
          >
            停止
          </Button>
          <Button
            size="sm"
            variant="outline"
            data-testid="factor-research-settings-restart"
            disabled={restartDisabled}
            onClick={() => setConfirmAction('restart')}
          >
            重启管线
          </Button>
          {onOpenMonitor && (
            <Button
              size="sm"
              variant="ghost"
              data-testid="factor-research-settings-open-monitor"
              onClick={onOpenMonitor}
            >
              打开监控页
            </Button>
          )}
        </div>
        {actionError !== null && (
          <p
            data-testid="factor-research-settings-error"
            className="text-xs"
            style={{ color: 'var(--status-error-default)' }}
          >
            {actionError}
          </p>
        )}
        <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
          停止与重启会终止当前研发会话（工作区与候选产物保留）；调度由研究侧代理按期望态调和。
        </p>
      </section>

      {/* 研发节奏配置 */}
      <section
        data-testid="factor-research-settings-config"
        className="rounded-xl border border-border p-4 space-y-3"
        style={{ background: 'var(--bg-base-secondary)' }}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-foreground">研发节奏配置</span>
          <span
            data-testid="factor-research-settings-config-source"
            className="text-[11px]"
            style={{ color: 'var(--text-tertiary)' }}
          >
            {CONFIG_SOURCE_LABEL[config?.source ?? 'defaults']}
            {config?.updated_at ? ` · 更新于 ${formatSessionTime(config.updated_at)}` : ''}
          </span>
        </div>

        {config && config.invalid_fields.length > 0 && (
          <p className="text-[11px]" style={{ color: 'var(--status-warning-default)' }}>
            配置文件含非法字段（已回退默认值）：{config.invalid_fields.join('、')}
          </p>
        )}

        <div className="grid grid-cols-2 gap-3">
          {FACTOR_RESEARCH_CONFIG_FIELDS.map((field) => (
            <label key={field.key} className="space-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <span>
                {field.label}（{field.min}–{field.max} {field.unit}）
              </span>
              <input
                type="number"
                inputMode="numeric"
                min={field.min}
                max={field.max}
                step={1}
                value={configRaw[field.key]}
                data-testid={`factor-research-settings-config-${field.key}`}
                onChange={(e) => onConfigChange(field.key, e.target.value)}
                className="w-full rounded-md border border-border px-2 py-1 text-sm"
                style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-default)' }}
              />
              <span className="block text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                {field.hint}
              </span>
            </label>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            data-testid="factor-research-settings-config-save"
            disabled={saveDisabled}
            onClick={() => void saveConfig()}
          >
            保存配置
          </Button>
          {configSaved && (
            <span
              data-testid="factor-research-settings-config-saved"
              className="text-xs"
              style={{ color: 'var(--status-success-default)' }}
            >
              已保存
            </span>
          )}
          {configError !== null && (
            <span
              data-testid="factor-research-settings-config-error"
              className="text-xs"
              style={{ color: 'var(--status-error-default)' }}
            >
              {configError}
            </span>
          )}
        </div>

        <div
          data-testid="factor-research-settings-agent-config"
          className="text-[11px]"
          style={{ color: 'var(--text-tertiary)' }}
        >
          {agentConfig
            ? `研究侧实际读取：采集 ${agentConfig.values.snapshot_interval_s}s · 会话间隔 ${agentConfig.values.session_gap_s}s · 短命退避 ${agentConfig.values.short_session_backoff_s}s · 短命阈值 ${agentConfig.values.short_session_threshold_s}s`
            : '研究侧未上报（可能为旧版代理）'}
        </div>
        <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
          采集间隔在下一采集周期生效；会话间隔、短命退避与阈值在下次启动看护时生效。
        </p>
      </section>

      {/* 恢复说明（控制通道完全离线时） */}
      {view.channelOffline && (
        <section
          data-testid="factor-research-settings-recovery"
          className="rounded-xl border p-4 space-y-2"
          style={{ borderColor: 'var(--status-warning-default)', background: 'var(--bg-base-secondary)' }}
        >
          <div className="text-xs font-medium text-foreground">控制通道离线</div>
          <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
            研究侧代理与启动器均无存活证据，页面无法启停或重启。请在宿主机执行以下入口恢复：
          </p>
          <code
            className="block rounded-md p-2 font-mono text-[11px]"
            style={{ background: 'var(--bg-overlay-l1)', color: 'var(--text-secondary)' }}
          >
            wsl -d rdagent -u root -- bash /root/start-factor-research.sh
          </code>
        </section>
      )}

      {/* 确认弹窗（停止 / 重启） */}
      <Dialog
        open={confirmAction !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmAction(null)
        }}
      >
        <DialogContent data-testid="factor-research-settings-confirm-dialog">
          <DialogHeader>
            <DialogTitle>{confirmAction === 'restart' ? '重启研发管线？' : '停止研发看护？'}</DialogTitle>
            <DialogDescription>
              将终止当前研发会话（进行中的实验作废）
              {confirmAction === 'restart' ? '并重新拉起看护' : '并停止后续循环'}；工作区与候选产物保留。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="secondary"
              size="sm"
              data-testid="factor-research-settings-cancel"
              onClick={() => setConfirmAction(null)}
            >
              取消
            </Button>
            <Button
              size="sm"
              data-testid="factor-research-settings-confirm"
              onClick={() => {
                const action = confirmAction
                setConfirmAction(null)
                if (action === 'restart') void runRestart()
                else if (action === 'stop') void runControl(false)
              }}
            >
              {confirmAction === 'restart' ? '确认重启' : '确认停止'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
