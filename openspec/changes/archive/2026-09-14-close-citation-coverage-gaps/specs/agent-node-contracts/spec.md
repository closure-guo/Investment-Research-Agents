## ADDED Requirements

### Requirement: 节点产出键 ⊆ AnalysisState 声明与图通道（系统性门禁）

任一节点写入 state 的键 SHALL 在 `AnalysisState` 声明且已建图通道——LangGraph 对未声明的 TypedDict 键在合并时**静默丢弃**（incident 027 教训：`price_check` 家族/citation 回路键曾因此整条哑火，fail 打回从未生效）。

系统 SHALL 提供系统性门禁测试（替代逐键补守卫）：对产出键集合可枚举的关键节点（至少 `compute_metrics`），以其全部可选分支的构造 state 运行节点，断言产出键 ⊆ `AnalysisState.__annotations__` ⊆ 编译图的 `channels`。新键未声明即测试红并列出键名。

#### Scenario: 未声明键被门禁拦截

- **WHEN** 某节点返回 `AnalysisState` 未声明的键
- **THEN** 门禁测试 SHALL 失败并列出未声明键（附「会被图静默丢弃」说明）

#### Scenario: 声明但未建通道同样红

- **WHEN** 键已声明但未出现在编译图的 `channels`（如声明位置不被图 schema 采用）
- **THEN** 门禁测试 SHALL 失败
