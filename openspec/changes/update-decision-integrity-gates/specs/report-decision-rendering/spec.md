# Delta for report-decision-rendering

## ADDED Requirements

（本 capability 无纯新增需求；见下方 MODIFIED。）

## MODIFIED Requirements

### Requirement: 交易决策节渲染操作参数

报告的「交易决策」节 SHALL 渲染 `TradeDecision` 的操作参数：方向、置信度、仓位档位为必含字段；`reasoning` 为必含字段。渲染 SHALL 与决策落库（decision-outcome 日批结算）消费的同一 JSON 对象同源，MUST NOT 只渲染自由文本理由而丢弃结构化参数。`reeval_triggers` 为全部 action 的必渲染对象：watch/hold 另渲染 `inaction_reason`；字段在场时 SHALL 结构化渲染，字段缺失时 SHALL 如实标注「未申报」，MUST NOT 编造条目。

(Previously: 仅非执行动作（watch/hold）渲染 `inaction_reason` 与 `reeval_triggers`，buy/sell 不渲染再评估触发条件行——601818 实证 sell 终稿无触发条件时报告无任何可见缺口。)

#### Scenario: buy/sell 决策渲染完整操作参数

- **WHEN** `final_trade_decision` 的 action 为 buy 或 sell，且 `entry_price`/`stop_loss`/`target_price`/`position_size` 均为有效值
- **THEN** 报告「交易决策」节 SHALL 依次渲染方向、置信度、仓位档位、入场价、止损价、目标价、再评估触发条件、理由
- **THEN** 价位数值 SHALL 与决策 JSON 中的原始值一致（不做四舍五入以外的加工）

#### Scenario: buy/sell 决策再评估触发条件缺失标注

- **WHEN** `final_trade_decision` 的 action 为 buy 或 sell，且清洗后 `reeval_triggers` 为空（含「已打回仍未申报」终检形态）
- **THEN** 报告 SHALL 渲染「再评估触发条件: 未申报」行，MUST NOT 整行省略，MUST NOT 编造条目

#### Scenario: watch/hold 决策渲染结构化不行动理由与再评估条件

- **WHEN** `final_trade_decision` 的 action 为 watch 或 hold
- **THEN** 报告 SHALL 渲染方向、置信度、仓位档位、理由，MUST NOT 渲染入场/止损/目标价行（watch/hold 语义上无建仓参数）
- **THEN** 决策对象携带 `inaction_reason` 时，报告 SHALL 渲染「不行动原因」行，内容与决策 JSON 原始值一致
- **THEN** 决策对象携带非空 `reeval_triggers` 时，报告 SHALL 渲染「再评估触发条件」编号条目，条目内容与决策 JSON 原始值逐条一致
- **THEN** 字段缺失时报告 SHALL 渲染「未申报」如实标注，MUST NOT 编造结构化条目

#### Scenario: watch/hold 旧决策对象兼容渲染

- **WHEN** `final_trade_decision` 为 watch 或 hold 且为历史形态（无 `inaction_reason`/`reeval_triggers` 字段）
- **THEN** 报告 SHALL 正常渲染方向、置信度、仓位档位、理由，不行动原因与再评估触发条件行 SHALL 标注「未申报」
- **AND** 渲染 MUST NOT 因字段缺失抛异常

### Requirement: 参数缺失时诚实标注

操作参数为 0、null、缺失，或为不在档位词表（`light`/`moderate`/`heavy`）内的非法字面量（如 `"none"`、`"null"`、空字符串）时，报告 SHALL 如实标注「未提供」，MUST NOT 静默跳过该行，MUST NOT 以占位数据（如 0）或 LLM 原始字面量冒充有效值。档位词表校验 SHALL 大小写不敏感，非法字面量在渲染前归一为缺失，归一 MUST NOT 回写修改决策对象本身（落库与 trace 保留原值以可观测）。

(Previously: 操作参数为 0、null 或缺失时，报告 SHALL 如实标注「未提供」，MUST NOT 静默跳过该行，MUST NOT 以占位数据（如 0）冒充有效价位。非法字面量无处理规则，`"none"` 等会原样透传到报告。)

#### Scenario: 价位缺失标注

- **WHEN** action 为 sell 但 `stop_loss` 与 `target_price` 均为 0（风险辩论中保守方定性为「风控半成品」的形态）
- **THEN** 报告 SHALL 在止损/目标价行渲染「未提供」
- **THEN** 报告 MUST NOT 因此省略仓位档位或其他有效参数的渲染

#### Scenario: 非法仓位档位字面量归一渲染

- **WHEN** watch 决策的 `position_size` 为字符串 `"none"`（不在 light/moderate/heavy 词表内）
- **THEN** 报告「仓位」行 SHALL 渲染「未提供」，MUST NOT 渲染字面量 `none`
- **THEN** 决策对象落库与 trace 中的原值 SHALL 保留 `"none"`（归一只作用于渲染，MUST NOT 静默改写上游产物）

#### Scenario: 合法档位不受影响

- **WHEN** buy 决策的 `position_size` 为 `light`/`moderate`/`heavy`（含大小写变体如 `Light`）
- **THEN** 报告 SHALL 按原值渲染档位，MUST NOT 归一为「未提供」
