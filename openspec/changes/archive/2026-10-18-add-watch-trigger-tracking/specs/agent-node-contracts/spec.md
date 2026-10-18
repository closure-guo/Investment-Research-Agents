# Delta for agent-node-contracts

## ADDED Requirements

### Requirement: watch 双向触发位结构化契约

`TradeDecision` SHALL 支持 `trigger_high`（上破触发位）与 `trigger_low`（下破触发位）两个可选数值字段，承载 watch 决策的双向再评估价位锚。`reeval_triggers` 自由文本继续承载完整语义（含非价位条件），结构化字段是其机器可消费的子集，两者 SHALL 并存——不得因结构化字段在场而清空文本条目。

action 为 watch 时终稿（`final_trade_decision`）SHALL 申报 `trigger_high` 与 `trigger_low` 至少其一；申报约束由终稿完整性检查节点承担（同 `final_reeval_check` 一次打回先例）：缺失时打回 risk_judge 一次要求申报，仍缺失 SHALL 放行并将检查结果如实标注「已打回仍未申报」，MUST NOT 死循环、MUST NOT 虚构数值。action 为 buy/sell/hold 时两字段无约束（buy/sell 的价位承诺由 entry/stop/target 承载）。

输入清洗 SHALL 与既有宽松先例一致：非数值、非有限值、负值或 0 一律归一为 `None`（未申报），清洗 MUST NOT 抛异常中断管线。

#### Scenario: watch 终稿触发位齐备直通

- **WHEN** risk_judge 终稿决策为 watch，且 `trigger_high`/`trigger_low` 均为有效数值
- **THEN** 终稿完整性检查 SHALL 通过，MUST NOT 产生打回或标注

#### Scenario: watch 终稿缺触发位打回一次后放行

- **WHEN** risk_judge 终稿决策为 watch，且 `trigger_high` 与 `trigger_low` 均缺失
- **THEN** 终稿完整性检查 SHALL 打回 risk_judge 一次，feedback 引用风险辩论中已出现的价位线索要求结构化申报
- **AND** 重试后仍缺失时 SHALL 放行 + 检查结果如实标注「已打回仍未申报」，MUST NOT 虚构数值

#### Scenario: 噪声形态清洗不炸管线

- **WHEN** LLM 输出的 `trigger_high` 为字符串 `"24.6"`、`null`、负数或 NaN
- **THEN** 可解析数值字符串 SHALL 归一为数值；`null`/负数/NaN/不可解析值 SHALL 归一为 `None`
- **AND** 校验 MUST NOT 因噪声形态抛 ValidationError 中断管线

#### Scenario: buy/sell 决策不受触发位约束

- **WHEN** 终稿决策为 buy 或 sell 且未申报 `trigger_high`/`trigger_low`
- **THEN** 终稿完整性检查 SHALL NOT 因此打回或标注（价位承诺由 entry/stop/target 承载，语义不变）
