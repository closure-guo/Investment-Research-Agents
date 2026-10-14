# factor-library Specification

## Purpose
TBD - created by archiving change add-factor-library-and-monitor. Update Purpose after archive.
## Requirements
### Requirement: 五道闸准入管线

候选因子 SHALL 依次通过以下闸口方可置为 `active`，任一闸口失败即拒收并记录失败闸口与原因：(1) 可计算性——在快照全市场上因子值缺失率 ≤ 20%；(2) 单因子检验——|RankIC| 均值显著且 |ICIR| ≥ 0.3；(3) 多重检验校正——同批次候选做置换检验并以 BH-FDR（q=0.1）控制；(4) 池级组合增量——加入候选后 active 池等权组合的样本外 IR 增量 > 0，且与池内任一因子 |IC 相关性| ≤ 0.7；(5) 样本外复核——训练/验证按时间切分，切分口径与 decision-backtest 的时点截断契约一致。

#### Scenario: 噪声因子拦截

- **GIVEN** 一批 50 个由随机数生成的无意义因子表达式
- **WHEN** 执行准入管线（固定随机种子）
- **THEN** 入库（置为 active）数量 < 5
- **AND** 每个被拒因子记录被拒闸口编号

#### Scenario: 冗余因子拦截

- **GIVEN** active 池已含因子 A，候选因子 B 与 A 的 IC 相关性为 0.92 且 B 单因子 ICIR = 0.5
- **WHEN** 执行闸④
- **THEN** B 被拒收，原因码 `REDUNDANT_WITH_POOL`，并记录相关对象 A

### Requirement: 双层查重

入库前 SHALL 执行结构层查重（与库内 candidate/active/degraded 因子做 AST 子树同构检测（retired 允许重提））与数值层查重（IC 相关性）；结构同构者直接拒收，原因码 `STRUCTURAL_DUPLICATE`。

#### Scenario: 换皮因子拒收

- **GIVEN** 库内含 `ts_mean(close, 10)`，新提案为 `ts_mean(close, 11)`
- **WHEN** 执行查重
- **THEN** 提案被拒收，原因码 `STRUCTURAL_DUPLICATE`

### Requirement: 分桶保种

active 因子 SHALL 按 category 分桶独立保留 top-k（k=10，按池级增量贡献排序）；新因子入库导致桶内超容时，桶内末位降级为 `retired`，不影响其他桶。

#### Scenario: 桶内末位淘汰

- **GIVEN** 动量桶已满 10 个 active 因子，新动量因子入库且增量贡献高于桶内末位
- **WHEN** 入库完成
- **THEN** 动量桶仍为 10 个 active，原末位转为 `retired`
- **AND** 其他桶的 active 集合不变

### Requirement: 衰减监控与自动降级

系统 SHALL 每日重算 active 因子的近 60 日滚动 IC；当某因子滚动 IC 均值连续 20 个交易日为负且 |滚动 ICIR| < 0.1 时，SHALL 自动将其状态置为 `degraded`、从选股打分权重中移除，并落库降级事件（含触发时的滚动指标值）。

#### Scenario: 衰减降级

- **GIVEN** 因子 F 连续 20 个交易日滚动 IC 均值为负且滚动 ICIR = 0.05
- **WHEN** 当日衰减监控日批执行
- **THEN** F 状态变为 `degraded`，降级事件落库含滚动 IC/ICIR 快照
- **AND** 当日选股打分不再包含 F

### Requirement: builtin 因子注册路径

系统 SHALL 内置手工因子基线包（20 日动量、5 日反转、换手率、20 日波动率、市值代理），以 `category=builtin` 注册入因子库；builtin 因子免 LLM 提案环节，但 SHALL 同样通过五道闸方可置为 active。

#### Scenario: builtin 因子受同等准入约束

- **GIVEN** builtin 反转因子在准入评估中 ICIR = 0.12
- **WHEN** 执行准入管线
- **THEN** 该因子被拒收于闸②，与 LLM 提案因子适用同一阈值

### Requirement: 因子库只读查询

系统 SHALL 提供因子库只读接口：列表 SHALL 返回每个因子的名称、表达式、状态（`active` / `retired`）、来源（`builtin` / `llm`）、ICIR、最近闸门结论与衰减标记；详情 SHALL 返回完整 DSL 表达式、IC 序列与事件历史（准入 / 降级 / 查重等事件及时间）。接口 SHALL NOT 暴露任何写操作（因子入库、状态变更、参数调整仍只经五道闸与衰减监控路径）；库为空时 SHALL 返回空列表而非错误。

#### Scenario: 列表与详情字段齐备

- **GIVEN** 库中含一条 active/llm 来源因子与一条 retired/builtin 因子
- **WHEN** 请求列表与其中一条详情
- **THEN** 列表含两因子的状态/来源/ICIR/衰减标记；详情含完整表达式、IC 序列与事件历史

#### Scenario: 空库不报错

- **GIVEN** 因子库为空
- **WHEN** 请求列表
- **THEN** 返回 200 与空列表，MUST NOT 以 500 或伪数据冒充

