# stock-screening Delta

## MODIFIED Requirements

### Requirement: L2 因子打分排序

系统 SHALL 对 L1 过滤通过的标的，基于因子库全部 active 因子做横截面打分并输出 Top-N（默认 100，可配置）候选列表；每个候选 SHALL 携带 `factor_contribs` 贡献分解，且全部贡献之和 SHALL 等于综合分（浮点容差 1e-9）。

可打分因子的权重 SHALL 按「方向 × 幅度」构成：方向 = 该因子 IC 均值的符号；幅度在近 60 个交易日 IC 样本数 ≥ 30（常量 `MIN_SAMPLES_FOR_PROPORTIONAL_WEIGHTS`）时正比于 |IC 均值|，样本数 < 30 时取等幅（所有低样本因子幅度相等）。权重 SHALL 按幅度之和归一；IC 均值为 0（含 |IC 均值| < 1e-12 的数值零——其符号无信息量）的因子权重为 0（不参与幅度归一分母）；样本数缺失（未提供 `ic_counts`）SHALL 视为 0（等幅）。非有限 IC 均值与幅度总和 < 1e-12 的拒绝语义维持不变。

#### Scenario: 贡献分解勾稽

- **GIVEN** 某日 L2 打分产出候选 C，综合分 1.823
- **WHEN** 汇总 C 的 factor_contribs
- **THEN** 各 contribution 之和与 1.823 的差 ≤ 1e-9
- **AND** 每条 contribution 含 factor_id、zscore、weight 三字段

#### Scenario: 低样本因子等幅

- **GIVEN** 两个可打分因子 A、B，IC 均值分别为 +0.10 与 -0.20，两者的近 60 交易日 IC 样本数均为 12（< 30）
- **WHEN** 计算权重
- **THEN** |w_A| == |w_B|（等幅），w_A > 0 且 w_B < 0（方向取 IC 均值符号）

#### Scenario: 高样本因子维持比例

- **GIVEN** 两个可打分因子 A、B，IC 均值分别为 +0.10 与 +0.20，两者的 IC 样本数均 ≥ 30
- **WHEN** 计算权重
- **THEN** w_A == 0.10 / 0.30 且 w_B == 0.20 / 0.30（|IC 均值| 比例与原式一致）

#### Scenario: 混合样本各自适用

- **GIVEN** 因子 A 样本数 40（IC 均值 +0.30）、因子 B 样本数 15（IC 均值 -0.05）
- **WHEN** 计算权重
- **THEN** 幅度为 A=0.30、B=1.0（B 等幅），按 1.30 归一后 w_A = 0.30/1.30、w_B = -1.0/1.30
- **AND** A 与 B 的样本数互换时（A=15、B=40），结果按同一规则随之互换

## ADDED Requirements

### Requirement: 因子榜与流动性榜秩相关披露

`selection_mode=factor` 的选股批次 SHALL 在批次 summary 中携带 `factor_liquidity_rank_corr`（对同一 L1 通过标的集，因子综合分排序与近 20 日日均成交额降序排序的 Spearman 秩相关，取值 [-1, 1]）与 `rank_corr_samples`（参与计算的标的数，等于该批 L1 通过数）；秩相关 SHALL 以秩变换 + Pearson 实现，MUST NOT 引入 scipy 依赖。`selection_mode=fallback` 的批次 MUST NOT 携带该字段。披露 MUST NOT 影响排序本身（只读读数，零回溯）。

#### Scenario: factor 批次披露读数

- **GIVEN** 某选股日批以 `selection_mode=factor` 完成且 L1 通过 3,986 只
- **WHEN** 读取该批次 summary
- **THEN** `factor_liquidity_rank_corr` 为 [-1, 1] 内有限浮点数，`rank_corr_samples` = 3986
- **AND** 批次历史接口返回同一对字段

#### Scenario: fallback 批次不携带

- **GIVEN** 某选股日批因无可打分因子降级为 `selection_mode=fallback`
- **WHEN** 读取该批次 summary
- **THEN** 不含 `factor_liquidity_rank_corr` 与 `rank_corr_samples` 字段

#### Scenario: 两榜完全一致时读数为 1

- **GIVEN** L1 通过集上因子综合分降序与近 20 日日均成交额降序给出完全相同的标的次序
- **WHEN** 计算披露读数
- **THEN** `factor_liquidity_rank_corr` = 1.0（浮点容差 1e-9）
