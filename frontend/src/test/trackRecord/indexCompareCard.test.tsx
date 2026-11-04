import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { IndexCompareCard } from '../../pages/trackRecord/IndexCompareCard'

const RESP = {
  span: 'all',
  window: { start: '2026-09-28', end: '2026-10-09' },
  agent_return: 0.05,
  indices: [
    { code: '000001', name: '上证指数', return: 0.03, effective_start_date: '2026-09-28', beat: true },
    { code: '000300', name: '沪深300', return: 0.01, effective_start_date: '2026-09-26', beat: true },
    { code: '000905', name: '中证500', return: 0.1, effective_start_date: '2026-09-28', beat: false },
    { code: '000852', name: '中证1000', return: null, effective_start_date: null, beat: null },
    { code: '399006', name: '创业板指', return: 0.02, effective_start_date: '2026-09-28', beat: true },
  ],
  as_of: '2026-10-02',
  disclaimer: '历史业绩不代表未来表现',
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('IndexCompareCard', () => {
  it('渲染 N/M 摘要并按收益降序排列,跑赢绿↑跑输红↓,请求带 span=all', async () => {
    // 既有 fetch mock 模式(trackRecordPage.test 同款):new Response + status 200
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(RESP), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    render(<IndexCompareCard span="all" />)
    await screen.findByTestId('index-compare-summary')
    // fetch 契约:相对路径 + span 查询参数(span=all)
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('span=all'))
    // 分母 = beat 非 null 数(spec: 无数据指数不参与 N/M 分母)→ 5 只中 000852 beat=null,故 3/4
    expect(screen.getByTestId('index-compare-summary').textContent).toContain('跑赢 3/4 个指数')
    const rows = screen.getAllByTestId(/^index-compare-row-/)
    // 首行为组合参考线(spec:组合条置顶,无 beat 标记),其后指数按收益降序:0.10, 0.03, 0.02, 0.01, null
    expect(rows.map(r => r.dataset.testid)).toEqual([
      'index-compare-row-agent', // 0.05 → 组合条置顶(对比基准线)
      'index-compare-row-000905', // 0.10
      'index-compare-row-000001', // 0.03
      'index-compare-row-399006', // 0.02
      'index-compare-row-000300', // 0.01
      'index-compare-row-000852', // null → 末位
    ])
    // 组合条:首行 + 收益文案 5.00%,无跑赢/跑输标记
    const agentRow = rows[0]
    expect(agentRow.dataset.testid).toBe('index-compare-row-agent')
    expect(agentRow.textContent).toContain('5.00%')
    expect(agentRow.textContent).toContain('对比基准线')
    expect(agentRow.textContent).not.toContain('跑赢')
    expect(agentRow.textContent).not.toContain('跑输')
    // 跑输红 ↓(000905)、跑赢绿 ↑(000001);无数据灰显「无数据」
    const row905 = screen.getByTestId('index-compare-row-000905')
    expect(row905.textContent).toContain('↓ 跑输')
    // 颜色挂在行内 beat 标记 span 上(style 属性),行 div 本身无 style
    expect(within(row905).getByText('↓ 跑输').getAttribute('style')).toContain('var(--status-error-default)')
    const row001 = screen.getByTestId('index-compare-row-000001')
    expect(row001.textContent).toContain('↑ 跑赢')
    expect(within(row001).getByText('↑ 跑赢').getAttribute('style')).toContain('var(--status-success-default)')
    expect(screen.getByTestId('index-compare-row-000852').textContent).toContain('无数据')
    // 起算日标注:000300 effective_start_date(2026-09-26)≠ 窗口首日(2026-09-28)
    expect(screen.getByTestId('index-compare-row-000300').textContent).toContain('(自 2026-09-26 起算)')
    // 风险提示常驻
    expect(screen.getByTestId('index-compare-card').textContent).toContain('历史业绩不代表未来表现')
  })

  it('agent_return 为 null 时展示空态,不渲染摘要与对比条,请求带 span=3m', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...RESP, agent_return: null,
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    render(<IndexCompareCard span="3m" />)
    expect(await screen.findByTestId('index-compare-empty')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('span=3m'))
    expect(screen.getByTestId('index-compare-empty').textContent).toContain('净值数据积累中')
    expect(screen.queryByTestId('index-compare-summary')).not.toBeInTheDocument()
    expect(screen.queryByTestId(/^index-compare-row-/)).not.toBeInTheDocument()
  })

  it('agent_return 有值但全部指数 beat=null(M=0)时展示指数空态,不渲染摘要与对比条', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...RESP,
      indices: RESP.indices.map(i => ({ ...i, return: null, effective_start_date: null, beat: null })),
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    render(<IndexCompareCard span="all" />)
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('span=all'))
    expect(await screen.findByTestId('index-compare-empty')).toBeInTheDocument()
    // M=0 专属文案(区别于 agent_return=null 的「净值数据积累中」)
    expect(screen.getByTestId('index-compare-empty').textContent).toContain('窗口内暂无指数数据,暂无法对比')
    expect(screen.queryByTestId('index-compare-summary')).not.toBeInTheDocument()
    expect(screen.queryByTestId(/^index-compare-row-/)).not.toBeInTheDocument()
  })

  it('return 有值但 effective_start_date 为 null 时不渲染「(自 起算)」标注', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...RESP,
      indices: RESP.indices.map(i => (i.code === '000001' ? { ...i, effective_start_date: null } : i)),
    }), { status: 200 })))
    render(<IndexCompareCard span="all" />)
    await screen.findByTestId('index-compare-summary')
    const row = screen.getByTestId('index-compare-row-000001')
    expect(row.textContent).toContain('3.00%')
    expect(row.textContent).not.toContain('(自')
    expect(row.textContent).not.toContain('起算')
  })

  it('fetch 失败时展示加载失败文案,不渲染摘要/空态/对比条', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('boom')))
    render(<IndexCompareCard span="6m" />)
    expect(await screen.findByText('对比数据加载失败')).toBeInTheDocument()
    expect(screen.queryByTestId('index-compare-summary')).not.toBeInTheDocument()
    expect(screen.queryByTestId('index-compare-empty')).not.toBeInTheDocument()
    expect(screen.queryByTestId(/^index-compare-row-/)).not.toBeInTheDocument()
  })

  it('HTTP 200 但响应体畸形(无 indices)时走失败态,不崩溃、不渲染摘要/空态/对比条', async () => {
    // 代理错误页等畸形 200 体:ok=true 但缺 indices → 守卫在 fetch 边界拦下,
    // 修复前 setData 直通,渲染期 data.indices.every 对 undefined 调用直接抛错炸整页
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ foo: 'bar' }) }))
    render(<IndexCompareCard span="all" />)
    expect(await screen.findByText('对比数据加载失败')).toBeInTheDocument()
    expect(screen.queryByTestId('index-compare-summary')).not.toBeInTheDocument()
    expect(screen.queryByTestId('index-compare-empty')).not.toBeInTheDocument()
    expect(screen.queryByTestId(/^index-compare-row-/)).not.toBeInTheDocument()
  })

  it('span 切换即清除旧窗口数据:新 span 拉取失败时不残留旧摘要/对比条', async () => {
    // 首次 span=all 成功返回;切换 span=3m 后拉取拒绝。
    // 修复前:effect 只重置 failed,旧 data 残留 → 失败文案与旧 span 摘要混排。
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(RESP), { status: 200 }))
      .mockRejectedValueOnce(new Error('boom'))
    vi.stubGlobal('fetch', fetchMock)
    const { rerender } = render(<IndexCompareCard span="all" />)
    expect(await screen.findByTestId('index-compare-summary')).toHaveTextContent('跑赢 3/4 个指数')
    rerender(<IndexCompareCard span="3m" />)
    expect(await screen.findByText('对比数据加载失败')).toBeInTheDocument()
    expect(screen.queryByTestId('index-compare-summary')).not.toBeInTheDocument()
    expect(screen.queryByTestId(/^index-compare-row-/)).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenLastCalledWith(expect.stringContaining('span=3m'))
  })

  // ── add-track-record-caliber-disclosure:组合构成必须在卡片内可见 ──

  it('渲染口径披露:观望不计入组合、交易日数、多空条数、期内已结算', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...RESP,
      caliber: { nav_points: 20, directional_views: 27, long_views: 0, short_views: 27, settled_count: 5 },
    }), { status: 200 })))
    render(<IndexCompareCard span="all" />)
    const caliber = await screen.findByTestId('index-compare-caliber')
    expect(caliber.textContent).toContain('仅统计买入/卖出观点')
    expect(caliber.textContent).toContain('观望/持有不计入组合仓位')
    expect(caliber.textContent).toContain('20 个交易日')
    expect(caliber.textContent).toContain('累计多 0')
    expect(caliber.textContent).toContain('空 27')
    expect(caliber.textContent).toContain('期内已结算 5 笔')
    // B1 标注：空头是评分语义，不是可执行仓位（A 股该路径不建模做空）
    expect(caliber.textContent).toContain('评分语义')
  })

  it('响应缺 caliber(旧后端)时不渲染披露行,其余内容照常', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(RESP), { status: 200 })))
    render(<IndexCompareCard span="all" />)
    await screen.findByTestId('index-compare-summary')
    expect(screen.queryByTestId('index-compare-caliber')).not.toBeInTheDocument()
    // 既有内容零回归
    expect(screen.getByTestId('index-compare-summary').textContent).toContain('跑赢 3/4 个指数')
  })

  it('净值不足(agent_return=null)时不渲染披露行', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...RESP,
      agent_return: null,
      caliber: { nav_points: 1, directional_views: 0, long_views: 0, short_views: 0, settled_count: 0 },
    }), { status: 200 })))
    render(<IndexCompareCard span="all" />)
    expect(await screen.findByTestId('index-compare-empty')).toBeInTheDocument()
    expect(screen.queryByTestId('index-compare-caliber')).not.toBeInTheDocument()
  })
})
