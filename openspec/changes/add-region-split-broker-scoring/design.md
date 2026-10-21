# Design: add-region-split-broker-scoring

## Approach

### region / score_family

不改物理分表（`analyst_claims` 只存内资、`foreign_broker_events` 只存外资），只把口径显式写进数据：

- `analyst_claims.region='domestic'`；`foreign_broker_events.region='foreign'`（默认值 + 迁移）。
- `analyst_scorecard_snapshots.region='domestic'` / `score_family='event_study'`。
- 新表 `foreign_sentiment_scorecards.region='foreign'` / `score_family='sentiment'`。
- 统一视图只做**读取拼装**，不合并指标：内资是胜率/超额，外资是命中率/平均市场收益/情绪净分。

### 内资深度

- `research_reports.depth_class`：复用 `research_reports.classify_depth(title)` 在 `normalize_em_row` 时确定性分类；旧行保持 null，v1 向前积累。
- `analyst_claims.depth_class`：EPS 修正继承报告 depth；评级/目标价事件没有报告标题，记 null（unknown）。
- 记分卡新增 `bucket_type='depth'`，桶键 deep/comment/unknown；org/rating 桶新增：
  - `weighted_mean_excess = Σ(w_i × excess_i) / Σ(w_i)`，权重 `deep=1.0, comment=0.6, unknown=0.4`；
  - `deep_n` = 该桶内 depth=deep 的样本数。
- 权重是**先验**，来自 `config/analyst_scoring.json`，不是校准结果；`n<30` 仍 `reliable=false`；权重只读，不进入决策。

### 外资情绪分

每条事件：

```
action_sign =
  rating_up / target_up        → +1
  rating_down / target_down    → -1
  positioning / sentiment      → 0（只用 direction）
  other / unknown              → 0
confidence_weight = high 1.0 / medium 0.7 / low 0.4
direction_sign = positive +1 / negative -1 / neutral|unknown 0

event_score =
  action 属于 up/down 动作：action_sign × confidence_weight
  action 属于 positioning/sentiment：direction_sign × confidence_weight
  其他：0
  direction=neutral|unknown 时先归零（避免方向不明的事件被动作带出分数）
```

- 该公式是**先验**，参数在 `config/analyst_scoring.json`；不宣称预测力，后续可用命中率校准。
- `event_score` 落 `foreign_broker_events.event_score` 便于审计。

### 外资事后命中（v1 只做市场级）

- 仅 `target_type='market'` 且 `direction ∈ {positive, negative}` 的事件参与结算。
- 基准：`000300`（`sessions.db.index_closes`，与内资同一序列）。
- 入场：发布日次一交易日收盘（index_closes 只有收盘）；出场：入场后第 20 个交易日收盘。
- `benchmark_return = exit/entry - 1`；`hit = (direction=positive and return>0) or (direction=negative and return<0)`。
- sector/stock：记 `unresolved(target_not_market)`；neutral/unknown：`unresolved(no_direction)`；缺行情：`unresolved(missing_benchmark)`。
- 结果落 `foreign_broker_event_outcomes`，与内资 `analyst_claim_outcomes` 分开。

### 外资记分卡

`foreign_sentiment_scorecards` 按 `broker` / `action` / `direction` 分桶：

- `n`、`hits`、`hit_rate`、`mean_return`、`net_score`（该桶事件 `event_score` 均值）、`reliable`（n≥30）。
- 只报描述统计；不做显著性、不做机构权重、不进入任何分析师上下文。

### 运行

- 17:05 `foreign_broker_news_daily`：抓取→过滤→抽取→落库；随后结算到期市场事件→落当日外资记分卡。无 feed 时仍执行结算与快照，保证存量事件继续到期。
- 内资 17:00 job 不变；记分卡在生成时读取 depth 权重配置。
- 统一接口 `GET /api/v1/ops/research/scorecards`：读两个快照，返回 `{domestic, foreign}`；任一为空时显式 `status=empty`。

## Alternatives Considered

- **合并成一张 scorecard 表**：否决。两套指标族不同（胜率/超额 vs 命中率/市场收益），合并会诱导读者横比。
- **外资做个股/行业级收益结算**：v1 否决。二手快讯的 target 是自由文本，映射到个股/行业指数不稳定，错误映射比不结算更糟。
- **深度权重直接乘进内资综合分**：否决。先验权重只用于展示 `weighted_mean_excess`，不改原始胜率/均值；避免用一个未校准的系数掩盖样本结构。
- **用 LLM 给外资情绪打分**：否决。分数必须可复算；LLM 只做事件抽取。

## Risks

- **样本极小**：外资市场级事件每天可能 0–3 条，n<30 长期存在；`reliable=false` 必须显式。
- **权重主观**：depth 与 confidence 权重都是先验；配置化 + 文档登记，后续用命中率校准。
- **基准单一**：000300 对港股/中概不适用；v1 只结算 `target_type=market` 且文本含中国/A股/港股的事件，后续再扩基准。
- **旧数据无 depth**：存量 claims 的 depth 为 unknown；v1 向前积累，不伪造回填。
- **重复事件**：同一快讯转载多次会放大 n；按 `(broker, action, target, published_at)` 去重计数的改进留待后续。
