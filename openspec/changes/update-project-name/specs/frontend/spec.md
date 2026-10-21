## MODIFIED Requirements

### Requirement: Empty State Landing Page

当无活动会话时，系统 SHALL 显示空状态首页，包含品牌标识、模式选择器、文本输入框和 API Key 状态指示。

(Previously: 品牌标题为"Finance Analysis Agent"，其余行为相同。)

#### Scenario: 首次进入显示空状态

- **GIVEN** 应用刚加载且无 currentSessionId
- **WHEN** 页面渲染
- **THEN** 显示 logo、标题"Investment Research Agents"、副标题"AI 驱动的 A 股投研分析系统"
- **AND** 显示模式选择器（默认选中"深度研究"）
- **AND** 显示文本输入框，placeholder 随模式变化
- **AND** 显示 4 个特性卡片（4 维并行分析、Bull/Bear 辩论、Risk 压力测试、结构化报告）

#### Scenario: 空状态下输入并发送

- **GIVEN** 空状态首页，API Key 已配置
- **WHEN** 用户在输入框输入文本并按 Enter（非 Shift+Enter）
- **THEN** 系统根据当前模式调用对应流程（深度 -> startAnalysis，快速 -> quickChat）
- **AND** 输入框清空
- **AND** 应用状态从 'empty' 切换到 'clarifying'

#### Scenario: 空状态下未配置 API Key 时发送

- **GIVEN** 空状态首页，API Key 未配置
- **WHEN** 用户输入文本并按 Enter
- **THEN** 不发起请求
- **AND** 弹出 API Key 配置弹窗
