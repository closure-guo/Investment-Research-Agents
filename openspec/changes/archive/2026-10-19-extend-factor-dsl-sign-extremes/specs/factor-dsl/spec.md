# Delta for factor-dsl（extend-factor-dsl-sign-extremes）

## MODIFIED Requirements

### Requirement: 因子表达式可解析契约

系统 SHALL 提供因子 DSL 解析器，将字符串表达式解析为 AST；支持的算子集合 SHALL 显式枚举（初版：`ts_mean/ts_std/ts_corr/ts_rank/delta/rank/zscore/add/sub/mul/div/max/min/abs/log`；扩展：`ref`（沿日期轴后移 N 期，前 N 行为 NaN）、`ts_sum`（N 期滚动求和）、`sqrt`（逐元素平方根，负值 → NaN）、`sign`（逐元素符号：正 → 1、负 → −1、零 → 0，NaN 传播）、`ts_max`/`ts_min`（N 期滚动最大/最小，窗口含当日，窗口不足或窗口含 NaN → NaN），窗口参数须为正整数数字字面量），行情字段 SHALL 限定价量六字段：`open/high/low/close/volume/amount`（均前复权口径）；任何不在枚举内的算子或字段 SHALL 在解析期报错。

(Previously: 扩展集合为 `ref`/`ts_sum`/`sqrt` 三项——无符号与滚动极值算子。)

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

#### Scenario: 滚动求和与平方根解析与求值

- **GIVEN** 表达式 `sqrt(ts_sum(volume, 5))`
- **WHEN** 调用解析器并求值
- **THEN** 解析产出根节点 `sqrt`、子树 `ts_sum(volume, 5)`
- **AND** 求值时 `ts_sum` 为 5 期滚动求和（前 4 行 NaN、逐标的独立），`sqrt` 对负值产出 NaN

#### Scenario: 符号与滚动极值解析与求值

- **GIVEN** 面板含标的 A 的收盘价序列 `[10, 11, 11, 9]`
- **WHEN** 解析并求值 `sign(sub(close, ref(close, 1)))` 与 `ts_max(close, 3)`
- **THEN** `sign` 逐元素产出 `[NaN, 1, 0, -1]`（差值为 NaN/正/零/负；NaN 传播）
- **AND** `ts_max(close, 3)` 产出 `[NaN, NaN, 11, 11]`（窗口含当日、前 2 行窗口不足为 NaN、逐标的独立）；`ts_min` 同口径取最小
- **AND** `ts_max(close, 0)` / `ts_max(close, 2.5)` / `ts_max(close, x)` 抛出 `DSLParseError`（窗口须正整数数字字面量）
