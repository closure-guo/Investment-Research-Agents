# Proposal: add-matching-engine

## Why

- 后续回测、仿真撮合（paper）、实盘三个场景若各自实现成交逻辑，回测结论无法迁移到执行层——"回测能成交、实盘涨跌停买不进"这类偏差会从根上污染所有评估结论。
- 现有 `evals/backtest/` 与 `outcome/` 日批结算各自隐含简化的成交假设（收盘价成交、无滑点、无涨跌停约束），两处假设不一致且无显式契约。
- 执行损耗（滑点/无法成交）目前不可度量，"虚拟决策 vs 实际成交"的对照无从谈起。

## What Changes

- 新增单一撮合引擎 `execution/matching.py`（纯函数）：输入订单 + 当日快照记录（含涨跌停状态），输出成交结果（成交价/成交量/未成交原因）。
- 内置 A 股交易约束：T+1（当日买入不可卖出）、涨跌停不可反向成交（一字涨停买单不成交、一字跌停卖单不成交）、100 股整数倍、佣金与印花税成本模型。
- 分档滑点模型：按标的流动性档位（以快照成交额分位划分）取不同滑点参数，参数写入配置而非硬编码。
- 三种运行模式共享同一撮合函数：`backtest`（历史快照驱动）/ `paper`（当日快照驱动）/ `live`（券商接口回报驱动，本 change 仅留接口，不实现）。

非目标（Out of scope）：

- 不实现任何券商实盘接口（QMT/PTrade 等仅留抽象）。
- 不引入第三方回测框架（backtrader/qlib 等）。
- 不改动 `evals/backtest/` 现有回放逻辑的对外接口（内部成交计算切换到本引擎）。

## Capabilities

### New Capabilities
- `matching-engine`: 回测/仿真/实盘三位一体的单一撮合引擎，A 股交易约束与成本模型的唯一实现处。

### Modified Capabilities
- `decision-backtest`: 回放成交计算切换至撮合引擎，防前视纪律不变。

## Impact

- **后端**：`src/finance_agent/execution/matching.py`（新增纯函数模块）、`evals/backtest/` 成交计算替换。
- **测试**：T+1/涨跌停/整数倍/滑点分档的逐约束单测；同一订单在 backtest 与 paper 模式下结果一致性单测。
- **验证**：用 10 个已知含涨跌停的交易日案例人工核对撮合结果，报告落 `tests/validation/`。
