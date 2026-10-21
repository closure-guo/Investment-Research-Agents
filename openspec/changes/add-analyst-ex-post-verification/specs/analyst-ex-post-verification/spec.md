## ADDED Requirements

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
