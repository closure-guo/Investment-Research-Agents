# Delta for factor-proposal（recover-factor-supply-losses）

## MODIFIED Requirements

### Requirement: 外部研发供给接入

系统 SHALL 提供外部研发供给（RD-Agent / Qlib 生态等外部研究员）的候选因子导入通道：外部候选以**合法 DSL 表达式**进入与本地提案完全相同的五道闸，来源标注 `source="external"`（与 `proposal`/`builtin` 并列）；逐条裁决留事件史，单条失败不阻断整批。表达式不可解析或超出 DSL 能力边界的候选 SHALL 计入 `errors` 桶拒收留痕，MUST NOT 绕闸手工入库。本系统因子库 SHALL 保持唯一生产因子真源：外部因子库（如 RD-Agent 的 SOTA 库）MUST NOT 作为生产因子来源被直接采信；外部回测结论（IC/收益等）MUST NOT 替代闸口读数，未过闸的外部候选 MUST NOT 入库。系统 SHALL 另提供**自动收割通道**（日批）：从采集器候选文件读取未处理的 RD-Agent 因子块（`name`/`description`/`formulation`）→ 经「外部表达式翻译契约」转译为合法 DSL → 进入与导入通道**同一五道闸**（`source="external"`）；已处理候选 SHALL 记录状态（重复运行 MUST NOT 重复导入）；转译失败 / 显式不可翻译 / 预检失败的候选 SHALL 逐条计入 `errors` 桶留痕（单条失败不阻断整批）；LLM 网关不可用时收割任务 SHALL fail loud（不静默跳过、不伪造候选）；拒收记录 SHALL 携带对应闸口的判定读数（`detail`：阈值/统计量等）。转译响应**不合载荷合同**（非合法 JSON / 缺必需字段）时，转译 SHALL 重试，重试上限 2 次（共 3 次尝试，与「提案输出合同」修复重试同款上限）；重试耗尽仍不合合同 SHALL 记 `transcription_error` 留痕（含尝试次数）并计入 `errors` 桶；**LLM 网关级不可用不重试**——逐字维持上款 fail loud 语义。收割通道 SHALL 支持**按已记录状态重放**：调用方指定状态集合时，状态命中该集合的已处理候选 SHALL 重新进入「转译 → 翻译 → 过闸」链路（新裁决覆盖原状态记录，并保留来源状态供审计）；重放候选 SHALL 先于未处理候选进入批次（单批上限截断下不被挤压）；未指定状态集合时行为逐字不变（幂等语义不受影响）。

(Previously: 无转译重试与按状态重放语义——转译响应不合载荷合同即记 `transcription_error` 一次性落账；已处理候选在后续运行中一律不再进入批次。)

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

#### Scenario: 收割批三态混合

- **GIVEN** 候选文件含 3 条未处理因子块（一条可严格转译、一条 LLM 显式不可翻译、一条转译产物超出 DSL 能力边界）
- **WHEN** 收割日批执行
- **THEN** 可转译者过五道闸并留事件史（拒收记录含闸口读数 `detail`）
- **AND** 两条不可翻译者计入 `errors` 桶留痕，整批照常完成

#### Scenario: 收割幂等

- **GIVEN** 同一候选文件已执行过一次收割
- **WHEN** 再次执行收割日批
- **THEN** 零新候选（`proposed=0`）、零 LLM 调用、零重复导入

#### Scenario: 网关不可用 fail loud

- **GIVEN** LLM 网关整体不可用
- **WHEN** 收割日批执行
- **THEN** 任务失败且错误可判读（MUST NOT 静默跳过候选或伪造转译产物）
- **AND** 未处理候选不记状态（网关恢复后重试）

#### Scenario: 转译响应合同失败重试

- **GIVEN** 转译 LLM 首次返回非合法 JSON、第二次返回合法载荷
- **WHEN** 收割批执行
- **THEN** 该候选转译成功（不记 `transcription_error`），verdict 含尝试次数
- **AND** 整批其余候选照常裁决

#### Scenario: 转译重试耗尽留痕

- **GIVEN** 转译 LLM 连续 3 次返回不合载荷合同的响应
- **WHEN** 收割批执行
- **THEN** 该候选记 `transcription_error`（含尝试次数）并计入 `errors` 桶，整批照常完成

#### Scenario: 按状态重放

- **GIVEN** 状态文件中某候选已记 `untranslatable`（翻译器能力边界），且翻译器此后已扩展
- **WHEN** 以该状态集合调用收割（重放）
- **THEN** 该候选重新执行转译 → 翻译 → 过闸，新裁决覆盖旧状态记录并保留来源状态
- **AND** 未命中状态集合的已处理候选不重新进入批次（幂等不受影响）

### Requirement: 外部表达式翻译契约

外部表达式翻译器（Qlib Alpha 表达式 → 本系统 DSL）SHALL 作**二值裁决**：输出语义严格等价的合法 DSL 表达式，或显式「不可翻译」结论（附缺失/语义不等价算子清单）；MUST NOT 输出语义近似的猜测翻译。逐算子映射 SHALL 可审计（映射表随产物留存）；字段与口径映射（如 `$close` → `close`、复权口径差异）SHALL 在映射表显式声明。翻译产物 SHALL 经「外部研发供给接入」通道过闸；「不可翻译」候选计入 `errors` 桶（gate=None）。翻译器 SHALL 支持两类输入形态：① Qlib Alpha 表达式（确定性逐算子映射，即上述裁决）；② RD-Agent 原生形态（formulation 与描述）——经 LLM 辅助转译为 Qlib 表达式后再经 ①，LLM 载荷合同 SHALL 为「合法 Qlib 表达式」XOR「显式 `untranslatable` + 原因」（MUST NOT 猜测/近似）；原始 formulation 与转译链（Qlib 表达式 → DSL）SHALL 随候选 hypothesis 留档。**负数常量 SHALL 以严格等价的减法/加法重排表达**（如 `-1 * x` → `sub(0,x)`、`x - (-k)` → `add(x,k)`、`(-a) * (-b)` → `mul(a,b)`），不再属于不可翻译边界；重排产物 SHALL 经同一复杂度硬约束复验；**负窗口（前瞻引用）仍为红线拒收**。

(Previously: 负数常量为不可翻译边界——`-1 * x` 类形态按 blocker 拒收。)

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

#### Scenario: 原生形态 LLM 辅助转译

- **GIVEN** RD-Agent 因子块含 formulation（数学公式）与描述
- **WHEN** 收割批转译执行
- **THEN** LLM 输出合法 Qlib 表达式（或显式 `untranslatable`+原因），产物经确定性映射再裁决
- **AND** 候选 hypothesis 含原始 formulation 与转译链（Qlib → DSL）供审计

#### Scenario: 负数常量严格等价重排

- **GIVEN** 外部表达式 `-1 * ($close / Ref($close, 1) - 1)`（负数常量形态）
- **WHEN** 翻译执行
- **THEN** 输出严格等价重排 `sub(0,sub(div(close,ref(close,1)),1))`，零 blocker
- **AND** 重排产物经复杂度硬约束复验后进入五道闸；`Ref($close, -1)` 类负窗口仍按「不可翻译」拒收
