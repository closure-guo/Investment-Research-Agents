# frontend delta: add-factor-library-page

## ADDED Requirements

### Requirement: 因子库页

系统 SHALL 提供 `/factors` 路由页，采用 PageShellB（筛选栏 + 数据表 + 行展开详情）形态：筛选栏 SHALL 支持状态（全部 / active / retired）与来源（全部 / builtin / llm）过滤；表格列 SHALL 含 名称、表达式（截断）、状态、来源、ICIR 与衰减标记；状态 SHALL 使用 StatusBadge，`llm` 来源 SHALL 携带 AiBadge；行展开 SHALL 展示完整表达式、IC 序列与事件历史；空库时 SHALL 如实展示空态文案。页面入口 SHALL 同源提供于侧边栏与命令面板。

#### Scenario: 筛选与徽章

- **GIVEN** 库中含 active/llm 与 retired/builtin 两条因子
- **WHEN** 选择来源筛选 `llm`
- **THEN** 仅显示 active/llm 因子，其来源带 AiBadge、状态为 StatusBadge active
- **AND** retired 因子被过滤

#### Scenario: 行展开详情

- **GIVEN** 列表中的一条因子
- **WHEN** 展开该行
- **THEN** 展示完整 DSL 表达式、IC 序列与事件历史（含时间与事件类型）
