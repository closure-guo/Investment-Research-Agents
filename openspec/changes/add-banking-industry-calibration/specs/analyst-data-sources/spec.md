# add-banking-industry-calibration delta — analyst-data-sources

## MODIFIED Requirements

### Requirement: 最新报告期快照获取

系统 SHALL 在深度分析数据准备阶段获取「最新报告期快照」（latest_period_snapshot）：从三大报表数据源取最新已披露报告期（不限于年报，含中报/季报/三季报）的关键科目，写入 state 键 `latest_period_snapshot`。快照 SHALL 至少包含：报告日、营业总收入（累计）、归母净利润（累计）、毛利率、资产负债率、存货、合同负债，以及营收/归母净利相对上年同期的同比变化率（同期数据可得时）。快照各科目口径为该报告期累计值，SHALL 标注「累计口径」。快照抓取或字段缺失 SHALL 降级为携带缺失标注的部分快照且不阻断管线。

利润表含「信用减值损失」列且最新期与上年同期均可得时，快照 SHALL 增列「信用减值损失」（亿元）与「信用减值损失同比(%)」（列存在即纳入，不做行业门控——银行是主诉求，制造业同样受益）；列或同期缺失时该两项 SHALL 缺席（MUST NOT 渲染 None/暂缺占位进快照行）。报告口径披露节 SHALL 在快照行内追加呈现（如「信用减值损失 X 亿（同比 Y%）」），字段缺席时快照行维持现状形态（零回归）。

#### Scenario: 年报之后已有中报时快照取中报

- GIVEN 拓荆科技最新年报为 2025-12-31、最新中报为 2026-06-30（后者披露更晚）
- WHEN 深度分析数据准备阶段执行快照获取
- THEN `latest_period_snapshot.报告日` SHALL 为 2026-06-30
- AND 毛利率 SHALL 按中报累计口径计算（营业总收入 29.13 亿、营业成本 17.18 亿 → 约 41.0%）
- AND 资产负债率 SHALL 为中报期末值（约 47.9%，而非年报的 64.11%）
- AND 存货/合同负债 SHALL 为中报期末值

#### Scenario: 同期数据缺失时同比留空

- GIVEN 最新报告期为上市后首份报告，无上年同期数据
- WHEN 快照获取执行
- THEN 营收/归母净利同比 SHALL 为 None 并标注缺失
- AND 其余科目 SHALL 照常装配，管线不中断

#### Scenario: 快照数据源失败降级

- GIVEN 快照依赖的报表接口调用失败
- WHEN 数据准备阶段完成
- THEN `latest_period_snapshot` SHALL 为空 dict 并留 ERROR 日志
- AND 管线 SHALL 继续执行（与公告/研报降级同语义）

#### Scenario: 银行快照携带信用减值损失及同比

- GIVEN 银行标的利润表含「信用减值损失」列、最新期 208.79 亿、上年同期 159.02 亿
- WHEN 快照获取执行
- THEN 快照 SHALL 含 信用减值损失=208.79（亿元）与 信用减值损失同比(%)≈31.30
- AND 报告口径披露节快照行 SHALL 追加呈现，使「主动提储 vs 资产质量恶化」可溯源

#### Scenario: 信用减值损失列缺失时快照行零变化

- GIVEN 利润表无「信用减值损失」列或同期缺失
- WHEN 快照获取执行
- THEN 快照 MUST NOT 含该两项的 None 占位
- AND 报告口径披露节快照行 SHALL 维持现状形态
