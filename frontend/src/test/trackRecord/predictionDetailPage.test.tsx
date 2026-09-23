import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PredictionDetailPage } from '../../pages/trackRecord/PredictionDetailPage'

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
  Toaster: () => null,
}))

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="mock-chart" />,
}))

const DETAIL = {
  prediction: {
    prediction_id: 'p1', source_type: 'live', symbol: '600519.SH', symbol_name: '贵州茅台',
    direction: 'long', entry_price: 100, target_price: 120, horizon_days: 252,
    confidence: 0.8, benchmark: '000300.SH', rationale_snapshot: '{"decision":"buy","markdown":"原文快照"}',
    langfuse_trace_id: null, status: 'resolved_win', created_at: '2026-09-01T10:00:00',
    resolved_at: '2026-09-02', exit_price: 115, raw_return: 0.15, excess_return: 0.1,
    resolution_rule: 'expiry', updated_at: 'x', version_seq: null, snapshot_hash: 'abc',
  },
  audit: [
    { log_id: 1, prediction_id: 'p1', action: 'status_change', old_status: 'open',
      new_status: 'resolved_win', detail: 'expiry', source: 'system', created_at: '2026-09-02T16:00:00' },
  ],
  marks: [
    { mark_id: 'm1', prediction_id: 'p1', mark_date: '2026-09-01', mark_price: 101, cum_return: 0.01, cum_excess: -0.02, benchmark_price: 3100 },
    { mark_id: 'm2', prediction_id: 'p1', mark_date: '2026-09-02', mark_price: 115, cum_return: 0.15, cum_excess: 0.1, benchmark_price: 3200 },
  ],
  as_of: '2026-09-04',
  disclaimer: '历史业绩不代表未来表现',
}

function mockFetch(payload: unknown = DETAIL) {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }))))
}

function renderPage() {
  return render(<PredictionDetailPage predictionId="p1" onBack={vi.fn()} />)
}

describe('观点详情页（add-track-record-stage-c）', () => {
  beforeEach(() => vi.spyOn(window, 'scrollTo').mockImplementation(() => {}))
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('渲染判定卡字段', async () => {
    mockFetch()
    renderPage()
    await screen.findByTestId('prediction-decision')
    expect(screen.getByTestId('prediction-decision').textContent).toContain('命中')
    expect(screen.getByTestId('prediction-decision').textContent).toContain('0.80')
    expect(screen.getByTestId('prediction-decision').textContent).toContain('+15.00%')
  })

  it('渲染叠加图/快照/审计时间轴', async () => {
    mockFetch()
    renderPage()
    await screen.findByTestId('prediction-decision')
    expect(screen.getByTestId('prediction-overlay')).toBeInTheDocument()
    expect(screen.getByTestId('prediction-snapshot').textContent).toContain('原文快照')
    const audit = screen.getByTestId('prediction-audit')
    expect(audit.textContent).toContain('status_change')
    expect(audit.textContent).toContain('open → resolved_win')
  })

  it('盯市序列不足时展示空态而非曲线', async () => {
    mockFetch({ ...DETAIL, marks: [] })
    renderPage()
    await screen.findByTestId('prediction-decision')
    expect(screen.getByText(/暂无盯市序列/)).toBeInTheDocument()
    expect(screen.queryByTestId('prediction-overlay')).not.toHaveClass('mock-chart')
  })

  it('免责声明常驻', async () => {
    mockFetch()
    renderPage()
    await screen.findByTestId('prediction-decision')
    expect(screen.getByTestId('prediction-detail-disclaimer')).toBeInTheDocument()
  })

  it('avoidance 终态渲染「回避」标签而非原始状态码', async () => {
    mockFetch({
      ...DETAIL,
      prediction: { ...DETAIL.prediction, status: 'avoidance', direction: 'neutral', resolved_at: '2026-09-30' },
    })
    renderPage()
    const card = await screen.findByTestId('prediction-decision')
    expect(card.textContent).toContain('回避')
    expect(card.textContent).not.toContain('avoidance')
  })

  it('404 显示错误态', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('', { status: 404 }))))
    renderPage()
    expect(await screen.findByText(/数据加载失败/)).toBeInTheDocument()
  })
})