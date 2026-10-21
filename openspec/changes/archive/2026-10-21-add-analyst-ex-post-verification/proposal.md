## Why

研报数据面已经每天落评级变动、研报元数据与一致预期，但没有任何事后检验：系统无法回答「哪家机构、哪类评级、哪个修正方向对后续超额收益有预测力」。这是把采集变成能力的唯一闭环，也是当前三件差异化能力（一致预期聚合、评级变动监控、分析师事后检验）里唯一尚未建设的一件。

## What Changes

- 新增事件式分析师主张表 `analyst_claims`：从 `rating_changes` 与 `research_reports` 生成**变动事件**（上调/下调/首次/维持、EPS 上修/下修），不记录「当前评级是什么」这类水平值。
- 新增逐主张结算表 `analyst_claim_outcomes`：每条主张按自己的 horizon 结算（评级事件 20 个交易日、EPS 修正 60 个交易日），入场为发布日次一交易日开盘，基准默认沪深 300，记录原始收益与超额收益。
- 新增确定性记分卡：按机构、评级、修正方向、行业分桶输出样本数、胜率、平均/中位超额收益；样本量不足 30 时显式标记「样本不足」，不得输出显著性结论。
- 新增静态机构梯队配置与事后校准输出：`config/analyst_tiers.json` 提供先验梯队，结算结果生成只读机构权重快照；v1 **不**自动改变任何分析师/交易员权重。
- 新增结算日批与只读查询端点；所有数字由代码计算，不调用 LLM。
- **不写入** `predictions` 表：系统自身战绩与卖方观点必须分开，避免污染战绩口径。

## Capabilities

### New Capabilities

- `analyst-ex-post-verification`: 卖方评级/EPS 事件的事件式抽取、逐主张结算、机构与评级分桶记分卡、机构权重先验与校准输出。

### Modified Capabilities

（无。本变更只新增表和只读输出，不改变现有分析与决策行为。）

## Impact

- 数据：`data/research_reports.db` 新增 `analyst_claims`、`analyst_claim_outcomes`、`analyst_scorecard_snapshots`（或等价聚合表）。
- 代码：`src/finance_agent/data/analyst_claims.py`（新）、`src/finance_agent/outcome/analyst_claims/`（结算 job，新）、`src/finance_agent/outcome/scheduler.py`、`src/finance_agent/ops_api.py` 或 `api.py`（只读端点）。
- 配置：`config/analyst_tiers.json`（新）。
- 文档：`docs/evals/metrics.md` §1 新增事后检验口径；`docs/evals/metrics/runs.jsonl` 不需要（不是 LLM 实验）。
- 依赖：无新增第三方依赖；行情数据复用既有快照/行情源。
- 非目标：不做自动权重、不改 prompt、不做前端页面、不接入付费数据源、不对目标价水平值做检验（目标价变动仅在存在前次目标价时生成事件）。
