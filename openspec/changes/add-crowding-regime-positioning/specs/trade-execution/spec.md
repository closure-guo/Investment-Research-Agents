# Delta: trade-execution（add-crowding-regime-positioning）

## MODIFIED Requirements

### Requirement: 组合仓位聚合

系统 SHALL 将同一交易日的 active Signal 聚合为目标持仓，支持等权与 strength 加权两档（可配置）；单票目标权重 SHALL 不超过单票上限（默认 20%），组合总仓位 SHALL 不超过总上限（默认 80%），超限部分 SHALL 按比例缩减并记录调整原因。聚合产出 SHALL 再应用市场状态总仓位系数（由 crowding-regime 判定提供，取值 [0, 1]）：每只目标权重 SHALL 乘以系数，系数 = 0 时全部目标权重为 0（flat 目标）；系数 ≠ 1.0 的应用 SHALL 逐票留痕 `REGIME_SCALE`，并 SHALL 在执行摘要记录状态与系数值。市场状态数据缺失或陈旧（最新记录距执行时刻超过 7 个自然日）时系数 SHALL 按 1.0 处理并在执行摘要留痕 `no_regime_data`，MUST NOT 静默降级。

(Previously: 系统 SHALL 将同一交易日的 active Signal 聚合为目标持仓，支持等权与 strength 加权两档（可配置）；单票目标权重 SHALL 不超过单票上限（默认 20%），组合总仓位 SHALL 不超过总上限（默认 80%），超限部分 SHALL 按比例缩减并记录调整原因。)

#### Scenario: 总仓位超限缩减

- **GIVEN** 6 条 long 信号按强度加权后合计目标仓位 96%
- **WHEN** 组合聚合执行
- **THEN** 输出合计 80%，各票等比例缩减，且调整记录含「TOTAL_CAP_SCALE_DOWN」

#### Scenario: 系数缩放

- **GIVEN** 聚合后目标合计 80%，当日 regime 判定为 elevated、系数 0.5
- **WHEN** 组合聚合执行
- **THEN** 各目标权重乘 0.5、合计 40%
- **AND** 逐票调整记录含 `REGIME_SCALE`；执行摘要含状态 elevated 与系数 0.5

#### Scenario: 空仓系数产出 flat 目标

- **GIVEN** 当日 regime 判定为 danger、系数 0
- **WHEN** 组合聚合执行
- **THEN** 全部目标权重为 0（flat 目标）
- **AND** 逐票调整记录含 `REGIME_SCALE`；执行摘要含状态 danger 与系数 0

#### Scenario: 状态数据缺失披露式降级

- **GIVEN** 市场状态记录缺失或距执行时刻超过 7 个自然日
- **WHEN** 组合聚合执行
- **THEN** 系数按 1.0 处理（聚合结果与无系数时一致）
- **AND** 执行摘要留痕 `no_regime_data`（MUST NOT 静默）

#### Scenario: 系数为 1.0 时不产生调整记录

- **GIVEN** 当日 regime 判定为 normal、系数 1.0
- **WHEN** 组合聚合执行
- **THEN** 目标权重与无系数时逐项一致
- **AND** 不产生 `REGIME_SCALE` 调整记录
