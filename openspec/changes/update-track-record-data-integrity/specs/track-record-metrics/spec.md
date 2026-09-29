# Delta for track-record-metrics

## ADDED Requirements

### Requirement: neutral 观点盯市语义与组合隔离

neutral 方向（hold/watch）观点的盯市记录 SHALL 按多头口径计算 `cum_return`（= 标的相对参考价走势，与回避判定引擎 `judgment` 同号），SHALL NOT 按空头取反号。neutral 方向观点的盯市记录 SHALL NOT 进入组合日收益、净值曲线与风险指标聚合——回避决策不产生持仓损益，其盯市数据仅供观点详情展示与回避判定参考。long/short 方向观点的盯市符号语义维持现行（long 多头、short 空头取反）。

#### Scenario: neutral 盯市不取反号

- **GIVEN** 某 neutral 观点参考价 100，首盯市日收盘 105
- **WHEN** 日批盯市
- **THEN** 该日 `cum_return` SHALL 为 +0.05（多头口径），SHALL NOT 为 -0.05

#### Scenario: neutral 不进组合净值

- **GIVEN** 池内仅剩 neutral 观点的盯市记录
- **WHEN** 构建净值曲线与风险指标
- **THEN** 该时段组合日收益 SHALL 记 0（空仓口径），agent 净值持平
- **AND** 年化/波动/夏普/回撤 SHALL NOT 因 neutral 盯市产生变动

#### Scenario: long/short 符号维持

- **GIVEN** 某 short 观点标的上涨 5%
- **WHEN** 日批盯市
- **THEN** 该日 `cum_return` SHALL 为 -0.05（现行空头取反语义保留）

### Requirement: 参考价失效盯市防护

盯市任务对 open 观点计算首盯市参考价校验：`entry_price` 与该观点 created 日（或其后首个交易日）K 线收盘的偏离超过阈值（配置项 `TRACK_ENTRY_PRICE_MAX_DEVIATION`，默认 0.30，覆盖各板块涨跌幅限制）时，SHALL 视参考价失效——跳过该观点、不写入 daily_marks，并记 WARN 供人工甄别（append-only 冻结语义下不修改 entry_price）。偏离在阈值内的观点正常盯市。

#### Scenario: 坏参考价不入盯市

- **GIVEN** 某 open 观点 `entry_price=1800`，其 created 后首个交易日收盘 1316（偏离 37%）
- **WHEN** 日批盯市
- **THEN** 该观点 SHALL 被跳过（skipped 计数），SHALL NOT 写入任何 daily_marks
- **AND** SHALL 记 WARN 日志（含 prediction_id 与两价偏离）

#### Scenario: 正常参考价不受影响

- **GIVEN** 某 open 观点 `entry_price=100`，created 后首个交易日收盘 102（偏离 2%）
- **WHEN** 日批盯市
- **THEN** 正常写入 daily_marks，行为与现行一致

### Requirement: 组合指标数据日期诚实性

总览 API 的组合指标块 `as_of` SHALL 为指标所依据盯市/净值数据的最新日期（equity_curve 最新 curve_date），SHALL NOT 使用快照写入日期（agent_metrics_daily.metric_date）冒充数据日期。无任何净值数据时 `available=false` 且不伪称当日数据。

#### Scenario: 数据停更时 as_of 如实

- **GIVEN** 盯市/净值数据最新日期为 2026-09-24，指标快照写入日期为 2026-09-28
- **WHEN** 请求总览
- **THEN** `portfolio.as_of` SHALL 为 2026-09-24

#### Scenario: 无净值数据

- **GIVEN** equity_curve 为空
- **WHEN** 请求总览
- **THEN** `portfolio.available` SHALL 为 false
- **AND** `portfolio.as_of` SHALL 为 null

## MODIFIED Requirements

### Requirement: 组合净值曲线

系统 SHALL 基于 open+resolved 观点维护 `equity_curve`：等权 1/N 日再平衡、空仓日记 0 收益，同步记录基准净值供叠加对比。观点在组合内的首盯市日贡献 0 收益（现金口径），入场参考价 SHALL NOT 参与净值与指标计算——组合日收益仅由相邻盯市日 `cum_return` 差分构成，对坏参考价免疫。净值序列 SHALL 以基准指数交易日历为骨架，覆盖首个盯市日以来的全部交易日：有盯市的日期按等权均值计，无盯市的交易日（空仓/整体缺数据）agent 日收益记 0；基准净值 SHALL 按同一交易日历逐日推进（基准收盘取自基准日 K，而非仅 marks 内记录价）。基准行情不可得时 SHALL 降级为现行 marks-only 口径并记 WARN。
(Previously: 首盯市日日收益 = 当日 cum_return（相对入场参考价），入场→首盯市日的整段漂移被记为单日组合收益；无盯市的交易日不产生净值点（n 偏小）；基准净值仅取自 marks 内记录的 benchmark_price。)

#### Scenario: 净值计算

- **WHEN** 日批任务运行
- **THEN** equity_curve 追加当日 agent 净值与基准净值，空仓日 agent 日收益为 0

#### Scenario: 首盯市日不放大入场漂移

- **GIVEN** 某观点 created 于周六，其参考价与周一盯市价因参考价错记相差 27%
- **WHEN** 构建净值曲线
- **THEN** 该观点首个盯市日对组合日收益贡献 SHALL 为 0
- **AND** 组合净值 SHALL NOT 因入场→首盯市日漂移产生单日跳变

#### Scenario: 交易日历覆盖空仓期

- **GIVEN** 首个盯市日以来某交易日全部观点无盯市（空仓或缺数据）
- **WHEN** 构建净值曲线
- **THEN** 该交易日 SHALL 产生一个 equity_curve 点，agent 日收益 0、净值持平
- **AND** 基准净值 SHALL 按基准日 K 同日推进

#### Scenario: 基准行情缺失降级

- **WHEN** 日批任务运行且基准指数日 K 拉取失败
- **THEN** SHALL 降级为仅按 daily_marks 内日期构建净值（现行行为）并记 WARN

### Requirement: 风险收益指标引擎

系统 SHALL 日批重算 `agent_metrics_daily`：样本量/胜率/平均超额/年化收益/波动率/夏普（无风险利率默认 2%，配置化）/最大回撤/风险分 `clip(round(0.6*dd+0.4*vol),1,10)`（映射表配置化）。年化收益/波动率/夏普/最大回撤 SHALL 基于覆盖全部交易日的净值序列计算（n = 首个盯市日以来的交易日数，空仓日记 0），SHALL NOT 仅按有盯市的日期数外推。聚合排除 neutral 方向观点的盯市记录。
(Previously: 年化/波动基于仅有盯市的日期序列（n = 盯市日数），空仓/缺数据交易日不计数，年化被系统性放大；neutral 方向盯市按空头符号混入聚合。)

#### Scenario: 指标快照

- **WHEN** `metrics-snapshot` 任务运行
- **THEN** agent_metrics_daily 写入当日全量指标行，重跑幂等（同日覆盖）

#### Scenario: 年化不因盯市缺口放大

- **GIVEN** 净值序列覆盖 20 个交易日，其中 6 日无盯市（空仓）
- **WHEN** 计算年化收益
- **THEN** n SHALL 为 20（交易日数）
- **AND** 年化 SHALL NOT 按 n=14 外推放大
