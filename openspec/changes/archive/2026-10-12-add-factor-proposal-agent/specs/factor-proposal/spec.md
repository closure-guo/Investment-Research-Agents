# factor-proposal delta: add-factor-proposal-agent

## ADDED Requirements

### Requirement: 提案输出合同

因子提案节点的每条提案 SHALL 包含 `expression`（合法 DSL 字符串）、`hypothesis`（经济学假设，≥30 字）、`category`（枚举：价量/反转/动量/流动性/波动率）；`expression` 无法通过 DSL 解析时 SHALL 触发修复重试，重试上限 2 次后仍失败的提案 SHALL 被丢弃并在 Langfuse trace 中标记 `proposal_rejected`。

#### Scenario: 合同违约修复流程

- **GIVEN** LLM 首轮返回的 expression 含未知算子
- **WHEN** 合同校验执行
- **THEN** 系统以解析错误消息为上下文发起修复重试
- **AND** 第 3 次返回仍不可解析时，该提案被丢弃且 trace 标记 `proposal_rejected`

### Requirement: 双模型分工

广度变异提案 SHALL 路由至 quick profile（每批次产出不少于 20 条候选）；基于因子库反馈（拒收原因分布、衰减因子复盘）的深度改进提案 SHALL 路由至 deep profile。

#### Scenario: 用途路由

- **GIVEN** 一次广度变异批次与一次深度改进批次
- **WHEN** 分别发起 LLM 调用
- **THEN** 前者经 PolicyRouter 解析到 quick profile，后者解析到 deep profile
- **AND** 两条调用在 Langfuse trace 中均带用途标签 `factor_proposal`

### Requirement: 跨桶采样防收敛

提案 prompt 组装时 SHALL 注入因子库当前分桶统计与各算子历史入库 IC 均值；当某一分桶的存量因子占比超过 50% 时，prompt SHALL 显式要求候选偏向其他分桶。

#### Scenario: 单桶占优时的引导

- **GIVEN** 因子库中动量类占比 70%
- **WHEN** 组装广度变异 prompt
- **THEN** prompt 文本包含动量桶占比信息与偏向其他桶的显式指令
