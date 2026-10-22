import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'
import { FactorResearchPage } from '../pages/factors/FactorResearchPage'
import { DEFAULT_FACTOR_RESEARCH_CONFIG } from '../lib/factorResearch'
import type { FactorResearchControl, FactorResearchStatusResponse } from '../types'

/**
 * 因子研发监控页单测（delta add-factor-research-monitor T3；spec frontend「因子研发监控页」）。
 *
 * - 运行中渲染：看护徽章（running）/pid/时长 + 循环·步骤·进度 x/5 + 因子 chips +
 *   日志尾 + 会话历史表 + 累计计数；
 * - 陈旧（stale=true）→ 「数据已陈旧」提示且内容仍可见；
 * - 缺失/损坏（available=false）→ 如实空态二分（「采集器未接入」/「快照损坏」），
 *   与加载中/错误态严格区分；
 * - 轮询刷新（注入 20ms 短间隔）；
 * - 路由 /factor-research 直达 + 侧边栏收起态入口（App 壳层）。
 *
 * fetch 全 mock（页面契约层单测；E2E 走真实端点 + fixture 快照文件）。
 */

/** 控制块完整默认值（restart 字段必填；delta add-factor-research-settings） */
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

function snapshotBody(overrides: Partial<FactorResearchStatusResponse> = {}): FactorResearchStatusResponse {
  return {
    available: true,
    age_seconds: 12,
    stale: false,
    collected_at: '2026-10-17T12:00:00+08:00',
    collected_at_epoch: 1760683200,
    config: {
      values: DEFAULT_FACTOR_RESEARCH_CONFIG,
      source: 'file',
      invalid_fields: [],
      updated_at: null,
    },
    supervisor: { running: true, pid: 7752, started_at: '2026-10-17T11:36:00+08:00' },
    current_session: {
      log_file: 'session-20261017-113600.log',
      started_at: '2026-10-17T11:36:00+08:00',
      ended_at: null,
      duration_s: 1440,
      alive: true,
      loop_index: 4,
      step_index: 0,
      step_name: 'direct_exp_gen',
      steps_total: 5,
      step_elapsed_s: 595,
      step_eta_s: 90,
      factors: ['ret_5d_reversal', 'max_ret_20d'],
      log_tail: ['Workflow Progress: 1/5 loop_index=4', 'plain tail line'],
    },
    sessions: [
      {
        log_file: 'session-20261017-113600.log',
        started_at: '2026-10-17T11:36:00+08:00',
        ended_at: null,
        duration_s: 1440,
      },
      {
        log_file: 'session-20261017-110000.log',
        started_at: '2026-10-17T11:00:00+08:00',
        ended_at: '2026-10-17T11:35:00+08:00',
        duration_s: 300,
      },
    ],
    totals: { sessions: 2, candidates: 135, workspace_tasks: 222 },
    control: controlBlock({
      desired_running: null,
      requested_at: null,
      pending: false,
      honor: 'live',
      honor_eta_seconds: 0,
    }),
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

function mockFetch(bodies: FactorResearchStatusResponse[] | FactorResearchStatusResponse) {
  const list = Array.isArray(bodies) ? bodies : [bodies]
  let calls = 0
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/api/factor-research/status') {
      const body = list[Math.min(calls, list.length - 1)]
      calls += 1
      return respond(body)
    }
    if (url === '/api/sessions') return respond({ sessions: [] })
    if (url === '/api/snapshot/status') {
      return respond({ latest_date: null, stale: false, message: null, last_error: null, last_error_date: null })
    }
    return respond({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('因子研发监控页（delta add-factor-research-monitor T3）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  it('运行中渲染：看护徽章/pid/进度/因子/日志尾/历史表/累计计数', async () => {
    mockFetch(snapshotBody())
    render(<FactorResearchPage onBack={() => {}} />)

    await screen.findByTestId('factor-research-supervisor')
    // 看护徽章：running 态 + 文案「运行中」
    const badge = screen.getByTestId('status-badge')
    expect(badge).toHaveAttribute('data-status', 'running')
    expect(badge.textContent).toBe('运行中')
    expect(screen.getByTestId('factor-research-supervisor-pid').textContent).toContain('7752')
    // 进度：循环 5（loop_index 0 基 +1）· 步骤中文映射 · 第 1/5 步
    expect(screen.getByTestId('factor-research-progress').textContent).toBe('循环 5 · 当前步骤 实验生成 · 第 1/5 步')
    expect(screen.getByTestId('factor-research-step-timing').textContent).toContain('本步已用 9分55秒')
    // 本会话因子 chips（两个）
    expect(screen.getAllByTestId('factor-research-factor').map((n) => n.textContent)).toEqual([
      'ret_5d_reversal',
      'max_ret_20d',
    ])
    // 日志尾
    expect(screen.getByTestId('factor-research-log-tail').textContent).toContain('Workflow Progress: 1/5')
    // 会话历史（新→旧，两行；旧行「已结束」）
    const rows = screen.getAllByTestId('factor-research-session-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('进行中')
    expect(rows[1].textContent).toContain('已结束')
    // 累计计数
    expect(screen.getByTestId('factor-research-totals').textContent).toContain('累计会话 2')
    expect(screen.getByTestId('factor-research-totals').textContent).toContain('候选实现 135')
    // 无陈旧提示
    expect(screen.queryByTestId('factor-research-stale-banner')).toBeNull()
  })

  it('陈旧快照：提示「数据已陈旧」且内容仍可见', async () => {
    mockFetch(snapshotBody({ stale: true, age_seconds: 400 }))
    render(<FactorResearchPage onBack={() => {}} />)

    const banner = await screen.findByTestId('factor-research-stale-banner')
    expect(banner.textContent).toContain('数据已陈旧')
    // 内容不被隐藏（陈旧 ≠ 不可用）
    expect(screen.getByTestId('factor-research-supervisor')).toBeTruthy()
    expect(screen.getByTestId('factor-research-progress')).toBeTruthy()
  })

  it('缺失空态：采集器未接入（与加载中/错误严格区分）', async () => {
    mockFetch({ available: false, reason: 'missing' })
    render(<FactorResearchPage onBack={() => {}} />)

    const empty = await screen.findByTestId('factor-research-empty-missing')
    expect(empty.textContent).toContain('采集器未接入')
    expect(screen.queryByTestId('factor-research-supervisor')).toBeNull()
    expect(screen.queryByTestId('factor-research-loading')).toBeNull()
  })

  it('损坏空态：快照损坏（与缺失文案区分）', async () => {
    mockFetch({ available: false, reason: 'corrupt' })
    render(<FactorResearchPage onBack={() => {}} />)

    const empty = await screen.findByTestId('factor-research-empty-corrupt')
    expect(empty.textContent).toContain('快照损坏')
    expect(screen.queryByTestId('factor-research-empty-missing')).toBeNull()
  })

  it('轮询刷新（注入 200ms）：候选计数随新快照更新', async () => {
    mockFetch([
      snapshotBody(),
      snapshotBody({
        totals: { sessions: 2, candidates: 141, workspace_tasks: 222 },
        stale: true,
      }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={200} />)

    await screen.findByTestId('factor-research-supervisor')
    expect(screen.getByTestId('factor-research-totals').textContent).toContain('候选实现 135')

    await waitFor(() =>
      expect(screen.getByTestId('factor-research-totals').textContent).toContain('候选实现 141')
    )
    expect(screen.getByTestId('factor-research-stale-banner')).toBeTruthy()
  })

  it('直达 /factor-research 渲染监控页（刷新保持路由语义）', async () => {
    mockFetch(snapshotBody())
    window.history.pushState({}, '', '/factor-research')
    render(<App />)

    expect(await screen.findByTestId('factor-research-page')).toBeTruthy()
    expect(await screen.findByTestId('factor-research-supervisor')).toBeTruthy()
  })

  it('侧边栏收起态入口点击进入 /factor-research', async () => {
    localStorage.setItem('fa_api_key', 'test-key')
    mockFetch(snapshotBody())
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('sidebar-trigger')).toBeTruthy())
    fireEvent.click(screen.getByTestId('sidebar-trigger'))
    const entry = await screen.findByTestId('sidebar-factor-research-collapsed')
    expect(entry.getAttribute('aria-label')).toBe('因子研发')
    fireEvent.click(entry)

    expect(window.location.pathname).toBe('/factor-research')
    expect(await screen.findByTestId('factor-research-page')).toBeTruthy()
  })

  it('命令面板入口：动作存在且点击进入 /factor-research', async () => {
    localStorage.setItem('fa_api_key', 'test-key')
    mockFetch(snapshotBody())
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('sidebar-trigger')).toBeTruthy())
    // Ctrl+K 打开命令面板（快捷键注册在 useHotkeys）
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    const action = await screen.findByTestId('action-factor-research')
    expect(action.textContent).toContain('打开因子研发监控')
    fireEvent.click(action)

    expect(window.location.pathname).toBe('/factor-research')
    expect(await screen.findByTestId('factor-research-page')).toBeTruthy()
  })

  it('首屏取数失败 → 错误态 + 重试恢复（与空态严格区分）', async () => {
    let calls = 0
    const fetchMock = vi.fn(() => {
      calls += 1
      if (calls === 1) return Promise.reject(new Error('network down'))
      return respond(snapshotBody())
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const error = await screen.findByTestId('factor-research-error')
    expect(error.textContent).toContain('监控数据加载失败')
    expect(screen.queryByTestId('factor-research-empty-missing')).toBeNull()

    fireEvent.click(screen.getByTestId('factor-research-retry'))
    await screen.findByTestId('factor-research-supervisor')
    expect(screen.queryByTestId('factor-research-error')).toBeNull()
  })

  it('无会话：如实空态（尚无会话记录 / 暂无会话记录），MUST NOT 编造进度', async () => {
    mockFetch(
      snapshotBody({
        current_session: null,
        sessions: [],
        totals: { sessions: 0, candidates: 0, workspace_tasks: 0 },
      })
    )
    render(<FactorResearchPage onBack={() => {}} />)

    await screen.findByTestId('factor-research-current')
    expect(screen.getByTestId('factor-research-current').textContent).toContain('尚无会话记录')
    expect(screen.getByTestId('factor-research-sessions').textContent).toContain('暂无会话记录')
    expect(screen.queryByTestId('factor-research-progress')).toBeNull()
    expect(screen.queryByTestId('factor-research-log-tail')).toBeNull()
  })

  it('未知步骤名原值如实；进度标记缺失 → 「暂无进度标记」（不臆造）', async () => {
    const base = snapshotBody()
    // 第一轮：未知步骤名（不在中文映射表内 → 原值）
    mockFetch(
      snapshotBody({
        current_session: { ...base.current_session!, step_name: 'custom_step_xyz' },
      })
    )
    const view = render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)
    await screen.findByTestId('factor-research-progress')
    expect(screen.getByTestId('factor-research-progress').textContent).toContain('当前步骤 custom_step_xyz')
    view.unmount()

    // 第二轮：无任何进度标记 → 如实「暂无进度标记」
    mockFetch(
      snapshotBody({
        current_session: {
          ...base.current_session!,
          loop_index: null,
          step_index: null,
          step_name: null,
          steps_total: null,
          step_elapsed_s: null,
          step_eta_s: null,
        },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)
    await screen.findByTestId('factor-research-progress')
    expect(screen.getByTestId('factor-research-progress').textContent).toBe('暂无进度标记')
    expect(screen.queryByTestId('factor-research-step-timing')).toBeNull()
  })
})

/**
 * 看护启停开关（delta add-factor-research-control T3；spec frontend「因子研发监控页」改编点）。
 *
 * - 两态位置按快照实态（不乐观翻转）；pending → 「切换中」+ 禁用；
 * - 停止二次确认（未确认不发请求）；启动直发；
 * - 陈旧/缺失/损坏 → 禁用 + 明示理由；409/网络失败如实提示且位置不翻转。
 */
describe('因子研发监控页 · 看护启停开关（delta add-factor-research-control）', () => {
  function mockControlFetch(
    statusBodies: FactorResearchStatusResponse[],
    putResponse: { status?: number; body?: unknown } = {}
  ) {
    let statusCalls = 0
    const putCalls: Array<{ body: unknown }> = []
    const restartCalls: number[] = []
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/factor-research/status') {
        const body = statusBodies[Math.min(statusCalls, statusBodies.length - 1)]
        statusCalls += 1
        return respond(body)
      }
      if (url === '/api/factor-research/control' && init?.method === 'PUT') {
        putCalls.push({ body: JSON.parse(String(init.body)) })
        return respond(
          putResponse.body ?? {
            control: controlBlock({
              desired_running: false,
              requested_at: '2026-10-17T23:10:00+08:00',
              pending: true,
              honor: 'live',
              honor_eta_seconds: 0,
            }),
          },
          putResponse.status ?? 200
        )
      }
      if (url === '/api/factor-research/restart' && init?.method === 'POST') {
        restartCalls.push(Date.now())
        return respond({
          control: controlBlock({
            desired_running: true,
            restart_request_id: 'r1',
            restart_pending: true,
          }),
        })
      }
      if (url === '/api/sessions') return respond({ sessions: [] })
      return respond({})
    })
    vi.stubGlobal('fetch', fetchMock)
    return { fetchMock, putCalls, restartCalls }
  }

  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('两态：运行中 → 开关为开且可操作，文案「已开启」', async () => {
    mockControlFetch([snapshotBody()])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const sw = await screen.findByTestId('factor-research-control-switch')
    expect(sw.getAttribute('aria-checked')).toBe('true')
    expect((sw as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain('已开启')
  })

  it('停止：二次确认后才发 PUT；pending 期间「切换中」+ 禁用；请求体仅布尔期望态', async () => {
    const { putCalls } = mockControlFetch([
      snapshotBody(),
      snapshotBody({
        control: controlBlock({
          desired_running: false,
          requested_at: '2026-10-17T23:10:00+08:00',
          pending: true,
          honor: 'live',
          honor_eta_seconds: 0,
        }),
      }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const sw = await screen.findByTestId('factor-research-control-switch')
    fireEvent.click(sw)

    // 二次确认弹窗出现（含「终止当前研发会话」语义）
    const confirm = await screen.findByTestId('factor-research-control-confirm')
    expect(screen.getByTestId('factor-research-control-confirm-dialog').textContent).toContain(
      '将终止当前研发会话'
    )
    // 未确认前 MUST NOT 发出控制请求
    expect(putCalls).toHaveLength(0)

    fireEvent.click(confirm)
    await waitFor(() => expect(putCalls).toHaveLength(1))
    expect(putCalls[0].body).toEqual({ desired_running: false })
    // pending → 切换中 + 禁用（位置仍为实态开，不冒充已停止）
    await waitFor(() =>
      expect(screen.getByTestId('factor-research-control-state').textContent).toContain('切换中')
    )
    expect((screen.getByTestId('factor-research-control-switch') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('factor-research-control-switch').getAttribute('aria-checked')).toBe('true')
  })

  it('取消确认：不发请求', async () => {
    const { putCalls } = mockControlFetch([snapshotBody()])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    fireEvent.click(await screen.findByTestId('factor-research-control-switch'))
    fireEvent.click(await screen.findByTestId('factor-research-control-cancel'))
    expect(putCalls).toHaveLength(0)
  })

  it('启动：关闭态点击直接 PUT（无二次确认）', async () => {
    const { putCalls } = mockControlFetch([
      snapshotBody({ supervisor: { running: false, pid: null, started_at: null } }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const sw = await screen.findByTestId('factor-research-control-switch')
    expect(sw.getAttribute('aria-checked')).toBe('false')
    fireEvent.click(sw)

    expect(screen.queryByTestId('factor-research-control-confirm-dialog')).toBeNull()
    await waitFor(() => expect(putCalls).toHaveLength(1))
    expect(putCalls[0].body).toEqual({ desired_running: true })
  })

  it('pending 渲染：切换中 + 禁用（MUST NOT 冒充已生效）', async () => {
    mockFetch(
      snapshotBody({
        control: controlBlock({
          desired_running: false,
          requested_at: '2026-10-17T23:10:00+08:00',
          pending: true,
          honor: 'live',
          honor_eta_seconds: 0,
        }),
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-control-switch')
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain('切换中')
    expect((screen.getByTestId('factor-research-control-switch') as HTMLButtonElement).disabled).toBe(true)
  })

  it('陈旧：开关禁用 + 明示理由，点击不发请求', async () => {
    const { putCalls } = mockControlFetch([
      snapshotBody({
        stale: true,
        age_seconds: 400,
        control: controlBlock({
          desired_running: null,
          requested_at: null,
          pending: false,
          honor: 'unavailable',
          honor_eta_seconds: null,
        }),
      }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const sw = await screen.findByTestId('factor-research-control-switch')
    expect((sw as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain(
      '研究侧与启动器均离线'
    )
    fireEvent.click(sw)
    expect(putCalls).toHaveLength(0)
  })

  it('缺失：控制条仍在但开关禁用 + 「控制通道未接入」', async () => {
    mockControlFetch([{ available: false, reason: 'missing' }])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-empty-missing')
    const sw = screen.getByTestId('factor-research-control-switch')
    expect((sw as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain('控制通道未接入')
  })

  it('409：开关立即禁用（禁用至下一轮刷新），成功刷新后解封并清除提示', async () => {
    // 闸门控制下一轮轮询的返回时机（全量并发下 30ms 轮询会与点击流竞态 → 确定性设计）
    let statusCalls = 0
    let release: () => void = () => {}
    const gate = new Promise<void>((r) => {
      release = r
    })
    let putCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/factor-research/status') {
        statusCalls += 1
        if (statusCalls >= 2) await gate
        return respond(snapshotBody())
      }
      if (url === '/api/factor-research/control' && init?.method === 'PUT') {
        putCalls += 1
        return respond({ detail: 'control_unavailable' }, 409)
      }
      return respond({})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={30} />)

    fireEvent.click(await screen.findByTestId('factor-research-control-switch'))
    fireEvent.click(await screen.findByTestId('factor-research-control-confirm'))

    await waitFor(() =>
      expect(screen.getByTestId('factor-research-control-error').textContent).toContain('控制通道不可用')
    )
    // 409 后立即禁用（spec：端点返回 409 时开关 SHALL 禁用并明示理由）
    expect(
      (screen.getByTestId('factor-research-control-switch') as HTMLButtonElement).disabled
    ).toBe(true)
    expect(putCalls).toBe(1)
    expect(screen.getByTestId('factor-research-control-switch').getAttribute('aria-checked')).toBe('true')

    // 放行下一轮成功刷新 → 解封 + 提示清除
    release()
    await waitFor(() =>
      expect(
        (screen.getByTestId('factor-research-control-switch') as HTMLButtonElement).disabled
      ).toBe(false)
    )
    expect(screen.queryByTestId('factor-research-control-error')).toBeNull()
  })

  it('损坏快照：开关禁用 + 「控制通道未接入（快照损坏）」', async () => {
    mockControlFetch([{ available: false, reason: 'corrupt' }])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-empty-corrupt')
    const sw = screen.getByTestId('factor-research-control-switch')
    expect((sw as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain('快照损坏')
  })

  it('网络失败：如实提示且开关位置保持实态', async () => {
    let statusCalls = 0
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/factor-research/status') {
        statusCalls += 1
        return respond(snapshotBody())
      }
      if (url === '/api/factor-research/control' && init?.method === 'PUT') {
        return Promise.reject(new Error('network down'))
      }
      return respond({})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    fireEvent.click(await screen.findByTestId('factor-research-control-switch'))
    fireEvent.click(await screen.findByTestId('factor-research-control-confirm'))
    await waitFor(() =>
      expect(screen.getByTestId('factor-research-control-error').textContent).toContain('网络错误')
    )
    expect(statusCalls).toBe(1) // 失败不触发刷新
    expect(screen.getByTestId('factor-research-control-switch').getAttribute('aria-checked')).toBe('true')
  })

  it('409：如实提示且开关位置保持实态（不翻转冒充成功）', async () => {
    const { putCalls } = mockControlFetch([snapshotBody()], {
      status: 409,
      body: { detail: 'control_unavailable' },
    })
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    fireEvent.click(await screen.findByTestId('factor-research-control-switch'))
    fireEvent.click(await screen.findByTestId('factor-research-control-confirm'))

    await waitFor(() =>
      expect(screen.getByTestId('factor-research-control-error').textContent).toContain('控制通道不可用')
    )
    expect(putCalls).toHaveLength(1)
    expect(screen.getByTestId('factor-research-control-switch').getAttribute('aria-checked')).toBe('true')
  })

  it('离线可开：honor=launcher → 开关不冒充运行中（位置关）、可点击开启并明示 ≤3 分钟', async () => {
    const { putCalls } = mockControlFetch([
      snapshotBody({
        stale: true,
        age_seconds: 400,
        control: controlBlock({
          desired_running: true,
          requested_at: null,
          pending: false,
          honor: 'launcher',
          honor_eta_seconds: 180,
        }),
        launcher: { available: true, fresh: true, agent_ok: true, age_seconds: 30 },
      }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const sw = await screen.findByTestId('factor-research-control-switch')
    expect((sw as HTMLButtonElement).disabled).toBe(false)
    // 核心回归：持久化意图 true + 快照陈旧 → 开关 MUST NOT 显示为开
    expect(sw.getAttribute('aria-checked')).toBe('false')
    const state = screen.getByTestId('factor-research-control-state').textContent ?? ''
    expect(state).toContain('状态未知')
    expect(state).toContain('已记录保持运行')
    expect(state).toContain('≤3 分钟')

    // 点击 = 重新确认启动（不是停止），请求体 desired_running=true
    fireEvent.click(sw)
    expect(screen.queryByTestId('factor-research-control-confirm-dialog')).toBeNull()
    await waitFor(() => expect(putCalls).toHaveLength(1))
    expect(putCalls[0].body).toEqual({ desired_running: true })
  })

  it('双离线：honor=unavailable → 禁用并明示研究侧与启动器均离线', async () => {
    mockControlFetch([
      snapshotBody({
        stale: true,
        age_seconds: 400,
        control: controlBlock({
          desired_running: true,
          requested_at: null,
          pending: false,
          honor: 'unavailable',
          honor_eta_seconds: null,
        }),
      }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const sw = await screen.findByTestId('factor-research-control-switch')
    expect((sw as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain(
      '研究侧与启动器均离线'
    )
  })

  it('已请求等待拉起：requested_at 晚于 collected_at → 明示等待启动器', async () => {
    mockFetch(
      snapshotBody({
        stale: true,
        age_seconds: 400,
        collected_at: '2026-10-19T18:00:00+08:00',
        control: controlBlock({
          desired_running: true,
          requested_at: '2026-10-19T19:00:00+08:00',
          pending: false,
          honor: 'launcher',
          honor_eta_seconds: 180,
        }),
        launcher: { available: true, fresh: true, agent_ok: true, age_seconds: 30 },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-control-switch')
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain(
      '等待启动器拉起'
    )
  })

  it('启动失败：pending + start-failed → 展示原因而非切换中', async () => {
    mockFetch(
      snapshotBody({
        supervisor: { running: false, pid: null, started_at: null },
        control: controlBlock({
          desired_running: true,
          requested_at: '2026-10-19T18:00:00+08:00',
          pending: true,
          honor: 'live',
          honor_eta_seconds: 0,
        }),
        agent: {
          last_action: 'start-failed',
          last_action_at: '2026-10-19T18:00:05+08:00',
          last_error: '看护未在启动窗口内就位（检查 launch 脚本与环境）',
        },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-control-switch')
    const state = screen.getByTestId('factor-research-control-state').textContent ?? ''
    expect(state).toContain('启动失败')
    expect(state).toContain('看护未在启动窗口内就位')
    expect(state).not.toContain('切换中')
  })

  it('连续短命会话：最近 3 次均 <120 秒 → 告警', async () => {
    mockFetch(
      snapshotBody({
        sessions: [
          { log_file: 's3.log', started_at: '2026-10-18T09:35:26+08:00', ended_at: '2026-10-18T09:35:49+08:00', duration_s: 23 },
          { log_file: 's2.log', started_at: '2026-10-18T09:25:02+08:00', ended_at: '2026-10-18T09:25:26+08:00', duration_s: 24 },
          { log_file: 's1.log', started_at: '2026-10-18T09:14:37+08:00', ended_at: '2026-10-18T09:15:02+08:00', duration_s: 25 },
        ],
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const alert = await screen.findByTestId('factor-research-short-sessions')
    expect(alert.textContent).toContain('连续短命会话')
    expect(alert.textContent).toContain('23')
  })

  // ── 重启管线（delta add-factor-research-settings）──

  it('重启：二次确认前零请求 → POST → 「重启中」+ 禁用', async () => {
    const restartAt = new Date().toISOString()
    const { putCalls, restartCalls } = mockControlFetch([
      snapshotBody(),
      snapshotBody({
        control: controlBlock({
          desired_running: true,
          restart_requested_at: restartAt,
          restart_requested_at_epoch: Math.floor(Date.now() / 1000),
          restart_request_id: 'r1',
          restart_pending: true,
        }),
      }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const restart = await screen.findByTestId('factor-research-restart-button')
    fireEvent.click(restart)
    expect(await screen.findByTestId('factor-research-restart-confirm-dialog')).toBeTruthy()
    expect(restartCalls).toHaveLength(0) // 未确认前 MUST NOT 发请求

    fireEvent.click(screen.getByTestId('factor-research-restart-confirm'))
    await waitFor(() => expect(restartCalls).toHaveLength(1))
    expect(putCalls).toHaveLength(0)
    await waitFor(() =>
      expect(screen.getByTestId('factor-research-control-state').textContent).toContain('重启中')
    )
    expect(
      (screen.getByTestId('factor-research-restart-button') as HTMLButtonElement).disabled
    ).toBe(true)
  })

  it('重启失败：agent.restart=restart-failed → 展示原因，不显示重启中', async () => {
    mockFetch(
      snapshotBody({
        control: controlBlock({
          desired_running: true,
          restart_requested_at: '2026-10-21T10:00:00+08:00',
          restart_requested_at_epoch: 1761002400,
          restart_request_id: 'r1',
          restart_pending: false,
        }),
        agent: {
          last_action: 'start-failed',
          last_action_at: '2026-10-21T10:00:05+08:00',
          last_error: '看护未在启动窗口内就位',
          restart: {
            request_id: 'r1',
            requested_at_epoch: 1761002400,
            result: 'restart-failed',
            handled_at: '2026-10-21T10:00:05+08:00',
            handled_at_epoch: 1761002405,
          },
        },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-control-switch')
    const state = screen.getByTestId('factor-research-control-state').textContent ?? ''
    expect(state).toContain('重启失败')
    expect(state).toContain('看护未在启动窗口内就位')
    expect(state).not.toContain('重启中')
  })

  it('旧代理未执行重启：restart_pending 超 60 秒 → 如实提示未被执行', async () => {
    const old = new Date(Date.now() - 120_000).toISOString()
    mockFetch(
      snapshotBody({
        control: controlBlock({
          desired_running: true,
          restart_requested_at: old,
          restart_requested_at_epoch: Math.floor((Date.now() - 120_000) / 1000),
          restart_request_id: 'r1',
          restart_pending: true,
        }),
        agent: { last_action: 'noop', last_action_at: new Date().toISOString(), last_error: null },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-control-switch')
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain(
      '重启请求未被执行'
    )
  })

  it('陈旧 + 意图 true：徽章为「状态未知」而非运行中/已停止', async () => {
    mockFetch(
      snapshotBody({
        stale: true,
        age_seconds: 400,
        control: controlBlock({ desired_running: true, honor: 'launcher', honor_eta_seconds: 180 }),
        launcher: { available: true, fresh: true, agent_ok: true, age_seconds: 30 },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const badge = await screen.findByTestId('status-badge')
    expect(badge).toHaveAttribute('data-status', 'degraded')
    expect(badge.textContent).toBe('状态未知')
  })
})

/**
 * 僵死披露（delta add-factor-research-stall-detection；spec frontend「因子研发监控页僵死披露」）。
 *
 * - stall 时展示横幅（含 formatDurationS 口径的无进展时长），并把步骤读数降级为
 *   「读数来自 N 前的日志」（MUST NOT 再渲染「本步已用 · 预计剩余」冒充当前进度）；
 * - 未 stall（含旧采集器 log_age_seconds=null）→ 无横幅、进度照常；
 * - 未上报新鲜度 MUST NOT 出现「进度正常」之类已验证断言。
 */
describe('因子研发监控页 · 僵死披露（delta add-factor-research-stall-detection）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('僵死：展示无进展横幅 + 进度读数标注定格，且不渲染当前进度断言', async () => {
    const base = snapshotBody()
    mockFetch(
      snapshotBody({
        progress: { log_age_seconds: 3600, stalled: true },
        current_session: { ...base.current_session!, step_elapsed_s: 603, step_eta_s: 905 },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    // 横幅文案含无进展时长（3600 秒 → 1小时0分，formatDurationS 既有口径）
    const banner = await screen.findByTestId('factor-research-stall-banner')
    expect(banner.textContent).toContain('无进展 1小时0分')
    // 步骤读数降级为「来自 N 前的日志」
    expect(screen.getByTestId('factor-research-progress-stale').textContent).toContain('1小时0分')
    // MUST NOT 继续渲染「本步已用 … · 预计剩余 …」
    expect(screen.queryByTestId('factor-research-step-timing')).toBeNull()
    // 零命令面：横幅内无可点击处置入口
    expect(banner.querySelector('button')).toBeNull()
    expect(banner.querySelector('a')).toBeNull()
  })

  it('未僵死：无横幅，进度区照常展示步骤读数', async () => {
    mockFetch(
      snapshotBody({
        progress: { log_age_seconds: 42, stalled: false },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-progress')
    expect(screen.queryByTestId('factor-research-stall-banner')).toBeNull()
    // 进度照常：步骤读数仍在（未被降级）
    expect(screen.getByTestId('factor-research-step-timing').textContent).toContain('本步已用')
    expect(screen.queryByTestId('factor-research-progress-stale')).toBeNull()
  })

  it('旧版采集器未上报新鲜度：无横幅且不冒充「进度正常」', async () => {
    mockFetch(
      snapshotBody({
        progress: { log_age_seconds: null, stalled: false },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-progress')
    expect(screen.queryByTestId('factor-research-stall-banner')).toBeNull()
    // 未上报 = 不可判定：MUST NOT 出现「进度正常」等已验证断言
    expect(document.body.textContent ?? '').not.toContain('进度正常')
  })
})

/**
 * distro 探活披露（delta add-factor-research-distro-liveness-probe；spec frontend
 * 「监控页 distro 探活披露」）。
 *
 * - stale=true + 探活新鲜 ok=false → 独立探活横幅（连续失败次数 + 最近探活时刻 +
 *   「采集器并未停止」+ 由人执行 wsl --terminate rdagent），零可点击处置入口；
 * - 同条件下陈旧横幅 MUST NOT 再声称「采集器可能已停止」，改指向 distro 探活失败；
 * - 探活正常 / 不可用 / 不新鲜 / 快照新鲜 → 不展示探活横幅，陈旧横幅保持既有表述。
 */
describe('因子研发监控页 · distro 探活披露（delta add-factor-research-distro-liveness-probe）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('探活失败且快照陈旧：展示探活横幅，陈旧横幅不再声称采集器已停止', async () => {
    mockFetch(
      snapshotBody({
        stale: true,
        age_seconds: 400,
        collected_at: new Date(Date.now() - 400_000).toISOString(),
        distro: {
          available: true,
          probed_at: '2026-10-22T14:30:00+08:00',
          probed_at_epoch: 1761114600,
          distro: 'rdagent',
          ok: false,
          exit_code: -1,
          detail: 'wsl: 无法启动 distro',
          consecutive_failures: 8,
          terminated_at: null,
          terminated_at_epoch: 0,
          terminate_count: 0,
          age_seconds: 40,
          fresh: true,
        },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const banner = await screen.findByTestId('factor-research-distro-probe-banner')
    expect(banner.textContent).toContain('连续 8 次')
    expect(banner.textContent).toContain('distro')
    // 明示采集器并未停止 + 恢复动作由人发起
    expect(banner.textContent).toContain('采集器并未停止')
    expect(banner.textContent).toContain('wsl --terminate rdagent')
    // 零命令面：探活横幅内无可点击处置入口
    expect(banner.querySelectorAll('button, a')).toHaveLength(0)

    // 同条件下陈旧横幅改指向 distro 探活失败（MUST NOT 误指采集器）
    const staleBanner = screen.getByTestId('factor-research-stale-banner')
    expect(staleBanner.textContent).not.toContain('采集器可能已停止')
    expect(staleBanner.textContent).toContain('distro 探活失败')
  })

  it('探活正常（ok=true）：无探活横幅，陈旧横幅保持既有「采集器可能已停止」表述', async () => {
    mockFetch(
      snapshotBody({
        stale: true,
        age_seconds: 400,
        distro: {
          available: true,
          probed_at: '2026-10-22T14:30:00+08:00',
          probed_at_epoch: 1761114600,
          distro: 'rdagent',
          ok: true,
          exit_code: 0,
          detail: null,
          consecutive_failures: 0,
          age_seconds: 40,
          fresh: true,
        },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const staleBanner = await screen.findByTestId('factor-research-stale-banner')
    expect(staleBanner.textContent).toContain('采集器可能已停止')
    expect(screen.queryByTestId('factor-research-distro-probe-banner')).toBeNull()
  })

  it('探活不可用（available=false）：不冒充结论（无横幅），陈旧横幅保持既有表述', async () => {
    mockFetch(
      snapshotBody({
        stale: true,
        age_seconds: 400,
        distro: { available: false, reason: 'missing' },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const staleBanner = await screen.findByTestId('factor-research-stale-banner')
    expect(staleBanner.textContent).toContain('采集器可能已停止')
    expect(screen.queryByTestId('factor-research-distro-probe-banner')).toBeNull()
  })

  it('快照新鲜（stale=false）即便探活失败：无探活横幅，页面照常展示进度', async () => {
    mockFetch(
      snapshotBody({
        stale: false,
        distro: {
          available: true,
          probed_at: '2026-10-22T14:30:00+08:00',
          probed_at_epoch: 1761114600,
          distro: 'rdagent',
          ok: false,
          exit_code: -1,
          detail: 'wsl: 无法启动 distro',
          consecutive_failures: 15,
          age_seconds: 40,
          fresh: true,
        },
      })
    )
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    await screen.findByTestId('factor-research-progress')
    expect(screen.queryByTestId('factor-research-distro-probe-banner')).toBeNull()
    expect(screen.queryByTestId('factor-research-stale-banner')).toBeNull()
  })
})


describe('收割链路区块（add-factor-harvest-ui）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  it('进度/漏斗/台账渲染（运行中）', async () => {
    const harvest = {
      available: true,
      job: {
        status: 'running',
        started_at: '2026-10-22T15:55:00+08:00',
        finished_at: null,
        schedule: { day_of_week: '*', hour: 15, minute: 55, timezone: 'Asia/Shanghai' },
        next_fire_time: null,
      },
      progress: {
        batches_done: 2,
        batch_limit: 12,
        processed: 440,
        pending: 380,
        admitted_in_run: 3,
        rejected_in_run: 65,
        started_at: '2026-10-22T15:55:00+08:00',
        eta_s: 12600,
      },
      funnel: {
        candidates: 820,
        processed: 440,
        untranslatable: 225,
        transcription_error: 7,
        reached_gates: 208,
        admitted: 7,
        rejected_by_gate: { '2': 150, '4': 30 },
      },
      recent_admitted: [
        {
          expression: 'ts_corr(close,volume,10)',
          factor_id: 7,
          created_at: '2026-10-22T16:51:10+08:00',
          icir_60d: 0.2,
        },
      ],
      recent_rejected: [
        {
          expression: 'sub(div(close,ref(close,20)),1)',
          gate: 2,
          reason: 'WEAK_ICIR',
          at: '2026-10-22T16:00:00+08:00',
        },
      ],
    }
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/factor-research/status') return respond(snapshotBody())
      if (url === '/api/factor-research/harvest') return respond(harvest)
      if (url === '/api/sessions') return respond({ sessions: [] })
      if (url === '/api/snapshot/status') {
        return respond({ latest_date: null, stale: false, message: null, last_error: null, last_error_date: null })
      }
      return respond({})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<FactorResearchPage onBack={() => {}} />)

    const section = await screen.findByTestId('factor-research-harvest')
    expect(section.textContent).toContain('每天 15:55')
    expect(screen.getByTestId('factor-research-harvest-status').textContent).toBe('运行中')
    const progress = screen.getByTestId('factor-research-harvest-progress').textContent ?? ''
    expect(progress).toContain('批次 2/12')
    expect(progress).toContain('剩余 380')
    expect(progress).toContain('本轮入库 3')
    // 批内活性：进度按批落盘，运行中必须显示秒级时钟（否则页面看起来「不动」）
    expect(screen.getByTestId('factor-research-harvest-live').textContent).toContain('进行中')
    expect(screen.getByTestId('factor-research-harvest-step-candidates').textContent).toContain('820')
    expect(screen.getByTestId('factor-research-harvest-step-admitted').textContent).toContain('7')
    expect(screen.getByTestId('factor-research-harvest-gates').textContent).toContain('ICIR 150')
    expect(screen.getByTestId('factor-research-harvest-admitted').textContent).toContain(
      'ts_corr(close,volume,10)'
    )
    expect(screen.getByTestId('factor-research-harvest-rejected').textContent).toContain('WEAK_ICIR')
  })

  it('不可用 → 区块级空态（不拖垮整页）', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/factor-research/status') return respond(snapshotBody())
      if (url === '/api/factor-research/harvest') {
        return respond({
          available: false,
          job: null,
          progress: null,
          funnel: null,
          recent_admitted: [],
          recent_rejected: [],
        })
      }
      if (url === '/api/sessions') return respond({ sessions: [] })
      if (url === '/api/snapshot/status') {
        return respond({ latest_date: null, stale: false, message: null, last_error: null, last_error_date: null })
      }
      return respond({})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<FactorResearchPage onBack={() => {}} />)

    await screen.findByTestId('factor-research-supervisor')
    expect(screen.getByTestId('factor-research-harvest-empty').textContent).toContain('未接入')
    // 主区块不受影响
    expect(screen.getByTestId('factor-research-supervisor')).toBeTruthy()
  })
})
