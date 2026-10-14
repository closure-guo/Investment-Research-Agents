import { EMPTY_VALUE } from '../../design/format'
import type { BrokerOrderRow } from '../../types'
import {
  BROKER_TABLE_CLASS,
  BROKER_TD_CLASS,
  BROKER_TH_CLASS,
  BrokerTableEmpty,
  formatNumber,
  formatOrderTime,
  formatSide,
} from './shared'

/**
 * 门禁拒绝 Tab 表（delta add-broker-console design「Tab 表：门禁拒绝」）。
 *
 * 视图 = orders 中 ``status=rejected``（后端 gate-log 端点已过滤，前端原样呈现，
 * 零业务复制）：列 时间 / 标的 / 方向 / 数量 / 原因码 / 关联决策——原因码为风控
 * 门禁的结构化拒单码（如 ``ST_FLAG``），关联决策给出 ``decision_id``（悬停可见
 * ``signal_id`` 证据链）。
 */
export function BrokerGateTable({ orders }: { orders: BrokerOrderRow[] }) {
  if (orders.length === 0) {
    return <BrokerTableEmpty testId="broker-gate-empty" message="暂无门禁拒绝记录" />
  }

  return (
    <table className={BROKER_TABLE_CLASS} data-testid="broker-gate-table">
      <thead>
        <tr>
          <th className={BROKER_TH_CLASS}>时间</th>
          <th className={BROKER_TH_CLASS}>标的</th>
          <th className={BROKER_TH_CLASS}>方向</th>
          <th className={`${BROKER_TH_CLASS} text-right`}>数量</th>
          <th className={BROKER_TH_CLASS}>原因码</th>
          <th className={BROKER_TH_CLASS}>关联决策</th>
        </tr>
      </thead>
      <tbody>
        {orders.map(order => (
          <tr key={order.order_id} data-testid={`broker-gate-row-${order.order_id}`}>
            <td className={BROKER_TD_CLASS}>{formatOrderTime(order.created_at)}</td>
            <td className={BROKER_TD_CLASS}>{order.symbol}</td>
            <td className={BROKER_TD_CLASS}>{formatSide(order.side)}</td>
            <td className={`${BROKER_TD_CLASS} text-right`}>{formatNumber(order.qty, 0)}</td>
            <td className={BROKER_TD_CLASS} data-testid={`broker-gate-reason-${order.order_id}`}>
              {order.reason ?? EMPTY_VALUE}
            </td>
            <td
              className={BROKER_TD_CLASS}
              data-testid={`broker-gate-decision-${order.order_id}`}
              title={`signal_id: ${order.signal_id}`}
            >
              {order.decision_id || EMPTY_VALUE}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
