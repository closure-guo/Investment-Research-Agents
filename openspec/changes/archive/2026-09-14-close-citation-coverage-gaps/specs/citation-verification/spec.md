## MODIFIED Requirements

### Requirement: comparative 基期值双端申报与校验

系统 SHALL 要求 comparative（同比/环比/对比）claim 声明双端数值：当期 `stated_value` + 基期 `stated_value_b`（field_ref_b 指向基期字段）。校验器 SHALL 对基期值与当期值使用相同的容差语义分别校验；未申报基期的 comparative claim SHALL 判 FAIL 并记录。prompt 侧 SHALL 强制比较值申报纪律（当前值 + 基期值 + 对应 field_ref/field_ref_b），改动后须经 prompt 发布流程。

在此基础上，比较型 claim SHALL 支持两类申报：

1. **方向型**（`stated_value` 为 `greater_than` / `less_than` / `equal_to`）：按 `field_ref` 与 `field_ref_b` 双端真值校验比较方向；`field_ref_b` 设而 `stated_value_b` 缺 SHALL 判 FAIL（比较基期不得裸奔）——既有语义不变。
2. **差值型**（`stated_value` 为数值）：SHALL 用双端真值重算差值（`a - b`）与申报值按容差比对，参考系取两操作数绝对值较大者（`max(|a|,|b|)`）；`direction` 已申报时 SHALL 同时校验符号方向（`negative` = 正文以正向数值表述负向差值），未申报时 SHALL 跳过方向检查并在结果上标记覆盖缺口（显式降级，不静默 PASS）。差值型 claim 的 `stated_value_b` SHALL 为可选（申报则按既有容差校验，缺省不判 FAIL——申报对象是差值本身）；但申报値经归一后≈任一端真值时（LLM 以某一端的值填 stated_value 的回声形态）SHALL 判 UNVERIFIABLE（未知语义不武断判错）。

差值型的两端路径（`field_ref` / `field_ref_b`）任一缺失/不可解析、或任一非数值时，SHALL 按既有语义判 FAIL（path_unresolvable），不得以 UNVERIFIABLE 吞掉。

#### Scenario: 基期值裸奔被拦截
- **GIVEN** 报告叙述「2025 净利率 19.07%，较 2024 年 21.93% 下滑」，claim 仅申报 2025 值（stated_value=19.07）
- **WHEN** 校验 comparative claim
- **THEN** 判 FAIL，原因：comparative 未申报基期 stated_value_b
- **AND** 引导 LLM 补申报 2024 基期（21.93）与 field_ref_b

#### Scenario: 差值型申报通过

- **GIVEN** 正文「MA5 较 MA20 低约 3.62」，claim 声明 `comparative`、`field_ref` 与 `field_ref_b` 双端、`stated_value=3.62`、`direction=negative`
- **WHEN** 双端重算差值 = -3.62（在容差内）
- **THEN** SHALL 判 PASS（符号方向一致）

#### Scenario: 差值型申报超容差

- **WHEN** 申报差值 5.0、重算差值 -3.62（超容差）
- **THEN** SHALL 判 value_mismatch FAIL

#### Scenario: 差值型方向未申报的显式降级

- **WHEN** 差值型 claim 未申报 `direction`
- **THEN** SHALL 仅按差值量级比对，并在结果上标记覆盖缺口；SHALL NOT 因方向未知而静默 PASS

## ADDED Requirements

### Requirement: 派生键重算注册覆盖门禁

`compute_metrics` 产出的全部派生键 SHALL ⊆ 计算型重算注册表（`_COMPUTATIONAL_RECALC`）∪ 显式豁免表（每条豁免 SHALL 附理由）；未注册且未豁免的派生键被按计算型引用时判 UNVERIFIABLE 并计入覆盖缺口。

系统 SHALL 提供门禁测试：以覆盖全部可选分支（K 线/基准/季报/同业/行情）的构造 state 运行 `compute_metrics`，断言其全部产出键已注册或已豁免——新派生键未注册即测试红，不得依赖人工记忆补齐。

#### Scenario: 新派生键未注册即红

- **WHEN** 有人在 `compute_metrics` 新增派生键而未同步注册表/豁免表
- **THEN** 覆盖门禁测试 SHALL 失败并列出缺失键

#### Scenario: 注册键可重算

- **GIVEN** 派生键已注册（如 `growth_rates`）
- **WHEN** 计算型 claim 引用该键的子路径且真值存在
- **THEN** SHALL 用同一份 compute 代码重算并按既有相对容差裁决（不再 UNVERIFIABLE）
