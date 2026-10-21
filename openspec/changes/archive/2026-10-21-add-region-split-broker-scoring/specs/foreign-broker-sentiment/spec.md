## ADDED Requirements

### Requirement: 外资情绪分

系统 SHALL 为每条外资快讯事件计算确定性情绪分，分数可复算且不调用 LLM。

- `event_score` SHALL 由动作、方向与置信度三要素按配置权重计算；公式登记 `docs/evals/metrics.md`。
- 动作属于评级/目标价上调或下调时 SHALL 使用动作符号 × 置信度；`positioning`/`sentiment` 使用方向符号 × 置信度；`other`/未知 SHALL 为 0。
- `direction=neutral|unknown` 时 SHALL 直接归零（方向不明的事件不得被动作带出分数）。
- `direction=neutral|unknown` SHALL 得 0 分。
- 分数 SHALL 落 `foreign_broker_events.event_score`，供审计与聚合。

#### Scenario: 高置信上调得正分

- **GIVEN** 一条事件 action=rating_up、direction=positive、confidence=high
- **WHEN** 计算情绪分
- **THEN** `event_score` SHALL 为 +1.0

#### Scenario: 方向未知为零分

- **GIVEN** 一条事件 direction=unknown
- **WHEN** 计算情绪分
- **THEN** `event_score` SHALL 为 0

#### Scenario: 仓位表述只用方向

- **GIVEN** 一条事件 action=positioning、direction=negative、confidence=medium
- **WHEN** 计算情绪分
- **THEN** `event_score` SHALL 为 -0.7

### Requirement: 外资市场级事后命中

系统 SHALL 对市场级外资事件做 000300 的 20 日方向命中结算；其他对象类型 SHALL 显式未解决。

- 仅 `target_type='market'` 且 `direction ∈ {positive, negative}` 的事件参与结算。
- 入场 SHALL 为发布日次一交易日收盘，出场为入场后第 20 个交易日收盘；基准 SHALL 为 `000300` 的 `index_closes`。
- `hit` SHALL 按方向与基准收益符号判定；结果落 `foreign_broker_event_outcomes`。
- `target_type ∈ {sector, stock}` SHALL 记 `unresolved(target_not_market)`；`neutral/unknown` 记 `unresolved(no_direction)`；缺基准记 `unresolved(missing_benchmark)`。
- 结算 MUST NOT 使用发布日之前或结算日之后的数据（无前视）。

#### Scenario: 看多且市场上涨为命中

- **GIVEN** 市场级事件 direction=positive，000300 在其 20 日窗口上涨
- **WHEN** 结算运行
- **THEN** `hit` SHALL 为 true
- **AND** 结果 SHALL 含 `benchmark_return`

#### Scenario: 行业事件不强行映射

- **GIVEN** 一条 `target_type=sector` 的事件
- **WHEN** 结算运行
- **THEN** 状态 SHALL 为 `unresolved`
- **AND** 原因 SHALL 为 `target_not_market`
- **AND** MUST NOT 用个股或宽基指数代替行业收益

### Requirement: 外资记分卡

系统 SHALL 按机构/动作/方向生成外资独立记分卡，不与内资共用指标。

- 每个桶 SHALL 输出 `n`、`hits`、`hit_rate`、`mean_return`、`net_score`（事件情绪分均值）、`reliable`。
- `n < 30` SHALL 标记 `reliable=false`；MUST NOT 输出显著性结论或机构权重。
- 记分卡 SHALL 落 `foreign_sentiment_scorecards`，`region='foreign'`、`score_family='sentiment'`。
- 同快照日重复生成 SHALL 幂等替换。

#### Scenario: 样本不足显式标注

- **GIVEN** 某机构只有 12 条已结算事件
- **WHEN** 记分卡生成
- **THEN** 该桶 SHALL 输出 `n=12` 与 `reliable=false`

#### Scenario: 同日重跑幂等

- **GIVEN** 当日外资记分卡已生成
- **WHEN** 日批重跑
- **THEN** 该快照日行数 SHALL 不变
- **AND** 数值 SHALL 可复算一致

### Requirement: 内外资不互相消费

外资记分卡 MUST NOT 进入内资事件研究与权重；内资记分卡 MUST NOT 读取外资结果。

- `analyst_scorecard_snapshots` 的生成 SHALL 只读 `analyst_claims` / `analyst_claim_outcomes`。
- `foreign_sentiment_scorecards` 的生成 SHALL 只读 `foreign_broker_events` / `foreign_broker_event_outcomes`。
- 统一视图 SHALL 只做读取拼装，不写回任一侧。

#### Scenario: 外资不改变内资权重

- **GIVEN** 外资表有 100 条事件
- **WHEN** 内资记分卡生成
- **THEN** 内资权重与无外资数据时 SHALL 一致

#### Scenario: 内资不改变外资命中率

- **GIVEN** 内资有 100 条已结算主张
- **WHEN** 外资记分卡生成
- **THEN** 外资 `hit_rate` SHALL 与无内资数据时一致
