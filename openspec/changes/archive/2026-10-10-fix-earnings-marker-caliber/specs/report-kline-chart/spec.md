# Delta: report-kline-chart

## MODIFIED Requirements

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
