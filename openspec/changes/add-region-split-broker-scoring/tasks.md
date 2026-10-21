## 1. 数据层与配置

- [x] 1.1 `research_reports` 增 `depth_class`（DDL + 迁移）
- [x] 1.2 `analyst_claims` 增 `region` / `depth_class`（默认 domestic / null）
- [x] 1.3 `analyst_scorecard_snapshots` 增 `region` / `score_family` / `weighted_mean_excess` / `deep_n`
- [x] 1.4 `foreign_broker_events` 增 `region='foreign'` / `event_score`
- [x] 1.5 新表 `foreign_broker_event_outcomes` / `foreign_sentiment_scorecards`
- [x] 1.6 `config/analyst_scoring.json`：depth 权重 + 外资动作/置信度权重（先验）
- [x] 1.7 迁移测试：旧库补默认标签、既有数值不变

## 2. 内资深度

- [x] 2.1 `normalize_em_row` 写入 `depth_class`（复用 `classify_depth`）；`upsert_reports` 持久化
- [x] 2.2 `extract_eps_claims` 继承报告 depth；评级/目标价 claims 置 null
- [x] 2.3 记分卡新增 `depth` 分桶；org/rating 桶增加 `weighted_mean_excess` / `deep_n`
- [x] 2.4 测试：深度分类、inherit、unknown 桶、加权不覆盖原始均值

## 3. 外资打分

- [x] 3.1 `event_score` 公式（动作 × 方向 × 置信度）并落库
- [x] 3.2 `foreign_broker_event_outcomes` 结算：仅 market，T+1 收盘入场、20 日、000300
- [x] 3.3 unresolved 语义：sector/stock、neutral/unknown、缺基准
- [x] 3.4 `foreign_sentiment_scorecards`：broker/action/direction 桶，n/hits/hit_rate/mean_return/net_score/reliable
- [x] 3.5 17:05 job 抓取后结算并落快照；无 feed 时仍结算存量
- [x] 3.6 测试：+1.0 / 0 / -0.7 情绪分、命中/未命中、行业不映射、幂等

## 4. 统一只读视图

- [x] 4.1 `GET /api/v1/ops/research/scorecards` 返回 domestic + foreign 两块
- [x] 4.2 空态分块显式（不互相填充）
- [x] 4.3 测试：两块独立、只读无副作用、过滤参数

## 5. 文档与验收

- [x] 5.1 `docs/evals/metrics.md` 登记 depth 权重、外资情绪分公式、命中口径
- [x] 5.2 `openspec validate add-region-split-broker-scoring --strict` 通过
- [x] 5.3 `uv run pytest` 定向 + `tests/outcome` / `tests/data` 回归全绿
- [x] 5.4 `uv run ruff check` 变更文件全过
- [x] 5.5 真实库副本跑一轮：内资 depth 桶/加权、外资无 feed 时结算存量为空态、统一视图两块返回
