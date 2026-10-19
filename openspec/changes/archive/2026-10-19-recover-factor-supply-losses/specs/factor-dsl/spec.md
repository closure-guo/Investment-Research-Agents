# Delta for factor-dsl（recover-factor-supply-losses）

## MODIFIED Requirements

### Requirement: 复杂度硬约束

解析器 SHALL 在解析期拒收以下表达式并返回含违规项的错误：(a) AST 深度超过 8；(b) 自由参数（数值字面量参数）超过 3 个；(c) 引用的原始行情字段超过 3 种。

(Previously: AST 深度超过 6 即拒收（深度上限经 4→5→6 两次提升后的取值）。)

#### Scenario: 深度边界

- **GIVEN** 表达式 A 解析后 AST 深度恰为 8，表达式 B 深度为 9
- **WHEN** 分别调用解析器
- **THEN** A 解析成功；B 抛出 `FactorComplexityError`，消息含「深度 9 > 上限 8」

#### Scenario: 深度 7 供给线解锁形态

- **GIVEN** 表达式 `ts_mean(div(abs(div(sub(close,ref(close,1)),ref(close,1))),amount),20)`（实测 AST 深度 7，自由参数 3、字段种类 2，均在限内）
- **WHEN** 调用解析器
- **THEN** 解析成功（深度 ≤ 8）；自由参数超 3 或字段种类超 3 的表达式仍按对应违规项拒收
