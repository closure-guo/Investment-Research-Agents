# Tasks

## 1. 权重构成（ranker.factor_weights）

- [x] 1.1 TDD 红：`tests/screening/test_ranker.py` 新增等幅/比例/混合三组权重用例（红：现式按 |IC 均值| 比例，等幅断言失败）
- [x] 1.2 绿：`factor_weights` 加 `ic_counts` 关键字参数与新常量 `MIN_SAMPLES_FOR_PROPORTIONAL_WEIGHTS = 30`（方向×幅度、幅度之和归一、零均值权重 0 不入分母、缺 counts 视为 0）
- [x] 1.3 回归：既有 `factor_weights` / `rank_universe` 用例全绿（高样本行为不变）；`rank_universe` 透传 `ic_counts`

## 2. 秩相关披露（ranker + pipeline）

- [x] 2.1 TDD 红：`spearman_rank_corr` 纯函数用例（完全一致=1.0、完全相反=-1.0、平局平均秩、样本不足/长度不等拒绝）
- [x] 2.2 绿：手写秩变换 + Pearson 实现（无 scipy/pandas）
- [x] 2.3 TDD 红→绿：pipeline factor 分支——`rank_universe(top_n=len(passed))` 全量排序后截断 Top-N 落库不变，summary 新增 `factor_liquidity_rank_corr` + `rank_corr_samples`；fallback 批次不含字段
- [x] 2.4 定向验证：既有 `tests/screening/test_pipeline.py` 全绿（store 入参仍为 Top-N、勾稽与确定性场景不回归）

## 3. 验证与收尾

- [x] 3.1 `uv run pytest tests/screening/ -q` 全绿
- [x] 3.2 `uv run ruff check src/finance_agent/screening/ tests/screening/` + `ruff format --check` 零告警
- [x] 3.3 `uv run mypy src/finance_agent/screening/`（不得新增存量外错误）
- [x] 3.4 `openspec validate refine-screening-activation-scoring --strict` 通过；tasks 全勾后归档前跑 `openspec status`
