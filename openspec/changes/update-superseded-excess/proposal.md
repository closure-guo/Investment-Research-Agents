# Proposal: update-superseded-excess

## Why

`superseded`（提前结算）的观点 `excess_return` 恒为 `NULL`——`job.py` 显式写死并注明「superseded 为提前结算:无完整同期基准区间,超额不可得」。这个理由不成立：**基准 DataFrame 在同一函数作用域内**（`settle_open_predictions` 开头就拉取 000300），入场日与出场日也都是已知的，超额完全可算。

后果是 §1.9 主指标（T+20 相对沪深300 超额）至今**零读数**：

| 事实 | 数值 |
|---|---|
| `predictions` 中 `excess_return` 非空 | 337 条里 **15 条** |
| 已结算 win/loss | 5 条，**全部 `superseded`，超额全 null** |
| 已闭仓方向性观点 | 56 条，全部 `superseded` |

即：唯一会产生的结算类型恰恰不写超额，指标因此永远为空。

同时，`superseded` 的分类用的是 `raw_return` 而不是超额，与 `track-record` spec「区间**超额收益** > +2% → resolved_win」相悖。

## What Changes

- 抽出 `judgment.settle_at_exit`：收益 / 超额 / ±2% 中性带分类的**唯一实现**，`expiry` 与 `superseded` 共用；
- `superseded` 结算补齐 `excess_return` 并落库（此前连字段都不写）；
- 分类口径回到 spec：按**超额**而非 raw；
- 基准不可得时超额退化为 `raw_return`（与 `resolve_prediction` 既有降级口径一致，不写 NULL）。

## Capabilities

- **Modified Capabilities**: `track-record`（superseded 结算的收益/超额/分类口径）

## Impact

- 代码：`judgment.py`（新增 `settle_at_exit`，`resolve_prediction` 改为委托）、`job.py`（superseded 分支改用该函数 + 落 `excess_return`）。
- 数据：**不回填历史**（存量 `superseded` 行超额仍为 null，按「存量不重算」惯例），新结算起生效；`scores_reported` 由 2 变 3（补 `decision_excess`）。
- 读数：§1.9 主指标从「零读数」变为「随日批逐步积累」；由于分类改按超额，个别观点可能从 win 变 neutral/loss（今后生效，历史不变）。
- 风险：`excess` 依赖基准区间；基准缺行的日期用「取 date 或之前最后收盘」回退（既有 `_bench_close_on_or_before` 语义）。
