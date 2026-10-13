import { describe, it, expect, vi } from 'vitest'
import { render, renderHook, screen, waitFor } from '@testing-library/react'
import { motion } from 'framer-motion'
import css from '../../index.css?raw'
import {
  BADGE_TRANSITION_MS,
  MAX_STAGGER_DELAY_MS,
  ROW_ENTRANCE_Y,
  STAGGER_MS,
  TAB_FADE_CLASS,
  TAB_FADE_MS,
  rowEntranceVariants,
  useRowEntrance,
} from '../../design/motion'

// 动效规范测试（unify-ui-design-system T5.1）：
// 参数写死可断言 + framer-motion 在 reduced-motion 下不调度任何动画（内容直接呈现）。
const motionState = vi.hoisted(() => ({ reduced: false }))
vi.mock('framer-motion', async importOriginal => {
  const actual = await importOriginal<typeof import('framer-motion')>()
  return { ...actual, useReducedMotion: () => motionState.reduced }
})

function Harness({ index }: { index: number }) {
  const entrance = useRowEntrance(index)
  return <motion.div data-testid="row-entrance" {...entrance} />
}

describe('动效常量（契约钉死）', () => {
  it('行入场：fade-in + translateY(8px)→0，stagger 30ms 且封顶', () => {
    expect(ROW_ENTRANCE_Y).toBe(8)
    expect(STAGGER_MS).toBe(30)
    const variants = rowEntranceVariants()
    expect(variants.hidden).toEqual({ opacity: 0, y: 8 })
    const show = variants.show as (i: number) => Record<string, unknown>
    const first = show(0) as { opacity: number; y: number; transition: { delay: number } }
    expect(first.opacity).toBe(1)
    expect(first.y).toBe(0)
    expect(first.transition.delay).toBe(0)
    expect((show(3) as { transition: { delay: number } }).transition.delay).toBeCloseTo(0.09)
    // 封顶：行数极大时延迟不超过 MAX_STAGGER_DELAY_MS
    expect((show(10000) as { transition: { delay: number } }).transition.delay).toBe(
      MAX_STAGGER_DELAY_MS / 1000,
    )
  })

  it('Tab 淡入 150ms、徽章变色 200ms ease-out', () => {
    expect(TAB_FADE_MS).toBe(150)
    expect(BADGE_TRANSITION_MS).toBe(200)
    expect(TAB_FADE_CLASS).toContain('animate-tab-fade')
    expect(TAB_FADE_CLASS).toContain('motion-reduce:animate-none')
  })

  it('index.css 定义 tabFadeIn keyframes（150ms）并在 reduced-motion 下禁用', () => {
    expect(css).toMatch(/@keyframes\s+tabFadeIn/)
    expect(css).toMatch(/\.animate-tab-fade\s*\{[^}]*animation:\s*tabFadeIn\s+150ms/)
    const idx = css.indexOf('@media (prefers-reduced-motion: reduce)')
    expect(idx).toBeGreaterThan(-1)
    expect(css.slice(idx)).toMatch(/\.animate-tab-fade\s*\{[^}]*animation:\s*none/)
  })
})

describe('useRowEntrance 降级行为', () => {
  it('reduced-motion：useRowEntrance 不返回 variants/animate，initial=false', () => {
    motionState.reduced = true
    try {
      const { result } = renderHook(() => useRowEntrance(1))
      expect(result.current.initial).toBe(false)
      expect(result.current.variants).toBeUndefined()
      expect(result.current.animate).toBeUndefined()
    } finally {
      motionState.reduced = false
    }
  })

  it('非降级：initial=hidden / animate=show / custom=index', () => {
    const { result } = renderHook(() => useRowEntrance(4))
    expect(result.current.initial).toBe('hidden')
    expect(result.current.animate).toBe('show')
    expect(result.current.custom).toBe(4)
    expect(result.current.variants?.hidden).toEqual({ opacity: 0, y: 8 })
  })

  it('reduced-motion：不调度动画，内容直接呈现（opacity 恒为 1、无初始位移）', () => {
    motionState.reduced = true
    try {
      render(<Harness index={2} />)
      const el = screen.getByTestId('row-entrance')
      expect(Number(el.style.opacity || '1')).toBe(1)
      expect(el.getAttribute('style') ?? '').not.toContain('translateY(8px)')
    } finally {
      motionState.reduced = false
    }
  })

  it('非 reduced-motion：以 hidden（opacity 0 + y 8px）起始，随后入场到位', async () => {
    render(<Harness index={0} />)
    const el = screen.getByTestId('row-entrance')
    await waitFor(() => expect(Number(el.style.opacity)).toBeLessThan(1))
    await waitFor(() => expect(Number(el.style.opacity || '1')).toBe(1))
  })
})
