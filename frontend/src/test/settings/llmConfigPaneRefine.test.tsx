// refine-llm-settings-pane：LLM 配置分区三段分组、只写密钥与字段级校验。
// 契约见 openspec/changes/refine-llm-settings-pane/specs/llm-config/spec.md。
import { render, screen, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LlmConfigPane } from '../../pages/settings/panes/LlmConfigPane'
import type { CapabilityMatrix, LLMConfig, ProfileStore } from '../../llmConfig'

function cfg(over: Partial<LLMConfig> = {}): LLMConfig {
  return { apiKey: '', model: '', baseUrl: '', thinking: '', ...over }
}

const noop = vi.fn()

function capability(over: Partial<CapabilityMatrix> = {}): CapabilityMatrix {
  return {
    non_stream: true,
    stream: true,
    tool_call: true,
    tool_followup: true,
    json_output: true,
    ...over,
  }
}

function renderPane({
  config = cfg(),
  capability: capabilityProp = null,
  store = { profiles: [], activeId: '' } as ProfileStore,
  onSave = noop,
}: {
  config?: LLMConfig
  capability?: CapabilityMatrix | null
  store?: ProfileStore
  onSave?: (cfg: LLMConfig) => void
} = {}) {
  return render(
    <LlmConfigPane
      config={config}
      backendDefaults={{ model: 'deepseek/deepseek-chat', baseUrl: '', thinking: 'enabled' }}
      profileStore={store}
      capability={capabilityProp}
      onProbeCapability={noop}
      onSave={onSave}
      onSaveAs={noop}
      onSwitchProfile={noop}
      onDeleteProfile={noop}
    />,
  )
}

describe('LlmConfigPane 三段分组', () => {
  it('渲染连接 / 模型 / 高级三段标题，高级段折叠', () => {
    renderPane()

    expect(screen.getByRole('heading', { name: '连接' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '模型' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '高级' })).toBeInTheDocument()

    // 高级段使用 <details> 折叠，默认未展开
    const advanced = screen.getByTestId('llm-advanced-section')
    expect(advanced.tagName).toBe('DETAILS')
    expect((advanced as HTMLDetailsElement).open).toBe(false)

    // 三段归属：连接段含 Provider 预设卡片、Base URL、API Key、测试连接
    expect(screen.getByText('Provider 预设')).toBeInTheDocument()
    expect(screen.getByTestId('base-url-input')).toBeInTheDocument()
    expect(screen.getByTestId('api-key-input')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /测试连接/ })).toBeInTheDocument()

    // 模型段含模型名、上下文窗口、最大输出
    expect(screen.getByPlaceholderText('deepseek/deepseek-chat')).toBeInTheDocument()
    expect(screen.getByTestId('context-length-input')).toBeInTheDocument()
    expect(screen.getByTestId('max-output-value')).toBeInTheDocument()
  })

  it('Provider 预设以卡片按钮呈现，选中项高亮（aria-pressed）', () => {
    renderPane({ config: cfg({ model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1', thinking: 'enabled' }) })

    const cards = screen.getAllByRole('button', { pressed: true })
    expect(cards.some(c => c.textContent?.includes('DeepSeek 官方'))).toBe(true)

    // 点击其它预设触发填充（仍调用 applyPreset 语义）
    fireEvent.click(screen.getByRole('button', { name: /本地 Ollama/ }))
    expect((screen.getByPlaceholderText('deepseek/deepseek-chat') as HTMLInputElement).value).toBe('openai/llama3')
    expect((screen.getByTestId('base-url-input') as HTMLInputElement).value).toBe('http://localhost:11434/v1')
  })
})

describe('LlmConfigPane API Key 只写', () => {
  it('已保存密钥不回显，眼睛按钮在掩码与明文之间切换', () => {
    renderPane({ config: cfg({ apiKey: 'sk-saved', model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1' }) })

    const keyInput = screen.getByTestId('api-key-input') as HTMLInputElement
    expect(keyInput.value).toBe('')
    expect(keyInput.type).toBe('password')
    expect(keyInput.placeholder).toContain('已保存')

    fireEvent.click(screen.getByRole('button', { name: '显示密钥' }))
    expect(keyInput.type).toBe('text')

    fireEvent.click(screen.getByRole('button', { name: '隐藏密钥' }))
    expect(keyInput.type).toBe('password')
  })

  it('未输入新密钥直接保存时，onSave 收到原 apiKey', () => {
    const onSave = vi.fn()
    renderPane({
      config: cfg({ apiKey: 'sk-original', model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1' }),
      onSave,
    })

    fireEvent.click(screen.getByRole('button', { name: '确认' }))

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave.mock.calls[0][0]).toMatchObject({ apiKey: 'sk-original' })
  })

  it('输入新密钥后保存时，onSave 收到新值', () => {
    const onSave = vi.fn()
    renderPane({
      config: cfg({ apiKey: 'sk-original', model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1' }),
      onSave,
    })

    fireEvent.change(screen.getByTestId('api-key-input'), { target: { value: 'sk-new' } })
    fireEvent.click(screen.getByRole('button', { name: '确认' }))

    expect(onSave.mock.calls[0][0]).toMatchObject({ apiKey: 'sk-new' })
  })
})

describe('LlmConfigPane 字段级校验', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('上下文长度填 0 时显示字段错误且不调用 onSave', () => {
    const onSave = vi.fn()
    renderPane({
      config: cfg({ model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1' }),
      onSave,
    })

    fireEvent.change(screen.getByTestId('context-length-input'), { target: { value: '0' } })
    fireEvent.click(screen.getByRole('button', { name: '确认' }))

    expect(screen.getByTestId('context-length-error')).toBeInTheDocument()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('上下文长度填 abc 时显示字段错误且不调用 onSave（spec 场景）', () => {
    const onSave = vi.fn()
    renderPane({
      config: cfg({ model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1' }),
      onSave,
    })

    fireEvent.change(screen.getByTestId('context-length-input'), { target: { value: 'abc' } })
    fireEvent.click(screen.getByRole('button', { name: '确认' }))

    expect(screen.getByTestId('context-length-error')).toBeInTheDocument()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('Base URL 填 ftp://x 时显示字段错误且不调用 onSave', () => {
    const onSave = vi.fn()
    renderPane({
      config: cfg({ model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1' }),
      onSave,
    })

    fireEvent.change(screen.getByTestId('base-url-input'), { target: { value: 'ftp://x' } })
    fireEvent.click(screen.getByRole('button', { name: '确认' }))

    expect(screen.getByTestId('base-url-error')).toBeInTheDocument()
    expect(onSave).not.toHaveBeenCalled()
  })
})

describe('LlmConfigPane 最大输出只读展示', () => {
  it('已探测时展示 capability.max_output', () => {
    renderPane({ capability: capability({ max_output: 8192 }) })

    expect(screen.getByTestId('max-output-value')).toHaveTextContent('8192')
  })

  it('未探测时提示「测试连接后探测」', () => {
    renderPane()

    expect(screen.getByTestId('max-output-value')).toHaveTextContent('测试连接后探测')
  })
})
