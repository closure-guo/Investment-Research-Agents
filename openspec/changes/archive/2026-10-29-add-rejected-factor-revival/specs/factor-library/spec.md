# Delta for factor-library

## ADDED Requirements

### Requirement: 拒绝池复苏重评通道

系统 SHALL 提供拒绝池复苏日批（`factor_revival_daily`，周一至周五 17:10，零 LLM 调用）：对 `rejected` 行按以下条件筛选复苏候选——(1) 库内 IC 记录数 ≥ `IC_WINDOW`（60）；(2) 最近 `IC_WINDOW` 个 IC 点的滚动 |ICIR| ≥ `MIN_ABS_ICIR`（与闸②同值，即「复苏静默线」）；(3) 冷却期外：该行最近一次 `created` 或 `re_rejected` 事件早于 `REVIVAL_COOLDOWN_DAYS`；(4) 单次运行提交数 ≤ `REVIVAL_DAILY_CAP`（同分并列按滚动 |ICIR| 降序、再按 factor_id 升序取前 N）。复苏提交 SHALL 走外部导入同一路径（DSL 预检 → 闸⓿~⑤，检验账本 `ledger_source="revival"`），表达式/假设/类别取自该 rejected 行原值。`rejected` SHALL 保持终态：过闸者以**新 candidate 行**入库，rejected 行不迁移、不删除；再拒者经既有 `record_rejected_factor` 同构 upsert 追加 `re_rejected` 事件（闸口与读数如实，冷却期自该事件自动重算）。复苏静默线 SHALL 只作提交预筛，MUST NOT 构成任何准入捷径——五道闸 SHALL 是唯一准入权威。无可复苏候选时 SHALL 零摘要零面板构建；存在可复苏候选而快照面板不可得时 SHALL fail loud（job 记 failed）。job 摘要 SHALL 含 `checked` / `eligible` / `cooldown_skipped` / `submitted` / `admitted` / `re_rejected` 读数。

#### Scenario: 复苏命中且过闸

- GIVEN 某 rejected 行库内 IC 90 点、近 60 点滚动 |ICIR| = 0.42、无 re_rejected 事件
- WHEN factor_revival_daily 运行
- THEN 该行表达式经外部导入路径提交五道闸
- AND 过闸后以新 candidate→active 行入库，原 rejected 行保持 rejected 状态不变
- AND 检验账本记录 ledger_source="revival"

#### Scenario: 冷却期内跳过

- GIVEN 某 rejected 行最近一次 re_rejected 事件在 10 天前（< 冷却期 30 天），且滚动 |ICIR| 超线
- WHEN factor_revival_daily 运行
- THEN 该行计入 cooldown_skipped，不提交

#### Scenario: 再拒后冷却重置

- GIVEN 某 rejected 行滚动 |ICIR| 超线且冷却期外，本次提交被闸④拒收
- WHEN 准入结果回写
- THEN 原 rejected 行追加 re_rejected 事件（含闸口与读数）
- AND 该行冷却期自该事件重算（次日不再被提交）

#### Scenario: 无可复苏候选零面板

- GIVEN 全部 rejected 行 IC 点数不足 60 或滚动 |ICIR| 低于静默线
- WHEN factor_revival_daily 运行
- THEN 摘要 submitted=0 且不构建 500 日准入面板

#### Scenario: 单次运行上限

- GIVEN eligible=8 > REVIVAL_DAILY_CAP=5
- WHEN factor_revival_daily 运行
- THEN 按滚动 |ICIR| 降序（并列按 factor_id 升序）提交 5 条
- AND 摘要 eligible=8、submitted=5

#### Scenario: 面板不可得 fail loud

- GIVEN 存在可复苏候选且快照库不可用
- WHEN factor_revival_daily 运行
- THEN job 以异常上抛（run_job 记 failed），不静默跳过
