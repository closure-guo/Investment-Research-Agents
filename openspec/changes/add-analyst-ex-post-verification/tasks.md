## 1. 数据层

- [x] 1.1 在 `research_reports.db` 新增 `analyst_claims`、`analyst_claim_outcomes`、`analyst_scorecard_snapshots` 三表与索引（幂等 DDL，沿用 WAL + busy_timeout 短连接纪律）
- [x] 1.2 实现评级序数映射与方向推导（买入=5/增持=4/中性=3/减持=2/卖出=1；不依赖 `rating_change` 文本）
- [x] 1.3 实现评级主张生成：上调/下调/维持/首次，事件键幂等
- [x] 1.4 实现 EPS 修正主张生成：同 `(code, org, 年度)` 与同机构前篇比较；无前篇不生成
- [x] 1.5 实现目标价主张生成：仅在有前次目标价时生成；无前值跳过
- [x] 1.6 单元测试：事件方向、首次评级不伪造前值、重复生成幂等、EPS 上修/下修

## 2. 结算

- [x] 2.1 实现逐主张结算：T+1 开盘入场、按 per-claim horizon 出场、复权价格
- [x] 2.2 实现基准同窗口收益与超额收益计算（默认 `000300.SH`）
- [x] 2.3 实现 unresolved 语义：停牌/缺行情/退市记原因，不填前值
- [x] 2.4 单元测试：按期结算、未到期不结算、停牌显式未解决、无前视入场

## 3. 记分卡与权重

- [x] 3.1 实现按机构/评级/主张类型/行业四维分桶的确定性记分卡（n、胜率、平均/中位超额）
- [x] 3.2 实现 `n < 30` 的 `reliable=false` 与「样本不足」展示语义
- [x] 3.3 新增 `config/analyst_tiers.json` 先验梯队；未列机构归 `unrated` 且不丢弃主张
- [x] 3.4 实现只读机构权重快照（公式与参数登记 `docs/evals/metrics.md` §1）
- [x] 3.5 测试：同日两次生成结果完全一致；权重快照不进入任何分析/决策路径

## 4. 调度与接口

- [x] 4.1 注册 `analyst_claim_settle_daily`（17:00）：先结算、后生成快照；注册表镜像守护测试覆盖
- [x] 4.2 实现部分失败语义：单条失败不中断，job 记 degraded，失败主张保持 pending
- [x] 4.3 新增只读记分卡查询接口（按机构/评级/类型/行业），返回 n/reliable/生成时间
- [x] 4.4 测试：job 注册、degraded 状态、接口过滤与空态

## 5. 验收

- [x] 5.1 `openspec validate add-analyst-ex-post-verification --strict` 通过
- [x] 5.2 `uv run pytest` 定向测试全绿；`uv run ruff check` 通过
- [x] 5.3 用真实库跑一轮：确认 `analyst_claims` 有行、`analyst_claim_outcomes` 到期后可结算、记分卡可查
- [x] 5.4 确认 `predictions` 行数、净值曲线、既有战绩页零变化
- [x] 5.5 `docs/evals/metrics.md` §1 已登记事件定义、horizon、基准、权重公式
