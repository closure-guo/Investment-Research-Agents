// ChatInputBar 集成：计量条替换免责声明（add-composer-stat-pills）
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatInputBar } from '../../App'

const STATS = {
  turns: 1,
  steps: 3,
  duration_ms: 1000,
  tokens_prompt: 900,
  tokens_completion: 100,
  tokens_total: 1000,
  context_peak_tokens: 900,
}

const noop = vi.fn()

describe('ChatInputBar 计量条', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('显示三枚控件且不再显示免责声明', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => STATS }),
    )
    render(
      <ChatInputBar
        onSend={noop}
        leftInset={0}
        mode="deep"
        setMode={noop}
        capability={null}
        onNewAnalysis={noop}
        apiKey="sk-test"
        onOpenSettings={noop}
        profileName="默认"
        profiles={[]}
        activeProfileId="a"
        onSwitchProfile={noop}
        sessionId="s1"
        running={false}
        contextWindow={100000}
      />,
    )

    expect(await screen.findByText('1 轮 3 步')).toBeInTheDocument()
    expect(screen.queryByText('AI 生成仅供参考，不构成投资建议')).not.toBeInTheDocument()
  })

  it('无会话时不渲染计量条也不请求', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    render(
      <ChatInputBar
        onSend={noop}
        leftInset={0}
        mode="deep"
        setMode={noop}
        capability={null}
        onNewAnalysis={noop}
        apiKey="sk-test"
        onOpenSettings={noop}
        profileName="默认"
        profiles={[]}
        activeProfileId="a"
        onSwitchProfile={noop}
      />,
    )
    expect(screen.queryByTestId('composer-stats')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
