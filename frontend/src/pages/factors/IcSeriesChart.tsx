import ReactECharts from 'echarts-for-react'
import { getChartTheme } from '../../Charts'
import type { FactorIcPoint } from '../../types'

/**
 * 因子 IC 折线（delta add-factor-ic-visibility）。
 *
 * 横轴 = 交易日（升序），纵轴 = RankIC；空序列如实展示空态，MUST NOT 渲染伪点。
 * 数据由详情接口的 `ic_series` 提供（后端最多近 60 点）。
 */
export function IcSeriesChart({ points }: { points: FactorIcPoint[] }) {
  if (points.length === 0) {
    return (
      <div data-testid="factor-ic-empty" className="text-muted-foreground">
        暂无 IC 记录（随衰减监控日批落库）
      </div>
    )
  }
  const theme = getChartTheme()
  const option = {
    grid: { left: 40, right: 12, top: 16, bottom: 24 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBorder,
      textStyle: { color: theme.tooltipTextColor, fontSize: 11 },
      valueFormatter: (value: number) => Number(value).toFixed(4),
    },
    xAxis: {
      type: 'category',
      data: points.map(point => point.trade_date),
      axisLabel: { color: theme.axisLabelColor, fontSize: 10 },
      axisLine: { lineStyle: { color: theme.splitLine } },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: theme.axisLabelColor,
        fontSize: 10,
        formatter: (value: number) => Number(value).toFixed(2),
      },
      splitLine: { lineStyle: { color: theme.splitLine } },
    },
    series: [
      {
        name: 'RankIC',
        type: 'line',
        data: points.map(point => point.rank_ic),
        showSymbol: points.length <= 20,
        symbolSize: 4,
        lineStyle: { width: 1.5, color: theme.brand },
        itemStyle: { color: theme.brand },
        areaStyle: { color: theme.brand, opacity: 0.08 },
      },
    ],
  }
  return (
    <div data-testid="factor-ic-chart">
      <ReactECharts option={option} style={{ height: '180px' }} />
    </div>
  )
}
