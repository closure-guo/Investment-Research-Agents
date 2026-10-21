## Why

上一批把内资与外资的**数据与消费**分开了，但没有完成「分开打分」：内资有事件研究记分卡，外资只有按动作/方向的描述计数；`depth_class`（深度/点评）只落库、没有进入记分卡；两套数据也没有 `region` / `score_family` 字段与统一只读视图。结果是「没有混用一把尺子，但也没造出第二把尺子」。

## What Changes

- **region / score_family 显式化**：`analyst_claims` 增 `region='domestic'`，`foreign_broker_events` 增 `region='foreign'`；`analyst_scorecard_snapshots` 增 `region` / `score_family='event_study'`；新增 `foreign_sentiment_scorecards`（`score_family='sentiment'`）。
- **内资深度进打分**：`research_reports` 增 `depth_class`（deep/comment/unknown，标题确定性分类）；EPS claims 继承该标签；记分卡新增 `depth` 分桶，并为 org/rating 桶增加 `weighted_mean_excess` 与 `deep_n`，权重来自 `config/analyst_scoring.json`（先验：deep=1.0 / comment=0.6 / unknown=0.4）。
- **外资独立打分**：新增确定性 `event_score`（动作 × 方向 × 置信度）；新增 `foreign_broker_event_outcomes` 与 `foreign_sentiment_scorecards`；v1 只对 `target_type='market'` 的事件做 000300 的 20 日方向命中结算（T+1 入场），sector/stock 留作描述统计并记 `unresolved`，不做个股映射。
- **统一只读视图**：`GET /api/v1/ops/research/scorecards` 返回 `{domestic: {...}, foreign: {...}}` 两块，各自保留指标族；不合并成单一分数。
- 17:05 外资快讯 job 在抓取后追加「结算到期市场事件 → 生成外资记分卡」；无 feed 时仍结算存量事件。
- 不做：外资个股/行业级收益结算、内资深度权重自动校准、权重进入任何决策路径。

## Capabilities

### New Capabilities

（无。）

### Modified Capabilities

- `analyst-ex-post-verification`: 增加 region/score_family 标签、depth 分桶与深度加权、统一只读视图的内资块。
- `foreign-broker-sentiment`: 增加事件情绪分、市场级 20 日命中结算、外资记分卡与统一只读视图的外资块。

## Impact

- 数据：`research_reports.depth_class`；`analyst_claims.region/depth_class`；`analyst_scorecard_snapshots.region/score_family/weighted_mean_excess/deep_n`；`foreign_broker_events.region/event_score`；新表 `foreign_broker_event_outcomes` / `foreign_sentiment_scorecards`。
- 代码：`data/research_reports.py`（depth 写入）、`data/analyst_claims_store.py`、`outcome/analyst_claims/extract.py`、`outcome/analyst_claims/scorecard.py`、`data/foreign_broker_news.py`、新 `outcome/foreign_broker_scoring.py`、`ops_api.py`、`outcome/scheduler.py`。
- 配置：`config/analyst_scoring.json`（新增）。
- 文档：`docs/evals/metrics.md` §1.18 增补 / 新增 §1.19。
- 非目标：不改 prompt、不加 LLM 调用、不改任何分析师上下文或交易决策。
