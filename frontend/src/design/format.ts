// 数字规范（unify-ui-design-system T2.2，spec: frontend delta）
//
// 全站统一数字呈现口径：
// - 涨跌数值带符号（+2.31% / -1.05%），并按语义令牌着色（红涨绿跌，A 股惯例）；
// - 计数类数值千分位分组（与 EvalOpsPane 的 toLocaleString('en-US') 同口径）；
// - 百分比两位小数；
// - 数值列一律 tabular-nums（等宽字形），避免行间数字跳动。
//
// 缺失/非法值统一返回占位符 EMPTY_VALUE（'—'），绝不折算成 0。
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { FALL_COLOR, FLAT_COLOR, RISE_COLOR } from './tokens'

/** 缺失/非法数值的统一占位符（与 EvalOpsPane 口径一致） */
export const EMPTY_VALUE = '—'

/** 数值列等宽类：tabular-nums（ScreeningPage 既有用法）+ font-mono（显式等宽字体） */
export const TABULAR_CLASS = 'tabular-nums font-mono'

/** 数值列 className 组合器（可叠加右对齐等附加类） */
export function tabular(...extra: ClassValue[]): string {
  return twMerge(clsx(TABULAR_CLASS, ...extra))
}

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/**
 * 带符号百分比：+2.31% / -1.05% / 0.00%（零不带符号）。
 *
 * 符号取自四舍五入后的显示值：正负 0.001 这类亚精度极小值显示为 0.00%（无符号），
 * 避免出现「+0.00%」；着色请配合 signToken 使用（signToken 按原始值符号，
 * 亚精度误差场景下以显示为准）。
 *
 * @param value 已是百分数的数值（2.31 → '+2.31%'，不做 100 倍换算）
 * @param digits 小数位，默认 2
 */
export function formatSignedPct(value: number | null | undefined, digits = 2): string {
  if (!isFiniteNumber(value)) return EMPTY_VALUE
  const rounded = Number(value.toFixed(digits))
  // rounded === 0 同时覆盖 -0（-0.00 显示为 0.00，不带负号）
  if (rounded === 0) return `${(0).toFixed(digits)}%`
  const sign = rounded > 0 ? '+' : ''
  return `${sign}${rounded.toFixed(digits)}%`
}

/** 千分位分组（计数/预算/花费类数值；缺失或非法 → '—'） */
export function formatThousands(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return EMPTY_VALUE
  return value.toLocaleString('en-US')
}

/**
 * 无符号百分比（两位小数）：58.24% / 0.00%。
 *
 * 不做隐式取绝对值——负值保留自身符号；幅度类展示请传入非负值。
 */
export function formatPct2(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return EMPTY_VALUE
  return `${value.toFixed(2)}%`
}

/**
 * 涨跌着色令牌：正 → RISE_COLOR（红）/ 负 → FALL_COLOR（绿）/ 零或缺失 → FLAT_COLOR（灰）。
 *
 * 用法：`style={{ color: signToken(change) }}`——禁止页面直接书写 Tailwind 红/绿裸色值类表达涨跌。
 */
export function signToken(value: number | null | undefined): string {
  if (!isFiniteNumber(value) || value === 0) return FLAT_COLOR
  return value > 0 ? RISE_COLOR : FALL_COLOR
}
