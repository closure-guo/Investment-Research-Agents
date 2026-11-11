# Delta for agent-node-contracts

## MODIFIED Requirements

### Requirement: watch 双向触发位结构化契约

`TradeDecision` SHALL 支持 `trigger_high`（上破触发位）与 `trigger_low`（下破触发位）两个可选数值字段，承载 watch 决策的双向再评估价位锚。`reeval_triggers` 自由文本继续承载完整语义（含非价位条件），结构化字段是其机器可消费的子集，两者 SHALL 并存——不得因结构化字段在场而清空文本条目。

action 为 watch 时终稿（`final_trade_decision`）SHALL 申报 `trigger_high` 与 `trigger_low` 至少其一；申报约束由终稿完整性检查节点承担（同 `final_reeval_check` 一次打回先例）：缺失时打回 risk_judge 一次要求申报，仍缺失 SHALL 放行并将检查结果如实标注「已打回仍未申报」，MUST NOT 死循环、MUST NOT 虚构数值。

action 为 buy 时两字段承载**条件入场形态**（update-conditional-entry-gate-waiver）：`trigger_high` = 上破确认买入位（严格高于最新收盘价）、`trigger_low` = 回踩承接买入位（严格低于最新收盘价），与 watch 触发位同语义同清洗；buy 的价位承诺仍由 entry/stop/target 承载，trigger 字段是其入场时点的机器可消费锚。action 为 sell/hold 时两字段无约束。

终稿完整性检查节点 SHALL 对 buy 新增**逆势立即入场软标注**（I-1 模式，不打回、不阻断、MUST NOT 死循环）：终稿为 buy、`trigger_high`/`trigger_low` 均未申报、且最新收盘价低于 MA20 与 MA60（技术逆势，technical_indicators 可用为前提，任一不可用 SHALL 跳过）时，以 state 键 `final_entry_form_check` 如实标注「逆势立即入场未申报条件触发位（收盘低于 MA20/MA60）——待人工复核」，标注经终稿完整性收集器进 FM 审批上下文；报告侧按既有 `_FM_INCOMPLETE_MARKERS` 过滤规则渲染。非逆势、已申报触发位、或技术指标不可用时 SHALL 不产生标注。

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

#### Scenario: buy/sell 决策不受触发位强制约束

- **WHEN** 终稿决策为 buy 或 sell 且未申报 `trigger_high`/`trigger_low`
- **THEN** 终稿完整性检查 SHALL NOT 因此打回（价位承诺由 entry/stop/target 承载，语义不变）
- **AND** buy 场景按逆势软标注条款处理（见下），sell 场景 SHALL NOT 产生任何触发位标注

#### Scenario: buy 逆势立即入场软标注

- **WHEN** risk_judge 终稿决策为 buy，`trigger_high`/`trigger_low` 均未申报，且最新收盘价低于 MA20 与 MA60
- **THEN** 终稿完整性检查 SHALL 写入 `final_entry_form_check` 软标注（如实标注逆势立即入场未申报条件触发位），MUST NOT 打回、MUST NOT 阻断管线
- **AND** 标注 SHALL 经终稿完整性收集器进入 FM 审批上下文

#### Scenario: buy 条件入场或非逆势不标注

- **WHEN** risk_judge 终稿决策为 buy 且已申报 `trigger_high` 或 `trigger_low` 任一；或未申报但收盘价不低于 MA20 与 MA60 之一；或 technical_indicators 不可用
- **THEN** `final_entry_form_check` SHALL 不产生标注（result=pass 且 note 为空）
