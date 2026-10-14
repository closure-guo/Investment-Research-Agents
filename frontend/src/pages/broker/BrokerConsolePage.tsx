import { useCallback, useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { getChartTheme } from '../../Charts'
import { PageShellA, type ShellMetricBlock, type ShellTab } from '../../components/shells/PageShellA'
import { EMPTY_VALUE, signToken } from '../../design/format'
import { BrokerGateTable } from './BrokerGateTable'
import { BrokerOrdersTable } from './BrokerOrdersTable'
import { BrokerPositionsTable } from './BrokerPositionsTable'
import { finiteOrNull, formatMoney, formatSignedMoney } from './shared'
import type {
  BrokerOrderRow,
  BrokerOrdersResponse,
  BrokerOverview,
  BrokerPositionsResponse,
} from '../../types'

/**
 * 执行台账页（delta add-broker-console T2.1；spec frontend「执行台账页」）。
 *
 * PageShellA 形态（指标条 + 主图表 + 底部 Tab 表）：
 * - 指标条：总资产 / 可用资金 / 持仓市值 / 浮动盈亏——市值不可得（快照缺失）时
 *   显式标注而非显示 0（``broker-market-value-note`` 横幅 + 受影响指标值「—」）；
 * - 主图表 = 持仓市值构成（按标的 ECharts 饼图，值取持仓行 market_value；
 *   空仓 → 空态不画空图）；
 * - Tab 表：持仓 / 订单 / 门禁拒绝（订单状态经 StatusBadge 五态映射）；
 * - 空账户如实：资金照显、图表与表格空态文案，MUST NOT 渲染伪持仓/伪订单。
 *
 * 只读页：不提供任何写入口（下单/撤单仍只经日批驱动器，手工入口会绕过信号/门禁
 * 契约）。取数 4 端点（overview/positions/orders/gate-log）并发拉取，失败 → 错误态
 * 可重试（与「空账户」空态严格区分）。
 */

/** 持仓市值构成饼图（复用 Charts 主题注入口径；对齐 MarketShareChart 写法） */
function PositionsValuePie({ rows }: { rows: BrokerPositionsResponse['positions'] }) {
  const theme = getChartTheme()
  const slices = rows
    .map(row => ({ name: row.symbol, value: row.market_value }))
    .filter((item): item is { name: string; value: number } => item.value !== null)

  if (slices.length === 0) {
    // 「无持仓」与「有持仓但快照缺价」是两回事：空态文案如实区分，都不画空图
    return (
      <div
        data-testid="broker-chart-empty"
        className="flex h-64 items-center justify-center text-sm text-muted-foreground"
      >
        {rows.length === 0 ? '无持仓，暂无市值构成' : '快照缺失，持仓市值构成不可得（不以 0 冒充）'}
      </div>
    )
  }

  const option = {
    tooltip: {
      trigger: 'item',
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBorder,
      textStyle: { color: theme.tooltipTextColor },
      valueFormatter: (value: number) => formatMoney(value),
    },
    legend: { bottom: 0, textStyle: { color: theme.textColor, fontSize: 10 } },
    series: [
      {
        name: '持仓市值',
        type: 'pie',
        radius: ['40%', '70%'],
        data: slices,
        label: { color: theme.textColor, fontSize: 10 },
        color: [
          theme.brand,
          theme.coral,
          theme.mint,
          theme.amber,
          theme.sky,
          theme.violet,
          theme.rose,
          theme.teal,
        ],
      },
    ],
  }

  return (
    <div data-testid="broker-positions-pie">
      <ReactECharts option={option} style={{ height: '280px' }} />
    </div>
  )
}

export function BrokerConsolePage({ onBack }: { onBack: () => void }) {
  // null = 加载中；error = 取数失败（与「空账户」空态严格区分）
  const [overview, setOverview] = useState<BrokerOverview | null>(null)
  const [positions, setPositions] = useState<BrokerPositionsResponse | null>(null)
  const [orders, setOrders] = useState<BrokerOrderRow[] | null>(null)
  const [gateLog, setGateLog] = useState<BrokerOrderRow[] | null>(null)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    setError(false)
    setOverview(null)
    setPositions(null)
    setOrders(null)
    setGateLog(null)
    try {
      const [overviewResp, positionsResp, ordersResp, gateResp] = await Promise.all([
        fetch('/api/broker/overview'),
        fetch('/api/broker/positions'),
        fetch('/api/broker/orders'),
        fetch('/api/broker/gate-log'),
      ])
      for (const resp of [overviewResp, positionsResp, ordersResp, gateResp]) {
        if (!resp.ok) throw new Error(String(resp.status))
      }
      setOverview((await overviewResp.json()) as BrokerOverview)
      setPositions((await positionsResp.json()) as BrokerPositionsResponse)
      setOrders(((await ordersResp.json()) as BrokerOrdersResponse).orders ?? [])
      setGateLog(((await gateResp.json()) as BrokerOrdersResponse).orders ?? [])
    } catch {
      setError(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const loading = overview === null || positions === null || orders === null || gateLog === null
  const note = overview?.market_value_note ?? null
  const marketValueMissing = note !== null

  const metrics: ShellMetricBlock[] = [
    {
      key: 'equity',
      label: '总资产',
      value: (
        <span data-testid="broker-metric-equity-value">
          {marketValueMissing ? EMPTY_VALUE : formatMoney(overview?.equity)}
        </span>
      ),
      sub: marketValueMissing ? '持仓市值不可得，总资产不可算' : '资金 + 持仓市值',
    },
    {
      key: 'cash',
      label: '可用资金',
      value: <span data-testid="broker-metric-cash-value">{formatMoney(overview?.cash)}</span>,
      sub: 'paper 账户余额',
    },
    {
      key: 'positions_value',
      label: '持仓市值',
      value: (
        <span data-testid="broker-metric-positions-value">
          {formatMoney(overview?.positions_value)}
        </span>
      ),
      sub: marketValueMissing
        ? `快照不可得${overview?.snapshot_date ? `（快照 ${overview.snapshot_date}）` : ''}`
        : overview?.positions_count
          ? `快照 ${overview?.snapshot_date ?? EMPTY_VALUE} close_raw 口径`
          : '无持仓（空集求和为 0）',
    },
    {
      key: 'unrealized_pnl',
      label: '浮动盈亏',
      value: (
        <span
          data-testid="broker-metric-pnl-value"
          style={{ color: signToken(overview?.unrealized_pnl) }}
        >
          {formatSignedMoney(finiteOrNull(overview?.unrealized_pnl))}
        </span>
      ),
      sub: marketValueMissing
        ? '市值不可得，浮盈不可算'
        : `成本 ${formatMoney(overview?.position_cost)}`,
    },
  ]

  const tabs: ShellTab[] = [
    {
      key: 'positions',
      label: '持仓',
      content: <BrokerPositionsTable positions={positions?.positions ?? []} />,
    },
    {
      key: 'orders',
      label: '订单',
      content: <BrokerOrdersTable orders={orders ?? []} />,
    },
    {
      key: 'gate-log',
      label: '门禁拒绝',
      content: <BrokerGateTable orders={gateLog ?? []} />,
    },
  ]

  return (
    <div data-testid="broker-page" className="flex h-screen flex-col">
      {/* 标题栏（固定）：返回入口 + 只读说明 */}
      <div className="shrink-0 border-b border-border px-6 pt-5 pb-4">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onBack}
            data-testid="broker-back"
            aria-label="返回对话"
            className="flex h-7 w-7 items-center justify-center rounded hover:bg-muted motion-reduce:transition-none"
          >
            <i className="fas fa-arrow-left text-sm"></i>
          </button>
          <i className="fas fa-book-open text-sm" style={{ color: 'var(--primary)' }}></i>
          <h1 className="text-base font-semibold text-foreground">执行台账</h1>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          paper 账户台账（只读）：资金 / 持仓 / 订单四态 / 门禁拒绝留痕；下单与撤单仍只经日批驱动器。
        </p>
      </div>

      <div className="min-h-0 flex-1">
        {error ? (
          <div
            data-testid="broker-error"
            className="flex items-center gap-2 px-6 py-8 text-sm text-foreground"
          >
            执行台账加载失败
            <button
              type="button"
              data-testid="broker-retry"
              onClick={() => {
                void load()
              }}
              className="rounded px-2 py-1 text-xs hover:bg-muted motion-reduce:transition-none"
            >
              重试
            </button>
          </div>
        ) : (
          <PageShellA
            metrics={metrics}
            loading={loading}
            chart={
              <div className="space-y-3">
                {/* 市值不可得的显式标注（spec：MUST NOT 以 0 冒充） */}
                {note && (
                  <div
                    data-testid="broker-market-value-note"
                    className="rounded-lg border px-3 py-2 text-xs"
                    style={{
                      borderColor: 'var(--status-warning-default)',
                      color: 'var(--status-warning-default)',
                    }}
                  >
                    {note}
                  </div>
                )}
                <PositionsValuePie rows={positions?.positions ?? []} />
              </div>
            }
            tabs={tabs}
          />
        )}
      </div>
    </div>
  )
}
