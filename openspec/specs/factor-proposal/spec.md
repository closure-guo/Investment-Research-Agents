# factor-proposal Specification

## Purpose
TBD - created by archiving change add-factor-proposal-agent. Update Purpose after archive.
## Requirements
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

### Requirement: 提案月批排程

系统 SHALL 将因子提案注册为统一日批通道的**月批任务**（每月 1 日 10:00 Asia/Shanghai）：依序运行两类提案批——① 广度变异批（按跨桶采样）② 深度改进批（上下文 SHALL 含拒收原因分布与衰减复盘，经 `deep` 档位路由）；两类批的候选 SHALL 逐条经五道闸准入（各自成批次族、各自一次闸③调用）——过闸者入因子库（active）、未过者留事件史（原因码），逐条裁决（单条失败不阻断整批）；运行历史 SHALL 含 `{proposed, admitted, rejected, errors}` 计数（两批合计）、`deep` 分桶计数与手动补跑通道；LLM 网关不可用时任务 SHALL fail loud（不静默、不伪造候选）。

#### Scenario: 提案过闸入库

- **GIVEN** 广度批产出 3 条候选（其中 1 条通过五道闸）且深度批产出 0 条
- **WHEN** 月批任务运行
- **THEN** 1 条入因子库（active）、2 条留事件史（含未过闸原因）
- **AND** 摘要 `{proposed: 3, admitted: 1, rejected: 2}`，`deep` 分桶计数全 0

#### Scenario: 网关不可用 fail loud

- **GIVEN** LLM 网关整体不可用
- **WHEN** 月批任务运行
- **THEN** 运行历史记 failed 且错误可判读
- **AND** 因子库零新增（MUST NOT 以 builtin 顶替或静默跳过）

### Requirement: 外部研发供给接入

系统 SHALL 提供外部研发供给（RD-Agent / Qlib 生态等外部研究员）的候选因子导入通道：外部候选以**合法 DSL 表达式**进入与本地提案完全相同的五道闸，来源标注 `source="external"`（与 `proposal`/`builtin` 并列）；逐条裁决留事件史，单条失败不阻断整批。表达式不可解析或超出 DSL 能力边界的候选 SHALL 计入 `errors` 桶拒收留痕，MUST NOT 绕闸手工入库。本系统因子库 SHALL 保持唯一生产因子真源：外部因子库（如 RD-Agent 的 SOTA 库）MUST NOT 作为生产因子来源被直接采信；外部回测结论（IC/收益等）MUST NOT 替代闸口读数，未过闸的外部候选 MUST NOT 入库。

#### Scenario: 外部候选过闸入库

- **GIVEN** 外部研究员产出一条候选、翻译为合法 DSL 表达式且通过五道闸
- **WHEN** 导入通道执行
- **THEN** 该因子入库为 active 且 `source="external"`、事件史含逐闸结论
- **AND** 摘要计数与本地提案同一口径（admitted/rejected/errors）

#### Scenario: 不可表达候选拒收留痕

- **GIVEN** 外部候选使用 DSL 无对应算子的表达式
- **WHEN** 导入通道执行
- **THEN** 该候选计入 `errors` 桶（gate=None）留痕，整批其余候选照常裁决

#### Scenario: 外部结论不替代闸口读数

- **GIVEN** 外部回测显示某候选 IC 显著为正，但闸② |ICIR| 不达标
- **WHEN** 导入通道执行
- **THEN** 该候选被拒收（以闸口读数为准），外部结论仅留事件史备注

### Requirement: 外部表达式翻译契约

外部表达式翻译器（Qlib Alpha 表达式 → 本系统 DSL）SHALL 作**二值裁决**：输出语义严格等价的合法 DSL 表达式，或显式「不可翻译」结论（附缺失/语义不等价算子清单）；MUST NOT 输出语义近似的猜测翻译。逐算子映射 SHALL 可审计（映射表随产物留存）；字段与口径映射（如 `$close` → `close`、复权口径差异）SHALL 在映射表显式声明。翻译产物 SHALL 经「外部研发供给接入」通道过闸；「不可翻译」候选计入 `errors` 桶（gate=None）。

#### Scenario: 严格等价翻译

- **GIVEN** 外部表达式 `Mean($close, 5)`（算子与窗口语义与本 DSL `ts_mean` 严格等价）
- **WHEN** 翻译执行
- **THEN** 输出 `ts_mean(close,5)` 且映射表记录该算子映射
- **AND** 产物作为 `source=external` 候选进入五道闸

#### Scenario: 无对应算子不可翻译

- **GIVEN** 外部表达式含本 DSL 无对应算子的操作（如滚动分位数 `Quantile`）
- **WHEN** 翻译执行
- **THEN** 输出「不可翻译」结论并附缺失算子清单，该候选计入 `errors` 桶
- **AND** 其余候选照常翻译与过闸（单条失败不阻断整批）

#### Scenario: 语义近似拒绝猜测

- **GIVEN** 外部算子可机械替换但语义不严格等价（如同名算子的窗口含当日口径不同）
- **WHEN** 翻译执行
- **THEN** 拒绝输出猜测翻译，按「不可翻译」处理并在结论中列明语义差异
- **AND** 映射表留档该算子的差异说明（供后续扩 DSL 能力时参考）

### Requirement: 拒收原因回喂

深度改进提案的 prompt 上下文 SHALL 包含近期批次（可配置窗口）的闸口拒收原因码分布（gate/reason 聚合），实现「提案 → 拒收 → 针对性改进」的反馈闭环；拒收分布为空时 prompt SHALL 如实标注「暂无拒收记录」而非省略该节。

#### Scenario: 拒收分布注入提案上下文

- **GIVEN** 近期批次存在闸口拒收记录（如闸② WEAK_ICIR 为主）
- **WHEN** 组装深度改进提案上下文
- **THEN** prompt 包含按 gate/reason 聚合的拒收分布，且改进指令与主因对应

#### Scenario: 无拒收记录时的诚实标注

- **GIVEN** 因子库与运行历史中无任何拒收记录
- **WHEN** 组装深度改进提案上下文
- **THEN** prompt 如实标注「暂无拒收记录」，不虚构分布

