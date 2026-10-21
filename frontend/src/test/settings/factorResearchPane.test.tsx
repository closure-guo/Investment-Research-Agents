import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { FactorResearchPane } from '../../pages/settings/panes/FactorResearchPane'
import { SettingsCenterPage } from '../../pages/settings/SettingsCenterPage'
import { emptyLlmConfig } from '../../llmConfig'
import { DEFAULT_FACTOR_RESEARCH_CONFIG } from '../../lib/factorResearch'
import type { FactorResearchControl, FactorResearchStatusResponse } from '../../types'

/**
 * 因子研发配置分区单测（delta add-factor-research-settings；spec frontend「因子研发配置页」）。
 *
 * - 设置中心新增分区入口，默认渲染服务实态/控制/节奏配置/恢复说明；
 * - 实态与意图分离：actual_state=unverified 时不显示运行中；
 * - 配置保存（四字段 PUT）、前端范围校验（非法不发请求）；
 * - 停止/重启二次确认；控制通道离线时禁用 + 恢复命令。
 */

function controlBlock(overrides: Partial<FactorResearchControl> = {}): FactorResearchControl {
  return {
    desired_running: null,
    requested_at: null,
    pending: false,
    honor: 'live',
    honor_eta_seconds: 0,
    restart_requested_at: null,
    restart_requested_at_epoch: null,
    restart_request_id: null,
    restart_pending: false,
    ...overrides,
  }
}

function statusBody(overrides: Partial<FactorResearchStatusResponse> = {}): FactorResearchStatusResponse {
  return {
    available: true,
    age_seconds: 10,
    stale: false,
    collected_at: new Date().toISOString(),
    collected_at_epoch: Math.floor(Date.now() / 1000),
    actual_state: 'running',
    config: {
      values: { ...DEFAULT_FACTOR_RESEARCH_CONFIG },
      source: 'file',
      invalid_fields: [],
      updated_at: null,
    },
    supervisor: {
      running: true,
      pid: 7752,
      started_at: new Date(Date.now() - 600_000).toISOString(),
    },
    current_session: null,
    sessions: [],
    totals: { sessions: 1, candidates: 10, workspace_tasks: 12 },
    agent: {
      last_action: 'noop',
      last_action_at: new Date().toISOString(),
      last_error: null,
      config: {
        values: { ...DEFAULT_FACTOR_RESEARCH_CONFIG },
        source: 'file',
        invalid_fields: [],
        updated_at: null,
      },
      restart: null,
    },
    launcher: { available: true, fresh: true, agent_ok: true, age_seconds: 20 },
    control: controlBlock(),
    ...overrides,
  }
}

function respond(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  )
}

function mockApi(bodies: FactorResearchStatusResponse[]) {
  let statusCalls = 0
  const putCalls: Array<Record<string, unknown>> = []
  const restartCalls: number[] = []
  const configCalls: Array<Record<string, unknown>> = []
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/api/factor-research/status') {
      const body = bodies[Math.min(statusCalls, bodies.length - 1)]
      statusCalls += 1
      return respond(body)
    }
    if (url === '/api/factor-research/control' && init?.method === 'PUT') {
      putCalls.push(JSON.parse(String(init.body)))
      return respond({ control: controlBlock({ desired_running: false, pending: true }) })
    }
    if (url === '/api/factor-research/restart' && init?.method === 'POST') {
      restartCalls.push(Date.now())
      return respond({ control: controlBlock({ desired_running: true, restart_pending: true }) })
    }
    if (url === '/api/factor-research/config' && init?.method === 'PUT') {
      const payload = JSON.parse(String(init.body)) as Record<string, number>
      configCalls.push(payload)
      return respond({
        config: {
          values: payload,
          source: 'file',
          invalid_fields: [],
          updated_at: new Date().toISOString(),
        },
      })
    }
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, putCalls, restartCalls, configCalls }
}

const baseProps = {
  config: emptyLlmConfig(),
  backendDefaults: { model: 'deepseek/deepseek-chat', baseUrl: '', thinking: 'enabled' },
  profileStore: { profiles: [], activeId: '' },
  capability: null,
  onProbeCapability: vi.fn(),
  onSave: vi.fn(),
  onSaveAs: vi.fn(),
  onSwitchProfile: vi.fn(),
  onDeleteProfile: vi.fn(),
  onBack: vi.fn(),
}

describe('因子研发配置分区', () => {
  beforeEach(() => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('设置中心新增「因子研发配置」分区并可直达', async () => {
    mockApi([statusBody()])
    render(<SettingsCenterPage {...baseProps} initialModule="factor-research" />)

    expect(screen.getByTestId('settings-nav-factor-research')).toHaveTextContent('因子研发配置')
    expect(await screen.findByTestId('factor-research-settings-pane')).toBeInTheDocument()
    expect(screen.getByTestId('factor-research-settings-state').textContent).toContain('已开启')
    expect(screen.getByTestId('factor-research-settings-agent-config').textContent).toContain(
      '研究侧实际读取'
    )
  })

  it('实态与意图分离：unverified + 意图 true → 状态未知（非运行中），启动器可用', async () => {
    mockApi([
      statusBody({
        stale: true,
        age_seconds: 400,
        actual_state: 'unverified',
        control: controlBlock({
          desired_running: true,
          honor: 'launcher',
          honor_eta_seconds: 180,
        }),
      }),
    ])
    render(<FactorResearchPane pollIntervalMs={60_000} />)

    expect(await screen.findByTestId('factor-research-settings-state')).toHaveTextContent('状态未知')
    expect(screen.getByTestId('factor-research-settings-state').textContent).toContain(
      '已记录保持运行'
    )
    expect(screen.getByTestId('factor-research-settings-desired').textContent).toContain('保持运行')
    expect(
      (screen.getByTestId('factor-research-settings-restart') as HTMLButtonElement).disabled
    ).toBe(false)
    expect(screen.queryByTestId('factor-research-settings-recovery')).toBeNull()
  })

  it('保存节奏配置：四字段 PUT + 已保存', async () => {
    const { configCalls } = mockApi([statusBody()])
    render(<FactorResearchPane pollIntervalMs={60_000} />)

    const input = (await screen.findByTestId(
      'factor-research-settings-config-snapshot_interval_s'
    )) as HTMLInputElement
    fireEvent.change(input, { target: { value: '30' } })
    fireEvent.click(screen.getByTestId('factor-research-settings-config-save'))

    await waitFor(() => expect(configCalls).toHaveLength(1))
    expect(configCalls[0]).toEqual({ ...DEFAULT_FACTOR_RESEARCH_CONFIG, snapshot_interval_s: 30 })
    expect(await screen.findByTestId('factor-research-settings-config-saved')).toHaveTextContent(
      '已保存'
    )
  })

  it('非法输入：前端拦截，MUST NOT 发请求', async () => {
    const { configCalls } = mockApi([statusBody()])
    render(<FactorResearchPane pollIntervalMs={60_000} />)

    const input = (await screen.findByTestId(
      'factor-research-settings-config-snapshot_interval_s'
    )) as HTMLInputElement
    fireEvent.change(input, { target: { value: '1' } })
    fireEvent.click(screen.getByTestId('factor-research-settings-config-save'))

    expect(await screen.findByTestId('factor-research-settings-config-error')).toHaveTextContent(
      '状态采集间隔需在 5–120 秒之间'
    )
    expect(configCalls).toHaveLength(0)
  })

  it('停止/重启：二次确认后才发请求', async () => {
    const { putCalls, restartCalls } = mockApi([statusBody()])
    render(<FactorResearchPane pollIntervalMs={60_000} />)

    fireEvent.click(await screen.findByTestId('factor-research-settings-stop'))
    expect(putCalls).toHaveLength(0)
    fireEvent.click(screen.getByTestId('factor-research-settings-confirm'))
    await waitFor(() => expect(putCalls).toHaveLength(1))
    expect(putCalls[0]).toEqual({ desired_running: false })

    fireEvent.click(screen.getByTestId('factor-research-settings-restart'))
    expect(restartCalls).toHaveLength(0)
    fireEvent.click(screen.getByTestId('factor-research-settings-confirm'))
    await waitFor(() => expect(restartCalls).toHaveLength(1))
  })

  it('控制通道离线：动作与保存禁用并展示恢复命令', async () => {
    mockApi([
      statusBody({
        stale: true,
        age_seconds: 400,
        actual_state: 'unverified',
        control: controlBlock({ desired_running: true, honor: 'unavailable' }),
        launcher: { available: false, reason: 'missing' },
      }),
    ])
    render(<FactorResearchPane pollIntervalMs={60_000} />)

    const recovery = await screen.findByTestId('factor-research-settings-recovery')
    expect(recovery.textContent).toContain('start-factor-research.sh')
    expect(screen.getByTestId('factor-research-settings-channel').textContent).toContain('不可用')
    for (const id of [
      'factor-research-settings-start',
      'factor-research-settings-stop',
      'factor-research-settings-restart',
      'factor-research-settings-config-save',
    ]) {
      expect((screen.getByTestId(id) as HTMLButtonElement).disabled).toBe(true)
    }
  })
})
