// ComposerStats：输入框下方三枚计量控件（add-composer-stat-pills）
// 契约：真实值展示、缺失各自隐藏、弹层互斥且 Escape/外部点击关闭。
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { ComposerStats, formatCompactDuration, formatCompactNumber } from '../../components/ComposerStats'

const FULL_STATS = {
  turns: 2,
  steps: 12,
  duration_ms: 192000,
  tokens_prompt: 120000,
  tokens_completion: 8000,
  tokens_total: 128000,
  context_peak_tokens: 45000,
}

function mockFetch(body: unknown) {
  const fn = vi.fn().mockResolvedValue({ ok: true, json: async () => body })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('ComposerStats', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('渲染三枚控件与真实数值', async () => {
    mockFetch(FULL_STATS)
    render(<ComposerStats sessionId="s1" contextWindow={200000} />)

    await waitFor(() => expect(screen.getByText('2 轮 12 步')).toBeInTheDocument())
    expect(screen.getByText('12.8万')).toBeInTheDocument()
    expect(screen.getByText('23%')).toBeInTheDocument()
    expect(screen.getByTestId('composer-stats')).toBeInTheDocument()
  })

  it('无 token 记账时隐藏 Token 用量控件', async () => {
    mockFetch({ ...FULL_STATS, tokens_prompt: 0, tokens_completion: 0, tokens_total: 0 })
    render(<ComposerStats sessionId="s1" contextWindow={200000} />)

    await waitFor(() => expect(screen.getByText('2 轮 12 步')).toBeInTheDocument())
    expect(screen.queryByText('12.8万')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Token 用量')).not.toBeInTheDocument()
    expect(screen.getByLabelText('上下文已用')).toBeInTheDocument()
  })

  it('无上下文峰值时隐藏上下文控件', async () => {
    mockFetch({ ...FULL_STATS, context_peak_tokens: 0 })
    render(<ComposerStats sessionId="s1" contextWindow={200000} />)

    await waitFor(() => expect(screen.getByText('2 轮 12 步')).toBeInTheDocument())
    expect(screen.queryByLabelText('上下文已用')).not.toBeInTheDocument()
  })

  it('三项均无数据时不渲染计量条', async () => {
    mockFetch({ ...FULL_STATS, steps: 0, tokens_total: 0, context_peak_tokens: 0 })
    render(<ComposerStats sessionId="s1" contextWindow={200000} />)

    await waitFor(() => expect(screen.queryByTestId('composer-stats')).not.toBeInTheDocument())
  })

  it('点击打开面板并展示明细，Escape 关闭', async () => {
    mockFetch(FULL_STATS)
    render(<ComposerStats sessionId="s1" contextWindow={200000} />)

    const activity = await screen.findByLabelText('会话统计')
    fireEvent.click(activity)
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('管线用时')).toBeInTheDocument()
    expect(screen.getByText('3m12s')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('弹层互斥：打开 Token 用量后会话统计面板关闭', async () => {
    mockFetch(FULL_STATS)
    render(<ComposerStats sessionId="s1" contextWindow={200000} />)

    fireEvent.click(await screen.findByLabelText('会话统计'))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Token 用量'))
    await waitFor(() => expect(screen.getAllByRole('dialog')).toHaveLength(1))
    expect(screen.getByText('输入')).toBeInTheDocument()
    expect(screen.queryByText('管线用时')).not.toBeInTheDocument()
  })

  it('无会话时不请求 stats', () => {
    const fn = mockFetch(FULL_STATS)
    render(<ComposerStats sessionId={null} />)
    expect(fn).not.toHaveBeenCalled()
  })
})

describe('ComposerStats 格式化', () => {
  it('大数用万/亿', () => {
    expect(formatCompactNumber(999)).toBe('999')
    expect(formatCompactNumber(12800)).toBe('1.3万')
    expect(formatCompactNumber(128000)).toBe('12.8万')
    expect(formatCompactNumber(250000000)).toBe('2.5亿')
  })

  it('时长在分钟边界切换', () => {
    expect(formatCompactDuration(45200)).toBe('45.2s')
    expect(formatCompactDuration(162000)).toBe('2m42s')
    expect(formatCompactDuration(0)).toBe('0s')
  })
})
