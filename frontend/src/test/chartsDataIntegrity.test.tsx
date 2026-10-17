/**
 * chart-data-integrity 前端呈现层契约（issue #336：单位错配全量标的复现）。
 *
 * 主规范 openspec/specs/chart-data-integrity/spec.md 三条 requirement 在前端
 * ECharts 层此前完全未落实（add-chart-data-integrity 只覆盖后端 matplotlib 路径）：
 * 1.「图表金额单位一致」：亿元轴标的序列 SHALL 以亿元渲染（原始元 / 1e8），
 *    MUST NOT 出现原始元数值配「亿元」轴标——本文件用 601818 会话 4a76bc58
 *    真实载荷量级（126311000000.0 等）钉死；
 * 2.「年度图表时间轴升序呈现」：数据层 index 0 = 最新（降序）契约不变，
 *    呈现层 SHALL 反转（最旧在左）；
 * 3.「图表缺数据禁止填 0 渲染」：柱图缺失年 SHALL 断柱（NaN），MUST NOT 画
 *    0 高度柱冒充真实值。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  RevenueProfitChart,
  CashflowChart,
  AssetsChart,
  ContractLiabChart,
  GrowthChart,
  MarginChart,
  RoeChart,
  DebtRatioChart,
  GrowthVsPriceChart,
  HeatmapChart,
  KpiCards,
} from '../Charts'
import type { ChartData } from '../types'

const captured: unknown[] = []
vi.mock('echarts-for-react', () => ({
  default: ({ option }: { option: unknown }) => {
    captured.push(option)
    return null
  },
}))

// ── 生产实况样本（降序：index 0 = 最新，与 data 契约一致）──
const annual = [
  {
    year: '2025', revenue: 126311000000.0, net_profit: 38826000000.0,
    gross_margin: null, net_margin: null, roe: 7.0, roa: null,
    ocf: 162907000000.0, total_assets: 7165319000000.0, equity: 604800000000.0,
    contract_liab: 12340000000.0, cip: null, debt_ratio: 91.5,
  },
  {
    year: '2024', revenue: 135415000000.0, net_profit: 41696000000.0,
    gross_margin: null, net_margin: null, roe: 8.1, roa: null,
    ocf: -204802000000.0, total_assets: 6959021000000.0, equity: 587700000000.0,
    contract_liab: 11000000000.0, cip: null, debt_ratio: 92.1,
  },
  {
    year: '2023', revenue: 145685000000.0, net_profit: 40792000000.0,
    gross_margin: null, net_margin: null, roe: 8.4, roa: null,
    ocf: -3614000000.0, total_assets: 6772796000000.0, equity: 552391000000.0,
    contract_liab: 12000000000.0, cip: null, debt_ratio: 92.8,
  },
]

const baseData: ChartData = {
  stock_code: '601818',
  stock_name: '光大银行',
  annual: annual as never,
  growth: {
    years: ['2025', '2024', '2023'],
    revenue_growth: [1.2, 2.3, 3.4],
    profit_growth: [1.1, 2.2, 3.3],
  },
  price: {
    daily: Array.from({ length: 40 }, (_, i) => ({
      date: `2026-08-${String((i % 28) + 1).padStart(2, '0')}`,
      close: 3 + i * 0.01,
    })),
    earnings_dates: ['2026-08-01', '2025-08-01'],
  },
  kpi: { market_cap: 183165000000.0, current_price: 3.1 },
  market_share: null,
}

function lastOption(): any {
  return captured[captured.length - 1]
}

function seriesByName(opt: any, name: string): any {
  const arr = Array.isArray(opt?.series) ? opt.series : [opt?.series]
  return arr.find((s: any) => s?.name === name)
}

beforeEach(() => {
  captured.length = 0
})

describe('图表金额单位一致（元 → 亿元，spec Scenario 现金流图数值为亿元）', () => {
  it('现金流图 162907000000.0 元 → 1629.07 亿元，轴标保持「亿元」', () => {
    render(<CashflowChart data={baseData} />)
    const opt = lastOption()
    const series = opt.series[0]
    // 升序呈现后 2025 在末位
    expect(series.data[series.data.length - 1]).toBeCloseTo(1629.07, 2)
    expect(opt.yAxis.name).toBe('亿元')
  })

  it('营收净利图以亿元渲染（营收 1263.11 / 净利 388.26）且升序，横轴为年份', () => {
    render(<RevenueProfitChart data={baseData} />)
    const opt = lastOption()
    const revenue = seriesByName(opt, '营业收入')
    const profit = seriesByName(opt, '归母净利润')
    expect(revenue.data[revenue.data.length - 1]).toBeCloseTo(1263.11, 2)
    expect(profit.data[profit.data.length - 1]).toBeCloseTo(388.26, 2)
    expect(opt.xAxis.data).toEqual(['2023', '2024', '2025'])
  })

  it('总资产与归母权益图以亿元渲染且升序', () => {
    render(<AssetsChart data={baseData} />)
    const opt = lastOption()
    const assets = seriesByName(opt, '总资产')
    const equity = seriesByName(opt, '归母权益')
    expect(assets.data[assets.data.length - 1]).toBeCloseTo(71653.19, 2)
    expect(equity.data[equity.data.length - 1]).toBeCloseTo(6048.0, 2)
    expect(opt.xAxis.data).toEqual(['2023', '2024', '2025'])
  })

  it('合同负债图以亿元渲染（123.4）', () => {
    render(<ContractLiabChart data={baseData} />)
    const opt = lastOption()
    expect(opt.series[0].data[opt.series[0].data.length - 1]).toBeCloseTo(123.4, 2)
  })

  it('KPI 卡营收/净利润以亿元呈现（1263.1亿 / 388.3亿）', () => {
    render(<KpiCards data={baseData} />)
    expect(screen.getByText('1263.1亿')).toBeTruthy()
    expect(screen.getByText('388.3亿')).toBeTruthy()
    expect(screen.queryByText(/126311000000\.0亿/)).toBeNull()
  })
})

describe('年度图表时间轴升序呈现（数据降序 → 呈现反转）', () => {
  it('增速图横轴与序列升序（最旧在左）', () => {
    render(<GrowthChart data={baseData} />)
    const opt = lastOption()
    expect(opt.xAxis.data).toEqual(['2023', '2024', '2025'])
    expect(seriesByName(opt, '营收增速').data).toEqual([3.4, 2.3, 1.2])
  })

  it('利润率 / ROE / 资产负债率图序列升序', () => {
    render(<MarginChart data={baseData} />)
    const margin = lastOption()
    expect(margin.xAxis.data).toEqual(['2023', '2024', '2025'])

    captured.length = 0
    render(<RoeChart data={baseData} />)
    const roe = lastOption()
    expect(roe.series[0].data).toEqual([8.4, 8.1, 7.0])

    captured.length = 0
    render(<DebtRatioChart data={baseData} />)
    const debt = lastOption()
    expect(debt.series[0].data).toEqual([92.8, 92.1, 91.5])
  })

  it('增速 vs 股价图三序列同向升序', () => {
    render(<GrowthVsPriceChart data={baseData} />)
    const opt = lastOption()
    expect(seriesByName(opt, '营收增速').data).toEqual([3.4, 2.3, 1.2])
    const prices = seriesByName(opt, '股价涨幅').data
    expect(prices.length).toBe(3)
  })

  it('热力图年报行升序（最旧在上）', () => {
    render(<HeatmapChart data={baseData} />)
    const opt = lastOption()
    expect(opt.yAxis.data).toEqual(['2025年报', '2026年报'])
  })
})

describe('图表缺数据禁止填 0（柱图缺失年断柱）', () => {
  it('净利缺失年断柱（NaN 而非 0 高度柱）', () => {
    const withGap: ChartData = {
      ...baseData,
      annual: annual.map(a => (a.year === '2024' ? { ...a, net_profit: null } : a)) as never,
    }
    render(<RevenueProfitChart data={withGap} />)
    const opt = lastOption()
    const profit = seriesByName(opt, '归母净利润').data
    // 升序：[2023, 2024, 2025] → 2024 在 index 1
    expect(Number.isNaN(profit[1])).toBe(true)
    expect(profit[1]).not.toBe(0)
  })

  it('现金流缺失年断柱', () => {
    const withGap: ChartData = {
      ...baseData,
      annual: annual.map(a => (a.year === '2023' ? { ...a, ocf: null } : a)) as never,
    }
    render(<CashflowChart data={withGap} />)
    const opt = lastOption()
    expect(Number.isNaN(opt.series[0].data[0])).toBe(true)
  })
})
