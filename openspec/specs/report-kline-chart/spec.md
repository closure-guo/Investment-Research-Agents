# report-kline-chart Specification

## Purpose
TBD - created by archiving change update-price-chart-kline. Update Purpose after archive.
## Requirements
### Requirement: K 线图表数据采集

系统 SHALL 在采集报告图表数据时，基于会话 state 中的日 K 线输出 K 线图表数据：最近 250 个交易日的开盘/最高/最低/收盘/成交量逐日序列（口径与 state 日 K 线一致，前复权）、MA5/MA20/MA60 均线序列（收盘价简单移动平均，与报告技术指标使用的均线口径一致，窗口前段无值处为空）、以及交易决策价位（入场价/止损价/目标价，取自最终交易决策或交易员方案，仅在场字段携带）。

#### Scenario: OHLCV 与均线序列输出

- **GIVEN** 会话 state 的日 K 线含最近 250 个交易日的开高低收与成交量
- **WHEN** 采集报告图表数据
- **THEN** 价格图表数据包含逐日的日期、开盘、最高、最低、收盘、成交量
- **AND** MA5/MA20/MA60 序列逐日对齐输出，均线窗口不足的前几日为空值

#### Scenario: 决策价位携带

- **GIVEN** 最终交易决策（或交易员方案）包含入场价、止损价、目标价
- **WHEN** 采集报告图表数据
- **THEN** 价格图表数据携带三个决策价位
- **GIVEN** 决策价位缺失（如 neutral/hold 无价位、审批未通过、历史会话 state）
- **THEN** 不携带对应价位字段，数据采集不报错、不影响其余字段输出

#### Scenario: 日 K 线缺失降级

- **GIVEN** 会话 state 无日 K 线，或日线数量少于 10 个交易日
- **THEN** 不输出价格图表数据，报告其余图表正常生成（沿用现状降级语义）

### Requirement: K 线图双端渲染

系统 SHALL 将报告股价图渲染为 K 线图，服务端 PNG 与前端交互图表 SHALL 消费同一份图表数据：蜡烛主图（开高低收）叠加 MA5/MA20/MA60 均线，成交量副图与蜡烛同日对齐；交易决策价位在场时 SHALL 渲染为水平参考线并带区分标签；涨跌配色 SHALL 遵循 A 股惯例（收阳为红系、收阴为绿系）；报告期截止日标注 SHALL 保留，且双端标签与标题措辞 MUST 如实表述为「报告期截止」，MUST NOT 表述为「财报发布日」或「年报发布日」（数据源为利润表报告日即报告期截止日，非披露日）。

#### Scenario: 服务端 PNG 蜡烛图

- **GIVEN** 价格图表数据完整（OHLCV + 均线）
- **WHEN** 生成报告图表 PNG
- **THEN** 股价图 PNG 为蜡烛主图 + 成交量副图 + MA5/MA20/MA60 叠加
- **AND** 报告期截止日竖线标注保留（注记「报告期止」）

#### Scenario: 前端交互式 K 线

- **GIVEN** 报告消息的 chartData 含 OHLC 字段
- **THEN** 股价图组件以 K 线形态渲染
- **AND** 支持 dataZoom 区间缩放与悬浮 tooltip（开高低收、涨跌幅、成交量）

#### Scenario: 决策价位参考线

- **GIVEN** 价格图表数据携带入场价/止损价/目标价
- **THEN** 图上渲染三条水平参考线并带区分标签（入场/止损/目标）
- **GIVEN** 未携带任一决策价位
- **THEN** 不渲染对应参考线，图表其余部分正常

#### Scenario: 涨跌配色

- **WHEN** 渲染 K 线蜡烛与成交量柱
- **THEN** 收阳（收盘≥开盘）为红系、收阴为绿系，前端配色取自主题变量
- **AND** 暗色主题下图线、标签、坐标轴保持可读

### Requirement: 历史会话后向兼容

前端 SHALL 容忍缺失 OHLC 字段的历史 chart_data（本变更之前会话）：股价图降级为收盘价折线渲染，SHALL NOT 报错或渲染空白。

#### Scenario: 历史会话回放降级

- **GIVEN** 历史会话的 chartData 价格序列仅含收盘价
- **WHEN** 渲染该会话报告
- **THEN** 股价图以收盘价折线呈现
- **AND** 其余图表与报告正文渲染不受影响

