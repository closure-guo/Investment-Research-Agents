# Delta for report-decision-rendering

## ADDED Requirements

### Requirement: watch 触发位与入池跟踪渲染

报告「交易决策」节对 watch 决策 SHALL 在「再评估触发条件」行之前渲染结构化触发位行：「上破触发位」「下破触发位」各一行，数值与决策 JSON 原始值一致（不做四舍五入以外的加工）；字段缺失时该如实标注「未申报」，MUST NOT 整行省略，MUST NOT 从 `reeval_triggers` 文本解析回填（文本解析回填会把校验器未认可的数字渲染成系统结论）。buy/sell/hold 决策不渲染该两行。

报告「交易决策」节 SHALL 渲染入池跟踪声明行：「本决策已入池跟踪，按 N 交易日窗口结算」（N 取实际 `horizon_days`），引导读者至战绩页查看结算结果。决策被门禁阻断或未入池时 MUST NOT 渲染该行。

报告日期与行情数据截止日间隔 > 3 个自然日时，「交易决策」节 SHALL 渲染数据真空提示行：「触发价位锚定 {截止日} 收盘价，期间 N 个自然日无行情，跳空缺口可能使触发条件失真」。间隔 ≤ 3 个自然日时 MUST NOT 渲染该行。间隔阈值 SHALL 为配置项。

#### Scenario: watch 决策渲染触发位行

- **WHEN** `final_trade_decision` 的 action 为 watch，且 `trigger_high=24.6`、`trigger_low=22.91`
- **THEN** 报告「交易决策」节 SHALL 渲染「上破触发位: 24.6」与「下破触发位: 22.91」行
- **AND** 数值与决策 JSON 原始值一致

#### Scenario: 触发位缺失如实标注

- **WHEN** watch 决策的 `trigger_high`/`trigger_low` 均缺失（含「已打回仍未申报」终检形态）
- **THEN** 两行 SHALL 如实渲染「未申报」，MUST NOT 从 `reeval_triggers` 文本解析数字回填

#### Scenario: buy 决策不渲染触发位行

- **WHEN** `final_trade_decision` 的 action 为 buy
- **THEN** 「交易决策」节 MUST NOT 渲染上破/下破触发位行（entry/stop/target 行语义不变）

#### Scenario: 入池跟踪声明

- **WHEN** 决策经审批入池（`horizon_days=20`）
- **THEN** 「交易决策」节 SHALL 渲染「本决策已入池跟踪，按 20 交易日窗口结算，结算结果见战绩页」
- **WHEN** 管线被门禁阻断、决策未入池
- **THEN** MUST NOT 渲染入池跟踪声明行

#### Scenario: 数据真空提示

- **WHEN** 报告日期为 2026-10-08，行情数据截止日为 2026-09-30（间隔 8 个自然日）
- **THEN** 「交易决策」节 SHALL 渲染数据真空提示行，含截止日与间隔天数
- **WHEN** 间隔 ≤ 3 个自然日
- **THEN** MUST NOT 渲染提示行
