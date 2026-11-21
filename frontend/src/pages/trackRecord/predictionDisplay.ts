// 方向/判定规则中文映射单一真源（update-track-record-display-clarity）：
// 列表与详情共用，避免两处漂移；原始英文值由调用方以 title 辅助保留供排查。
export const DIRECTION_LABEL: Record<string, string> = {
  long: '看多',
  short: '看空',
  neutral: '中性',
}

export const RESOLUTION_RULE_LABEL: Record<string, string> = {
  expiry: '到期结算',
  superseded: '被新观点替代·提前结算',
  duplicate_of_day: '同日重复关闭',
  stale_no_market: '长期无行情',
}

// ── 三价极性契约（add-price-polarity-contract，#606）─────────────────────────

export interface DeclaredPrices {
  entryPrice: number | null
  stopLoss: number | null
  targetPrice: number | null
  polarity: string | null
}

/** 快照 declared_prices 读取（读侧宽松）：非 dict declared_prices / 非法 JSON /
 *  缺快照 → null；极性以服务端 declared_polarity 为准（快照解析兜底）。 */
export function readDeclaredPrices(
  record: { rationale_snapshot?: unknown; declared_polarity?: string | null } | null | undefined,
): DeclaredPrices | null {
  if (!record) return null
  let snap: unknown = record.rationale_snapshot
  if (typeof snap === 'string') {
    if (!snap.trim()) return null
    try {
      snap = JSON.parse(snap)
    } catch {
      return null
    }
  }
  if (typeof snap !== 'object' || snap === null) return null
  const prices = (snap as Record<string, unknown>)['declared_prices']
  if (typeof prices !== 'object' || prices === null) return null
  const p = prices as Record<string, unknown>
  const num = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null
  const pol =
    typeof record.declared_polarity === 'string' && record.declared_polarity
      ? record.declared_polarity
      : typeof p['polarity'] === 'string' && p['polarity']
        ? (p['polarity'] as string)
        : null
  return {
    entryPrice: num(p['entry_price']),
    stopLoss: num(p['stop_loss']),
    targetPrice: num(p['target_price']),
    polarity: pol,
  }
}

/** 极性 → 止损/目标惯例语义标注（白名单；未知极性与未声明回退裸标签）。 */
export const POLARITY_STOP_LABEL: Record<string, string> = {
  exit: '清仓位·跌破清出',
  short: '失效位·观点失效',
  long: '失效位·观点失效',
}

export const POLARITY_TARGET_LABEL: Record<string, string> = {
  exit: '反弹减仓带',
  short: '兑现位·下行兑现',
  long: '兑现位',
}
