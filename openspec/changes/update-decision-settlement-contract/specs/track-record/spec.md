# Delta for track-record

## MODIFIED Requirements

### Requirement: 观点数据模型（append-only + 快照冻结）

系统 SHALL 建立 `predictions` 表记录每条观点（Agent 一次可判定输出的最小单元：标的、方向、时间窗口，缺任一要素不进入统计）。表含 `source_type`（backtest/live，回测实盘分离）、`direction`（long/short/neutral）、`confidence`（0-1，校准用）、`rationale_snapshot`（完整分析原文 + 引用数据快照 + **TradeDecision 申报价位 entry/stop/target**）、`entry_price`（**参考价：决策时点实时价或最近收盘，供展示与盯市**）、`horizon_days`（默认取配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`，默认值 20 交易日，上限 1 年）、`avoidance_status`（neutral 观点回避判定结果，系统计算写入，可空）、`settle_entry_price`（**结算入场价：判定时按标准化口径从行情派生**——决策归属日收盘；收盘后决策取次一交易日收盘；非交易日归属其后首个交易日；判定前 NULL，判定产出写入后冻结）。观点快照（rationale_snapshot/direction/entry_price/created_at）写入后 SHALL 冻结，禁止任何接口修改；判定结果由系统计算，只允许状态流转（open → resolved_* / unresolvable；neutral 观点 → avoidance_* 独立流转）。
(Previously: entry_price 为「参考价」且兼作判定基准、horizon_days 默认 252、rationale_snapshot 不含申报价位、无 avoidance_status / settle_entry_price 列。)

#### Scenario: 观点写入即冻结

- **WHEN** 一条观点写入 predictions
- **THEN** rationale_snapshot SHALL 含当时完整分析原文、引用数据与申报价位（如有）
- **AND** 后续任何接口（含服务端内部接口）尝试修改 direction/entry_price/rationale_snapshot/created_at SHALL 失败并留记录

#### Scenario: 回测实盘分离

- **WHEN** 任何对外接口返回战绩数据
- **THEN** 响应 SHALL 按 `source_type` 区分 backtest 与 live
- **AND** SHALL 不存在合并 backtest 与 live 的服务端接口

#### Scenario: 缺要素观点不入统计

- **GIVEN** 一条观点缺标的、方向或时间窗口任一要素
- **WHEN** 写入
- **THEN** SHALL 存档但不进入战绩统计

#### Scenario: horizon 默认值显式落库

- **WHEN** 观点落库且未自带 horizon
- **THEN** horizon_days SHALL 显式写入配置默认值（20 交易日）
- **AND** SHALL NOT 依赖表级默认值隐式生效

#### Scenario: 判定基准为派生入场价

- **GIVEN** 某观点参考价（entry_price）已落库
- **WHEN** 判定任务结算该观点
- **THEN** 区间收益 SHALL 基于 `settle_entry_price`（行情派生）计算，SHALL NOT 基于参考价
- **AND** 派生值 SHALL 随判定结果落库并冻结

#### Scenario: 存量观点不追溯

- **GIVEN** 切点日前落库的 open 观点（horizon_days=252）
- **WHEN** 判定任务处理或口径切点生效
- **THEN** 该观点 SHALL 按其落库时的 horizon 判定，SHALL NOT 被改写为新默认值
- **AND** 已结算存量行 SHALL NOT 重算

### Requirement: 判定规则（Outcome Resolution）

系统 SHALL 按统一规则判定观点：**默认判定窗口 T+20 交易日**（配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`；观点自带 `horizon_days` 则以自带为准，上限 1 年）；**判定入场基准 SHALL 为 `settle_entry_price`（判定时从行情派生，非参考价）**；对同一标的发出方向相反或目标价不同的新观点时，旧观点 SHALL 立即以当前价结算（superseded，入场基准同为派生 `settle_entry_price`）。方向判定：long 观点区间超额收益 > +2% → resolved_win；< −2% → resolved_loss；±2% 区间内 → resolved_neutral（计入总数但不计入胜率分子分母）；short 对称。**中性方向（neutral）观点 SHALL 按回避语义判定**：horizon 到点按 long 口径计算区间超额收益，超额 < −2% → avoidance_win（回避下跌正确）、> +2% → avoidance_loss（错过上行）、±2% 内 → avoidance_neutral；结果写 `avoidance_status`，SHALL NOT 写 resolved_* 状态、SHALL NOT 进入胜率。中性带 ±2% 为全局配置项（回避判定共用）。停牌/退市/长期无行情 SHALL 标记 unresolvable，计入样本量，不计入胜率，UI 单独标识。**判定链路 SHALL 对 long/short 观点向 Langfuse 上报 decision_hit / decision_return / decision_excess（沿既有规范），neutral 观点 SHALL NOT 上报决议类 Score。**
(Previously: 默认判定窗口 T+252、判定基于参考价（entry_price）、neutral 观点按 short 符号判定并计入胜率（实现缺口）、判定链路无 Score 上报。)

#### Scenario: 到期判定

- **GIVEN** 某 long 观点到达 horizon 终点（默认第 20 个交易日）
- **WHEN** 判定任务结算
- **THEN** 按区间超额收益判定 resolved_win / resolved_loss / resolved_neutral

#### Scenario: 中性带判定

- **GIVEN** 某 long 观点区间超额收益为 +1.5%（在中性带 ±2% 内）
- **WHEN** 判定
- **THEN** 该观点 SHALL 为 resolved_neutral
- **AND** 计入总数但不计入胜率分子分母

#### Scenario: 中性观点回避判定

- **GIVEN** 某 neutral 观点（watch 映射）到达 horizon 终点，按 long 口径区间超额为 −5%（标的跑输基准超 2%）
- **WHEN** 判定任务结算
- **THEN** avoidance_status SHALL 为 avoidance_win（回避正确）
- **AND** 该观点 SHALL NOT 获得 resolved_* 状态，SHALL NOT 进入胜率分子分母

#### Scenario: 回避错过上行

- **GIVEN** 某 neutral 观点到达 horizon 终点，按 long 口径区间超额为 +6%
- **WHEN** 判定
- **THEN** avoidance_status SHALL 为 avoidance_loss（错过上行）

#### Scenario: 提前结算（观点变更）

- **WHEN** 对同一标的发出方向相反或目标价不同的新观点
- **THEN** 旧观点 SHALL 立即以当前价格结算判定
- **AND** resolution_rule SHALL 记录为 superseded

#### Scenario: 不可判定

- **GIVEN** 标的停牌 / 退市 / 长期无行情
- **WHEN** 判定任务处理
- **THEN** 该观点 SHALL 标记 unresolvable
- **AND** 计入样本量，不计入胜率

### Requirement: 基础统计（胜率 + 平均超额 + 显著性门槛）

系统 SHALL 计算并返回胜率与平均超额收益。胜率 = resolved_win / (resolved_win + resolved_loss)，neutral 与 unresolvable 不进分母；**胜率与平均超额 SHALL 仅统计 long/short 方向观点**。neutral 观点的回避判定结果 SHALL 单独统计为**回避正确率** = avoidance_win / (avoidance_win + avoidance_loss)（avoidance_neutral 与 unresolvable 不进分母），作为独立辅助指标以单独字段返回，SHALL NOT 混入胜率；回避正确率展示门槛与胜率一致（样本 <10 不展示）。显著性门槛 SHALL 约束展示：样本量 < 10 不展示胜率与评级（仅展示样本数与「样本积累中」）；样本量 10–29 展示胜率并标注「样本较少」；样本量 ≥ 30 完整展示。**默认判定窗口 252→20 为口径切点**：切点 SHALL 按 `track-record-versioning`「战绩分段不混算」机制登记，切点前后已结算行 SHALL NOT 混入同一胜率读数。评级（0–5 星）属后续增量（阶段 A 不做）。
(Previously: 无回避正确率统计；无默认窗口口径切点分段约束。)

#### Scenario: 胜率口径

- **GIVEN** 10 条观点（4 win / 4 loss / 2 neutral）
- **WHEN** 计算胜率
- **THEN** win_rate SHALL 为 0.5

#### Scenario: 回避正确率独立统计

- **GIVEN** 12 条 neutral 观点（6 avoidance_win / 3 avoidance_loss / 3 avoidance_neutral）
- **WHEN** 计算统计
- **THEN** 回避正确率 SHALL 为 6/9 ≈ 0.667，以独立字段返回
- **AND** win_rate SHALL NOT 包含任何 neutral 观点的判定结果

#### Scenario: 回避样本不足不展示

- **GIVEN** 已判定 neutral 观点（avoidance_win + avoidance_loss）< 10
- **WHEN** 请求统计
- **THEN** 回避正确率 SHALL 返回 null 与「样本积累中」标注

#### Scenario: 样本量门槛

- **GIVEN** 某 Agent 已判定观点数为 9
- **WHEN** 请求总览
- **THEN** 响应 SHALL 不返回胜率与评级
- **AND** SHALL 返回样本数与「样本积累中」标注

#### Scenario: 口径切点分段

- **GIVEN** 库内同时存在 252 窗口与 20 窗口结算的观点
- **WHEN** 计算对外展示的胜率
- **THEN** SHALL 按 `track-record-versioning` 分段机制区分口径，SHALL NOT 混算单一读数

#### Scenario: 空库

- **GIVEN** 无观点记录
- **WHEN** 请求总览
- **THEN** 计数类字段 SHALL 返回 0，胜率与超额 SHALL 返回 null
