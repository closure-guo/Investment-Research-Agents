import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { StockPriceChart } from '../Charts'
import type { ChartData } from '../types'

// update-price-chart-kline：股价图 K 线双分支契约。
// OHLC 在场 → candlestick（数据序 [open,close,low,high]）+ 成交量副图 + MA 叠加
// + 决策价位 markLine（yAxis 坐标）；仅收盘价（历史会话）→ 收盘折线降级，不报错。

const captured: unknown[] = []
vi.mock('echarts-for-react', () => ({
  default: ({ option }: { option: unknown }) => {
    captured.push(option)
    return null
  },
}))

type DailyEntry = ChartData['price']['daily'][number]

function makeDaily(n: number, withOHLC: boolean): DailyEntry[] {
  return Array.from({ length: n }, (_, i) => {
    const close = 10 + i * 0.1
    const entry: DailyEntry = {
      date: `2026-01-${String(i + 1).padStart(2, '0')}`,
      close,
    }
    if (withOHLC) {
      entry.open = close - 0.05
      entry.high = close + 0.2
      entry.low = close - 0.2
      entry.volume = 1000 + i
    }
    return entry
  })
}

function baseData(withOHLC: boolean): ChartData {
  return {
    stock_code: '600519',
    stock_name: '贵州茅台',
    annual: [],
    growth: { years: [], revenue_growth: [], profit_growth: [] },
    price: {
      daily: makeDaily(12, withOHLC),
      earnings_dates: ['2026-01-03'],
      ma: withOHLC
        ? { ma5: Array.from({ length: 12 }, (_, i) => (i >= 4 ? 10.1 : null)), ma20: [], ma60: [] }
        : undefined,
      decision_levels: withOHLC
        ? { entry_price: 11.2, stop_loss: 9.9, target_price: 12.5 }
        : undefined,
    },
    kpi: {},
    market_share: null,
  } as ChartData
}

function seriesOf(opt: any): any[] {
  return Array.isArray(opt?.series) ? opt.series : [opt?.series]
}

describe('StockPriceChart K 线分支', () => {
  beforeEach(() => {
    captured.length = 0
  })

  it('OHLC 在场渲染 candlestick + 成交量副图 + MA 叠加 + 决策价位 markLine', () => {
    render(<StockPriceChart data={baseData(true)} />)
    const opt: any = captured[captured.length - 1]
    const candle = seriesOf(opt).find((s) => s?.type === 'candlestick')
    expect(candle).toBeDefined()
    // ECharts 蜡烛数据序 [open, close, low, high]
    expect(candle.data[0]).toEqual([9.95, 10, 9.8, 10.2])
    expect(seriesOf(opt).some((s) => s?.name === '成交量' && s?.type === 'bar')).toBe(true)
    expect(seriesOf(opt).filter((s) => typeof s?.name === 'string' && s.name.startsWith('MA')).length).toBe(3)
    const mlItems: any[] = candle.markLine.data
    expect(mlItems.filter((i) => 'xAxis' in i).length).toBe(1) // 报告期截止日
    expect(mlItems.filter((i) => i?.yAxis === 11.2).length).toBe(1) // 入场
    expect(mlItems.filter((i) => i?.yAxis === 9.9).length).toBe(1) // 止损
    expect(mlItems.filter((i) => i?.yAxis === 12.5).length).toBe(1) // 目标
  })

  it('仅收盘价（历史会话）降级为收盘折线，无 candlestick', () => {
    render(<StockPriceChart data={baseData(false)} />)
    const opt: any = captured[captured.length - 1]
    expect(seriesOf(opt).some((s) => s?.type === 'candlestick')).toBe(false)
    expect(seriesOf(opt)[0]?.type).toBe('line')
  })

  it('OHLC 半缺（open 缺失）按降级处理', () => {
    const d = baseData(true)
    ;(d.price.daily as any[]).forEach((e) => delete e.open)
    render(<StockPriceChart data={d} />)
    const opt: any = captured[captured.length - 1]
    expect(seriesOf(opt).some((s) => s?.type === 'candlestick')).toBe(false)
  })

  it('决策价位半缺只画在场价位', () => {
    const d = baseData(true)
    d.price.decision_levels = { entry_price: 11.2 }
    render(<StockPriceChart data={d} />)
    const opt: any = captured[captured.length - 1]
    const candle = seriesOf(opt).find((s) => s?.type === 'candlestick')
    const yItems: any[] = candle.markLine.data.filter((i: any) => 'yAxis' in i)
    expect(yItems.length).toBe(1)
    expect(yItems[0].yAxis).toBe(11.2)
  })

  // add-watch-trigger-tracking：watch 决策触发位（trigger_high/trigger_low）→
  // 「上破触发」「下破触发」点线 markLine（与入场/止损/目标虚线可区分，配色对齐后端 PNG）。
  it('watch 触发位渲染为上破/下破触发点线 markLine', () => {
    const d = baseData(true)
    d.price.decision_levels = { ...baseData(true).price.decision_levels!, trigger_high: 11.8, trigger_low: 9.5 }
    render(<StockPriceChart data={d} />)
    const opt: any = captured[captured.length - 1]
    const candle = seriesOf(opt).find((s) => s?.type === 'candlestick')
    const triggers: any[] = candle.markLine.data.filter((i: any) =>
      ['上破触发', '下破触发'].includes(i?.label?.formatter),
    )
    expect(triggers).toHaveLength(2)
    const high = triggers.find((t) => t.label.formatter === '上破触发')
    const low = triggers.find((t) => t.label.formatter === '下破触发')
    expect(high.yAxis).toBe(11.8)
    expect(low.yAxis).toBe(9.5)
    // 与入场/止损/目标参考线可区分：点线（对齐后端 PNG 渲染）
    for (const t of triggers) expect(t.lineStyle?.type).toBe('dotted')
    expect(high.lineStyle?.color).toBe('#E67E22')
    expect(low.lineStyle?.color).toBe('#8E44AD')
  })

  it('无触发位字段时不渲染触发 markLine', () => {
    render(<StockPriceChart data={baseData(true)} />)
    const opt: any = captured[captured.length - 1]
    const candle = seriesOf(opt).find((s) => s?.type === 'candlestick')
    const triggers: any[] = candle.markLine.data.filter((i: any) =>
      ['上破触发', '下破触发'].includes(i?.label?.formatter),
    )
    expect(triggers).toHaveLength(0)
  })
})

describe('K 线 tooltip formatter（开高低收/涨跌幅/成交量/MA）', () => {
  beforeEach(() => {
    captured.length = 0
  })

  // 构造 axis 触发的 params：candlestick 项 + 3 个 MA 项 + 成交量项（data 为 { value } 对象）。
  // idx=5 → 2026-01-06：open 10.45 / close 10.5 / low 10.3 / high 10.7 / volume 1005。
  function buildParams(idx: number, candleData?: (number | null)[]): unknown[] {
    const d = makeDaily(12, true)[idx]
    return [
      { seriesName: 'K线', seriesType: 'candlestick', axisValue: d.date, data: candleData ?? [d.open, d.close, d.low, d.high] },
      { seriesName: 'MA5', seriesType: 'line', axisValue: d.date, data: 10.1 },
      { seriesName: 'MA20', seriesType: 'line', axisValue: d.date, data: null },
      { seriesName: 'MA60', seriesType: 'line', axisValue: d.date, data: null },
      { seriesName: '成交量', seriesType: 'bar', axisValue: d.date, data: { value: d.volume, itemStyle: {} } },
    ]
  }

  function formatterOf(): (p: unknown) => string {
    render(<StockPriceChart data={baseData(true)} />)
    const opt: any = captured[captured.length - 1]
    expect(typeof opt.tooltip.formatter).toBe('function')
    return opt.tooltip.formatter
  }

  it('呈现开/高/低/收、涨跌幅、成交量与 MA 值（MA 缺失显示 --）', () => {
    const fmt = formatterOf()
    const out = fmt(buildParams(5))
    expect(out).toContain('开 10.45')
    expect(out).toContain('高 10.70')
    expect(out).toContain('低 10.30')
    expect(out).toContain('收 10.50')
    expect(out).toContain('+0.5%') // (10.5-10.45)/10.45 ≈ +0.478% → 一位小数
    expect(out).toContain('成交量 1005')
    expect(out).toContain('MA5 10.10') // ma5[5] = 10.1（组件闭包按日期下标取值）
    expect(out).toContain('MA20 --') // ma20 序列为空 → '--'
    expect(out).toContain('MA60 --')
  })

  it('open 为 0 或 null 时涨跌幅显示 --', () => {
    const fmt = formatterOf()
    expect(fmt(buildParams(5, [0, 10.5, 10.3, 10.7]))).toContain('涨跌幅 --')
    expect(fmt(buildParams(5, [null, 10.5, 10.3, 10.7]))).toContain('涨跌幅 --')
  })
})
