import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { RoeChart, StockPriceChart } from '../Charts'
import type { ChartData } from '../types'

// 复现 bug：ROE 图 markLine.data 混入裸 lineStyle 项（无 yAxis/xAxis/coord），
// ECharts 渲染时读 coord of undefined → "Cannot read properties of undefined
// (reading 'coord')"（MarkLineView.js:132 Uncaught in promise），ROE 图崩溃。
// 不变量：markLine.data 每项必须带坐标字段（yAxis/xAxis/coord）。

const captured: unknown[] = []
vi.mock('echarts-for-react', () => ({
  default: ({ option }: { option: unknown }) => {
    captured.push(option)
    return null
  },
}))

const baseData: ChartData = {
  stock_code: '600519',
  stock_name: '贵州茅台',
  annual: [
    { year: '2021', revenue: 1, profit: 1, roe: 30 },
    { year: '2022', revenue: 1, profit: 1, roe: 28 },
    { year: '2023', revenue: 1, profit: 1, roe: 31 },
  ] as never,
  growth: { years: [], revenue_growth: [], profit_growth: [] },
  price: {
    daily: Array.from({ length: 12 }, (_, i) => ({
      date: `2026-08-${String(i + 1).padStart(2, '0')}`,
      close: 1700 + i,
    })),
    earnings_dates: ['2026-08-01'],
  },
  kpi: {},
  market_share: null,
}

function markLineDataItems(option: any): any[] {
  const series = option?.series
  const arr = Array.isArray(series) ? series : [series]
  const items: any[] = []
  for (const s of arr) {
    const data = s?.markLine?.data
    if (Array.isArray(data)) items.push(...data)
  }
  return items
}

describe('Charts markLine 不变量：data 每项必须带坐标字段', () => {
  beforeEach(() => captured.length = 0)

  it('RoeChart 的 markLine.data 不含裸 lineStyle 项', () => {
    render(<RoeChart data={baseData} />)
    const opt = captured[captured.length - 1] as any
    const items = markLineDataItems(opt)
    expect(items.length).toBeGreaterThan(0)
    for (const item of items) {
      const hasCoord =
        item && typeof item === 'object' &&
        ('yAxis' in item || 'xAxis' in item || 'coord' in item ||
         (Array.isArray(item) && item.length === 2))
      expect(hasCoord, `markLine.data 项缺少坐标字段: ${JSON.stringify(item)}`).toBe(true)
    }
  })

  it('StockPriceChart 的 markLine.data 项均带 xAxis（报告期截止日标注）', () => {
    render(<StockPriceChart data={baseData} />)
    const opt = captured[captured.length - 1] as any
    const items = markLineDataItems(opt)
    for (const item of items) {
      expect(item && 'xAxis' in item).toBe(true)
    }
  })
})
