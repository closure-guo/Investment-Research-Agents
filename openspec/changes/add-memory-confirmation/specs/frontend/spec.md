# frontend delta: add-memory-confirmation

## ADDED Requirements

### Requirement: 记忆确认页

系统 SHALL 提供 `/memory` 路由页，采用 PageShellC（左列表 + 右详情 + 确认/驳回）形态：左侧为语义记忆条款列表并支持 待确认 / 已确认 / 已驳回 三态筛选（默认待确认），右侧为条款详情（完整正文、来源、提炼时间）与「确认」「驳回」动作；条款正文 SHALL 携带 AiBadge（LLM 提炼产物）；条款状态 SHALL 使用 StatusBadge；确认动作旁 SHALL 明示「确认后即刻生效注入风控辩论」；列表为空时 SHALL 如实说明等待月批提炼（MUST NOT 以加载中或错误态冒充）。页面入口 SHALL 同源提供于侧边栏与命令面板。

#### Scenario: 确认流

- **GIVEN** 待确认列表含条款 C
- **WHEN** 用户选中 C 并点击「确认」
- **THEN** 待确认列表移除 C、已确认列表出现 C（状态徽章为已确认）
- **AND** 无页面级报错

#### Scenario: 空态如实

- **GIVEN** 库中无任何 pending 条款
- **WHEN** 打开页面
- **THEN** 展示空态文案（等待月批提炼），MUST NOT 显示骨架屏或错误提示冒充
