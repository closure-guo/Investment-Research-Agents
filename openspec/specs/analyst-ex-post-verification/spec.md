# analyst-ex-post-verification Specification

## Purpose
TBD - created by archiving change add-analyst-ex-post-verification. Update Purpose after archive.
## Requirements
### Requirement: 事件式卖方主张生成

系统 SHALL 从 `rating_changes` 与 `research_reports` 生成事件式分析师主张，只记录**变动**，MUST NOT 把「当前评级是什么」这类水平值当作主张。

- 评级主张 SHALL 通过评级序数比较确定性推导方向：买入=5、增持=4、中性=3、减持=2、卖出=1；当前 > 前次为 `rating_upgrade`，当前 < 前次为 `rating_downgrade`，相等为 `rating_maintain`，前次缺失或首次评级为 `rating_initiation`。
- MUST NOT 依赖巨潮 `rating_change` 文本判断方向（实测该字段只有「维持/未知」）。
- EPS 修正主张 SHALL 在同一 `(code, org, 预测年度)` 上与前一篇同机构研报比较，生成 `eps_revision_up` 或 `eps_revision_down`；无前篇时不生成。
- 目标价主张 SHALL 只在同一 `(code, org)` 存在带目标价的前次事件且本次也有目标价时生成 `target_price_up` / `target_price_down`；否则跳过。
- 每条主张 SHALL 有幂等主键，重复日批/补跑 MUST NOT 产生重复主张。

#### Scenario: 评级上调事件

- **GIVEN** 同一标的与机构的前次评级为「增持」，本次评级为「买入」
- **WHEN** 主张生成 job 运行
- **THEN** SHALL 生成一条 `rating_upgrade` 主张
- **AND** SHALL 记录 `prev_rating=增持`、`rating=买入`、`published_at`

#### Scenario: 维持评级作为对照组

- **GIVEN** 前次评级与本次评级均为「买入」
- **WHEN** 主张生成 job 运行
- **THEN** SHALL 生成一条 `rating_maintain` 主张
- **AND** 该主张 SHALL 参与记分卡分桶，但不得被宣称为上调信号

#### Scenario: 首次评级不伪造前值

- **GIVEN** 巨潮记录 `is_first_rating=1` 且无前次评级
- **WHEN** 主张生成 job 运行
- **THEN** SHALL 生成 `rating_initiation`
- **AND** MUST NOT 编造 `prev_rating`

#### Scenario: 同一事件重复生成幂等

- **GIVEN** 某评级事件已生成主张
- **WHEN** 同日日批重跑
- **THEN** `analyst_claims` 行数 SHALL 不变

### Requirement: 逐主张结算

系统 SHALL 按每条主张自己的 horizon 独立结算，MUST NOT 用统一 horizon 覆盖全部主张。

- 入场 SHALL 为发布日次一交易日开盘价；MUST NOT 用发布日收盘价入场（前视）。
- 出场 SHALL 为入场后第 H 个交易日收盘价；评级事件默认 H=20，EPS 修正与目标价事件默认 H=60（可配置）。
- 基准默认 `000300.SH`；SHALL 记录原始收益、基准同窗口收益与超额收益。
- 停牌、缺行情、退市 SHALL 记 `unresolved` 与原因，MUST NOT 用前值填充或静默丢弃。
- 价格 SHALL 使用复权口径。

#### Scenario: 评级事件按期结算

- **GIVEN** 一条 20 日 horizon 的评级上调主张，发布日为 T
- **WHEN** 第 T+21 个交易日收盘后结算 job 运行
- **THEN** SHALL 以 T+1 开盘入场、T+21 收盘出场计算收益
- **AND** SHALL 写入 `raw_return`、`benchmark_return`、`excess_return` 与 `status=resolved`

#### Scenario: 未到期不结算

- **GIVEN** 一条主张的 horizon 尚未走完
- **WHEN** 当日结算 job 运行
- **THEN** 该主张 SHALL 保持 pending，MUST NOT 写入收益

#### Scenario: 停牌标的显式未解决

- **GIVEN** 主张标的在入场日停牌
- **WHEN** 结算 job 运行
- **THEN** SHALL 记 `status=unresolved` 与 `reason=suspended`
- **AND** MUST NOT 用停牌前价格伪造入场价

### Requirement: 机构与评级分桶记分卡

系统 SHALL 以确定性代码生成记分卡，按机构、评级、主张类型、行业四个维度分桶，MUST NOT 调用 LLM 计算任何数字。

- 每个桶 SHALL 输出样本数 `n`、胜率（超额收益 > 0 的占比）、平均超额、中位超额。
- `n < 30` 时 SHALL 标记 `reliable=false`，对外展示 MUST 显示「样本不足」。
- v1 MUST NOT 输出显著性检验结论或「该机构有效/无效」的因果判断。

#### Scenario: 样本不足显式标注

- **GIVEN** 某机构只有 12 条已结算主张
- **WHEN** 记分卡生成
- **THEN** 该机构桶 SHALL 输出 `n=12` 与 `reliable=false`
- **AND** 展示文本 SHALL 含「样本不足」

#### Scenario: 分桶数字可复算

- **GIVEN** 同一批已结算主张
- **WHEN** 记分卡连续生成两次
- **THEN** 两次输出的 `n`、胜率、平均/中位超额 SHALL 完全一致

### Requirement: 机构梯队与权重快照（只读）

系统 SHALL 提供静态机构梯队配置作为先验，并基于已结算主张生成只读机构权重快照。

- 先验配置 SHALL 位于 `config/analyst_tiers.json`，未列出的机构归 `unrated`，MUST NOT 因未知而丢弃其主张。
- 权重快照 SHALL 只读，v1 MUST NOT 被分析师、辩论、交易员、风控或执行链读取用于决策。
- 权重公式与参数 SHALL 在 `docs/evals/metrics.md` §1 登记。

#### Scenario: 未评级机构不被丢弃

- **GIVEN** 某机构不在梯队配置中
- **WHEN** 主张生成与结算运行
- **THEN** 其主张 SHALL 正常入库并结算
- **AND** 梯队 SHALL 记为 `unrated`，不参与先验加权

#### Scenario: 权重不改变决策

- **GIVEN** 权重快照已生成
- **WHEN** 执行一次深度分析
- **THEN** 分析上下文与交易决策 SHALL 不读取该权重
- **AND** 报告与决策字段 SHALL 与未生成快照时一致

### Requirement: 结算日批与只读接口

系统 SHALL 注册每交易日结算 job，并提供只读查询接口。

- job 默认 17:00 触发（晚于研报数据面 16:52–16:56），先结算到期主张，再生成当日记分卡快照。
- 单条主张失败 MUST NOT 中断其他主张；job 整体失败 SHALL 记入 `collection_runs` 并保留已完成部分。
- 只读接口 SHALL 支持按机构、评级、主张类型、行业查询记分卡，并返回 `n`、`reliable`、生成时间。
- 接口与 job MUST NOT 调用 LLM。

#### Scenario: 日批注册

- **GIVEN** 调度器启动
- **WHEN** 读取注册表
- **THEN** SHALL 包含 `analyst_claim_settle_daily`
- **AND** 注册表镜像守护测试 SHALL 覆盖该 job

#### Scenario: 部分失败可恢复

- **GIVEN** 3 条主张到期，其中 1 条行情源报错
- **WHEN** 结算 job 运行
- **THEN** 其余 2 条 SHALL 正常结算
- **AND** 失败 1 条 SHALL 保持 pending 并记错误原因
- **AND** job 状态 SHALL 为 degraded，MUST NOT 静默为 ok

### Requirement: 与系统自身战绩隔离

分析师主张与结算 MUST NOT 写入 `predictions` 表，也 MUST NOT 计入系统净值、胜率或既有战绩页。

#### Scenario: 战绩口径不受污染

- **GIVEN** 系统中已有 `predictions` 行
- **WHEN** 分析师主张结算 job 运行
- **THEN** `predictions` 行数与净值曲线 SHALL 不变

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

