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

### Requirement: 收割摘要读数口径

收割通道运行摘要 SHALL 提供 `errors` 桶的构成披露 `errors_breakdown`（`placeholders` = 本批不可翻译与转译失败的占位数；`real` = `errors` 减 `placeholders`，下截 0）；`errors` 总数与「转译失败 / 不可翻译 / 预检失败逐条计入 errors 桶留痕」的既有语义 SHALL 逐字不变。占位条目在导入预检中必失败（占位表达式非合法 DSL），故正常运行 `real` 恒为 0；`real > 0` 即「已转译候选意外未过预检」，是翻译器或编排缺陷的信号。

#### Scenario: 纯拒译批的读数分列

- **GIVEN** 一批 2 条候选全部不可翻译、无已转译候选
- **WHEN** 收割批运行
- **THEN** 摘要 `errors=2`（留痕语义不变）且 `errors_breakdown={"placeholders": 2, "real": 0}`

#### Scenario: 已转译候选意外预检失败单独可见

- **GIVEN** 导入摘要报告 `errors=3`，其中 2 条为本批已知占位
- **WHEN** 收割批构造摘要
- **THEN** `errors_breakdown={"placeholders": 2, "real": 1}`——真实预检失败不必与已知拒译心算区分

### Requirement: 回喂清单配额公平性

跨会话记忆桥导出载荷（已探索清单）SHALL 在分档分配上保证类别级最低代表性：rejected 档内**每个出现过拒收的闸口 SHALL 获得保底出场配额**（默认每闸 ≥5 条，可配置；该闸累计拒收不足保底数时全量出场），保底之外的余量 SHALL 按各闸累计拒收量占比分配；单一闸口 MUST NOT 独占 rejected 档全部出场位。untranslatable 档 SHALL 在既有按日轮转语义之上保证**失败类别（stage）最低代表性**——单一 stage MUST NOT 独占该档配额。保底未用满的空位 SHALL 归还其他类别使用（不得留空位浪费总量）。既有语义 SHALL 零回退：admitted 全量在前、按规范化表达式去重、总量 `limit` 上界、`contract` 块与 `gate_rejections` 聚合照常存在。载荷 SHALL 附配额分配读数（每档与每类别的实际出场条数），供监控页与判效核对。

#### Scenario: 闸②垄断被打破

- GIVEN 累计 rejected 含闸② 395 条与闸④ 84 条（其余闸口拒收量可忽略），`limit=80`、untranslatable 保底配额 20、admitted 12 条
- WHEN 执行记忆桥导出
- THEN rejected 档出场中闸④ ≥5 条（保底命中）
- AND 闸② 出场数少于 rejected 档全部出场位（垄断解除）
- AND 载荷总条数 ≤ `limit`

#### Scenario: 保底不足额全量出场且空位归还

- GIVEN 某闸口累计拒收仅 2 条（< 保底 5）
- WHEN 执行记忆桥导出
- THEN 该闸 2 条全部出场
- AND 未用满的 3 个空位由其余闸口按占比使用，`returned` 总条数不因保底机制下降

#### Scenario: untranslatable 失败类别代表性

- GIVEN untranslatable 档配额 20，translator 段 300 条、LLM 段 60 条
- WHEN 执行记忆桥导出
- THEN 两个 stage 均有出场（各自达到最低代表性）
- AND 档内按日轮转语义保持（早期批次仍可见）

#### Scenario: 配额分配读数随载荷落盘

- GIVEN 任一次成功导出
- WHEN 载荷写出
- THEN 载荷含配额分配读数字段（每档与每类别实际出场条数）
- AND 读数与 `explored` 清单实际构成一致（可程序核对）

#### Scenario: 既有语义零回退

- GIVEN 与上一版相同的收割状态与候选文件（含 admitted、untranslatable、rejected、transcription_error 四档与契约块）
- WHEN 执行记忆桥导出
- THEN admitted 仍全量且位于清单首位、按规范化表达式去重、总量 ≤ `limit`
- AND `contract` 块与 `gate_rejections` 聚合字段照常存在（feed-dsl-contract-to-factor-generator 语义不受影响）

### Requirement: 生成侧 DSL 契约回喂

跨会话因子记忆桥的导出载荷（`explored_factors.json`）SHALL 随已探索清单携带机器可读的 **DSL 表达契约块**（`contract`）：复杂度上限（AST 深度 / 自由参数 / 字段种类，数值取自 `factors.dsl` 复杂度常量单一真相源）、可表达算子白名单与行情字段白名单（取自 `factors.qlib_translate` 算子/字段映射表中**有 DSL 对应**的条目）、表达规则文本——数值常量 MUST 为具体数值（禁用 ε/epsilon 等符号常量）、表达式 MUST NOT 含横截面（当日跨标的）运算、MUST NOT 含矩阵/回归/机器学习流水线。导出载荷 SHALL 另携带闸口拒收计数（gate 编号 → 条数，按已裁决状态聚合）。生成侧消费端（RD-Agent `explored_feedback` 渲染块）SHALL 把契约块渲染进首轮生成 prompt（位于已探索清单之前）；契约块缺失或字段异常时 SHALL 仅渲染已探索清单（fail-safe，MUST NOT 阻断生成、MUST NOT 虚构契约内容）；渲染总量 SHALL 维持字符预算上界并在超限时截断标注。

#### Scenario: 契约块随导出载荷落盘

- GIVEN 收割状态文件（含已裁决候选）与候选文件
- WHEN 执行跨会话记忆桥导出
- THEN 载荷含 `contract` 块：复杂度上限数值与 `factors.dsl` 常量逐字一致、算子白名单与字段白名单均非空且只含有 DSL 对应的条目、规则文本同时覆盖「数值常量必须具体」「禁横截面运算」「禁矩阵/回归/ML」三条
- AND 载荷含按 gate 编号聚合的拒收计数（无拒收时为空聚合而非缺字段）

#### Scenario: 消费端渲染契约进首轮 prompt

- GIVEN 导出载荷含 `contract` 块与已探索清单
- WHEN 生成侧渲染跨会话记忆块
- THEN 渲染产物先呈现契约（规则 + 复杂度上限 + 算子/字段白名单 + 拒收计数）再呈现已探索清单，且总长不超过字符预算

#### Scenario: 契约缺失 fail-safe

- GIVEN 旧版导出载荷（无 `contract` 块，仅有已探索清单）
- WHEN 生成侧渲染跨会话记忆块
- THEN 仅渲染已探索清单，不报错、不虚构契约内容，生成流程不受阻断

#### Scenario: 载荷损坏 fail-safe（既有语义保持）

- GIVEN 反馈文件缺失或 JSON 损坏
- WHEN 生成侧渲染跨会话记忆块
- THEN 渲染返回空串（fail-safe 既有语义逐字保持），生成流程不受阻断

