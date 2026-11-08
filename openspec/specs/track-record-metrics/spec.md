# track-record-metrics Specification

## Purpose
TBD - created by archiving change add-track-record-stage-b. Update Purpose after archive.
## Requirements
### Requirement: 每日盯市记录

系统 SHALL 每个交易日对全部 status=open 观点写入 `daily_marks` 记录(mark_date/mark_price/cum_return/cum_excess),停牌或缺数据时跳过且不报错。盯市任务 SHALL 在同一日批内对 INDEX_COMPARE_UNIVERSE 指数集拉取当日收盘并幂等写入 `index_closes`(语义细则见 index-comparison 能力);指数落库 SHALL NOT 改变本需求盯市记录的行为与汇总口径。
(Previously: 仅要求 open 观点写 daily_marks,无指数收盘落库职责。)

#### Scenario: 正常盯市

- **WHEN** 日批 `daily-marking` 任务运行且 open 观点对应标的有当日收盘价
- **THEN** 写入一条 daily_marks,cum_return 与 cum_excess 相对 entry_price 与基准同步期收益计算

#### Scenario: 缺数据容错

- **WHEN** 标的当日无行情(停牌/接口失败)
- **THEN** 该观点当日不产生 daily_marks,任务继续处理其余观点且整体返回成功

#### Scenario: 指数收盘顺带落库不影响盯市

- **WHEN** 日批运行,指数集收盘拉取全部失败
- **THEN** daily_marks 与 equity_curve 行为不变,盯市任务状态仍为成功
- **AND** 失败详情只出现在日志 WARNING,不进入 marked/skipped/errors 汇总

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

### Requirement: 组合净值曲线

系统 SHALL 基于**日主观点池**（open+resolved 观点中每 (symbol, 决策归属日) 的 created_at 最晚者；同日重复观点 SHALL NOT 进入组合）维护 `equity_curve`：**仓位单位为标的**——同一标的在同一盯市日存在多条 long/short 观点的盯市记录时，SHALL 先取该标的各观点当日收益的算术平均作为该标的的当日收益，再按当日有盯市的**去重标的数 M** 等权 1/M 日再平衡；空仓日记 0 收益，同步记录基准净值供叠加对比。观点在组合内的首盯市日贡献 0 收益（现金口径），入场参考价 SHALL NOT 参与净值与指标计算——组合日收益仅由相邻盯市日 `cum_return` 差分构成，对坏参考价免疫。盯市日批计算时点 SHALL 消费「截至该批次的日主视图」——决策归属日规则（收盘后产出归属次日）保证当日归属观点集合在盘后批次时点已封闭，日主判定确定性成立。净值序列 SHALL 以基准指数交易日历为骨架，覆盖首个盯市日以来的全部交易日：有盯市的日期按等权均值计，无盯市的交易日（空仓/整体缺数据）agent 日收益记 0；基准净值 SHALL 按同一交易日历逐日推进（基准收盘取自基准日 K，而非仅 marks 内记录价）。基准行情不可得时 SHALL 降级为现行 marks-only 口径并记 WARN。

(Previously: 组合分母为「日主观点数 N」——同一标的跨决策日的多条 open 观点各占 1/N，互不去重。实测 600029 在 2026-10-22~10-26 两条并存致单票权重 100%，11-04 三条并存。)

#### Scenario: 同标的跨日观点合并为一个仓位

- **GIVEN** 某标的在 2026-10-20 与 2026-10-22 各产出一条仍 open 的 short 观点，两条在 2026-10-26 均有盯市记录
- **WHEN** 构建当日组合
- **THEN** 该标的当日 SHALL 只占 1 份（1/M，M = 当日去重标的数）
- **AND** 该标的当日收益 SHALL 等于两条观点当日收益的算术平均
- **AND** 其权重 SHALL NOT 因观点条数增加而翻倍

#### Scenario: 不同标的仍按等权

- **GIVEN** 当日有 A、B 两个标的、A 有 2 条观点、B 有 1 条观点
- **WHEN** 构建当日组合
- **THEN** A 与 B 各占 1/2
- **AND** A 的收益为其两条观点的算术平均

#### Scenario: 标的映射缺失时按观点退化

- **GIVEN** 调用方未提供 `symbol_by_prediction` 映射
- **WHEN** 聚合组合日收益
- **THEN** 系统 SHALL 按观点逐条聚合（与修订前行为一致）
- **AND** SHALL NOT 抛错

#### Scenario: 净值计算

- **WHEN** 日批任务运行
- **THEN** equity_curve 追加当日 agent 净值与基准净值，空仓日 agent 日收益为 0
- **AND** 组合等权分母 SHALL 为当日有盯市的去重标的数 M（标的集合由日主观点池构成）

#### Scenario: 同日重复不进组合

- **GIVEN** 某标的同一交易日产出 7 条观点（1 日主 + 6 同日重复）
- **WHEN** 盯市日批构建组合
- **THEN** 该标的当日 SHALL 在组合中计 1 份（日主观点）
- **AND** 6 条同日重复观点 SHALL NOT 进入分母与盯市贡献

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

### Requirement: 无风险利率序列同步

系统 SHALL 将中债国债收益率曲线 1 年期到期收益率（源：中债估值中心，经 AKShare `bond_china_yield` 获取）按交易日落库至 `risk_free_rates` 表（rate_date 主键 + rate + source），写入幂等（同日覆盖）。日批 SHALL 在指标快照计算前执行 rf 同步；同步失败 SHALL 隔离——不得使盯市或快照失败，不计入 marked/skipped/errors。快照消费侧 SHALL 经回退链解析逐日 rf：库内 carry-forward（目标日无记录时沿用最近可得历史日）→ 全库无数据时回退 `TRACK_RISK_FREE_RATE` 常数（默认 0.02）。

#### Scenario: 日批同步落库

- **WHEN** 日批运行且 chinabond 接口正常
- **THEN** 当日（及缺失区间内）1 年期国债收益率写入 risk_free_rates，重跑幂等

#### Scenario: 同步失败隔离

- **WHEN** chinabond 接口超时或异常
- **THEN** 日批盯市与指标快照仍成功完成，夏普/α 按回退链（carry-forward 或常数）计算，rf 同步失败仅记 WARNING

#### Scenario: 库缺当日记录时向前沿用

- **GIVEN** risk_free_rates 最新记录为 2026-09-28（1.2257%），当日 09-29 无记录
- **WHEN** 解析 09-29 的 rf
- **THEN** 使用 1.2257%（carry-forward），不因单日缺数据回退常数

#### Scenario: 全库无数据兜底

- **GIVEN** risk_free_rates 为空表（首次部署未回填）
- **WHEN** 指标快照计算
- **THEN** 全部日期 rf 取 `TRACK_RISK_FREE_RATE` 常数，快照正常产出

### Requirement: 风险收益指标引擎

系统 SHALL 日批重算 `agent_metrics_daily`：样本量/胜率/平均超额/年化收益/波动率/夏普/**组合 β 与年化 Jensen α**/最大回撤/风险分 `clip(round(0.6*dd+0.4*vol),1,10)`（映射表配置化）。年化收益/波动率/夏普/最大回撤 SHALL 基于覆盖全部交易日的净值序列计算（n = 首个盯市日以来的交易日数，空仓日记 0），SHALL NOT 仅按有盯市的日期数外推。聚合排除 neutral 方向观点的盯市记录。**无风险利率口径**：rf 为逐日年化序列，取自 `risk_free_rates`（中债国债收益率曲线 1 年期，回退链见「无风险利率序列同步」需求）；夏普为标准逐日超额定义 `mean(r_t − rf_t/252) / std(r_t − rf_t) × √252`。β/α 口径：由 equity 双净值序列推导同日期日收益对（组合日收益 = 相邻 agent_nav 之比减一；基准日收益 = 相邻 benchmark_nav 之比减一；仅取两序列均非空的日期，首个盯市日不计入），β 为 OLS 斜率 `Σ((rb−μb)(ra−μa))/Σ((rb−μb)²)`；α 为日频 CAPM 残差年化 `mean(ra_t − rf_t/252 − β×(rb_t − rf_t/252))×252`，rf_t 与夏普同源。重叠日收益对 < 20 时 β 与 α SHALL 为 null（不产出噪声读数）。历史快照重算 SHALL as-of 过滤（重算某 metric_date 行仅用 mark_date/rf ≤ 该日的数据），可经手动重算补齐。

(Previously: 夏普口径为 `(年化收益 − rf) / 年化波动率`，rf 为全窗口单一常数（默认 2%，env `TRACK_RISK_FREE_RATE` 配置化）；α 的 rf 亦为常数 `rf/252`；β/α 定义同 add-portfolio-beta-alpha。年化/波动原基于仅有盯市的日期序列（n = 盯市日数），空仓/缺数据交易日不计数，年化被系统性放大；neutral 方向盯市按空头符号混入聚合。)

#### Scenario: 指标快照

- **WHEN** `metrics-snapshot` 任务运行
- **THEN** agent_metrics_daily 写入当日全量指标行，重跑幂等（同日覆盖）

#### Scenario: 年化不因盯市缺口放大

- **GIVEN** 净值序列覆盖 20 个交易日，其中 6 日无盯市（空仓）
- **WHEN** 计算年化收益
- **THEN** n SHALL 为 20（交易日数）
- **AND** 年化 SHALL NOT 按 n=14 外推放大

#### Scenario: 夏普按逐日 rf 计算

- **GIVEN** 净值序列日收益 [0.01, −0.02, 0.015]，对应日 rf_t 年化 [1.20%, 1.21%, 1.22%]
- **WHEN** 指标引擎重算
- **THEN** 夏普 = mean(r_t − rf_t/252)/std(r_t − rf_t)×√252（逐日超额），年化收益/波动率/β 口径不变

#### Scenario: β/α 正常计算

- **GIVEN** equity 双净值序列有 ≥ 20 个重叠日收益对，组合日收益与基准日收益存在稳定线性关系（β=0.8）
- **WHEN** 指标引擎重算
- **THEN** beta ≈ 0.8（OLS 斜率），jensen_alpha 按逐日 rf_t 的 CAPM 残差年化，两值随快照落库

#### Scenario: 样本不足时置空

- **GIVEN** 重叠日收益对 < 20（如当前净值曲线仅 17 个交易日）
- **WHEN** 指标引擎重算
- **THEN** beta 与 jensen_alpha 为 null，其余指标不受影响
- **AND** 快照落库 null 值，overview 不展示误导性读数

#### Scenario: 旧库幂等迁移

- **GIVEN** 已有 agent_metrics_daily 表但无 beta/jensen_alpha 列的存量库
- **WHEN** 初始化建表逻辑运行
- **THEN** 两列经 ALTER TABLE 补齐,既有数据不丢,重跑不重复加列

### Requirement: 总览展示风险维度

总览 API SHALL 返回组合指标（年化/回撤/夏普/风险分），战绩页 SHALL 展示净值曲线图与风险卡，收益与风险成对出现（P4）。

#### Scenario: 页面成对展示

- **WHEN** 用户打开历史战绩页
- **THEN** 总览区同时呈现收益类（胜率/平均超额/年化）与风险类（回撤/波动/夏普/风险分）指标及净值曲线

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

