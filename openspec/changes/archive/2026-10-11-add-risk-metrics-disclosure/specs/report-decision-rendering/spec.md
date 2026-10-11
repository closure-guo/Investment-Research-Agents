# Delta: report-decision-rendering（add-risk-metrics-disclosure）

## ADDED Requirements

### Requirement: 风险指标与价位参考口径披露

报告口径披露节（`_format_freshness_section` 确定性渲染块）SHALL 在既有数据源之外增列风控决策链路的两个确定性计算产物，使交易决策与风控辩论节首现的风险数字/触发价位可在报告内溯源（issue #242 断点 1）：

- `state.risk_metrics`：年化波动率、最大回撤、VaR95（历史模拟法）以百分数两位小数渲染；`beta` 仅在基准数据可得（键存在）时渲染，缺失时 SHALL 省略该段（SHALL NOT 渲染「beta 暂缺」——beta 缺席是数据语义而非字段缺失）；其余单项缺失 SHALL 按既有「暂缺」语义标注。
- `state.price_levels`：`available=True` 时 SHALL 渲染入场参考价、止损带、目标带（区间 low–high）；`available=False` 时 SHALL 渲染「价位参考不可用」并附 reason（诚实标注，对齐估值缺失文案语义）。

两键全缺时 SHALL NOT 增行（零回归）；新增内容 SHALL 为披露节块内列表项（不产生新章节标题，不影响导出切章与段数）。

#### Scenario: 全字段渲染

- **GIVEN** state.risk_metrics = {volatility: 0.1568, max_drawdown: 0.1868, var_95: 0.0173, beta: 0.004}，price_levels.available=True 且含 entry_ref/stop_band_long/target_band_long
- **WHEN** 生成报告
- **THEN** 口径披露节含风险指标行（15.68%/18.68%/1.73%/0.004）与价位参考行（入场参考/止损带/目标带区间值）

#### Scenario: beta 缺失时省略该段

- **GIVEN** risk_metrics 无 beta 键（基准 K 线不可得）
- **WHEN** 生成报告
- **THEN** 风险指标行含波动率/最大回撤/VaR95 三项，且无 beta 字样

#### Scenario: 价位不可用诚实标注

- **GIVEN** price_levels = {available: false, reason: "insufficient_kline"}
- **WHEN** 生成报告
- **THEN** 披露节含「价位参考不可用」标注行并附原因

#### Scenario: 两键全缺零回归

- **GIVEN** state 无 risk_metrics 且无 price_levels
- **WHEN** 生成报告
- **THEN** 披露节不增行；若三旧数据源也全缺则披露节整体不出现
