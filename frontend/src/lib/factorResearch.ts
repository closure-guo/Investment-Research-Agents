import { formatSessionTime } from './format'
import type {
  FactorResearchActualState,
  FactorResearchConfigValues,
  FactorResearchStatusResponse,
  HarvestChainResponse,
} from '../types'

/**
 * 因子研发监控/配置共享取数与派生（delta add-factor-research-settings）。
 *
 * 单一职责：把「端点契约 → 页面可判读状态」的派生集中在一处，监控页与设置分区共用，
 * 避免两处各自解释 actual_state / pending / restart_pending 而漂移。
 *
 * 红线：开关位置只取 actual_state（验证过的实态）；持久化意图 desired_running
 * 只在离线文案中披露，MUST NOT 决定开关位置。
 */

/** 轮询间隔（默认 10s，契约 ≤15s；测试可注入） */
export const FACTOR_RESEARCH_POLL_MS = 10_000
/** 重启请求在 live 通道下的「未被执行」披露阈值（毫秒） */
export const RESTART_LIVE_TIMEOUT_MS = 60_000

export const DEFAULT_FACTOR_RESEARCH_CONFIG: FactorResearchConfigValues = {
  snapshot_interval_s: 15,
  session_gap_s: 60,
  short_session_backoff_s: 600,
  short_session_threshold_s: 120,
}

export interface FactorResearchConfigField {
  key: keyof FactorResearchConfigValues
  label: string
  unit: string
  min: number
  max: number
  hint: string
}

/** 四个节奏旋钮（顺序固定；与后端 FACTOR_RESEARCH_CONFIG_BOUNDS 字面一致） */
export const FACTOR_RESEARCH_CONFIG_FIELDS: FactorResearchConfigField[] = [
  {
    key: 'snapshot_interval_s',
    label: '状态采集间隔',
    unit: '秒',
    min: 5,
    max: 120,
    hint: '下一采集周期生效',
  },
  {
    key: 'session_gap_s',
    label: '会话间隔',
    unit: '秒',
    min: 0,
    max: 3600,
    hint: '下次启动看护生效',
  },
  {
    key: 'short_session_backoff_s',
    label: '短命会话退避',
    unit: '秒',
    min: 0,
    max: 7200,
    hint: '下次启动看护生效',
  },
  {
    key: 'short_session_threshold_s',
    label: '短命会话阈值',
    unit: '秒',
    min: 30,
    max: 3600,
    hint: '下次启动看护生效',
  },
]

export const CONFIG_SOURCE_LABEL: Record<string, string> = {
  file: '配置文件',
  defaults: '默认值（配置文件缺失）',
  corrupt: '配置文件损坏（回退默认值）',
}

export class FactorResearchRequestError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string
  ) {
    super(`HTTP ${status}${detail ? `: ${detail}` : ''}`)
    this.name = 'FactorResearchRequestError'
  }
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const resp = await fetch(input, init)
  if (!resp.ok) {
    let detail = ''
    try {
      detail = ((await resp.json()) as { detail?: string }).detail ?? ''
    } catch {
      // 非 JSON 响应：按状态码如实提示
    }
    throw new FactorResearchRequestError(resp.status, detail)
  }
  return (await resp.json()) as T
}

export function fetchFactorResearchStatus(): Promise<FactorResearchStatusResponse> {
  return requestJson<FactorResearchStatusResponse>('/api/factor-research/status')
}

/** 收割链路只读载荷（add-factor-harvest-ui）；与监控快照同节拍轮询。 */
export function fetchHarvestChain(): Promise<HarvestChainResponse> {
  return requestJson<HarvestChainResponse>('/api/factor-research/harvest')
}

export function submitFactorResearchControl(desired: boolean): Promise<{ control: unknown }> {
  return requestJson('/api/factor-research/control', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ desired_running: desired }),
  })
}

export function submitFactorResearchRestart(): Promise<{ control: unknown }> {
  return requestJson('/api/factor-research/restart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
}

export function submitFactorResearchConfig(
  values: FactorResearchConfigValues
): Promise<{ config: unknown }> {
  return requestJson('/api/factor-research/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(values),
  })
}

/** 控制错误 → 页面可读文案（409 control_unavailable 单列，其余按状态码） */
export function controlErrorMessage(err: unknown): { text: string; unavailable: boolean } {
  if (err instanceof FactorResearchRequestError) {
    if (err.status === 409 && err.detail === 'control_unavailable') {
      return { text: '控制通道不可用（快照未更新，请求未受理）', unavailable: true }
    }
    return { text: `请求失败（HTTP ${err.status}）`, unavailable: false }
  }
  return { text: '请求失败：网络错误', unavailable: false }
}

/** 配置输入的整数字符串校验（返回 null = 通过；否则为可读错误） */
export function validateConfigInputs(
  raw: Record<keyof FactorResearchConfigValues, string>
): { values: FactorResearchConfigValues } | { error: string } {
  const values = { ...DEFAULT_FACTOR_RESEARCH_CONFIG }
  for (const field of FACTOR_RESEARCH_CONFIG_FIELDS) {
    const text = (raw[field.key] ?? '').trim()
    if (!/^\d+$/.test(text)) {
      return { error: `${field.label}必须为整数` }
    }
    const n = Number(text)
    if (!Number.isSafeInteger(n) || n < field.min || n > field.max) {
      return { error: `${field.label}需在 ${field.min}–${field.max} ${field.unit}之间` }
    }
    values[field.key] = n
  }
  return { values }
}

export function formatDurationS(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return '—'
  }
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}小时${m}分`
  if (m > 0) return `${m}分${s}秒`
  return `${s}秒`
}

export const AGENT_ACTION_LABEL: Record<string, string> = {
  no_intent: '无控制意图',
  noop: '期望与实态一致',
  started: '已启动看护',
  stopped: '已停止看护',
  'start-failed': '启动失败',
  'stop-failed': '停止失败',
}

export const ACTUAL_STATE_LABEL: Record<FactorResearchActualState, string> = {
  running: '运行中',
  stopped: '已停止',
  unverified: '状态未知',
}

export interface FactorResearchControlView {
  actualState: FactorResearchActualState
  /** 验证过的运行中（开关位置唯一依据） */
  running: boolean
  pending: boolean
  restartPending: boolean
  honor: 'live' | 'launcher' | 'unavailable'
  snapshotLive: boolean
  reachable: boolean
  switchDisabled: boolean
  restartDisabled: boolean
  /** 「停止」按钮禁用（与开关同禁用源 + 无可停止对象判定） */
  stopDisabled: boolean
  stateText: string
  /** 最近一次重启失败（同 request_id）的可读文案；无 → null */
  restartFailureText: string | null
  /** 重启请求超时未被执行（旧版代理） */
  restartTimedOut: boolean
  /** 控制通道完全离线（用于设置页恢复说明） */
  channelOffline: boolean
}

/**
 * 控制视图派生（监控页与设置分区共用）。
 *
 * @param nowMs 当前时刻（测试可注入；默认 Date.now()）
 */
export function deriveControlView(
  data: FactorResearchStatusResponse | null,
  opts: {
    submitting?: boolean
    restartSubmitting?: boolean
    channelBlocked?: boolean
    nowMs?: number
  } = {}
): FactorResearchControlView {
  const submitting = opts.submitting === true
  const restartSubmitting = opts.restartSubmitting === true
  const channelBlocked = opts.channelBlocked === true
  const nowMs = opts.nowMs ?? Date.now()

  const control = data?.control ?? null
  const launcher = data?.launcher ?? null
  const agentInfo = data?.agent ?? null
  const honor = control?.honor ?? 'unavailable'
  const snapshotLive = data !== null && data.available === true && data.stale !== true
  const actualState: FactorResearchActualState =
    data?.actual_state ?? (snapshotLive ? ((data?.supervisor?.running ?? false) ? 'running' : 'stopped') : 'unverified')
  const running = actualState === 'running'
  const pending = control?.pending === true
  const restartPending = control?.restart_pending === true
  const reachable = (honor === 'live' && snapshotLive) || honor === 'launcher'
  const switchDisabled =
    submitting || restartSubmitting || pending || restartPending || channelBlocked || !reachable
  const restartDisabled = switchDisabled
  // 停止按钮（delta add-factor-research-stop-button）：与开关共享禁用源；另在「无可停止对象」
  // （实态已停止且无运行意图）时禁用。实态不可证（unverified）但有运行意图 → 可点，用于撤销
  // 持久化意图（与设置分区同一判定）。
  const stopDisabled =
    switchDisabled || (actualState === 'stopped' && control?.desired_running !== true)

  const collectedAt = data?.collected_at ? formatSessionTime(data.collected_at) : '—'
  const requestedAt = control?.requested_at ? formatSessionTime(control.requested_at) : null
  const restartRequestedAt = control?.restart_requested_at
    ? formatSessionTime(control.restart_requested_at)
    : null
  const awaitingLauncher =
    honor === 'launcher' &&
    control?.requested_at != null &&
    data?.collected_at != null &&
    Date.parse(control.requested_at) > Date.parse(data.collected_at)
  const awaitingRestartLauncher =
    honor === 'launcher' &&
    control?.restart_requested_at != null &&
    data?.collected_at != null &&
    Date.parse(control.restart_requested_at) > Date.parse(data.collected_at)

  const restartHandledSameRequest =
    control?.restart_request_id != null &&
    agentInfo?.restart?.request_id != null &&
    agentInfo.restart.request_id === control.restart_request_id
  const restartFailureText =
    restartHandledSameRequest && agentInfo?.restart?.result === 'restart-failed'
      ? `重启失败（${formatSessionTime(agentInfo.restart.handled_at)}）：${
          agentInfo?.last_error ?? '原因未记录'
        }；可再次点击重启`
      : null

  let restartTimedOut = false
  if (
    restartPending &&
    honor === 'live' &&
    control?.restart_requested_at != null &&
    Date.parse(control.restart_requested_at) + RESTART_LIVE_TIMEOUT_MS < nowMs
  ) {
    restartTimedOut = true
  }

  let stateText: string
  if (data === null) {
    stateText = '—'
  } else if (data.available !== true) {
    if (honor === 'launcher') {
      stateText = '状态未知——研究侧启动中（等待首个快照），启动器已确认代理存活'
    } else if (data.reason === 'corrupt') {
      stateText = '状态未知——控制通道未接入（快照损坏）'
    } else {
      stateText = '状态未知——控制通道未接入（采集器未接入）'
    }
  } else if (data.stale === true) {
    if (honor === 'launcher') {
      if (awaitingRestartLauncher) {
        stateText = `状态未知——重启请求已提交（${restartRequestedAt}），等待启动器拉起`
      } else if (awaitingLauncher) {
        stateText = `状态未知——已请求（${requestedAt}），等待启动器拉起`
      } else if (control?.desired_running === true) {
        stateText = `状态未知（研究侧离线，最近采集 ${collectedAt}）——已记录保持运行，将在 ≤3 分钟内自动拉起`
      } else {
        stateText = `状态未知（研究侧离线，最近采集 ${collectedAt}）——启动器可用，将在 ≤3 分钟内拉起研究侧；开启看护需点击开关`
      }
    } else {
      stateText = '状态未知——控制通道暂不可用（研究侧与启动器均离线）'
    }
  } else if (restartTimedOut) {
    stateText = '重启请求未被执行（研究侧代理可能为旧版本，需部署新版代理）'
  } else if (restartPending) {
    stateText = restartRequestedAt
      ? `重启中…（${restartRequestedAt} 发起，等待研发侧受理）`
      : '重启中…（等待研发侧受理）'
  } else if (restartFailureText !== null) {
    stateText = restartFailureText
  } else if (pending) {
    const failedAction =
      agentInfo?.last_action === 'start-failed' || agentInfo?.last_action === 'stop-failed'
        ? agentInfo.last_action
        : null
    if (failedAction !== null) {
      const label = failedAction === 'start-failed' ? '启动失败' : '停止失败'
      const at = agentInfo?.last_action_at ? formatSessionTime(agentInfo.last_action_at) : '—'
      stateText = `${label}（${at}）：${agentInfo?.last_error ?? '原因未记录'}；代理将自动重试`
    } else {
      stateText = control?.requested_at
        ? `切换中…（${formatSessionTime(control.requested_at)} 发起，等待研发侧受理）`
        : '切换中…（等待研发侧受理）'
    }
  } else if (actualState === 'running') {
    stateText = '已开启'
  } else if (actualState === 'stopped') {
    stateText = '已关闭'
  } else {
    stateText = '状态未知'
  }

  return {
    actualState,
    running,
    pending,
    restartPending,
    honor,
    snapshotLive,
    reachable,
    switchDisabled,
    restartDisabled,
    stopDisabled,
    stateText,
    restartFailureText,
    restartTimedOut,
    channelOffline: data !== null && actualState === 'unverified' && honor === 'unavailable',
  }
}

// ── 磁盘水位（delta add-factor-workspace-retention-disk-watch；incident 051）──
// 端点契约：GET /api/factor-research/status 的 ``data.disk`` 块（后端从采集器原始字节
// 只读派生 GB + low_watermark，阈值单源在后端）。disk 增量字段的本模块契约类型不回改
// types.ts；旧快照（无 disk 块）与 available=false / corrupt 时端点给兜底块
// （全 null + low_watermark=false），派生 MUST 降级为「不渲染」且不崩。

/** 磁盘水位只读派生块（GB 读数；null = 读数未上报，MUST NOT 编造数字）。 */
export interface FactorResearchDiskBlock {
  guest_free_gb: number | null
  host_free_gb: number | null
  workspace_gb: number | null
  low_watermark: boolean
  /** 先命中的低水位侧（guest 优先）；未判低水位 → null */
  low_watermark_reason: 'guest' | 'host' | null
  /** 看护停发原因（非空 = 新会话已被暂停拉起，保护行为非故障） */
  paused_reason: string | null
}

/** 带 disk 块的快照响应（既有响应类型之上的增量字段）。 */
export type FactorResearchStatusResponseWithDisk = FactorResearchStatusResponse & {
  disk?: FactorResearchDiskBlock | null
}

export interface FactorResearchDiskView {
  disk: FactorResearchDiskBlock
  /** 水位卡是否渲染：缺 disk 块，或三读数全 null 且未判低水位 → 不渲染（旧快照兼容） */
  showCard: boolean
  /** 低水位横幅是否渲染：low_watermark=true 或 paused_reason 非空 */
  showBanner: boolean
}

/** 快照 → 磁盘水位视图（兜底块/畸形字段如实置空，MUST NOT 编造读数）。 */
export function deriveDiskView(data: FactorResearchStatusResponse | null): FactorResearchDiskView {
  const raw = (data as FactorResearchStatusResponseWithDisk | null)?.disk ?? null
  const num = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null
  const rawReason = raw?.low_watermark_reason
  const rawPause = raw?.paused_reason
  const disk: FactorResearchDiskBlock = {
    guest_free_gb: num(raw?.guest_free_gb),
    host_free_gb: num(raw?.host_free_gb),
    workspace_gb: num(raw?.workspace_gb),
    low_watermark: raw?.low_watermark === true,
    low_watermark_reason: rawReason === 'guest' || rawReason === 'host' ? rawReason : null,
    paused_reason: typeof rawPause === 'string' && rawPause !== '' ? rawPause : null,
  }
  const hasReading =
    disk.guest_free_gb !== null || disk.host_free_gb !== null || disk.workspace_gb !== null
  return {
    disk,
    showCard: raw !== null && (hasReading || disk.low_watermark),
    showBanner: disk.low_watermark || disk.paused_reason !== null,
  }
}

/** 磁盘读数展示（GB，1 位小数；未上报 → 「—」，调用方须配 title 说明）。 */
export function formatDiskGb(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(1)
}
