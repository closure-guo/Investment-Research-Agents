import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AiBadge, AI_BADGE_HINT } from '../../components/AiBadge'
import { AI_ACCENT } from '../../design/tokens'

// AI 角标测试（unify-ui-design-system T4.2）：
// spec 场景：LLM 生成内容携带紫系角标，hover 出现「由模型生成，仅供参考」。
describe('AiBadge AI 生成内容角标', () => {
  it('渲染紫系角标并可被无障碍读屏识别', () => {
    render(<AiBadge />)
    const badge = screen.getByTestId('ai-badge')
    expect(badge).toHaveAttribute('aria-label', AI_BADGE_HINT)
    expect(badge.style.color).toBe(AI_ACCENT.fg)
    expect(badge.style.backgroundColor).toBe(AI_ACCENT.bg)
    expect(badge.style.borderColor).toBe(AI_ACCENT.border)
  })

  it('hover 展示规定提示文案（精确文本）', async () => {
    const user = userEvent.setup()
    render(<AiBadge />)
    const badge = screen.getByTestId('ai-badge')
    expect(screen.queryByText('由模型生成，仅供参考')).not.toBeInTheDocument()
    await user.hover(badge)
    const tooltip = await screen.findByText('由模型生成，仅供参考')
    expect(tooltip).toBeInTheDocument()
    expect(AI_BADGE_HINT).toBe('由模型生成，仅供参考')
  })

  it('键盘聚焦同样可触达提示（可访问性）', async () => {
    const user = userEvent.setup()
    render(<AiBadge />)
    await user.tab()
    expect(screen.getByTestId('ai-badge')).toHaveFocus()
    expect(await screen.findByText('由模型生成，仅供参考')).toBeInTheDocument()
    await user.tab()
    await waitFor(() => {
      expect(screen.queryByText('由模型生成，仅供参考')).not.toBeInTheDocument()
    })
  })

  it('角标小巧不喧宾夺主：默认文案 AI + 小号状态类', () => {
    render(<AiBadge />)
    const badge = screen.getByTestId('ai-badge')
    expect(badge.textContent).toBe('AI')
    expect(badge.className).toMatch(/text-\[10px\]/)
  })
})
