# Proposal: update-track-record-symbol-aggregation

## Why

组合净值的仓位单位是「观点」，不是「标的」。同一只股票可以同时挂多条 open 观点（不同决策日的观点各自存活到 horizon 或被 superseded，互不覆盖），每条各拿 1/N：

- 生产实测 600029 南方航空 2026-10-22~10-26 有 **2 条** open short 观点并存 → 各占 50%，**单票权重 100%**；
- 2026-11-04 有 **3 条** 并存；
- 结果是「组合」在某些交易日就是一只股票，波动/回撤读数失去组合含义，且与 `duplicate_of_day` 的口径打架（同日重复被丢弃、跨日重复被加权重复计价）。

2026-11-04 归因：该现象使单票风险被重复计价，10-26 单日 -1.70% 全部来自同一只票的两条记录。

## What Changes

- 组合日收益聚合改为**标的级**：同一盯市日、同一标的多条 long/short 观点，先取当日收益算术平均（该标的一个仓位），再按**去重标的数 M** 等权 1/M。
- 首盯市日 0 贡献、neutral 排除、交易日历骨架、基准推进口径**全部不变**。
- `metrics.md` §1.12② 已先行修订（口径先于代码）。

## Capabilities

- **Modified Capabilities**: `track-record-metrics`（组合净值曲线的分母口径）

## Impact

- 代码：`metrics.py::daily_portfolio_returns`（新增可选 `symbol_by_prediction`）、`model.py::prediction_symbols`、`build_equity_curve_points` / `compute_metrics_snapshot` / rf 重算三处透传。
- 数据：`equity_curve` / `agent_metrics_daily` 需全量重算；`daily_marks` 不变。
- 读数：净值、年化、波动、夏普、回撤、风险分全部会变；指数对比读数随之变化。
- 兼容：`symbol_by_prediction` 缺省时退化为按观点聚合（既有单测与外部调用不受影响）。
