# Delta for report-decision-rendering

## MODIFIED Requirements

### Requirement: 交易决策节渲染操作参数

报告的「交易决策」节 SHALL 渲染 `TradeDecision` 的操作参数：方向、置信度、仓位档位为必含字段；`reasoning` 为必含字段。渲染 SHALL 与决策落库（decision-outcome 日批结算）消费的同一 JSON 对象同源，MUST NOT 只渲染自由文本理由而丢弃结构化参数。`reeval_triggers` 为全部 action 的必渲染对象：watch/hold 另渲染 `inaction_reason`；字段在场时 SHALL 结构化渲染，字段缺失时 SHALL 如实标注「未申报」，MUST NOT 编造条目。

sell 决策 SHALL 按 `sell_type` 分模板渲染（update-sell-action-typing，issue #188：减仓与做空是不同执行结构，共用建仓模板对持有者语义不通）：

- `sell_type="exit"`（持有者减仓）：渲染「减仓节奏」行（`exit_schedule` 原文；缺失如实「未申报」），MUST NOT 渲染入场/止损/目标价行（exit 无新建仓参数，与 watch/hold 的无建仓参数语义同型）；重新介入条件由 `reeval_triggers` 承载（既有渲染不变）；
- `sell_type="short"`（做空建仓）：按 buy/sell 既有参数行渲染（entry/stop/target + 派生指标），并渲染做空披露行（下条）；
- `sell_type` 为 None（**历史形态**，add-holdings-context 后新决策不再产出 None——未申报默认改 exit）：按 short 模板渲染以兼容既有报告，并渲染做空披露行。

**做空披露行**（add-holdings-context）：凡按做空建仓模板呈现的 sell（`sell_type="short"` 与历史 None 形态），报告 SHALL 在参数行之后渲染确定性披露行：

`做空披露: 做空建仓需融资融券信用账户（标的须为两融标的且有券源），A 股普通证券账户不可执行；本条为方向研判，不代表可直接执行`

披露行文本由代码直出，MUST NOT 依赖 LLM 生成或改写；`sell_type="exit"`、buy、watch、hold MUST NOT 渲染该行。

(Previously: sell 与 buy 共用一套参数行模板，无 sell_type 概念——后补 sell_type 分模板，但未申报形态仍按 short 模板渲染且报告不披露做空在 A 股普通账户不可执行，「做空建仓」参数行被呈现为可执行建议。)

#### Scenario: buy/sell 决策渲染完整操作参数

- **WHEN** `final_trade_decision` 的 action 为 buy，或 action 为 sell 且 `sell_type="short"`，且 `entry_price`/`stop_loss`/`target_price`/`position_size` 均为有效值
- **THEN** 报告「交易决策」节 SHALL 依次渲染方向、置信度、仓位档位、入场价、止损价、目标价、再评估触发条件、理由
- **THEN** 价位数值 SHALL 与决策 JSON 中的原始值一致（不做四舍五入以外的加工）
- **AND** action 为 sell 且 `sell_type="short"` 时 SHALL 渲染做空披露行

#### Scenario: exit（减仓）渲染减仓节奏而非建仓参数

- **WHEN** `final_trade_decision` 的 action 为 sell 且 `sell_type="exit"`，携带 `exit_schedule="分两批：现价减半、跌破600清仓"`
- **THEN** 报告 SHALL 渲染方向（卖出·减仓类标识）、置信度、仓位档位、「减仓节奏: 分两批：现价减半、跌破600清仓」、再评估触发条件（重新介入条件）、理由
- **THEN** 报告 MUST NOT 渲染入场价/止损价/目标价行（exit 语义无新建仓参数）
- **AND** 报告 MUST NOT 渲染做空披露行（exit 对持有者可执行，无需披露）
- **WHEN** `exit_schedule` 缺失
- **THEN** 「减仓节奏」行 SHALL 如实渲染「未申报」，MUST NOT 编造

#### Scenario: sell 未申报 sell_type 按默认模板渲染

- **WHEN** action 为 sell 且 `sell_type` 为 None（add-holdings-context 之前产出的历史决策对象；此后未申报默认改 exit，None 仅剩历史形态）
- **THEN** 报告 SHALL 按 short 模板渲染既有参数行（行为与现状一致，历史报告兼容）
- **AND** 报告 SHALL 渲染做空披露行——历史形态同样按做空建仓模板呈现，不披露即把不可执行结构呈现为可执行建议

#### Scenario: 做空披露行文本由代码直出

- **WHEN** 渲染做空披露行
- **THEN** 行文本 SHALL 与本节规定的固定文本一致
- **AND** 文本 SHALL NOT 取自决策 `reasoning` 或任何 LLM 生成内容

#### Scenario: buy 决策不渲染做空披露行

- **WHEN** `final_trade_decision` 的 action 为 buy / watch / hold
- **THEN** 「交易决策」节 MUST NOT 渲染做空披露行

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
