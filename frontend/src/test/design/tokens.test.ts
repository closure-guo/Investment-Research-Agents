import { describe, it, expect } from 'vitest'
import css from '../../index.css?raw'
import {
  AI_ACCENT,
  BADGE_TOKENS,
  CHART_CONVENTIONS,
  FALL_COLOR,
  FLAT_COLOR,
  RISE_COLOR,
  SIGN_COLORS,
  STATUS_KEYS,
} from '../../design/tokens'

// 令牌契约测试（unify-ui-design-system T2.1）：
// 1) CSS 变量在 :root（浅色）与 .dark（深色）双主题齐备；
// 2) 涨跌语义不随主题翻转（A 股惯例：rise 红 / fall 绿）；
// 3) TS 导出的令牌映射与 CSS 变量名一一对应，可被页面组件稳定引用。
//
// 解析 raw CSS 而非 computed style：jsdom 不执行 Tailwind/PostCSS 管线且
// 对自定义属性继承支持有限（themePalette.test.ts 同口径）。
const light = css.slice(0, css.indexOf('.dark'))
const dark = css.slice(css.indexOf('.dark'))

function declaration(block: string, name: string): string {
  const m = block.match(new RegExp(`${name}:\\s*([^;]+);`))
  return m ? m[1].trim() : ''
}

function hexToRgb(hex: string): [number, number, number] {
  const m = hex.trim().match(/^#([0-9a-f]{6})$/i)
  if (!m) throw new Error(`不是十六进制色值: ${hex}`)
  const n = parseInt(m[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/** 递归收集令牌对象中引用的全部 var(--*) 变量名 */
function collectVarNames(value: unknown, acc: string[] = []): string[] {
  if (typeof value === 'string') {
    for (const m of value.matchAll(/var\((--[a-z0-9-]+)\)/g)) acc.push(m[1])
  } else if (Array.isArray(value)) {
    value.forEach(v => collectVarNames(v, acc))
  } else if (value && typeof value === 'object') {
    Object.values(value).forEach(v => collectVarNames(v, acc))
  }
  return acc
}

const BADGE_SUFFIXES = ['bg', 'fg', 'border'] as const

describe('设计令牌：涨跌语义色（A 股惯例）', () => {
  it('浅色/深色双主题均声明 rise/fall/flat', () => {
    for (const block of [light, dark]) {
      expect(declaration(block, '--rise')).toMatch(/^#/)
      expect(declaration(block, '--fall')).toMatch(/^#/)
      expect(declaration(block, '--flat')).toMatch(/^#/)
    }
  })

  it('语义不随主题翻转：rise 恒为红系、fall 恒为绿系', () => {
    for (const block of [light, dark]) {
      const [r, g, b] = hexToRgb(declaration(block, '--rise'))
      expect(r).toBeGreaterThan(g)
      expect(r).toBeGreaterThan(b)
      const [fr, fg, fb] = hexToRgb(declaration(block, '--fall'))
      expect(fg).toBeGreaterThan(fr)
      expect(fg).toBeGreaterThan(fb)
    }
  })

  it('涨跌取值对齐既有 status-error/success 色族（浅色 #EF4444/#10B981）', () => {
    expect(declaration(light, '--rise')).toBe(declaration(light, '--status-error-default'))
    expect(declaration(light, '--fall')).toBe(declaration(light, '--status-success-default'))
    expect(declaration(dark, '--rise')).toBe(declaration(dark, '--status-error-default'))
    expect(declaration(dark, '--fall')).toBe(declaration(dark, '--status-success-default'))
  })

  it('flat 为既有文字/边框体系中的中性灰（低饱和）', () => {
    for (const block of [light, dark]) {
      const [r, g, b] = hexToRgb(declaration(block, '--flat'))
      const spread = Math.max(r, g, b) - Math.min(r, g, b)
      expect(spread).toBeLessThanOrEqual(8)
      expect(r).toBeGreaterThan(100)
      expect(r).toBeLessThan(200)
    }
  })
})

describe('设计令牌：五态徽章', () => {
  it('双主题均声明五态 × bg/fg/border 共 15 个变量', () => {
    for (const block of [light, dark]) {
      for (const status of STATUS_KEYS) {
        for (const suffix of BADGE_SUFFIXES) {
          expect(declaration(block, `--badge-${status}-${suffix}`)).not.toBe('')
        }
      }
    }
  })

  it('状态色语义正确：active 绿 / running 蓝 / failed 红 / retired 中性灰', () => {
    for (const block of [light, dark]) {
      const [ar, ag, ab] = hexToRgb(declaration(block, '--badge-active-bg'))
      expect(ag).toBeGreaterThan(ar)
      expect(ag).toBeGreaterThan(ab)
      const [rr, rg, rb] = hexToRgb(declaration(block, '--badge-running-bg'))
      expect(rb).toBeGreaterThan(rr)
      expect(rb).toBeGreaterThan(rg)
      const [fr, fg, fb] = hexToRgb(declaration(block, '--badge-failed-bg'))
      expect(fr).toBeGreaterThan(fg)
      expect(fr).toBeGreaterThan(fb)
      const [tr, tg, tb] = hexToRgb(declaration(block, '--badge-retired-bg'))
      expect(Math.max(tr, tg, tb) - Math.min(tr, tg, tb)).toBeLessThanOrEqual(16)
    }
  })

  it('degraded 为琥珀描边态（边框琥珀色、底色非实心）', () => {
    for (const block of [light, dark]) {
      const border = declaration(block, '--badge-degraded-border')
      const [r, g, b] = hexToRgb(border)
      expect(r).toBeGreaterThan(b)
      expect(g).toBeGreaterThan(b)
      const bg = declaration(block, '--badge-degraded-bg')
      // 描边态：底色为半透明 tint（非实心十六进制）
      expect(bg.startsWith('rgba(') || bg === 'transparent').toBe(true)
    }
  })
})

describe('设计令牌：AI 紫系标识', () => {
  it('双主题声明 accent/accent-bg/accent-fg/accent-border', () => {
    for (const block of [light, dark]) {
      for (const name of ['--ai-accent', '--ai-accent-bg', '--ai-accent-fg', '--ai-accent-border']) {
        expect(declaration(block, name)).not.toBe('')
      }
    }
  })

  it('accent 主色为紫系（蓝分量占优、红次之、绿最低）', () => {
    for (const block of [light, dark]) {
      const [r, g, b] = hexToRgb(declaration(block, '--ai-accent'))
      expect(b).toBeGreaterThan(r)
      expect(r).toBeGreaterThan(g)
    }
  })

  it('不引入历史 TRAE 品牌紫（#4B3FE3/#6C5FF0/#8F84F5）', () => {
    expect(css).not.toMatch(/#4B3FE3|#6C5FF0|#8F84F5/i)
  })
})

describe('设计令牌：TS 映射契约（快照）', () => {
  it('涨跌与符号映射指向语义变量', () => {
    expect(RISE_COLOR).toBe('var(--rise)')
    expect(FALL_COLOR).toBe('var(--fall)')
    expect(FLAT_COLOR).toBe('var(--flat)')
    expect(SIGN_COLORS).toEqual({ rise: 'var(--rise)', fall: 'var(--fall)', flat: 'var(--flat)' })
  })

  it('BADGE_TOKENS 五态齐全且指向同名变量，running 带呼吸动画标记', () => {
    expect(Object.keys(BADGE_TOKENS)).toEqual([...STATUS_KEYS])
    for (const status of STATUS_KEYS) {
      expect(BADGE_TOKENS[status].bg).toBe(`var(--badge-${status}-bg)`)
      expect(BADGE_TOKENS[status].fg).toBe(`var(--badge-${status}-fg)`)
      expect(BADGE_TOKENS[status].border).toBe(`var(--badge-${status}-border)`)
      expect(BADGE_TOKENS[status].label).not.toBe('')
    }
    expect(BADGE_TOKENS.running.animation).toBe('breath')
    expect(BADGE_TOKENS.active.animation).toBeUndefined()
    expect(BADGE_TOKENS.active.label).toBe('正常')
    expect(BADGE_TOKENS.degraded.label).toBe('降级')
    expect(BADGE_TOKENS.retired.label).toBe('已停用')
    expect(BADGE_TOKENS.running.label).toBe('运行中')
    expect(BADGE_TOKENS.failed.label).toBe('失败')
  })

  it('AI_ACCENT 指向 AI 变量族', () => {
    expect(AI_ACCENT).toEqual({
      accent: 'var(--ai-accent)',
      bg: 'var(--ai-accent-bg)',
      fg: 'var(--ai-accent-fg)',
      border: 'var(--ai-accent-border)',
    })
  })

  it('语义令牌引用的变量在两个主题块均有声明', () => {
    const referenced = new Set(
      collectVarNames({ RISE_COLOR, FALL_COLOR, FLAT_COLOR, SIGN_COLORS, BADGE_TOKENS, AI_ACCENT }),
    )
    expect(referenced.size).toBeGreaterThan(0)
    for (const name of referenced) {
      expect(declaration(light, name), `${name} 缺少浅色声明`).not.toBe('')
      expect(declaration(dark, name), `${name} 缺少深色声明`).not.toBe('')
    }
  })
})

describe('图表规范常量 CHART_CONVENTIONS（本 change 不重构既有图表）', () => {
  it('主色实线 / 基准灰虚线 / 损耗半透明填充', () => {
    expect(CHART_CONVENTIONS.mainLine.color).toMatch(/^var\(--chart-/)
    expect(CHART_CONVENTIONS.mainLine.lineStyle).toBe('solid')
    expect(CHART_CONVENTIONS.baseline.color).toBe('var(--border)')
    expect(CHART_CONVENTIONS.baseline.lineStyle).toBe('dashed')
    expect(CHART_CONVENTIONS.lossFill.color).toMatch(/^var\(--chart-/)
    expect(CHART_CONVENTIONS.lossFill.opacity).toBeGreaterThan(0)
    expect(CHART_CONVENTIONS.lossFill.opacity).toBeLessThan(1)
  })

  it('事件标记图标集非空且配套色取自 chart 令牌', () => {
    const icons = CHART_CONVENTIONS.eventMarkers.icons
    expect(Object.keys(icons).length).toBeGreaterThanOrEqual(3)
    for (const icon of Object.values(icons)) {
      expect(icon).toMatch(/^fa-/)
    }
    expect(CHART_CONVENTIONS.eventMarkers.color).toMatch(/^var\(--chart-/)
  })

  it('图表引用的变量在 :root 均已声明（深色块按需覆盖子集，故只校验浅色块）', () => {
    for (const name of new Set(collectVarNames(CHART_CONVENTIONS))) {
      expect(declaration(light, name), `${name} 未在 :root 声明`).not.toBe('')
    }
  })
})
