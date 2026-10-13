import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import css from '../../index.css?raw'
import { StatusBadge } from '../../components/StatusBadge'
import { BADGE_TOKENS, STATUS_KEYS } from '../../design/tokens'
import { BADGE_TRANSITION_MS } from '../../design/motion'

// 五态徽章测试（unify-ui-design-system T4.1）：
// 五态渲染契约 + running 呼吸动画 + prefers-reduced-motion 双重降级
// （CSS 媒体查询在 index.css 内 + JS 侧 useReducedMotion 门控，后者可被 mock 验证）。
const motionState = vi.hoisted(() => ({ reduced: false }))
vi.mock('framer-motion', async importOriginal => {
  const actual = await importOriginal<typeof import('framer-motion')>()
  return { ...actual, useReducedMotion: () => motionState.reduced }
})

describe('StatusBadge 五态徽章', () => {
  it('五态均可渲染：testid / data-status / 默认文案与规范一致', () => {
    for (const status of STATUS_KEYS) {
      const { unmount } = render(<StatusBadge status={status} />)
      const el = screen.getByTestId('status-badge')
      expect(el).toHaveAttribute('data-status', status)
      expect(BADGE_TOKENS[status].label).toBeTruthy()
      expect(el.textContent).toBe(BADGE_TOKENS[status].label)
      unmount()
    }
  })

  it('五态配色取自令牌变量（bg/fg/border 三通道）', () => {
    for (const status of STATUS_KEYS) {
      const { unmount } = render(<StatusBadge status={status} />)
      const el = screen.getByTestId('status-badge')
      expect(el.style.backgroundColor).toBe(BADGE_TOKENS[status].bg)
      expect(el.style.color).toBe(BADGE_TOKENS[status].fg)
      expect(el.style.borderColor).toBe(BADGE_TOKENS[status].border)
      unmount()
    }
  })

  it('自定义 label 覆盖默认文案（步骤级状态复用）', () => {
    render(<StatusBadge status="running" label="拉取行情" />)
    expect(screen.getByTestId('status-badge').textContent).toBe('拉取行情')
    expect(screen.getByTestId('status-badge')).toHaveAttribute('data-status', 'running')
  })

  it('running 态带呼吸动画类；其余四态无动画', () => {
    const { unmount } = render(<StatusBadge status="running" />)
    expect(screen.getByTestId('status-badge').className).toContain('status-badge-running')
    unmount()
    for (const status of ['active', 'degraded', 'retired', 'failed'] as const) {
      const { unmount: u } = render(<StatusBadge status={status} />)
      expect(screen.getByTestId('status-badge').className).not.toContain('status-badge-running')
      u()
    }
  })

  it('基础类始终携带 transition 契约 + Tailwind reduced-motion 兜底', () => {
    render(<StatusBadge status="active" />)
    const el = screen.getByTestId('status-badge')
    expect(el.className).toContain('status-badge')
    expect(el.className).toContain('motion-reduce:animate-none')
  })

  it('prefers-reduced-motion（useReducedMotion）时不挂动画类，内容直接呈现', () => {
    motionState.reduced = true
    try {
      render(<StatusBadge status="running" />)
      const el = screen.getByTestId('status-badge')
      expect(el.className).not.toContain('status-badge-running')
      // 降级不等于隐藏：文案与配色照常
      expect(el.textContent).toBe('运行中')
      expect(el.style.backgroundColor).toBe(BADGE_TOKENS.running.bg)
    } finally {
      motionState.reduced = false
    }
  })
})

describe('StatusBadge 样式契约落在 index.css（新增选择器，不动既有规则）', () => {
  it('呼吸动画 keyframes 与运行态类已定义', () => {
    expect(css).toMatch(/@keyframes\s+badge-breath/)
    expect(css).toMatch(/\.status-badge-running\s*\{[^}]*animation:\s*badge-breath[^}]*infinite/)
  })

  it('徽章变色过渡 200ms ease-out（与 BADGE_TRANSITION_MS 对齐）', () => {
    expect(BADGE_TRANSITION_MS).toBe(200)
    expect(css).toMatch(/\.status-badge\s*\{[^}]*background-color 200ms ease-out/)
    expect(css).toMatch(/\.status-badge\s*\{[^}]*color 200ms ease-out/)
    expect(css).toMatch(/\.status-badge\s*\{[^}]*border-color 200ms ease-out/)
  })

  it('prefers-reduced-motion 媒体查询下动画与过渡均禁用', () => {
    const idx = css.indexOf('@media (prefers-reduced-motion: reduce)')
    expect(idx).toBeGreaterThan(-1)
    const reduceBlock = css.slice(idx)
    expect(reduceBlock).toMatch(/\.status-badge-running\s*\{[^}]*animation:\s*none/)
    expect(reduceBlock).toMatch(/\.status-badge\s*\{[^}]*transition:\s*none/)
  })
})
