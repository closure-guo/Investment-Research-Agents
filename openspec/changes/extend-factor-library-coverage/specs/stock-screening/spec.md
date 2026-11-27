# Delta for stock-screening（extend-factor-library-coverage）

## ADDED Requirements

### Requirement: 因子当日值缺口披露

L2 打分 SHALL 对每个参与打分的 active 因子统计 as_of 截面上的有效因子值个数（非 NaN，在 L1 通过标的集合上）；有效个数 < `MIN_IC_SAMPLES`（30）的因子 SHALL 记入选股日批运行摘要的 `factor_value_gaps`（逐项含 `factor_id` / `valid_n` / 表达式引用的可选参考字段清单），其当日贡献按既有 NaN → 0 语义为 0。该披露 SHALL NOT 改变打分权重、综合分与排序；无缺口时 `factor_value_gaps` 为空列表。

#### Scenario: 参考字段当日缺口被披露且不影响排序

- **GIVEN** active 因子 F 的表达式为 `log(float_cap)`，as_of 当日侧表无行（F 在 as_of 截面全 NaN）；同批另有 2 条价量因子
- **WHEN** 选股日批 L2 打分执行
- **THEN** 运行摘要 `factor_value_gaps` 含 F（`valid_n == 0`，引用字段 `["float_cap"]`）
- **AND** F 对每只候选的贡献为 0，Top-N 排序与「F 不参与打分」时逐项一致

#### Scenario: 无缺口时披露为空

- **WHEN** 所有参与打分的 active 因子在 as_of 截面的有效值个数均 ≥ 30
- **THEN** `factor_value_gaps == []`，打分结果与披露上线前逐字一致
