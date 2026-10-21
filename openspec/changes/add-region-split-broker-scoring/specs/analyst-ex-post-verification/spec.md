## ADDED Requirements

### Requirement: 内外资口径标签

内资主张与外资事件 SHALL 带显式口径标签，禁止在同一行上混用两套指标。

- `analyst_claims` SHALL 含 `region='domestic'`；`foreign_broker_events` SHALL 含 `region='foreign'`。
- `analyst_scorecard_snapshots` SHALL 含 `region='domestic'` 与 `score_family='event_study'`；外资记分卡 SHALL 使用 `region='foreign'` 与 `score_family='sentiment'`。
- 旧库迁移 SHALL 为既有行补默认标签，MUST NOT 改变既有数值。

#### Scenario: 迁移补默认标签

- **GIVEN** 旧库中已有内资主张
- **WHEN** `init_db` 运行
- **THEN** `analyst_claims.region` SHALL 为 `domestic`
- **AND** 既有主张的数值字段 SHALL 不变

#### Scenario: 两套指标不混行

- **GIVEN** 同一天同时存在内资与外资快照
- **WHEN** 查询统一视图
- **THEN** 内资行 SHALL 只含 event_study 指标
- **AND** 外资行 SHALL 只含 sentiment 指标
- **AND** MUST NOT 出现同时含胜率与命中率字段的混合行

### Requirement: 内资深度分桶与深度加权

内资记分卡 SHALL 使用研报深度标签，并按配置的先验权重给出深度加权均值。

- `research_reports` SHALL 在入库时按标题确定性写入 `depth_class ∈ {deep, comment, unknown}`。
- `analyst_claims` SHALL 为 EPS 修正主张继承报告 depth；评级/目标价事件无报告标题时 SHALL 为 null。
- 记分卡 SHALL 输出 `bucket_type='depth'` 的 deep/comment/unknown 桶。
- org/rating 桶 SHALL 输出 `deep_n` 与 `weighted_mean_excess`；权重来自 `config/analyst_scoring.json`（默认 deep=1.0 / comment=0.6 / unknown=0.4）。
- `weighted_mean_excess` SHALL NOT 替换原始 `mean_excess`/`win_rate`；`n<30` 仍 `reliable=false`。

#### Scenario: 深度报告分类入 claims

- **GIVEN** 一篇标题含「深度」的个股研报产生 EPS 修正
- **WHEN** 主张生成运行
- **THEN** 该主张 `depth_class` SHALL 为 `deep`

#### Scenario: 评级事件无深度

- **GIVEN** 一条评级变动来自巨潮事件、没有对应报告标题
- **WHEN** 主张生成运行
- **THEN** 该主张 `depth_class` SHALL 为 null
- **AND** SHALL 在 depth 桶中计入 `unknown`

#### Scenario: 深度加权不覆盖原始均值

- **GIVEN** 一个 org 桶含 1 条 deep（excess=0.10）与 1 条 comment（excess=-0.10）
- **WHEN** 记分卡生成
- **THEN** `mean_excess` SHALL 为 0
- **AND** `weighted_mean_excess` SHALL 为正（deep 权重更高）
- **AND** `deep_n` SHALL 为 1

### Requirement: 统一只读视图（内资块）

系统 SHALL 提供统一只读视图，返回内资与外资两块独立指标，不合并成单一分数。

- `GET /api/v1/ops/research/scorecards` SHALL 返回 `{domestic: {snapshot_date, rows, status}, foreign: {...}}`。
- 内资块 SHALL 来自 `analyst_scorecard_snapshots`，外资块 SHALL 来自 `foreign_sentiment_scorecards`。
- 任一块为空时 SHALL 显式 `status=empty`，MUST NOT 用另一块的数据填充。
- 接口 MUST NOT 写入任何表，MUST NOT 调用 LLM。

#### Scenario: 两块独立返回

- **GIVEN** 内资有记分卡、外资无
- **WHEN** 调用统一视图
- **THEN** `domestic.status` SHALL 为 `ok`
- **AND** `foreign.status` SHALL 为 `empty`
- **AND** `foreign.rows` SHALL 为空列表

#### Scenario: 只读无副作用

- **GIVEN** 统一视图被调用
- **WHEN** 对比调用前后
- **THEN** 两张快照表行数 SHALL 不变
