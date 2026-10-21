// SettingsCenterPage 设置中心页壳（add-agent-settings-center Task 7）
// 左侧垂直导航六分区 + 右侧内容区；initialModule 定位激活分区。
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { SettingsCenterPage } from '../../pages/settings/SettingsCenterPage'
import { emptyLlmConfig } from '../../llmConfig'


const baseProps = {
  config: emptyLlmConfig(),
  backendDefaults: { model: 'deepseek/deepseek-chat', baseUrl: '', thinking: 'enabled' },
  profileStore: { profiles: [], activeId: '' },
  capability: null,
  onProbeCapability: vi.fn(),
  onSave: vi.fn(),
  onSaveAs: vi.fn(),
  onSwitchProfile: vi.fn(),
  onDeleteProfile: vi.fn(),
  onBack: vi.fn(),
}

describe('SettingsCenterPage', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('默认激活 LLM 配置分区并显示其内容', () => {
    render(<SettingsCenterPage {...baseProps} />)
    // 左侧导航含各分区（含 delta add-factor-research-settings 新增的因子研发配置）
    expect(screen.getByText('缓存管理')).toBeInTheDocument()
    expect(screen.getByText('会话管理')).toBeInTheDocument()
    expect(screen.getByText('运行信息')).toBeInTheDocument()
    expect(screen.getByText('数据监控')).toBeInTheDocument()
    expect(screen.getByText('因子研发配置')).toBeInTheDocument()
    expect(screen.getByText('战绩展示偏好')).toBeInTheDocument()
  })

  it('点击左侧导航切换右侧内容区', async () => {
    // 切换会挂载 CachePane 并拉取 /api/cache/stats，stub 掉该请求（返回空统计）消除 act() 噪音
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: { entries: 0, bytes: 0, expired: 0, permanent: 0, per_type: [] },
      probe: { entries: 0, expired: 0 },
      monitor: { hits: 0, misses: 0, fails: {}, last_hit: null },
    }))))
    render(<SettingsCenterPage {...baseProps} />)
    // 断言聚焦内容区：点击导航后右侧渲染 cache-pane（而非重复匹配导航按钮本身）
    fireEvent.click(screen.getByTestId('settings-nav-cache'))
    expect(await screen.findByTestId('cache-pane')).toBeInTheDocument()
  })

  it('initialModule=llm 时定位到 LLM 配置分区', () => {
    render(<SettingsCenterPage {...baseProps} initialModule="llm" />)
    // 出现 LLM 配置表单字段（如 Provider 预设）
    expect(screen.getByText(/Provider/)).toBeInTheDocument()
  })
})
