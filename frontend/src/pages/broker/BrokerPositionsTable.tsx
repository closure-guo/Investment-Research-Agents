import { EMPTY_VALUE, signToken } from '../../design/format'
import type { BrokerPositionRow } from '../../types'
import {
  BROKER_TABLE_CLASS,
  BROKER_TD_CLASS,
  BROKER_TH_CLASS,
  BrokerTableEmpty,
  formatMoney,
  formatNumber,
} from './shared'

/**
 * 持仓 Tab 表（delta add-broker-console design「Tab 表：持仓」）。
 *
 * 列：标的 / 数量 / 成本 / 现价 / 市值 / 浮盈——现价来自快照 ``close_raw``，不可得时
 * 该行现价/市值/浮盈显示「—」并保留行本身（账户有持仓是事实，价格缺失是事实，
 * 两者都要如实说；MUST NOT 用 0 冒充）。
 */
export function BrokerPositionsTable({ positions }: { positions: BrokerPositionRow[] }) {
  if (positions.length === 0) {
    return <BrokerTableEmpty testId="broker-positions-empty" message="暂无持仓" />
  }

  return (
    <table className={BROKER_TABLE_CLASS} data-testid="broker-positions-table">
      <thead>
        <tr>
          <th className={BROKER_TH_CLASS}>标的</th>
          <th className={`${BROKER_TH_CLASS} text-right`}>数量</th>
          <th className={`${BROKER_TH_CLASS} text-right`}>成本</th>
          <th className={`${BROKER_TH_CLASS} text-right`}>现价</th>
          <th className={`${BROKER_TH_CLASS} text-right`}>市值</th>
          <th className={`${BROKER_TH_CLASS} text-right`}>浮盈</th>
        </tr>
      </thead>
      <tbody>
        {positions.map(row => (
          <tr key={row.symbol} data-testid={`broker-position-row-${row.symbol}`}>
            <td className={BROKER_TD_CLASS}>{row.symbol}</td>
            <td className={`${BROKER_TD_CLASS} text-right`}>{formatNumber(row.qty, 0)}</td>
            <td className={`${BROKER_TD_CLASS} text-right`}>{formatNumber(row.avg_cost)}</td>
            <td
              className={`${BROKER_TD_CLASS} text-right`}
              data-testid={`broker-position-close-${row.symbol}`}
            >
              {formatNumber(row.close)}
            </td>
            <td className={`${BROKER_TD_CLASS} text-right`}>{formatMoney(row.market_value)}</td>
            <td
              className={`${BROKER_TD_CLASS} text-right`}
              data-testid={`broker-position-pnl-${row.symbol}`}
              style={{ color: signToken(row.unrealized_pnl) }}
            >
              {row.unrealized_pnl === null ? EMPTY_VALUE : formatMoney(row.unrealized_pnl)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
