# Design: add-analyst-ex-post-verification

## Approach

### 事件如何定义

只检验**变动**，不检验水平。水平值（「当前评级是买入」）没有信息增量，且会被覆盖度偏差主导；变动才是有时点的信号。

**评级事件**（来源 `rating_changes`）：

- 评级序数映射：买入=5、增持=4、中性=3、减持=2、卖出=1。
- 当前评级 vs `prev_rating` 比较：升序 → `rating_upgrade`，降序 → `rating_downgrade`，相等 → `rating_maintain`（作为对照组保留），`prev_rating` 缺失或 `is_first_rating=1` → `rating_initiation`。
- MUST NOT 依赖巨潮的 `rating_change` 文本：当前实测只有「维持/未知」两种取值，方向必须由序数比较确定性推导。

**EPS 修正事件**（来源 `research_reports.eps_forecasts`）：

- 同一 `(code, org, 预测年度)` 与同机构上一篇研报比较：上调 → `eps_revision_up`，下调 → `eps_revision_down`，相等不生成事件。
- 首次覆盖（无同机构前篇）不生成 EPS 修正事件，只记录为 `rating_initiation` 的附属信息。

**目标价事件**：v1 只在同一 `(code, org)` 存在前次事件且前次与本次都带目标价时生成 `target_price_up/down`；否则跳过（巨潮目标价覆盖仅约 42%，且没有前值时不能把「首次给目标价」当变动）。目标价水平值本身不单独检验。

### 结算口径

- `published_at` = 研报发布日期；入场 = 发布日次一交易日**开盘价**（避免用发布日收盘价造成前视）。
- 出场 = 入场后第 H 个交易日收盘价；评级事件 H=20，EPS 修正 H=60，目标价事件 H=60（均可配置）。
- 基准 = 000300.SH，同窗口收益；超额 = 原始收益 − 基准收益。
- 停牌/缺行情：该主张记 `unresolved` 与原因，MUST NOT 用前值填充、MUST NOT 静默丢弃。
- 所有价格取复权口径，与既有战绩结算保持一致。

### 存储

在 `data/research_reports.db` 新增三张表：

- `analyst_claims`：`claim_key`（幂等主键）、`claim_type`、`code`、`org`、`analyst`、`published_at`、`rating`、`prev_rating`、`target_price`、`prev_target_price`、`eps_year`、`eps_value`、`prev_eps_value`、`horizon_days`、`source`、`created_at`。
- `analyst_claim_outcomes`：`claim_key` 主键、`entry_date`、`entry_price`、`exit_date`、`exit_price`、`raw_return`、`benchmark_return`、`excess_return`、`status`（pending/resolved/unresolved）、`reason`、`resolved_at`。
- `analyst_scorecard_snapshots`：`snapshot_date` + `bucket_type` + `bucket_key` 主键，含 `n`、`wins`、`win_rate`、`mean_excess`、`median_excess`、`reliable`、`weight`、`tier`、`updated_at`。

### 记分卡

- 分桶维度：`org`、`rating`、`claim_type`（上调/下调/首次）、`industry`（取 `research_reports.industry`，缺失归 unknown）。
- 输出 `n`、胜率（excess>0 的占比）、平均超额、中位超额；`n < 30` 时 `reliable=false`，报告里必须显示「样本不足」。
- 不做 p 值/显著性宣告：样本是滚动累积的、事件不独立，v1 只给描述统计。
- 机构权重快照：`weight = clip(0.5 + mean_excess / 0.10, 0.5, 1.5)`（先验占位，待校准），只读输出，不接入任何决策路径。

### 调度

- 新 job `analyst_claim_settle_daily`：每交易日 17:00（晚于研报数据面 16:52–16:56、早于 cohort 18:00），先结算到期主张，再生成当日记分卡快照。
- 幂等：同一 `claim_key` 只结算一次；已 resolved 的不重算；行情源缺失时保持 pending 并留痕，下一日重试。
- 失败语义：单个主张失败不影响其他主张；job 整体 failed 时写 `collection_runs` 并保留已完成部分。

## Alternatives Considered

- **复用 `predictions` 表**：否决。该表是系统自身观点的战绩账本，混入卖方观点会让「系统胜率」不再可解释；且 `predictions` 的 `direction` 语义是 long/short/neutral，无法表达「评级上调」。
- **用 LLM 给每条研报打分**：否决。记分卡的数字必须可复算，LLM 只适合抽取，不适合做统计。
- **只做水平值统计（当前买入占比 vs 后续收益）**：否决。水平值受覆盖度与评级通胀影响，无法区分「一直买入」和「刚上调」。
- **按 12 个月目标价 horizon 结算**：v1 否决。样本积累太慢（一年才有第一批），先 20/60 日；12 个月版本等事件量足够后再加。

## Risks

- **事件稀疏**：巨潮当前约 24 条/日，20 个交易日后才有第一批结算；前 2 个月记分卡样本必然不足。对策：`reliable=false` 显式标注，不提前下结论。
- **生存偏差**：退市/停牌标的不进入结算。对策：退市样本记 `unresolved(delisted)`，单独计数，不混入胜率。
- **基准选择敏感**：000300.SH 对中小盘不公。对策：配置化基准，v1 只报 000300，后续可加中证 1000 并列。
- **同事件多源重复**：同一评级变动可能从巨潮和研报元数据各来一次。对策：事件键含 `source` 优先级，巨潮优先；同 `(code, org, date, claim_type)` 去重。
- **数据回溯偏差**：`rating_changes` 从 2026-10-20 才开始积累，无法回测历史。对策：明确 v1 是「向前积累」，不宣称历史统计有效；如需历史，另立回填 delta。
