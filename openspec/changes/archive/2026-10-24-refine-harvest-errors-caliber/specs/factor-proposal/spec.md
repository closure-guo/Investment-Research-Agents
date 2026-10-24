# Delta for factor-proposal

## ADDED Requirements

### Requirement: 收割摘要读数口径

收割通道运行摘要 SHALL 提供 `errors` 桶的构成披露 `errors_breakdown`（`placeholders` = 本批不可翻译与转译失败的占位数；`real` = `errors` 减 `placeholders`，下截 0）；`errors` 总数与「转译失败 / 不可翻译 / 预检失败逐条计入 errors 桶留痕」的既有语义 SHALL 逐字不变。占位条目在导入预检中必失败（占位表达式非合法 DSL），故正常运行 `real` 恒为 0；`real > 0` 即「已转译候选意外未过预检」，是翻译器或编排缺陷的信号。

#### Scenario: 纯拒译批的读数分列

- **GIVEN** 一批 2 条候选全部不可翻译、无已转译候选
- **WHEN** 收割批运行
- **THEN** 摘要 `errors=2`（留痕语义不变）且 `errors_breakdown={"placeholders": 2, "real": 0}`

#### Scenario: 已转译候选意外预检失败单独可见

- **GIVEN** 导入摘要报告 `errors=3`，其中 2 条为本批已知占位
- **WHEN** 收割批构造摘要
- **THEN** `errors_breakdown={"placeholders": 2, "real": 1}`——真实预检失败不必与已知拒译心算区分
