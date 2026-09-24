# Delta for decision-outcome

## MODIFIED Requirements

### Requirement: 决策落库

每个产出的观点 SHALL 写入 `predictions` 表（取代原 `decision_log`）：不再只记 approve，**reject、hold/watch、neutral 方向的观点同样落库**（全量记录）。记录 SHALL 含观点快照（rationale_snapshot，写入后冻结）、direction（action 映射：buy→long、sell→short、hold/watch→neutral）、confidence、entry_price（**参考价**：决策时点实时价或最近收盘，注明口径，供展示与盯市）、horizon_days（显式写入，默认取配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`，默认值 20 交易日）、source_type（backtest/live）、权威 created_at（服务端生成）。参考价不可得时 SHALL 存档并记 WARN，状态保持 open（判定不依赖参考价）。neutral 观点判定后 `status` SHALL 落终态 `avoidance`（细粒度结果在 `avoidance_status`），离开 open 池。

**结算入场价与参考价分离**：结算任务 SHALL 按标准化口径从行情派生 `settle_entry_price`——**决策归属日（created_at 所在日）收盘价**；决策产出时间在当日收盘后的取**次一交易日收盘价**；决策归属日非交易日的取其后首个交易日收盘价。派生值随判定结果落库（判定产出，写入后冻结可审计）；`raw_return` / `excess_return` SHALL 基于 `settle_entry_price` 计算，SHALL NOT 基于参考价。TradeDecision 申报的 entry_price / stop_loss / target_price SHALL 随观点快照冻结写入 rationale_snapshot（供审计），SHALL NOT 参与任何结算计算。

写入 SHALL NOT 阻断业务管线（旁路失败仅日志，不影响报告产出）。
(Previously: entry_price 为「参考价，注明口径」——实现取实时 quote 优先、K 线最新收盘兜底；horizon_days 不写入，全落表默认 252；申报价不参与落库；判定收益基于参考价计算（与回测腿入场口径不可比）。另本次收敛 rationale_snapshot 需求——「完整分析原文」不再要求入快照，收敛为「观点摘要字段 + 申报价位」，完整原文改由 `session_id` 关联会话报告。)

#### Scenario: 决策产出即落库

- **WHEN** Fund Manager 产出任一观点（`TradeDecision` 并写入 state）
- **THEN** 系统 SHALL 同步向 `predictions` 插入一条记录
- **AND** 记录含 langfuse_trace_id / created_at（服务端权威）/ ticker / direction / entry_price（参考价口径）/ horizon_days（显式写入，默认 20）/ confidence / source_type 等
- **AND** 观点保持 open 直至判定结算

#### Scenario: 全量观点落库（含 reject）

- **WHEN** Fund Manager 产出任一决策（approve/reject/return）
- **THEN** 系统 SHALL 同步向 `predictions` 插入一条记录
- **AND** direction SHALL 由 action 映射（buy→long、sell→short、hold/watch→neutral）
- **AND** rationale_snapshot SHALL 冻结含观点摘要字段（`action`、`fund_manager_decision` 及其 reasoning）与申报价位（如有）
- **AND** 完整分析原文 SHALL 存于会话报告并可由该观点的 `session_id` 关联（不重复存于快照）

#### Scenario: 申报价冻结入快照且不参与结算

- **GIVEN** 某 buy 观点申报了 entry_price / stop_loss / target_price
- **WHEN** 观点落库
- **THEN** 三个申报价 SHALL 冻结写入 rationale_snapshot，可审计读取
- **AND** 结算入场价 SHALL 由行情派生（`settle_entry_price`，见下），SHALL NOT 使用申报 entry_price 或参考价

#### Scenario: 结算入场价由行情派生

- **GIVEN** 某观点判定任务执行时行情可得
- **WHEN** 结算
- **THEN** `settle_entry_price` SHALL 取决策归属日收盘价（created_at 在归属日收盘后 → 次一交易日收盘；归属日非交易日 → 其后首个交易日收盘）
- **AND** `raw_return` / `excess_return` SHALL 基于 `settle_entry_price` 与 horizon 终点价计算
- **AND** 判定前该列 SHALL 为 NULL（参考价仍正常展示与盯市）

#### Scenario: 落库失败不阻断业务

- **GIVEN** SQLite 写入异常
- **WHEN** 观点落库失败
- **THEN** 业务管线 SHALL 正常完成报告产出
- **AND** 失败仅记 ERROR 日志

#### Scenario: trace 关联可追溯

- **WHEN** 落库完成
- **THEN** `langfuse_trace_id` SHALL 指向产生该观点的 Langfuse trace
- **AND** 后续可反向联查

#### Scenario: 参考价不可得存档不阻断

- **GIVEN** 某观点参考价不可得（quote 与 K 线均无）
- **WHEN** 落库
- **THEN** SHALL 存档并记 WARN，状态保持 open（判定不依赖参考价，结算时由行情派生 `settle_entry_price`）
- **AND** 判定时行情仍不可得（长期无数据）SHALL 按 unresolvable 处理

#### Scenario: 无可靠入场价存档不计分

- **GIVEN** 某观点参考价不可得（quote 与 K 线均无）且判定时行情长期不可得
- **WHEN** 判定任务处理该观点
- **THEN** SHALL 落 `unresolvable`（存档、计入样本但不进入胜率与平均超额统计——`prediction_stats` 的 settled = win+loss，unresolvable 不进分母）
- **AND** SHALL 记 WARN 日志

> 归档门禁注记（2026-09-24）：本 Scenario 沿用归档前主规范的同名条目（archive 工具要求 MODIFIED 块覆盖现有全部 Scenario，不得静默丢弃）。**语义按 Δ2 两段式改写**：落库时不再直接判 `unresolvable`（见上一条「参考价不可得存档不阻断」——保持 open + WARN），只有判定时行情长期不可得才落 `unresolvable` 并不计分；「存档但不进入战绩统计」的结论仍成立（unresolvable 不进 settled 与 avg_excess 人口）。

### Requirement: 事后行情追踪

系统 SHALL 运行日批判定任务（每个交易日收盘后），对所有 `open` 观点按 track-record 判定规则结算：**默认 horizon T+20 交易日**（配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`；观点自带 horizon_days 则以其为准，上限 1 年）；方向相反或目标价不同的新观点触发旧观点立即结算（superseded）；long 区间超额 > +2% → resolved_win，< −2% → resolved_loss，±2% 内 → resolved_neutral；short 对称。任务 SHALL 幂等（重复执行不重复结算）且失败可重试。
(Previously: 默认 horizon T+252 交易日。)

#### Scenario: 到期判定（horizon 终点）

- **GIVEN** 某 open long 观点到达 horizon 终点（默认第 20 个交易日）
- **WHEN** 判定任务处理
- **THEN** 按区间超额收益判定 resolved_win / resolved_loss / resolved_neutral
- **AND** exit_price / raw_return / excess_return 记录实际值

#### Scenario: 提前结算（superseded）

- **WHEN** 对同一标的发出方向相反或目标价不同的新观点
- **THEN** 旧观点 SHALL 立即以当前价格结算
- **AND** resolution_rule SHALL 记录为 superseded

#### Scenario: 止损触发结算

- **GIVEN** 旧规则下价格触及止损价的 open 观点
- **WHEN** 判定任务处理
- **THEN** SHALL NOT 以止损触发提前结算（horizon 判定取代止损/目标/超期路径）
- **AND** 结算统一以 horizon 到点的区间超额收益为准

#### Scenario: 目标达成结算

- **GIVEN** 旧规则下价格触及目标价的 open 观点
- **WHEN** 判定任务处理
- **THEN** SHALL NOT 以目标达成触发提前结算
- **AND** 判定统一以 horizon 终点区间超额收益为准

#### Scenario: 止损与目标同日触及

- **GIVEN** 同一交易日价格既触及止损价又触及目标价
- **WHEN** 判定任务处理
- **THEN** SHALL NOT 适用「同日按止损优先」规则（该规则随旧结算路径一并取代）

#### Scenario: 超期强制结算

- **GIVEN** 某 open 观点持有天数超过旧 `MAX_HOLD_DAYS`（20 交易日）
- **WHEN** 判定任务处理
- **THEN** SHALL NOT 按超期强制以结算日收盘结算
- **AND** 该观点保持 open 至 horizon 到点（或被 superseded）

#### Scenario: 幂等结算

- **GIVEN** 某观点已 resolved 或 unresolvable
- **WHEN** 判定任务再次处理
- **THEN** SHALL 跳过该观点，不重复结算 / 不重复上报 Score

#### Scenario: 行情缺失重试

- **WHEN** 某标的当日行情拉取失败
- **THEN** 该观点本次跳过，下次任务重试
- **AND** 连续 N 日（可配）无数据 SHALL 标记 unresolvable 告警

### Requirement: 决策效果 Score 反向上报

决策结算后系统 SHALL 向 Langfuse 反向上报三个 Score，关联原 `langfuse_trace_id`：`decision_hit`（方向是否正确，BOOLEAN）、`decision_return`（方向符号化实际收益率，NUMERIC）、`decision_excess`（相对基准超额，NUMERIC）。**仅 long/short 方向观点 SHALL 上报上述 Score；neutral 方向观点（回避判定）SHALL NOT 上报 decision_* Score**——回避语义（标的跑输 = 回避正确）与方向符号化语义不兼容，回避统计走独立字段与统计函数通道。上报失败（trace 不存在 / 已过期）SHALL 仅记 WARN，不阻断结算。
(Previously: action 为 sell/hold/watch 时 decision_return 一律取负上报——hold/watch 混入方向符号化语义。)

#### Scenario: 结算即上报 Score

- **WHEN** long/short 观点结算完成（状态转 resolved_*）
- **THEN** 系统 SHALL 调 `langfuse.score(trace_id=..., name=..., value=...)` 上报三个 Score
- **AND** comment 含 settle_price / hold_days / 基准收益摘要

#### Scenario: 方向符号化

- **WHEN** 观点方向为 long（buy）
- **THEN** `decision_return = (settle_price - entry_price) / entry_price`
- **WHEN** 观点方向为 short（sell）
- **THEN** `decision_return` SHALL 取负（即卖出后下跌为正收益）
- **AND** `decision_hit = decision_return > 0`

#### Scenario: 中性观点不上报 Score

- **GIVEN** 某 neutral 观点（watch/hold）完成回避判定
- **WHEN** 判定任务处理
- **THEN** SHALL NOT 上报 decision_hit / decision_return / decision_excess Score
- **AND** neutral 观点回避判定 SHALL 写 `avoidance_status` 与终态 `status="avoidance"`，并随判定落 `settle_entry_price` / `exit_price` / `raw_return` / `excess_return` / `resolved_at`；SHALL NOT 上报 decision_* Score

#### Scenario: 基准超额

- **WHEN** 上报 decision_excess
- **THEN** `decision_excess = decision_return - benchmark_return`
- **AND** benchmark_return 对 short 方向同样符号化取负后再比

#### Scenario: trace 不可查容错

- **GIVEN** langfuse_trace_id 对应 trace 已过期或不存在
- **WHEN** 上报 Score
- **THEN** SHALL 记 WARN 不阻断
- **AND** 判定结果字段仍正常记录
