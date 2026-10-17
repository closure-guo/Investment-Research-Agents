# Delta for factor-dsl（raise-factor-dsl-depth-cap）

## MODIFIED Requirements

### Requirement: 复杂度硬约束

解析器 SHALL 在解析期拒收以下表达式并返回含违规项的错误：(a) AST 深度超过 5；(b) 自由参数（数值字面量参数）超过 3 个；(c) 引用的原始行情字段超过 3 种。

(Previously: AST 深度超过 4 即拒收。)

#### Scenario: 深度边界

- **GIVEN** 表达式 A 解析后 AST 深度恰为 5，表达式 B 深度为 6
- **WHEN** 分别调用解析器
- **THEN** A 解析成功；B 抛出 `FactorComplexityError`，消息含「深度 6 > 上限 5」
