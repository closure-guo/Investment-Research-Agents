import type { PredictionStatus } from '../../types'

// 观点判定状态展示映射（战绩页 + 观点详情页共用单一来源）。
// 与后端 PREDICTIONS_STATUSES / _STATUS_LABELS（src/finance_agent/outcome/track_record/model.py）逐项对齐：
// avoidance = neutral 观点回避判定终态，标签「回避」；细粒度回避结果（avoidance_win/loss/neutral）
// 在 avoidance_status 列，本页不做数值展示（留待后续增量）。
export const PREDICTION_STATUS_LABEL: Record<PredictionStatus, string> = {
  open: '进行中',
  resolved_win: '命中',
  resolved_loss: '未中',
  resolved_neutral: '中性',
  avoidance: '回避',
  unresolvable: '不可判定',
}

// 状态标签色（语义令牌）：命中=成功绿、未中=错误红、中性/回避=次要灰（同族）、
// 进行中=主色蓝、不可判定=三级灰斜杠
export const PREDICTION_STATUS_CLS: Record<PredictionStatus, string> = {
  open: 'text-[color:var(--status-primary-default)]',
  resolved_win: 'text-[color:var(--status-success-default)]',
  resolved_loss: 'text-[color:var(--status-error-default)]',
  resolved_neutral: 'text-[color:var(--text-secondary)]',
  avoidance: 'text-[color:var(--text-secondary)]',
  unresolvable: 'text-[color:var(--text-tertiary)] line-through',
}
