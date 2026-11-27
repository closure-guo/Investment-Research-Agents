# Delta for factor-proposal（extend-factor-library-coverage）

## MODIFIED Requirements

### Requirement: 提案输出合同

因子提案节点的每条提案 SHALL 包含 `expression`（合法 DSL 字符串）、`hypothesis`（经济学假设，≥30 字）、`category`（枚举九选一：价量 / 反转 / 动量 / 流动性 / 波动率 / 事件状态 / 规模 / 残差 / 行业相对——后四类分别对应比较计数与上市天数构念、可选参考字段 `float_cap`、市场残差构念 `ts_resid` / `cs_mean`、行业中性化构念 `ind_neutralize`）；`expression` 无法通过 DSL 解析时 SHALL 触发修复重试，重试上限 2 次后仍失败的提案 SHALL 被丢弃并在 Langfuse trace 中标记 `proposal_rejected`。提案 prompt 模板（权威源 `prompts/factor_proposal.md`）列举的算子、字段、类别与复杂度规则 SHALL 与 `factors.dsl` 真相源一致（自由参数按去重数值计数）。

(Previously: category 枚举五选一——价量 / 反转 / 动量 / 流动性 / 波动率。)

#### Scenario: 合同违约修复流程

- **GIVEN** LLM 首轮返回的 expression 含未知算子
- **WHEN** 合同校验执行
- **THEN** 系统以解析错误消息为上下文发起修复重试
- **AND** 第 3 次返回仍不可解析时，该提案被丢弃且 trace 标记 `proposal_rejected`

#### Scenario: 新类别通过合同校验

- **GIVEN** LLM 返回一条提案 `{"expression": "log(float_cap)", "hypothesis": "流通市值小的标的流动性溢价更高……", "category": "规模"}`
- **WHEN** 合同校验执行
- **THEN** 该提案通过（`category="规模"` 在九类枚举内，表达式可解析）
- **AND** `category="基本面"` 等枚举外取值仍按合同违约进入修复重试

#### Scenario: 提示词模板与真相源一致

- **WHEN** 组装广度变异或深度改进提案 prompt
- **THEN** 模板列举的算子清单、字段清单与 `factors.dsl` 的算子表 / 字段集一致（含可选参考字段与新算子）
- **AND** 模板中的类别清单为九类、复杂度规则写明「去重后的数字字面量 ≤ 3」

## MODIFIED Requirements

### Requirement: 外部表达式翻译契约

外部表达式翻译器（Qlib Alpha 表达式 → 本系统 DSL）SHALL 作**二值裁决**：输出语义严格等价的合法 DSL 表达式，或显式「不可翻译」结论（附缺失/语义不等价算子清单）；MUST NOT 输出语义近似的猜测翻译。逐算子映射 SHALL 可审计（映射表随产物留存）；字段与口径映射（如 `$close` → `close`、复权口径差异）SHALL 在映射表显式声明。翻译产物 SHALL 经「外部研发供给接入」通道过闸；「不可翻译」候选计入 `errors` 桶（gate=None）。翻译器 SHALL 支持两类输入形态：① Qlib Alpha 表达式（确定性逐算子映射，即上述裁决）；② RD-Agent 原生形态（formulation 与描述）——经 LLM 辅助转译为 Qlib 表达式后再经 ①，LLM 载荷合同 SHALL 为「合法 Qlib 表达式」XOR「显式 `untranslatable` + 原因」（MUST NOT 猜测/近似）；原始 formulation 与转译链（Qlib 表达式 → DSL）SHALL 随候选 hypothesis 留档。**负数常量 SHALL 以严格等价的减法/加法重排表达**（如 `-1 * x` → `sub(0,x)`、`x - (-k)` → `add(x,k)`、`(-a) * (-b)` → `mul(a,b)`），不再属于不可翻译边界；**一元负号作用于表达式时 SHALL 以严格等价 `0 − x` 表达**（`-(A)` → `sub(0,A)`；`--k` 归一为 `k`），同属可翻译面；重排产物 SHALL 经同一复杂度硬约束复验；**负窗口（前瞻引用）仍为红线拒收**。

(Previously: 一元负号仅接受数字字面量——`-(A)` 类形态按语法错误拒收；「无对应算子」示例为滚动分位数 `Quantile`——本 change 起 `Quantile`/`Med`/`Skew`/`Kurt`/`Count`/`WMA` 与比较/条件九算子均已成为等价映射。)

#### Scenario: 严格等价翻译

- **GIVEN** 外部表达式 `Mean($close, 5)`（算子与窗口语义与本 DSL `ts_mean` 严格等价）
- **WHEN** 翻译执行
- **THEN** 输出 `ts_mean(close,5)` 且映射表记录该算子映射
- **AND** 产物作为 `source=external` 候选进入五道闸

#### Scenario: 无对应算子不可翻译

- **GIVEN** 外部表达式含本 DSL 无对应算子的操作（如指数移动平均 `EMA`、滚动回归斜率 `Slope`）
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

#### Scenario: 表达式一元负号改写

- **GIVEN** 外部表达式 `-($close - 1)`（一元负号作用于括号表达式）
- **WHEN** 翻译执行
- **THEN** 输出严格等价 `sub(0,sub(close,1))`；`--3` 归一为 `3`
- **AND** `-1 * x` 类负数常量重排路径（上一场景）逐字不变
