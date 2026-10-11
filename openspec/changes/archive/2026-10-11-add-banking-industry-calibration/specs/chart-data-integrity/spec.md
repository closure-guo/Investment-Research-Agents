# add-banking-industry-calibration delta — chart-data-integrity

## MODIFIED Requirements

### Requirement: 图表缺数据禁止填 0 渲染

图表对缺失数据的渲染 SHALL 分三级语义，任何一级 MUST NOT 将缺失渲染为 0：**单元格级**（热力图）缺数据渲染为缺失标注（掩膜/占位文本）；**序列级**部分缺失断开该点（不画柱/线段）；**序列级整体缺失**时呈现占位说明文案或跳过该图（对齐既有 contract_liab/debt_ratio 全缺跳过语义），MUST NOT 静默只渲染部分序列冒充完整对比图。

占位说明 SHALL 区分「数据缺失」与「行业不适用」两类语义：银行业标的（`is_banking_industry` 判定，industry 经 `collect_chart_data` 携带）的毛利率与净利率子图在毛利率整体缺失时，占位文本 SHALL 为「银行业不适用毛利率口径（无营业成本概念）」类行业不适用表述，MUST NOT 误述为「利润率数据缺失」（银行数据没有缺失，是口径不适用）；非银行业整体缺失维持「数据缺失」类占位。

#### Scenario: 热力图窗口外年份渲染缺失标注

- **GIVEN** K 线仅覆盖近一年，热力图中 4 个年报行的窗口日期不在 K 线范围内
- **WHEN** 热力图渲染
- **THEN** 该 4 行单元格 SHALL 渲染为缺失标注（掩膜/「缺」类占位文本）
- **AND** MUST NOT 渲染为「0.0」

#### Scenario: 股价涨幅序列整体缺失时占位说明

- **GIVEN** K 线窗口不足以计算任何一年的年度股价涨幅
- **WHEN** 增速vs股价图渲染
- **THEN** 图内 SHALL 呈现占位说明（如「股价涨幅数据不足」）
- **AND** 财务增速柱正常渲染，MUST NOT 呈现为「股价涨幅为 0」

#### Scenario: 柱图缺失值断开不画零高柱

- **GIVEN** 年度序列中某年金额字段缺失（None）
- **WHEN** 营收净利/现金流/总资产权益柱图渲染
- **THEN** 该年 SHALL 无柱（NaN 断开），MUST NOT 画 0 高度柱冒充真实值

#### Scenario: 银行业利润率图占位为行业不适用

- **GIVEN** 银行业标的毛利率各年 None（银行报表无营业成本列，#240 断线修复后走占位分支）、chart_data 携带行业「银行」
- **WHEN** 毛利率与净利率子图渲染
- **THEN** 占位文本 SHALL 含「银行业不适用毛利率口径」
- **AND** MUST NOT 出现「利润率数据缺失」类数据缺失误述

#### Scenario: 非银行业整体缺失维持数据缺失占位

- **GIVEN** 非银行业标的毛利率与净利率各年 None
- **WHEN** 利润率子图渲染
- **THEN** 占位文本 SHALL 维持「利润率数据缺失」现状形态
