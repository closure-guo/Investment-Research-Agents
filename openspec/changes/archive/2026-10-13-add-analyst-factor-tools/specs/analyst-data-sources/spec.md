# analyst-data-sources delta: add-analyst-factor-tools

## ADDED Requirements

### Requirement: 因子库只读工具

技术分析师 SHALL 可通过 `query_factor_library` 工具按标的查询 active 因子的当前取值、截面 z-score 与近 60 日 IC；工具输出 SHALL 为结构化 JSON；该工具 SHALL NOT 提供任何因子库写入或表达式求值能力。

#### Scenario: 工具查询

- **GIVEN** 因子库含 active 因子 F（20 日动量），分析标的 300750
- **WHEN** 技术分析师调用 `query_factor_library("300750")`
- **THEN** 返回 JSON 含 F 的取值、z-score、近 60 日 IC
- **AND** 工具模块内不存在任何写库函数（静态扫描可验证）
