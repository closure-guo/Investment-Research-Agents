# Delta for factor-dsl（extend-factor-dsl-ref）

## MODIFIED Requirements

### Requirement: 因子表达式可解析契约

系统 SHALL 提供因子 DSL 解析器，将字符串表达式解析为 AST；支持的算子集合 SHALL 显式枚举（初版：`ts_mean/ts_std/ts_corr/ts_rank/delta/rank/zscore/add/sub/mul/div/max/min/abs/log`；扩展 `ref`：`ref(x, N)` = `x` 沿日期轴后移 N 期（前 N 行为 NaN），窗口参数须为正整数数字字面量），行情字段 SHALL 限定价量六字段：`open/high/low/close/volume/amount`（均前复权口径）；任何不在枚举内的算子或字段 SHALL 在解析期报错。

(Previously: 算子集合枚举为初版 15 算子，无后移引用算子——外部表达式动量/反转/收益率族因缺 `ref` 不可翻译。)

#### Scenario: 合法表达式解析

- **GIVEN** 表达式 `rank(ts_corr(close, volume, 10))`
- **WHEN** 调用解析器
- **THEN** 返回 AST，根节点为 `rank`，子树为 `ts_corr(close, volume, 10)`
- **AND** 序列化后再解析得到的 AST 与原 AST 相等

#### Scenario: 未知算子拒收

- **GIVEN** 表达式 `future_peek(close, 10)`（算子不存在）
- **WHEN** 调用解析器
- **THEN** 抛出 `DSLParseError`，消息含未知算子名 `future_peek` 及其字符位置

#### Scenario: 后移引用解析与窗口形态

- **GIVEN** 表达式 `ref(close, 5)`
- **WHEN** 调用解析器
- **THEN** 返回 AST，根节点为 `ref`、第 2 参数为窗口 5
- **AND** `ref(close, 0)` / `ref(close, -5)` / `ref(close, x)` 均抛出 `DSLParseError`（窗口须正整数数字字面量）

#### Scenario: 后移引用求值语义

- **GIVEN** 面板含标的 A 的收盘价序列 `[10, 11, 12, 13]`
- **WHEN** 求值 `ref(close, 2)`
- **THEN** 每行取同标的前 2 期收盘价（`[NaN, NaN, 10, 11]`）
- **AND** 不跨标的影响（各标的独立后移）
