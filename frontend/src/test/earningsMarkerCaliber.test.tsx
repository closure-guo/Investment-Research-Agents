import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StockPriceChart, HeatmapChart } from '../Charts'
import type { ChartData } from '../types'

// fix-earnings-marker-caliber（issue #243 子项3）：earnings_dates 数据源是
// 利润表「报告日」= 报告期截止日，非披露日。卡片标题措辞必须如实表述
// 「报告期截止」，禁止「发布日」。

vi.mock('echarts-for-react', () => ({
  default: () => null,
}))

function makeDaily(n: number): ChartData['price']['daily'] {
  return Array.from({ length: n }, (_, i) => ({
    date: `2026-01-${String(i + 1).padStart(2, '0')}`,
    close: 10 + i * 0.1,
  }))
}

function baseData(): ChartData {
  return {
    stock_code: '601818',
    stock_name: '光大银行',
    annual: [],
    growth: { years: [], revenue_growth: [], profit_growth: [] },
    price: {
      daily: makeDaily(12),
      earnings_dates: ['2026-01-03'],
      ma: undefined,
      decision_levels: undefined,
    },
    kpi: {},
    market_share: null,
  } as ChartData
}

describe('报告期截止日措辞（fix-earnings-marker-caliber）', () => {
  it('折线卡片标题表述为报告期截止日而非发布日', () => {
    render(<StockPriceChart data={baseData()} />)
    expect(screen.getByText(/报告期截止日/)).toBeInTheDocument()
    expect(screen.queryByText(/发布日/)).not.toBeInTheDocument()
  })

  it('热力图卡片标题表述为报告期窗口而非发布窗口', () => {
    const data = baseData()
    data.price.daily = makeDaily(40)
    data.price.earnings_dates = ['2026-01-03', '2026-01-20']
    render(<HeatmapChart data={data} />)
    expect(screen.getByText(/年报报告期窗口股价变化/)).toBeInTheDocument()
    expect(screen.queryByText(/发布窗口/)).not.toBeInTheDocument()
  })
})
