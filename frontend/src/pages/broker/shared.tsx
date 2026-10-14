import { EMPTY_VALUE } from '../../design/format'
import type { StatusKey } from '../../design/tokens'

/**
 * 执行台账页共享口径（delta add-broker-console T2.1）。
 *
 * 三张 Tab 表（持仓/订单/门禁拒绝）与主图表共用同一套格式化与表格样式：
 * 数值缺失一律返回「—」（design/format.ts EMPTY_VALUE），MUST NOT 折算成 0。
 */

/** 订单状态 → StatusBadge 五态映射（spec 逐字：本页唯一映射源） */
export const ORDER_STATUS_BADGE: Record<string, { status: StatusKey; label: string }> = {
  pending: { status: 'running', label: '挂单' },
  filled: { status: 'active', label: '已成交' },
  rejected: { status: 'failed', label: '已拒绝' },
  expired: { status: 'retired', label: '已过期' },
}

/** 表格类名（三表共用）：等宽数字列 + 行分隔线 */
export const BROKER_TABLE_CLASS = 'w-full border-collapse tabular-nums font-mono'
export const BROKER_TH_CLASS = 'border-b border-border px-3 py-1.5 text-left text-[11px] font-normal text-muted-foreground'
export const BROKER_TD_CLASS = 'border-b border-border px-3 py-1.5 text-xs text-foreground'

/** 表格空态行（如实文案：空账户/无记录与加载中、错误态严格区分） */
export function BrokerTableEmpty({ testId, message }: { testId: string; message: string }) {
  return (
    <div data-testid={testId} className="px-3 py-6 text-center text-xs text-muted-foreground">
      {message}
    </div>
  )
}

/** 金额格式化（两位小数千分位，带 ¥）；null/缺值 → 「—」（MUST NOT 折算成 0） */
export function formatMoney(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return EMPTY_VALUE
  return `¥${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** 带符号金额（浮动盈亏口径）：正 → +¥x，负 → -¥x，零 → ¥0.00 */
export function formatSignedMoney(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return EMPTY_VALUE
  const formatted = formatMoney(Math.abs(value))
  if (value === 0) return formatted
  return `${value > 0 ? '+' : '-'}${formatted}`
}

/** 窄化数值（null/NaN → null：界面据此显示占位符而非 0） */
export function finiteOrNull(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 数值格式化（无货币符号；缺失 → 「—」）：数量/成本/现价等列共用 */
export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return EMPTY_VALUE
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** 买卖方向展示（库内原值 buy/sell 逐字映射为中文；未知原值如实透传） */
export function formatSide(side: string): string {
  if (side === 'buy') return '买入'
  if (side === 'sell') return '卖出'
  return side
}

/** 时间列：ISO 字符串截到分钟（YYYY-MM-DD HH:MM）；缺值 → 「—」 */
export function formatOrderTime(value: string | null | undefined): string {
  if (!value) return EMPTY_VALUE
  return value.length >= 16 ? value.slice(0, 16).replace('T', ' ') : value
}
