# Delta for factor-dsl（extend-factor-library-coverage）

## MODIFIED Requirements

### Requirement: 因子表达式可解析契约

系统 SHALL 提供因子 DSL 解析器，将字符串表达式解析为 AST；支持的算子集合 SHALL 显式枚举（初版：`ts_mean/ts_std/ts_corr/ts_rank/delta/rank/zscore/add/sub/mul/div/max/min/abs/log`；扩展：`ref`（沿日期轴后移 N 期，前 N 行为 NaN）、`ts_sum`（N 期滚动求和）、`sqrt`（逐元素平方根，负值 → NaN）、`sign`（逐元素符号：正 → 1、负 → −1、零 → 0，NaN 传播）、`ts_max`/`ts_min`（N 期滚动最大/最小，窗口含当日，窗口不足或窗口含 NaN → NaN），窗口参数须为正整数数字字面量）。再扩展（本 change）以下算子：

- **时序统计**（窗口含当日、窗口不足或窗口含 NaN → NaN，逐标的独立，窗口参数须为正整数数字字面量）：`ts_median`（滚动中位数）、`ts_skew`（滚动样本偏度，与 `Series.rolling(N, min_periods=N).skew()` 逐值一致）、`ts_kurt`（滚动超额峰度，与 `Series.rolling(N, min_periods=N).kurt()` 逐值一致，窗口内全相等 → NaN）、`ts_quantile(x, N, q)`（滚动分位数，线性插值口径同 pandas 默认；`q` 须为 [0,1] 内数字字面量，否则解析期报错）、`ts_decay_linear`（线性加权移动平均：权重自窗口首日 1 线性递增至当日 N）、`ts_arg_max`/`ts_arg_min`（窗口内最大/最小值距当日的期数，0 = 当日，并列取最近一期）；
- **计数**：`ts_count(x, N)`（窗口内非 NaN 值个数；窗口不足或窗口内全 NaN → NaN）；
- **回归**：`ts_resid(y, x, N)`（逐标的滚动普通最小二乘 `y = a + b·x` 在窗口末点的残差；任一序列窗口含 NaN 或 `x` 窗口内方差为 0 → NaN）；
- **横截面**：`cs_mean(x)`（当日全市场等权均值，广播到每只标的；当日有效样本 < `MIN_IC_SAMPLES` → 当日 NaN）、`ind_neutralize(x)`（行业中性化：当日同行业（面板 `industry` 分组）内去均值；行业标签缺失或组内有效样本 < 5 → NaN）；
- **逐元素**：`exp`（自然指数；溢出 → NaN）；
- **比较与条件**：`gt/lt/ge/le/eq/ne(a, b)`（逐元素比较：成立 → 1、不成立 → 0，任一侧 NaN → NaN）、`if_else(cond, a, b)`（`cond` 非 0 取 `a`、为 0 取 `b`、`cond` 为 NaN → NaN）。

行情字段 SHALL 限定为价量六字段：`open/high/low/close/volume/amount`（均前复权口径）；另设**可选参考字段** `float_cap`（流通市值，元）、`turnover`（换手率，小数）、`listed_days`（上市天数，自然日）——均为不复权口径，由参考数据面按需携带（见 `factor-reference-data`）：面板携带时按值求值，面板未携带时引用它们的表达式求值 SHALL 报错（消息含缺失字段名），MUST NOT 静默补 NaN。任何不在枚举内的算子或字段 SHALL 在解析期报错。

(Previously: 算子集为初版 15 个加扩展 `ref`/`ts_sum`/`sqrt`/`sign`/`ts_max`/`ts_min` 六个，共 21 个——无比较 / 条件 / 高阶时序统计 / 回归 / 截面聚合 / 行业中性化算子；行情字段仅价量六字段。)

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

#### Scenario: 可选参考字段解析与求值两态

- **GIVEN** 表达式 `log(float_cap)`（`float_cap` 为可选参考字段）
- **WHEN** 调用解析器
- **THEN** 解析成功（可选参考字段在字段枚举内，计入字段种类数）
- **AND** 在携带 `float_cap` 帧的面板上求值得到流通市值的自然对数
- **AND** 在未携带该帧的面板上求值抛出 `ValueError`（消息含「面板缺少字段 'float_cap'」），MUST NOT 静默补 NaN

#### Scenario: 比较与条件算子求值

- **GIVEN** 面板含标的 A 的 `close` 序列 `[10, 11, NaN, 9]`、`volume` 序列 `[100, 200, 300, 400]`
- **WHEN** 求值 `gt(close, 10)` 与 `if_else(gt(close, 10), volume, 0)`
- **THEN** `gt(close, 10)` 产出 `[0, 1, NaN, 0]`（成立 → 1、不成立 → 0、任一侧 NaN → NaN；`lt/ge/le/eq/ne` 同口径）
- **AND** `if_else(gt(close, 10), volume, 0)` 产出 `[0, 200, NaN, 0]`（条件非 0 取第二参数、为 0 取第三参数、条件为 NaN → NaN）

#### Scenario: 事件计数与零分母保护

- **GIVEN** 面板含标的 A 的 `close` 序列 `[10, 11, 11, 9.9]`
- **WHEN** 求值 `ts_sum(ge(sub(div(close,ref(close,1)),1),0.095),3)`
- **THEN** 产出 `[NaN, NaN, NaN, 1]`（日收益为 `[NaN, 0.10, 0.00, -0.10]`，日收益 ≥ 9.5% 记 1；第 4 行窗口 `[1, 0, 0]` 求和为 1，含 NaN 的窗口仍为 NaN）
- **AND** `if_else(eq(sub(high,low),0),0,div(sub(close,low),sub(high,low)))` 在 `high = low` 的行产出 0 而非 NaN（零分母保护由条件算子显式表达）

#### Scenario: 滚动分位数解析与求值

- **GIVEN** 面板含标的 A 的 `close` 序列 `[10, 11, 9, 12]`
- **WHEN** 求值 `ts_quantile(close, 3, 0.5)`、`ts_quantile(close, 3, 0.25)` 与 `ts_median(close, 3)`
- **THEN** `ts_quantile(close, 3, 0.5)` 与 `ts_median(close, 3)` 均产出 `[NaN, NaN, 10, 11]`（窗口含当日、前 2 行窗口不足为 NaN）
- **AND** `ts_quantile(close, 3, 0.25)` 第 3 行为 9.5（线性插值口径同 pandas 默认）
- **AND** `ts_quantile(close, 3, 1.5)` / `ts_quantile(close, 3, x)` 抛出 `DSLParseError`（q 须为 [0,1] 内数字字面量）

#### Scenario: 滚动计数容忍窗口内缺失

- **GIVEN** 面板含标的 A 的 `close` 序列 `[10, NaN, 9, 12, NaN]`
- **WHEN** 求值 `ts_count(close, 3)`
- **THEN** 产出 `[NaN, NaN, 2, 2, 2]`（窗口内非 NaN 值个数；`ts_count` 是唯一容忍窗口内 NaN 的时序算子）
- **AND** 窗口内全 NaN 的行产出 NaN，前 N−1 行窗口不足同为 NaN

#### Scenario: 滚动偏度峰度与线性衰减求值

- **GIVEN** 面板含标的 A 的 `close` 序列 `[10, 11, 12, 16]`
- **WHEN** 求值 `ts_skew(close, 4)` 与 `ts_kurt(close, 4)`；另在序列 `[10, 11, 12]` 上求值 `ts_decay_linear(close, 3)`
- **THEN** `ts_skew(close, 4)` 前 3 行为 NaN、第 4 行为正（右偏），取值与 `Series.rolling(4, min_periods=4).skew()` 逐值一致
- **AND** `ts_kurt(close, 4)` 取值与 `Series.rolling(4, min_periods=4).kurt()` 逐值一致（超额峰度口径；窗口内全相等 → NaN）
- **AND** `ts_decay_linear(close, 3)` 产出 `[NaN, NaN, 68/6]`（权重 1、2、3，最近一期最重）

#### Scenario: 距极值点期数

- **GIVEN** 面板含标的 A 的 `close` 序列 `[10, 11, 9, 9]` 与标的 B 的 `close` 序列 `[11, 11, 9, 9]`
- **WHEN** 求值 `ts_arg_max(close, 3)` 与 `ts_arg_min(close, 3)`
- **THEN** 标的 A 的 `ts_arg_max(close, 3)` 产出 `[NaN, NaN, 1, 2]`、`ts_arg_min(close, 3)` 产出 `[NaN, NaN, 0, 0]`（窗口内极值距当日的期数，0 = 当日；逐标的独立）
- **AND** 标的 B 的 `ts_arg_max(close, 3)` 第 3 行为 0（并列取最近一期）

#### Scenario: 滚动回归残差

- **GIVEN** 面板含标的 A 的 `close` 序列 `[3, 5, 8, 9]`（作 `y`）与 `volume` 序列 `[1, 2, 3, 4]`（作 `x`）
- **WHEN** 求值 `ts_resid(close, volume, 3)`
- **THEN** 产出 `[NaN, NaN, 1/6, -1/3]`（第 3 行窗口 `(1,3) (2,5) (3,8)` 的最小二乘拟合为 `y = 1/3 + 2.5·x`，残差 `8 − 47/6`；第 4 行同法；前 2 行窗口不足为 NaN）
- **AND** `y` 恰为 `a + b·x` 时残差为 0；`x` 窗口内恒定或任一序列窗口含 NaN 时产出 NaN

#### Scenario: 全市场均值广播

- **GIVEN** 面板某日恰有 30 只标的的 `close` 增量值非 NaN 且均值为 2、其余标的缺失；另一日仅 29 只标的有效
- **WHEN** 求值 `cs_mean(delta(close, 1))`
- **THEN** 第一日全部标的（含值缺失者）的 `cs_mean` 均为 2（当日全市场等权均值广播到每只标的）
- **AND** 第二日全部为 NaN（当日有效样本 < `MIN_IC_SAMPLES`）

#### Scenario: 行业中性化

- **GIVEN** 面板携带 `industry` 分组；某日行业 G1 有 5 只标的的 `close` 值为 `1, 2, 3, 4, 5`，行业 G2 仅 1 只标的，另有 1 只标的无行业标签
- **WHEN** 求值 `ind_neutralize(close)`
- **THEN** G1 各标的产出 `[-2, -1, 0, 1, 2]`（当日同行业内去均值）；G2 该标的与无标签标的产出 NaN（组内有效样本 < 5 或标签缺失）
- **AND** 面板未携带 `industry` 分组时求值抛出 `ValueError`（MUST NOT 退化为全市场去均值）

#### Scenario: 指数函数与溢出

- **GIVEN** 面板含标的 A 的 `close` 序列 `[10, -1]`
- **WHEN** 求值 `exp(log(close))`
- **THEN** 产出 `[10, NaN]`（`log` 非正 → NaN；`exp` 结果溢出时同样归 NaN，不产生 inf）

### Requirement: 复杂度硬约束

解析器 SHALL 在解析期拒收以下表达式并返回含违规项的错误：(a) AST 深度超过 8；(b) 自由参数超过 3 个——自由参数 SHALL 按**去重后的数值字面量取值**计数（相等取值只计一次，如 `1` 与 `1.0`；窗口参数计入）；(c) 引用的字段（价量字段与可选参考字段合计）超过 3 种。

(Previously: 自由参数按 AST 中 Number 节点总数计数——同一取值重复出现被重复计数；字段种类仅计价量字段。)

#### Scenario: 深度边界

- **GIVEN** 表达式 A 解析后 AST 深度恰为 8，表达式 B 深度为 9
- **WHEN** 分别调用解析器
- **THEN** A 解析成功；B 抛出 `FactorComplexityError`，消息含「深度 9 > 上限 8」

#### Scenario: 深度 7 供给线解锁形态

- **GIVEN** 表达式 `ts_mean(div(abs(div(sub(close,ref(close,1)),ref(close,1))),amount),20)`（实测 AST 深度 7，自由参数 3、字段种类 2，均在限内）
- **WHEN** 调用解析器
- **THEN** 解析成功（深度 ≤ 8）；自由参数超 3 或字段种类超 3 的表达式仍按对应违规项拒收

#### Scenario: 同取值重复出现不重复计数

- **GIVEN** 表达式 `div(ts_mean(sub(div(close,ref(close,1)),1),20),ts_std(sub(div(close,ref(close,1)),1),20))`（Number 节点 6 个，取值仅 `1` 与 `20` 两种）
- **WHEN** 调用解析器
- **THEN** 解析成功（去重后自由参数 2 ≤ 3；AST 深度 5 ≤ 8）
- **AND** `sub(mul(close,1),mul(close,1.0))` 的自由参数计为 1

#### Scenario: 去重后仍超上限照常拒收

- **GIVEN** 表达式 `sub(div(ts_mean(close,5),add(ts_std(close,10),ts_max(close,20))),0.5)`（取值 `5/10/20/0.5` 四种互不相同，深度与字段均在限内）
- **WHEN** 调用解析器
- **THEN** 抛出 `FactorComplexityError`，消息含「自由参数 4 > 上限 3」
