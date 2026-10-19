import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from '../App'
import { FactorResearchPage } from '../pages/factors/FactorResearchPage'
import type { FactorResearchStatusResponse } from '../types'

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

function snapshotBody(overrides: Partial<FactorResearchStatusResponse> = {}): FactorResearchStatusResponse {
  return {
    available: true,
    age_seconds: 12,
    stale: false,
    collected_at: '2026-10-17T12:00:00+08:00',
    collected_at_epoch: 1760683200,
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
    control: {
      desired_running: null,
      requested_at: null,
      pending: false,
      honor: 'live',
      honor_eta_seconds: 0,
    },
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

  it('轮询刷新（注入 20ms）：候选计数随新快照更新', async () => {
    mockFetch([
      snapshotBody(),
      snapshotBody({
        totals: { sessions: 2, candidates: 141, workspace_tasks: 222 },
        stale: true,
      }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={20} />)

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
            control: {
          desired_running: false,
          requested_at: '2026-10-17T23:10:00+08:00',
          pending: true,
          honor: 'live',
          honor_eta_seconds: 0,
        },
          },
          putResponse.status ?? 200
        )
      }
      if (url === '/api/sessions') return respond({ sessions: [] })
      return respond({})
    })
    vi.stubGlobal('fetch', fetchMock)
    return { fetchMock, putCalls }
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
        control: {
          desired_running: false,
          requested_at: '2026-10-17T23:10:00+08:00',
          pending: true,
          honor: 'live',
          honor_eta_seconds: 0,
        },
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
        control: {
          desired_running: false,
          requested_at: '2026-10-17T23:10:00+08:00',
          pending: true,
          honor: 'live',
          honor_eta_seconds: 0,
        },
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
        control: {
          desired_running: null,
          requested_at: null,
          pending: false,
          honor: 'unavailable',
          honor_eta_seconds: null,
        },
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

  it('离线可开：honor=launcher → 开关可用、位置取意图、明示 ≤3 分钟', async () => {
    const { putCalls } = mockControlFetch([
      snapshotBody({
        stale: true,
        age_seconds: 400,
        control: {
          desired_running: true,
          requested_at: null,
          pending: false,
          honor: 'launcher',
          honor_eta_seconds: 180,
        },
        launcher: { available: true, fresh: true, agent_ok: true, age_seconds: 30 },
      }),
    ])
    render(<FactorResearchPage onBack={() => {}} pollIntervalMs={60_000} />)

    const sw = await screen.findByTestId('factor-research-control-switch')
    expect((sw as HTMLButtonElement).disabled).toBe(false)
    expect(sw.getAttribute('aria-checked')).toBe('true')
    expect(screen.getByTestId('factor-research-control-state').textContent).toContain('≤3 分钟')

    fireEvent.click(sw)
    fireEvent.click(await screen.findByTestId('factor-research-control-confirm'))
    await waitFor(() => expect(putCalls).toHaveLength(1))
    expect(putCalls[0].body).toEqual({ desired_running: false })
  })

  it('双离线：honor=unavailable → 禁用并明示研究侧与启动器均离线', async () => {
    mockControlFetch([
      snapshotBody({
        stale: true,
        age_seconds: 400,
        control: {
          desired_running: true,
          requested_at: null,
          pending: false,
          honor: 'unavailable',
          honor_eta_seconds: null,
        },
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
        control: {
          desired_running: true,
          requested_at: '2026-10-19T19:00:00+08:00',
          pending: false,
          honor: 'launcher',
          honor_eta_seconds: 180,
        },
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
        control: {
          desired_running: true,
          requested_at: '2026-10-19T18:00:00+08:00',
          pending: true,
          honor: 'live',
          honor_eta_seconds: 0,
        },
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
})
