import { describe, it, expect } from 'vitest'
import {
  EMPTY_VALUE,
  TABULAR_CLASS,
  formatPct2,
  formatSignedPct,
  formatThousands,
  signToken,
  tabular,
} from '../../design/format'

// 数字规范边界测试（unify-ui-design-system T2.2）：
// 负值 / 零 / 正值 / 大数 / NaN / null 全覆盖；涨跌符号遵循 A 股惯例（涨用 rise）。
describe('数字规范：formatSignedPct（带符号百分比）', () => {
  it('正值带 + 号、负值带 - 号、零不带符号', () => {
    expect(formatSignedPct(2.31)).toBe('+2.31%')
    expect(formatSignedPct(-1.05)).toBe('-1.05%')
    expect(formatSignedPct(0)).toBe('0.00%')
    expect(formatSignedPct(-0)).toBe('0.00%')
  })

  it('四舍五入后为零的极小值按零显示（不带符号）', () => {
    expect(formatSignedPct(0.001)).toBe('0.00%')
    expect(formatSignedPct(-0.001)).toBe('0.00%')
  })

  it('digits 参数控制小数位（默认两位）', () => {
    expect(formatSignedPct(2.345, 1)).toBe('+2.3%')
    expect(formatSignedPct(-12.3456, 3)).toBe('-12.346%')
    expect(formatSignedPct(0, 1)).toBe('0.0%')
  })

  it('大数正常分组前的原值输出（不截断、不科学计数）', () => {
    expect(formatSignedPct(12345.678)).toBe('+12345.68%')
    expect(formatSignedPct(-987654.3)).toBe('-987654.30%')
  })

  it('NaN/null/undefined/Infinity 返回占位符', () => {
    expect(formatSignedPct(NaN)).toBe(EMPTY_VALUE)
    expect(formatSignedPct(null)).toBe(EMPTY_VALUE)
    expect(formatSignedPct(undefined)).toBe(EMPTY_VALUE)
    expect(formatSignedPct(Infinity)).toBe(EMPTY_VALUE)
  })
})

describe('数字规范：formatThousands（千分位）', () => {
  it('千分位分组（与 EvalOpsPane 同口径 toLocaleString en-US）', () => {
    expect(formatThousands(0)).toBe('0')
    expect(formatThousands(999)).toBe('999')
    expect(formatThousands(1000)).toBe('1,000')
    expect(formatThousands(1234567)).toBe('1,234,567')
    expect(formatThousands(-1234.5)).toBe('-1,234.5')
  })

  it('NaN/null/undefined/Infinity 返回占位符', () => {
    expect(formatThousands(NaN)).toBe(EMPTY_VALUE)
    expect(formatThousands(null)).toBe(EMPTY_VALUE)
    expect(formatThousands(undefined)).toBe(EMPTY_VALUE)
    expect(formatThousands(-Infinity)).toBe(EMPTY_VALUE)
  })
})

describe('数字规范：formatPct2（无符号两位小数）', () => {
  it('正值与零', () => {
    expect(formatPct2(58.24)).toBe('58.24%')
    expect(formatPct2(0)).toBe('0.00%')
    expect(formatPct2(2.346)).toBe('2.35%')
  })

  it('负值保留自身符号（不做隐式取绝对值）', () => {
    expect(formatPct2(-1.5)).toBe('-1.50%')
  })

  it('NaN/null/undefined 返回占位符', () => {
    expect(formatPct2(NaN)).toBe(EMPTY_VALUE)
    expect(formatPct2(null)).toBe(EMPTY_VALUE)
    expect(formatPct2(undefined)).toBe(EMPTY_VALUE)
  })
})

describe('数字规范：signToken（涨跌着色令牌）', () => {
  it('正 → rise（红）、负 → fall（绿）、零 → flat（灰）', () => {
    expect(signToken(2.31)).toBe('var(--rise)')
    expect(signToken(-1.05)).toBe('var(--fall)')
    expect(signToken(0)).toBe('var(--flat)')
    expect(signToken(-0)).toBe('var(--flat)')
  })

  it('亚精度极小值按原始符号着色（着色与显示各自口径，见 format.ts 注释）', () => {
    expect(signToken(0.001)).toBe('var(--rise)')
    expect(signToken(-0.001)).toBe('var(--fall)')
  })

  it('缺失/非法值按 flat 处理，绝不误着色', () => {
    expect(signToken(NaN)).toBe('var(--flat)')
    expect(signToken(null)).toBe('var(--flat)')
    expect(signToken(undefined)).toBe('var(--flat)')
  })
})

describe('数字规范：tabular 等宽类', () => {
  it('基础类与 ScreeningPage 用法一致（tabular-nums），并显式声明等宽字体', () => {
    expect(TABULAR_CLASS).toBe('tabular-nums font-mono')
    expect(tabular()).toBe('tabular-nums font-mono')
  })

  it('tabular() 可叠加额外类名', () => {
    expect(tabular('text-right')).toBe('tabular-nums font-mono text-right')
  })
})
