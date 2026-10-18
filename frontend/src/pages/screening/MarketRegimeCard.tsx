import { useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { getChartTheme } from '../../Charts'
import type {
  RegimeAvailablePayload,
  RegimeHistoryRow,
  RegimeIndicatorValue,
  RegimeIndicators,
  RegimePayload,
  RegimeState,
} from '../../types'

/**
 * 选股页市场温度计（delta add-crowding-regime-positioning T5；spec frontend
 * 「选股页市场温度计区块」）。
 *
 * - 取数 ``GET /api/regime``（组件内 fetch + alive 清理，同 IndexCompareCard）：
 *   有记录 → 三态徽章（语义色）/ 总仓位系数 / 四指标（当日值 + 历史分位）/
 *   近一年触发指标分位曲线（history 升序）/ reasons 一行；
 * - ``available=false`` → 「温度计未接入」空态，按 missing/corrupt 区分文案，
 *   MUST NOT 用骨架屏或错误提示冒充；
 * - ``stale=true`` → 明示「数据已陈旧」+ as_of，内容照常可见；
 * - 只读：不渲染任何可切换状态或覆盖系数的控件（v1 无手动覆盖入口）。
 *
 * 三态徽章为组件内本地实现（StatusKey 不含 normal/elevated/danger，不扩展设计令牌）。
 */

const STATE_META: Record<RegimeState, { label: string; color: string }> = {
  normal: { label: '正常', color: 'var(--status-success-default)' },
  elevated: { label: '偏热', color: 'var(--status-warning-default)' },
  danger: { label: '过热', color: 'var(--status-error-default)' },
}

const EMPTY_TEXT: Record<'missing' | 'corrupt', string> = {
  missing: '温度计未接入：暂无市场状态数据（等待日批采集）',
  corrupt: '温度计未接入：市场状态数据损坏（请检查 regime 库或配置）',
}

interface MetricCard {
  key: string
  label: string
  value: string
  pctile: string
}

function formatRatio(value: number | null): string {
  return value === null || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(1)}%`
}

function formatCount(value: number | null): string {
  return value === null || !Number.isFinite(value) ? '—' : String(Math.round(value))
}

function formatAmount(value: number | null): string {
  return value === null || !Number.isFinite(value) ? '—' : `${(value / 1e8).toFixed(2)} 亿元`
}

function formatPctile(value: number | null): string {
  return value === null || !Number.isFinite(value) ? '—' : `${Math.round(value * 100)}%`
}

function formatMultiplier(value: number): string {
  return Number.isFinite(value) ? String(Number(value.toFixed(2))) : '—'
}

function indicatorMetrics(indicators: RegimeIndicators): MetricCard[] {
  const smallCap: RegimeIndicatorValue = indicators.small_cap_share
  const up = indicators.up_count
  const down = indicators.down_count
  const limitUp = indicators.limit_up_count
  const limitDown = indicators.limit_down_count
  const amount = indicators.total_amount
  return [
    {
      key: 'small_cap_share',
      label: '小市值组成交额占比',
      value: formatRatio(smallCap.value),
      pctile: `分位 ${formatPctile(smallCap.pctile)}`,
    },
    {
      key: 'breadth',
      label: '涨跌家数',
      value: `↑ ${formatCount(up.value)} / ↓ ${formatCount(down.value)}`,
      pctile: `涨分位 ${formatPctile(up.pctile)} / 跌分位 ${formatPctile(down.pctile)}`,
    },
    {
      key: 'limit',
      label: '涨停跌停家数',
      value: `涨停 ${formatCount(limitUp.value)} / 跌停 ${formatCount(limitDown.value)}`,
      pctile: `涨停分位 ${formatPctile(limitUp.pctile)} / 跌停分位 ${formatPctile(limitDown.pctile)}`,
    },
    {
      key: 'amount',
      label: '全市场成交额',
      value: formatAmount(amount.value),
      pctile: `分位 ${formatPctile(amount.pctile)}`,
    },
  ]
}

function buildChartOption(history: RegimeHistoryRow[]) {
  const theme = getChartTheme()
  return {
    textStyle: { color: theme.textColor, fontSize: 10 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBorder,
      textStyle: { color: theme.tooltipTextColor },
    },
    grid: { left: 40, right: 16, top: 24, bottom: 24 },
    xAxis: {
      type: 'category',
      data: history.map(row => row.trade_date),
      axisLabel: { color: theme.axisLabelColor, fontSize: 9 },
      axisLine: { lineStyle: { color: theme.splitLine } },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 1,
      axisLabel: {
        color: theme.axisLabelColor,
        fontSize: 9,
        formatter: (value: number) => `${Math.round(value * 100)}%`,
      },
      splitLine: { lineStyle: { color: theme.splitLine } },
    },
    series: [
      {
        name: '小市值占比分位',
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        data: history.map(row => row.small_cap_share_pctile),
        lineStyle: { color: theme.brand, width: 1.5 },
        itemStyle: { color: theme.brand },
      },
      {
        name: '成交额分位',
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        data: history.map(row => row.total_amount_pctile),
        lineStyle: { color: theme.coral, width: 1.5 },
        itemStyle: { color: theme.coral },
      },
    ],
  }
}

// 载荷必需指标键（与 payload.py::_PAYLOAD_METRIC_KEYS 同口径）
const INDICATOR_KEYS = [
  'small_cap_share',
  'up_count',
  'down_count',
  'limit_up_count',
  'limit_down_count',
  'total_amount',
] as const

function isNullableNumber(value: unknown): boolean {
  return value === null || typeof value === 'number'
}

function isIndicatorValue(value: unknown): boolean {
  if (typeof value !== 'object' || value === null) return false
  const block = value as { value?: unknown; pctile?: unknown }
  return isNullableNumber(block.value) && isNullableNumber(block.pctile)
}

function isValidAvailablePayload(payload: RegimeAvailablePayload): boolean {
  const indicators = payload.indicators as unknown as Record<string, unknown> | null
  return (
    typeof payload.as_of === 'string' &&
    typeof payload.multiplier === 'number' &&
    typeof payload.age_days === 'number' &&
    Array.isArray(payload.reasons) &&
    payload.reasons.every(reason => typeof reason === 'string') &&
    Array.isArray(payload.history) &&
    payload.history.every(row => {
      if (typeof row !== 'object' || row === null) return false
      const item = row as unknown as Record<string, unknown>
      return (
        typeof item.trade_date === 'string' &&
        isNullableNumber(item.small_cap_share_pctile) &&
        isNullableNumber(item.total_amount_pctile)
      )
    }) &&
    typeof indicators === 'object' &&
    indicators !== null &&
    INDICATOR_KEYS.every(key => isIndicatorValue(indicators[key])) &&
    (payload.state === 'normal' || payload.state === 'elevated' || payload.state === 'danger') &&
    typeof payload.stale === 'boolean'
  )
}

export function MarketRegimeCard() {
  // null = 加载中；failed = 取数/载荷畸形（与 available=false 的「未接入」空态严格区分）
  const [data, setData] = useState<RegimePayload | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    fetch('/api/regime')
      .then(resp => (resp.ok ? resp.json() : Promise.reject(new Error(String(resp.status)))))
      .then((payload: RegimePayload) => {
        if (!alive) return
        // 畸形 200 体守卫：缺 available / 有记录但字段不全 → 失败态，不炸整页
        if (!payload || typeof payload.available !== 'boolean') {
          setFailed(true)
          return
        }
        if (payload.available && !isValidAvailablePayload(payload)) {
          setFailed(true)
          return
        }
        setData(payload)
      })
      .catch(() => {
        if (alive) setFailed(true)
      })
    return () => {
      alive = false
    }
  }, [])

  const available = data?.available === true ? data : null
  const stateMeta = available ? STATE_META[available.state] : null
  const metrics = available ? indicatorMetrics(available.indicators) : []
  const hasHistory = available !== null && available.history.length > 0
  const emptyText =
    data && !data.available ? EMPTY_TEXT[data.reason] ?? EMPTY_TEXT.missing : null

  return (
    <div
      data-testid="market-regime-card"
      className="rounded-lg border p-4"
      style={{ borderColor: 'var(--border-neutral-l1)' }}
    >
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <i
            className="fas fa-temperature-half text-sm"
            style={{ color: 'var(--primary)' }}
            aria-hidden="true"
          ></i>
          <span className="text-sm font-medium text-foreground">市场温度计</span>
        </div>
        {available && stateMeta && (
          <span
            data-testid="regime-state-badge"
            data-state={available.state}
            className="rounded-full border px-2 py-0.5 text-[10px] font-medium"
            style={{ color: stateMeta.color, borderColor: stateMeta.color }}
          >
            {stateMeta.label}
          </span>
        )}
      </div>

      {failed && (
        <div data-testid="regime-error" className="text-xs" style={{ color: 'var(--status-error-default)' }}>
          市场温度计加载失败
        </div>
      )}

      {!failed && data === null && (
        <div data-testid="regime-loading" className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
          加载中…
        </div>
      )}

      {!failed && emptyText !== null && (
        <div data-testid="regime-empty" className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
          {emptyText}
        </div>
      )}

      {!failed && available && (
        <>
          {available.stale && (
            <div
              data-testid="regime-stale-notice"
              className="mb-2 rounded border px-2 py-1 text-xs"
              style={{
                borderColor: 'var(--status-warning-default)',
                color: 'var(--status-warning-default)',
              }}
            >
              数据已陈旧：as_of {available.as_of}（距今 {available.age_days} 天）
            </div>
          )}

          <div className="mb-3 flex items-center gap-3">
            <span data-testid="regime-multiplier" className="text-sm font-semibold text-foreground">
              总仓位系数 {formatMultiplier(available.multiplier)}
            </span>
            <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              as_of {available.as_of}
            </span>
          </div>

          <div data-testid="regime-metrics" className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {metrics.map(metric => (
              <div
                key={metric.key}
                data-testid={`regime-metric-${metric.key}`}
                className="rounded border px-2 py-1.5"
                style={{ borderColor: 'var(--border-neutral-l1)' }}
              >
                <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                  {metric.label}
                </div>
                <div className="text-sm font-medium tabular-nums text-foreground">{metric.value}</div>
                <div className="text-[10px] tabular-nums" style={{ color: 'var(--text-tertiary)' }}>
                  {metric.pctile}
                </div>
              </div>
            ))}
          </div>

          {hasHistory && (
            <div data-testid="regime-chart" className="mt-3">
              <ReactECharts option={buildChartOption(available.history)} style={{ height: '200px' }} />
            </div>
          )}

          <div data-testid="regime-reasons" className="mt-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
            理由：{available.reasons.length > 0 ? available.reasons.join('；') : '—'}
          </div>
        </>
      )}
    </div>
  )
}
