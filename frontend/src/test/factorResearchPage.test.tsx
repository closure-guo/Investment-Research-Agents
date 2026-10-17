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
