import { StatusBadge } from '../../components/StatusBadge'
import { EMPTY_VALUE } from '../../design/format'
import type { BrokerOrderRow } from '../../types'
import {
  BROKER_TABLE_CLASS,
  BROKER_TD_CLASS,
  BROKER_TH_CLASS,
  BrokerTableEmpty,
  ORDER_STATUS_BADGE,
  formatNumber,
  formatOrderTime,
  formatSide,
} from './shared'

/**
 * 订单 Tab 表（delta add-broker-console design「Tab 表：订单」）。
 *
 * 列：时间 / 标的 / 方向 / 数量 / 状态 / 成交价 / 原因码——状态用 StatusBadge
 * 五态映射（pending→running、filled→active、rejected→failed、expired→retired，
 * 见 shared.ORDER_STATUS_BADGE）；原因码为结构化原值（未成交/拒单/撤销）。
 */
export function BrokerOrdersTable({ orders }: { orders: BrokerOrderRow[] }) {
  if (orders.length === 0) {
    return <BrokerTableEmpty testId="broker-orders-empty" message="暂无订单" />
  }

  return (
    <table className={BROKER_TABLE_CLASS} data-testid="broker-orders-table">
      <thead>
        <tr>
          <th className={BROKER_TH_CLASS}>时间</th>
          <th className={BROKER_TH_CLASS}>标的</th>
          <th className={BROKER_TH_CLASS}>方向</th>
          <th className={`${BROKER_TH_CLASS} text-right`}>数量</th>
          <th className={BROKER_TH_CLASS}>状态</th>
          <th className={`${BROKER_TH_CLASS} text-right`}>成交价</th>
          <th className={BROKER_TH_CLASS}>原因码</th>
        </tr>
      </thead>
      <tbody>
        {orders.map(order => {
          const badge = ORDER_STATUS_BADGE[order.status]
          return (
            <tr key={order.order_id} data-testid={`broker-order-row-${order.order_id}`}>
              <td className={BROKER_TD_CLASS}>{formatOrderTime(order.created_at)}</td>
              <td className={BROKER_TD_CLASS}>{order.symbol}</td>
              <td className={BROKER_TD_CLASS}>{formatSide(order.side)}</td>
              <td className={`${BROKER_TD_CLASS} text-right`}>{formatNumber(order.qty, 0)}</td>
              <td className={BROKER_TD_CLASS} data-testid={`broker-order-status-${order.order_id}`}>
                {badge ? (
                  <StatusBadge status={badge.status} label={badge.label} />
                ) : (
                  // 未知状态不臆造映射：原值如实展示
                  order.status
                )}
              </td>
              <td className={`${BROKER_TD_CLASS} text-right`}>
                {order.fill_price === null ? EMPTY_VALUE : formatNumber(order.fill_price)}
              </td>
              <td
                className={BROKER_TD_CLASS}
                data-testid={`broker-order-reason-${order.order_id}`}
              >
                {order.reason ?? EMPTY_VALUE}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
