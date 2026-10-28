# Delta for factor-proposal

## MODIFIED Requirements

### Requirement: 外部研发供给接入

系统 SHALL 提供外部研发供给（RD-Agent / Qlib 生态等外部研究员）的候选因子导入通道：外部候选以**合法 DSL 表达式**进入与本地提案完全相同的五道闸，来源标注 `source="external"`（与 `proposal`/`builtin` 并列）；逐条裁决留事件史，单条失败不阻断整批。表达式不可解析或超出 DSL 能力边界的候选 SHALL 计入 `errors` 桶拒收留痕，MUST NOT 绕闸手工入库。本系统因子库 SHALL 保持唯一生产因子真源：外部因子库（如 RD-Agent 的 SOTA 库）MUST NOT 作为生产因子来源被直接采信；外部回测结论（IC/收益等）MUST NOT 替代闸口读数，未过闸的外部候选 MUST NOT 入库。系统 SHALL 另提供**自动收割通道**（日批）：从采集器候选文件读取未处理的 RD-Agent 因子块（`name`/`description`/`formulation`）→ 经「外部表达式翻译契约」转译为合法 DSL → 进入与导入通道**同一五道闸**（`source="external"`）；已处理候选 SHALL 记录状态（重复运行 MUST NOT 重复导入）；转译失败 / 显式不可翻译 / 预检失败的候选 SHALL 逐条计入 `errors` 桶留痕（单条失败不阻断整批）；LLM 网关不可用时收割任务 SHALL fail loud（不静默跳过、不伪造候选）；拒收记录 SHALL 携带对应闸口的判定读数（`detail`：阈值/统计量等）。转译响应**不合载荷合同**（非合法 JSON / 缺必需字段）时，转译 SHALL 重试，重试上限 2 次（共 3 次尝试，与「提案输出合同」修复重试同款上限）；重试耗尽仍不合合同 SHALL 记 `transcription_error` 留痕（含尝试次数）并计入 `errors` 桶；**LLM 网关级不可用不重试**——逐字维持上款 fail loud 语义。收割通道 SHALL 支持**按已记录状态重放**：调用方指定状态集合时，状态命中该集合的已处理候选 SHALL 重新进入「转译 → 翻译 → 过闸」链路（新裁决覆盖原状态记录，并保留来源状态供审计）；重放候选 SHALL 先于未处理候选进入批次（单批上限截断下不被挤压）；未指定状态集合时行为逐字不变（幂等语义不受影响）。

收割通道入库的 `category` SHALL 按**候选机理**映射到固定桶集：**量与流动性 / 波动率 / 动量反转 / 量价交互 / 外部-其他**（兜底桶）。映射 SHALL 为确定性关键词表（对候选的机理标签——hypothesis 中的机理短语——与因子名做关键词匹配；映射表随产物留存可审计，零 LLM 成本），全部未命中 SHALL 落兜底桶 `外部-其他`（分类失败 MUST NOT 影响准入裁决——桶归属错置的代价仅限桶内保种排名，兜底桶保底不丢因子）。`source="external"` 标注语义不变。存量 `category="外部"` 的因子 SHALL 按同一映射一次性重分类（幂等可重跑，事件史留迁移记录）。人工导入通道（非收割）的 category 语义不变。

(Previously: 收割通道入库 category 硬编码为单值「外部」——全部外部因子共居一个分桶保种桶（top-k=10 全局单桶），量/波动/反转/量价交互同桶互挤，分桶保种机制对收割链路不生效。)

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
- **THEN** 该候选重新执行转译 → 翻译 → 过闸，新裁决覆盖原状态记录并保留来源状态
- **AND** 未命中状态集合的已处理候选不重新进入批次（幂等不受影响）

#### Scenario: 机理桶分类入库

- **GIVEN** 收割候选的机理标签为 Volume/Liquidity（量与流动性族），通过五道闸入库
- **WHEN** 入库完成
- **THEN** 该因子 `category="量与流动性"`，与既有波动率桶因子分属两桶（分桶保种按桶独立生效）
- **AND** 该桶超容时仅桶内末位淘汰，其他桶 active 集合不变

#### Scenario: 关键词未命中落兜底桶

- **GIVEN** 收割候选的机理标签与因子名均未命中任何桶关键词
- **WHEN** 该候选过闸入库
- **THEN** `category="外部-其他"`（兜底桶），准入裁决不受分类影响

#### Scenario: 存量外部因子一次性重分类

- **GIVEN** 库内既有 12 条 `category="外部"` 因子（机理覆盖量、波动、价格-成交量交互等族）
- **WHEN** 执行存量重分类（重跑一次）
- **THEN** 各因子按同一确定性映射获得对应桶 category，事件史留迁移记录，重跑结果不变（幂等）
