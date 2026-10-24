import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import App from '../App'
import { buildLayerTree, applyNodeEvent, serializeLayerTree, type LayerNode } from '../pipelineTree'
import type { SessionDetail } from '../types'

// add-pipeline-graph-view：默认视图改为 graph；本文件验证会话恢复的时间轴行为。
// 注意：describe 级 beforeEach 会 clear localStorage（晚于顶层 hook），故用
// setItem 包装器在各 clear 之后调用——见下方 installListPref()。
function installListPref() {
  localStorage.setItem('fa_pipeline_view', 'list')
}

// selectSession 按会话 status 恢复管线 UI（resume-pipeline-across-sessions Task 5）：
// - running + pipeline_snapshot → 恢复分层时间轴 + 进入 analyzing + 启动 2s 轮询
// - completed + pipeline_snapshot → 报告消息 + 静态完成时间轴（插在报告消息之前）
// - 无快照 → 走现有 report/chat_history 恢复逻辑
// 交互入口：渲染 App（sidebar 打开）→ 点击会话列表项触发 selectSession。

// 构造指定 running 节点的快照树：沿途节点按序列 node_start，目标节点保持 running
function treeWithRunningNode(targetNodeId: string, path: string[]): LayerNode[] {
  let tree = buildLayerTree()
  let ts = 1_000
  for (const nodeId of path) {
    tree = applyNodeEvent(tree, { type: 'node_start', node_id: nodeId } as never, ts)
    if (nodeId !== targetNodeId) {
      tree = applyNodeEvent(tree, { type: 'node_complete', node_id: nodeId } as never, ts + 100)
    }
    ts += 1_000
  }
  return tree
}

// 构造全完成树：所有配置节点依次 start + complete
function treeAllCompleted(): LayerNode[] {
  let tree = buildLayerTree()
  let ts = 1_000
  for (const layer of tree) {
    for (const child of layer.children) {
      tree = applyNodeEvent(tree, { type: 'node_start', node_id: child.nodeId } as never, ts)
      tree = applyNodeEvent(tree, { type: 'node_complete', node_id: child.nodeId } as never, ts + 100)
      ts += 1_000
    }
  }
  return tree
}

function makeSnapshot(tree: LayerNode[], currentNodeId: string, progress: number): string {
  // layerTree 为内嵌的序列化 JSON 字符串（snapshot 整体 JSON 内的字符串字段）
  return JSON.stringify({
    layerTree: serializeLayerTree(tree),
    currentNodeId,
    progress,
    updatedAt: 1_700_000_000_000,
  })
}

function makeSessionDetail(overrides: Partial<SessionDetail>): SessionDetail {
  return {
    session_id: 's1',
    stock_code: '600519',
    stock_name: '贵州茅台',
    display_name: '贵州茅台分析',
    status: 'completed',
    created_at: '2026-07-01T00:00:00Z',
    duration_ms: 60_000,
    session_type: 'analysis',
    report_markdown: '',
    chart_data: {} as SessionDetail['chart_data'],
    analyst_reports: {},
    agent_process: {},
    analyst_summaries: {},
    chat_history: [],
    pipeline_snapshot: null,
    ...overrides,
  }
}

// fetch stub：/api/sessions 列表 + 按 id 的会话详情（detailResponses 支持按调用次数出队）
function stubFetch(detailResponses: Array<{ ok: boolean; body: unknown }>) {
  let detailCallCount = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/api/sessions' && (!init || !init.method || init.method === 'GET')) {
      return new Response(JSON.stringify({ sessions: [] }), { status: 200 })
    }
    if (url.startsWith('/api/sessions/')) {
      // add-composer-stat-pills：计量条 stats 端点独立分流，不得消耗会话详情调用序
      if (url.endsWith('/stats')) return new Response('{}', { status: 404 })
      const idx = Math.min(detailCallCount, detailResponses.length - 1)
      detailCallCount += 1
      const item = detailResponses[idx]
      return new Response(JSON.stringify(item.body), { status: item.ok ? 200 : 500 })
    }
    return new Response('{}', { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

// 渲染 App 并点击会话项触发 selectSession（sidebar 默认打开，列表空时点击无效，
// 所以直接通过列表 sessions 为空则改走：把会话塞进 loadSessions 响应不灵活，
// 这里直接渲染后通过 props 内部点击不可行——改为 mock /api/sessions 返回目标会话并点击）
async function renderAndSelect(sessionId: string, displayName: string) {
  render(<App />)
  await waitFor(() => expect(screen.getByText(displayName)).toBeInTheDocument())
  await act(async () => {
    fireEvent.click(screen.getByText(displayName))
  })
  await act(async () => {})
}

function stubFetchWithSessionList(sessionId: string, displayName: string, detailResponses: Array<{ ok: boolean; body: unknown }>) {
  let detailCallCount = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/api/sessions' && (!init || !init.method)) {
      return new Response(
        JSON.stringify({
          sessions: [{
            session_id: sessionId,
            stock_code: '600519',
            stock_name: '贵州茅台',
            display_name: displayName,
            status: 'completed',
            created_at: '2026-07-01T00:00:00Z',
            duration_ms: 60_000,
          }],
        }),
        { status: 200 },
      )
    }
    if (url.startsWith(`/api/sessions/${sessionId}`)) {
      // add-composer-stat-pills：stats 端点不参与会话详情调用序
      if (url.endsWith('/stats')) return new Response('{}', { status: 404 })
      // SSE stream 端点：新架构下 running 会话优先走 journal replay，但本组用例
      // 测的是「无在途 SSE（404）时轮询接管」场景——rebuild 只保留 user 消息，
      // resume 收到 404 保持 streaming 相位，轮询 effect 启动后用 updatePipelineSnapshot
      // 从 detail.pipeline_snapshot 创建/刷新管线消息（SSE 不可用时的兜底路径）。
      if (url.includes('/stream')) {
        return new Response('{}', { status: 404 })
      }
      const idx = Math.min(detailCallCount, detailResponses.length - 1)
      detailCallCount += 1
      const item = detailResponses[idx]
      return new Response(JSON.stringify(item.body), { status: item.ok ? 200 : 500 })
    }
    return new Response('{}', { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('selectSession 按会话状态恢复管线', () => {
  beforeEach(() => {
    localStorage.clear()
    installListPref()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('running 会话恢复快照时间轴并进入 analyzing（渲染 pipeline-timeline）', async () => {
    const tree = treeWithRunningNode('trader', ['check_cache', 'trader'])
    const snapshot = makeSnapshot(tree, 'trader', 0.5)
    const detail = makeSessionDetail({ status: 'running', pipeline_snapshot: snapshot })
    stubFetchWithSessionList('s1', '运行中会话', [{ ok: true, body: detail }])

    await renderAndSelect('s1', '运行中会话')

    // 新架构：rebuild 只保留 user 消息，resume 收到 404（无在途 SSE）后保持 streaming，
    // 轮询 effect 启动首个 tick 用 detail.pipeline_snapshot 创建 pipeline 消息 → 时间轴渲染
    await waitFor(() => {
      expect(screen.getByTestId('pipeline-timeline')).toBeInTheDocument()
    }, { timeout: 5000 })
  })

  it('completed 会话恢复报告消息 + 静态时间轴（时间轴在报告之前）', async () => {
    const tree = treeAllCompleted()
    const snapshot = makeSnapshot(tree, '', 1)
    const detail = makeSessionDetail({
      status: 'completed',
      pipeline_snapshot: snapshot,
      report_markdown: '# 交易决策报告\n买入',
      chat_history: [{ role: 'user', content: '分析贵州茅台', ts: '2026-07-01T00:00:00Z' }],
    })
    stubFetchWithSessionList('s1', '已完成会话', [{ ok: true, body: detail }])

    await renderAndSelect('s1', '已完成会话')
    // enhance-pipeline-progress：完成态时间线默认折叠为摘要条，展开后执行原断言
    fireEvent.click(screen.getByTestId('pipeline-summary'))

    // 报告消息渲染（ReactMarkdown 渲染 markdown 内容）
    expect(screen.getAllByText('交易决策报告').length).toBeGreaterThan(0)
    // 静态时间轴与报告并存，且时间轴 DOM 在报告之前
    // （completed 态时间轴为静态快照，渲染过滤器需放行已完成时间轴）
    const timeline = screen.getByTestId('pipeline-timeline')
    const report = screen.getAllByText('交易决策报告').pop()!
    expect(timeline.compareDocumentPosition(report) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('无快照 completed 会话走现有逻辑（报告恢复、无时间轴）', async () => {
    const detail = makeSessionDetail({
      status: 'completed',
      pipeline_snapshot: null,
      report_markdown: '# 历史报告',
    })
    stubFetchWithSessionList('s1', '历史会话', [{ ok: true, body: detail }])

    await renderAndSelect('s1', '历史会话')

    expect(screen.getAllByText('历史报告').length).toBeGreaterThan(0)
    expect(screen.queryByTestId('pipeline-timeline')).not.toBeInTheDocument()
  })

  it('running 会话在无活跃 SSE 时每 2s 轮询快照并更新进度，completed 后停止并恢复报告', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const treeRunning = treeWithRunningNode('trader', ['check_cache', 'trader'])
    const snapshotRunning = makeSnapshot(treeRunning, 'trader', 0.5)
    const detailRunning = makeSessionDetail({ status: 'running', pipeline_snapshot: snapshotRunning })

    const treeDone = treeAllCompleted()
    const snapshotDone = makeSnapshot(treeDone, '', 1)
    const detailCompleted = makeSessionDetail({
      status: 'completed',
      pipeline_snapshot: snapshotDone,
      report_markdown: '# 最终报告',
    })

    const fetchMock = stubFetchWithSessionList('s1', '轮询会话', [
      { ok: true, body: detailRunning },   // selectSession 首次加载
      { ok: true, body: detailRunning },   // 第 1 次轮询：仍 running
      { ok: true, body: detailCompleted }, // 第 2 次轮询：completed → selectSession 完整恢复
      { ok: true, body: detailCompleted }, // selectSession 恢复报告的详情请求
    ])

    render(<App />)
    await waitFor(() => expect(screen.getByText('轮询会话')).toBeInTheDocument())
    await act(async () => {
      fireEvent.click(screen.getByText('轮询会话'))
    })
    // resume 404 后轮询首个 tick 创建 pipeline 消息
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_100)
    })
    // 恢复 running 时间轴
    expect(screen.getByTestId('pipeline-timeline')).toBeInTheDocument()

    const detailCallsBefore = fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/sessions/s1')).length

    // 推进 2s：触发第 1 次轮询（running，仅更新快照）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_100)
    })
    const detailCallsMid = fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/sessions/s1')).length
    expect(detailCallsMid).toBeGreaterThan(detailCallsBefore)

    // 再推进 2s：轮询得到 completed → selectSession 恢复报告，轮询停止
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_100)
    })
    expect((await screen.findAllByText('最终报告')).length).toBeGreaterThan(0)

    const detailCallsDone = fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/sessions/s1')).length
    // 再推进 4s：轮询已停止，不再有新的详情请求
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_200)
    })
    const detailCallsAfter = fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/sessions/s1')).length
    expect(detailCallsAfter).toBe(detailCallsDone)
  })

  it('恢复态轮询期间用户发起新分析：轮询让位，不写消息、不调 selectSession', async () => {
    // 竞态场景（Task 5 review）：startAnalysis 设置 abortRef 前无 setState、effect 不重跑、
    // interval 不清理；若轮询读到 completed 会调 selectSession → abortStreaming() 掐断新 SSE。
    // 修复后 interval 回调开头复查 abortRef，SSE 在线时轮询直接返回。
    vi.useFakeTimers({ shouldAdvanceTime: true })
    localStorage.setItem('fa_api_key', 'test-key')

    const treeRunning = treeWithRunningNode('trader', ['check_cache', 'trader'])
    const snapshotRunning = makeSnapshot(treeRunning, 'trader', 0.5)
    const detailRunning = makeSessionDetail({ status: 'running', pipeline_snapshot: snapshotRunning })

    // 关键陷阱：新分析 SSE 建立后，恢复态的 currentSessionId（s1）仍指向旧会话，
    // 而旧会话恰在此刻 completed → 若轮询不让位，必然触发 selectSession → abortStreaming。
    const treeDone = treeAllCompleted()
    const snapshotDone = makeSnapshot(treeDone, '', 1)
    const detailCompleted = makeSessionDetail({
      status: 'completed',
      pipeline_snapshot: snapshotDone,
      report_markdown: '# 旧会话报告',
    })

    // 永不结束的 SSE 流：模拟新分析已建立订阅、正在流式产出（SSE 在线期间）
    const sseStream = new ReadableStream<Uint8Array>({
      start() { /* 保持打开，不推送、不关闭 */ },
    })
    let detailCallCount = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/sessions' && (!init || !init.method)) {
        return new Response(
          JSON.stringify({
            sessions: [{
              session_id: 's1',
              stock_code: '600519',
              stock_name: '贵州茅台',
              display_name: '轮询让位会话',
              status: 'completed',
              created_at: '2026-07-01T00:00:00Z',
              duration_ms: 60_000,
            }],
          }),
          { status: 200 },
        )
      }
      if (url === '/api/analyze') {
        return new Response(sseStream, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.startsWith('/api/sessions/')) {
        // add-composer-stat-pills：stats 端点独立分流，不消耗 detailCallCount 序
        if (url.endsWith('/stats')) return new Response('{}', { status: 404 })
        // SSE stream 端点：模拟「无在途 SSE、靠轮询恢复」，返回 404 让 resume 安静保持相位
        if (url.includes('/stream')) {
          return new Response('{}', { status: 404 })
        }
        detailCallCount += 1
        // 首次为 selectSession 恢复（running + 快照）；第 2 次（轮询首个 tick 创建 pipeline）
        // 仍 running；此后若轮询未让位则命中 completed，走 selectSession → abortStreaming()，
        // 放大竞态后果便于断言
        const body = detailCallCount <= 2 ? detailRunning : detailCompleted
        return new Response(JSON.stringify(body), { status: 200 })
      }
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await waitFor(() => expect(screen.getByText('轮询让位会话')).toBeInTheDocument())
    await act(async () => {
      fireEvent.click(screen.getByText('轮询让位会话'))
    })
    // 恢复 running：resume 收到 404 保持 streaming，轮询首个 tick 创建 pipeline 消息
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_100)
    })
    expect(screen.getByTestId('pipeline-timeline')).toBeInTheDocument()

    // 模拟用户在恢复态发起新分析：ChatInput 输入 → 点击发送 → startAnalysis 设置 abortRef 并建立 SSE。
    // 注意：此过程无 setState 改变 appState/currentSessionId（仅 session_created 事件才会），
    // 轮询 effect 不重跑、interval 不清理——正是竞态窗口。
    const input = screen.getByPlaceholderText(/输入股票名称或代码|输入问题/)
    await act(async () => {
      fireEvent.change(input, { target: { value: '分析宁德时代' } })
      fireEvent.click(screen.getByTestId('send-button'))
    })
    // 等待 /api/analyze 请求发出（abortRef 已设置、SSE 已建立）
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u) === '/api/analyze')).toBe(true))

    const callsBefore = fetchMock.mock.calls.length

    // 推进 6s（3 个轮询周期）：轮询应让位——零网络请求、零消息写入、零 selectSession 调用
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_300)
    })
    expect(fetchMock.mock.calls.length).toBe(callsBefore)
    // selectSession 未被误调：旧会话报告未插入、未被 abortStreaming 掐断（时间轴仍在 analyzing 渲染）
    expect(screen.queryByText('旧会话报告')).not.toBeInTheDocument()
    expect(screen.getByTestId('pipeline-timeline')).toBeInTheDocument()
    // 用户刚发起的分析输入仍在消息流中
    expect(screen.getByText('分析宁德时代')).toBeInTheDocument()
  })

  it('running 会话轮询超过 5 分钟后停止并提示管线可能已中断', async () => {
    // 超时保护（Final Review Fix 2）：ReAct 路径切走后 status 可能永久 running，
    // 轮询无限进行会泄漏资源。超过 MAX_POLLING_MS（5 分钟）后停止轮询并提示用户。
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const treeRunning = treeWithRunningNode('trader', ['check_cache', 'trader'])
    const snapshotRunning = makeSnapshot(treeRunning, 'trader', 0.5)
    const detailRunning = makeSessionDetail({ status: 'running', pipeline_snapshot: snapshotRunning })

    // 永远返回 running（管线卡住不完成，模拟 ReAct 路径切走后台未续跑）
    const fetchMock = stubFetchWithSessionList('s1', '卡住会话', [
      { ok: true, body: detailRunning },
    ])

    render(<App />)
    await waitFor(() => expect(screen.getByText('卡住会话')).toBeInTheDocument())
    await act(async () => {
      fireEvent.click(screen.getByText('卡住会话'))
    })
    // resume 404 后轮询首个 tick 创建 pipeline 消息
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_100)
    })
    expect(screen.getByTestId('pipeline-timeline')).toBeInTheDocument()

    // 推进超过 5 分钟（MAX_POLLING_MS = 300_000，每 2s 一次轮询）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(305_000)
    })

    // 轮询已停止：再推进 10s 不应有新的详情请求
    const callsAtTimeout = fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/sessions/s1')).length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    const callsAfterWait = fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/sessions/s1')).length
    expect(callsAfterWait).toBe(callsAtTimeout)

    // 提示消息出现
    expect(screen.getByText(/管线可能已中断/)).toBeInTheDocument()
  })
})

describe('selectSession 结构化时序恢复（persist-full-session-timeline）', () => {
  beforeEach(() => {
    localStorage.clear()
    installListPref()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('chat_history 条目含 agentTimeline 时按结构化时序恢复（不拍平为"思考在前、工具在后"）', async () => {
    // 结构化时序：思考A -> 工具调用 -> 思考B（交错顺序）。
    // 旧逻辑 buildTimelineFromHistory 会拍平成 思考A+B -> 工具调用，可通过横幅 DOM 顺序区分。
    const detail = makeSessionDetail({
      session_type: 'chat',
      chat_history: [
        { role: 'user', content: '茅台怎么样', ts: '2026-07-01T00:00:00Z' },
        {
          role: 'assistant',
          content: '最终回答',
          ts: '2026-07-01T00:01:00Z',
          // 拍平字段仍存在（旧逻辑会用它），但应被 agentTimeline 优先
          thinking: '思考A思考B',
          tool_calls: [{ name: 'get_stock_data', args: {}, result_text: 'ok', done: true }],
          agentTimeline: [
            { type: 'thinking', content: '思考A', done: true },
            { type: 'tool_call', name: 'get_stock_data', args: '', result: 'ok', done: true },
            { type: 'thinking', content: '思考B', done: true },
          ],
        },
      ],
    })
    stubFetchWithSessionList('s1', '结构化对话会话', [{ ok: true, body: detail }])

    await renderAndSelect('s1', '结构化对话会话')

    const chatMsg = screen.getByTestId('stream-output')
    const banners = chatMsg.querySelectorAll('button')
    // 结构化恢复：3 个横幅按 思考A -> 工具 -> 思考B 顺序（拍平恢复只有 2 个横幅且顺序相反）
    expect(banners).toHaveLength(3)
    // 中间为工具调用横幅（折叠态显示"工具调用· 1 次"），展开后可见工具名 label
    expect(banners[1].textContent).toContain('工具调用')
    fireEvent.click(banners[1])
    expect((await screen.findByText('[get_stock_data]')).closest('[data-testid="stream-output"]')).toBeTruthy()
  })

  it('旧数据无 agentTimeline 时回退近似恢复（不报错）', async () => {
    const detail = makeSessionDetail({
      session_type: 'chat',
      chat_history: [
        { role: 'user', content: '茅台怎么样', ts: '2026-07-01T00:00:00Z' },
        {
          role: 'assistant',
          content: '最终回答',
          ts: '2026-07-01T00:01:00Z',
          thinking: '历史思考内容',
          tool_calls: [{ name: 'get_stock_data', args: {}, result_text: 'ok', done: true }],
          // 无 agentTimeline 字段
        },
      ],
    })
    stubFetchWithSessionList('s1', '旧版对话会话', [{ ok: true, body: detail }])

    await renderAndSelect('s1', '旧版对话会话')

    // 回退近似：思考在前、工具调用在后，两个横幅
    const chatMsg = screen.getByTestId('stream-output')
    const banners = chatMsg.querySelectorAll('button')
    expect(banners).toHaveLength(2)
    expect(banners[1].textContent).toContain('工具调用')
    expect(screen.getByText('最终回答')).toBeInTheDocument()
  })

  it('completed 会话恢复 pipeline_timelines 为管线消息的 nodeTimelines', async () => {
    const tree = treeAllCompleted()
    const snapshot = makeSnapshot(tree, '', 1)
    const detail = makeSessionDetail({
      status: 'completed',
      pipeline_snapshot: snapshot,
      report_markdown: '# 报告',
      chat_history: [{ role: 'user', content: '分析茅台', ts: '2026-07-01T00:00:00Z' }],
      pipeline_timelines: {
        bull_r1: [{ type: 'thinking', content: '多头结构化思考内容', done: true }],
      },
    })
    stubFetchWithSessionList('s1', '结构化管线会话', [{ ok: true, body: detail }])

    await renderAndSelect('s1', '结构化管线会话')
    // enhance-pipeline-progress：完成态时间线默认折叠为摘要条，展开后执行原断言
    fireEvent.click(screen.getByTestId('pipeline-summary'))

    // 管线消息渲染 nodeTimelines 分组：节点角色名标题 + 该节点思考横幅
    expect(screen.getByText('多头分析师')).toBeInTheDocument()
    const pipelineMsg = screen.getByTestId('pipeline-timeline').closest('.msg-system')!
    const thinkingBtn = Array.from(pipelineMsg.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('思考已完成'),
    )
    expect(thinkingBtn).toBeTruthy()
    fireEvent.click(thinkingBtn!)
    expect(await screen.findByText('多头结构化思考内容')).toBeInTheDocument()
  })

  // running 会话的 pipeline_timelines 恢复已迁移至 journal replay 架构：
  // rebuild 不再生成 pipeline 消息（只留 user），nodeTimelines 由 resume 重放
  // thinking_token(node=...) 事件重建。该路径在 store 层 replay 测试中覆盖。

  it('旧数据无 pipeline_timelines 时管线消息不设 nodeTimelines（不渲染节点时序区）', async () => {
    const tree = treeAllCompleted()
    const snapshot = makeSnapshot(tree, '', 1)
    const detail = makeSessionDetail({
      status: 'completed',
      pipeline_snapshot: snapshot,
      report_markdown: '# 报告',
      chat_history: [{ role: 'user', content: '分析茅台', ts: '2026-07-01T00:00:00Z' }],
      // 无 pipeline_timelines 字段
    })
    stubFetchWithSessionList('s1', '旧版管线会话', [{ ok: true, body: detail }])

    await renderAndSelect('s1', '旧版管线会话')
    // enhance-pipeline-progress：完成态时间线默认折叠为摘要条，展开后执行原断言
    fireEvent.click(screen.getByTestId('pipeline-summary'))

    // 时间轴树照常恢复，但无节点时序分组（无角色名标题）
    expect(screen.getByTestId('pipeline-timeline')).toBeInTheDocument()
    expect(screen.queryByText('多头分析师')).not.toBeInTheDocument()
  })
})

// ── pipeline_anchor 锚点定位报告插入位置（fix-history-report-anchor）──
describe('selectSession 按 pipeline_anchor 插入报告消息', () => {
  beforeEach(() => {
    localStorage.clear()
    installListPref()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  // 辅助：断言元素 a 在文档中位于 b 之前
  function assertBefore(a: Element, b: Element) {
    expect(
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  }

  it('多轮澄清会话：报告插在最后一条 user 之后（锚点=3）', async () => {
    // chat_history: [user1「分析热门股票」, assistant1(思考), user2「中际旭创」]
    // 期望 DOM 顺序：user1 → assistant → user2 → pipeline-timeline → 报告
    const tree = treeAllCompleted()
    const snapshot = makeSnapshot(tree, '', 1)
    const detail = makeSessionDetail({
      status: 'completed',
      pipeline_snapshot: snapshot,
      report_markdown: '# 分析报告',
      pipeline_anchor: 3,
      chat_history: [
        { role: 'user', content: '分析热门股票', ts: '2026-07-01T00:00:00Z' },
        {
          role: 'assistant',
          content: '我来搜索',
          ts: '2026-07-01T00:00:01Z',
          agentTimeline: [{ type: 'thinking', content: '搜索思考', done: true }],
        },
        { role: 'user', content: '中际旭创', ts: '2026-07-01T00:00:02Z' },
      ],
    })
    stubFetchWithSessionList('s1', '多轮澄清会话', [{ ok: true, body: detail }])

    await renderAndSelect('s1', '多轮澄清会话')
    // enhance-pipeline-progress：完成态时间线默认折叠为摘要条，展开后执行原断言
    fireEvent.click(screen.getByTestId('pipeline-summary'))

    const user1 = screen.getByText('分析热门股票')
    const user2 = screen.getByText('中际旭创')
    const timeline = screen.getByTestId('pipeline-timeline')
    const report = screen.getAllByText('分析报告').pop()!

    // 关键断言：user2 在 timeline 之前（当前实现会失败：报告插在 user1 后，user2 被挤到报告后）
    assertBefore(user2, timeline)
    // timeline 在 report 之前
    assertBefore(timeline, report)
    // user1 在 user2 之前
    assertBefore(user1, user2)
  })

  it('报告后追问会话：报告插在第一条 user 之后（锚点=1）', async () => {
    // chat_history: [user1「分析茅台」, user2「再看看风险」, assistant2(追问回复)]
    // 期望 DOM 顺序：user1 → pipeline-timeline → 报告 → user2 → assistant2
    const tree = treeAllCompleted()
    const snapshot = makeSnapshot(tree, '', 1)
    const detail = makeSessionDetail({
      status: 'completed',
      pipeline_snapshot: snapshot,
      report_markdown: '# 茅台报告',
      pipeline_anchor: 1,
      chat_history: [
        { role: 'user', content: '分析茅台', ts: '2026-07-01T00:00:00Z' },
        { role: 'user', content: '再看看风险', ts: '2026-07-01T00:00:01Z' },
        { role: 'assistant', content: '追问回复', ts: '2026-07-01T00:00:02Z' },
      ],
    })
    stubFetchWithSessionList('s1', '追问会话', [{ ok: true, body: detail }])

    await renderAndSelect('s1', '追问会话')
    // enhance-pipeline-progress：完成态时间线默认折叠为摘要条，展开后执行原断言
    fireEvent.click(screen.getByTestId('pipeline-summary'))

    const user1 = screen.getByText('分析茅台')
    const user2 = screen.getByText('再看看风险')
    const timeline = screen.getByTestId('pipeline-timeline')
    const report = screen.getAllByText('茅台报告').pop()!

    // 报告在 user1 之后、user2 之前
    assertBefore(user1, timeline)
    assertBefore(timeline, report)
    assertBefore(report, user2)
  })

  it('无锚点旧会话回退：报告插在第一个 user 之后', async () => {
    // pipeline_anchor 为 null/缺失（旧数据），保持现有回退行为
    const tree = treeAllCompleted()
    const snapshot = makeSnapshot(tree, '', 1)
    const detail = makeSessionDetail({
      status: 'completed',
      pipeline_snapshot: snapshot,
      report_markdown: '# 旧报告',
      // 不设 pipeline_anchor（旧会话）
      chat_history: [
        { role: 'user', content: '分析茅台', ts: '2026-07-01T00:00:00Z' },
      ],
    })
    stubFetchWithSessionList('s1', '旧版会话', [{ ok: true, body: detail }])

    await renderAndSelect('s1', '旧版会话')
    // enhance-pipeline-progress：完成态时间线默认折叠为摘要条，展开后执行原断言
    fireEvent.click(screen.getByTestId('pipeline-summary'))

    const user1 = screen.getByText('分析茅台')
    const timeline = screen.getByTestId('pipeline-timeline')
    const report = screen.getAllByText('旧报告').pop()!

    // 回退：user1 → timeline → report
    assertBefore(user1, timeline)
    assertBefore(timeline, report)
  })
})
