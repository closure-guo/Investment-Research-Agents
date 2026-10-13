# Proposal: add-paper-broker

## Why

- 风控门禁之后需要一个真实的"执行末端"来闭环：没有仿真撮合，仓位调整只是数字游戏，执行损耗（滑点/无法成交）永远不可见。
- 现有战绩仪表盘记录的是"虚拟决策"的盈亏，与仿真成交的盈亏之差正是执行损耗的度量——这个对照是判断系统能否走向实盘的唯一硬证据。
- 券商实盘接口（QMT/PTrade）合规与稳定性成本高，必须用 Broker 抽象隔离，先跑 paper。

## What Changes

- 新增 `execution/brokers/base.py`：Broker 抽象接口（`place_order/query_positions/query_cash/cancel`），及 `execution/brokers/paper.py` 实现：订单经 `matching-engine` 撮合，持仓/资金落 SQLite。
- paper 账户与虚拟决策的战绩在仪表盘并排展示：同一批决策的「虚拟结算净值 vs 仿真成交净值」双曲线，差值标注为执行损耗。
- paper broker 每日结算复用 `outcome/` 日批（结算价取快照收盘价）。
- README 增补免责声明：本系统仅供研究，不构成投资建议。

非目标（Out of scope）：

- 不实现 QMT/PTrade/任何实盘接口（仅 base.py 留抽象）。
- 不做人工确认半自动模式（paper 跑稳至少两个月后再评估）。
- 不改动现有虚拟决策结算逻辑。

## Capabilities

### New Capabilities
- `broker-execution`: Broker 抽象与仿真撮合执行。

### Modified Capabilities
- `track-record`: 新增虚拟 vs 仿真双曲线对照视图。

## Impact

- **后端**：`src/finance_agent/execution/brokers/base.py`、`brokers/paper.py`（新增）。
- **前端**：战绩仪表盘新增仿真曲线系列与执行损耗标注。
- **测试**：paper 下单-撮合-持仓-资金全链路单测；双曲线数据接口单测。
- **验证**：回放 2026-09 一个月决策，人工核对 10 笔仿真成交的成交价/费用，报告落 `tests/validation/`。
