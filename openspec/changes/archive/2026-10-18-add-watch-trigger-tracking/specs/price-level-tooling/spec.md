# Delta for price-level-tooling

## ADDED Requirements

### Requirement: 结构化触发位方向校验

`TradeDecision` 的结构化触发位（`trigger_high`/`trigger_low`）SHALL 由确定性代码与最新收盘价做方向校验，复用既有空洞形态语义：`trigger_high` ≤ 最新收盘（上破门槛在当前时点已经满足）或 `trigger_low` ≥ 最新收盘（下破门槛已经满足）时，SHALL 各登记一条 `empty_trigger` anomaly（附字段名、申报值、最新收盘价）。校验 MUST NOT 依赖对 `reeval_triggers` 文本的解析——结构化字段直接消费。

anomaly 处置 SHALL 完全复用既有门禁分层（定向打回重试一次 → 修正放行 / 未恶化放行待人工终裁 / 恶化阻断），与文本价位校验的 anomaly 共用同一通道与同一打回预算（合计一次打回，MUST NOT 因结构化与文本两类 anomaly 分别打回形成多重打回循环）。文本 `reeval_triggers` 的既有偏差/空洞校验语义 SHALL 保持不变。

校验器自身异常 SHALL fail-open（放行 + 告警日志）；无已验证最新收盘价可核对时 SHALL 直通。

#### Scenario: 上破触发位空洞检测

- **WHEN** watch 终稿申报 `trigger_high=22.61`，最新收盘价为 23.03（高于 22.61）
- **THEN** 系统 SHALL 登记一条空洞形态 anomaly（上破触发价不高于现价，条件已满足）
- **AND** 该 anomaly 与既有文本校验 anomaly 同门禁处置（共用一次打回预算）

#### Scenario: 下破触发位空洞检测

- **WHEN** watch 终稿申报 `trigger_low=24.60`，最新收盘价为 23.03（低于 24.60）
- **THEN** 系统 SHALL 登记一条空洞形态 anomaly（下破触发价不低于现价，条件已满足）

#### Scenario: 有效双向触发位直通

- **WHEN** watch 终稿申报 `trigger_high=24.6`、`trigger_low=22.91`，最新收盘价为 23.03
- **THEN** SHALL 不产生 anomaly、不触发打回，决策照常进入审批

#### Scenario: 无收盘价可核对直通

- **WHEN** 已验证指标中无可用最新收盘价（K 线缺失且无 entry_ref 回退）
- **THEN** 结构化触发位校验 SHALL 直通，MUST NOT 凭空报 anomaly
