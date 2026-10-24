## MODIFIED Requirements

### Requirement: Chat Input Bar

系统 SHALL 在非空状态下显示固定的底部输入栏，包含模式切换、文本输入、发送功能，以及输入框下方的计量条（会话统计、Token 用量、上下文已用）。输入栏 SHALL NOT 显示免责声明；免责声明只保留在报告页脚与导出产物中。

(Previously: 系统 SHALL 在非空状态下显示固定的底部输入栏，包含模式切换、文本输入和发送功能。底部显示「AI 生成仅供参考，不构成投资建议」。)

#### Scenario: 底部输入栏渲染

- **GIVEN** appState 不为 'empty'
- **THEN** 底部固定显示输入栏，包含深度/快速模式切换按钮、textarea 输入框、发送按钮
- **AND** 输入框下方显示计量条（数据缺失的控件按 `composer-stat-pills` 规则隐藏）
- **AND** 输入栏不再显示「AI 生成仅供参考，不构成投资建议」

#### Scenario: 输入栏发送

- **GIVEN** 底部输入栏，当前模式为 'deep'
- **WHEN** 用户输入文本并按 Enter（非 Shift+Enter）
- **THEN** 调用 startAnalysis 发送文本
- **AND** 输入框清空

#### Scenario: 输入栏模式切换锁定

- **GIVEN** currentSessionId 不为 null（已有会话）
- **THEN** 模式切换按钮禁用，显示锁定图标
- **GIVEN** currentSessionId 为 null
- **THEN** 模式切换按钮可用
