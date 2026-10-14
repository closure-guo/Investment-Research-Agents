# Proposal: add-broker-console

## Why

paper broker（`add-paper-broker`）的台账只有数据库：资金、持仓、订单（含 pending/filled/rejected/expired 与原因码）无任何界面；风控门禁的拒绝记录（`add-execution-chain-driver` 落库后）同样无回查面。设计系统 proposal 的 7 页面愿景明列「broker」与「门禁日志」——执行侧是「自动交易」可信度的证据面，看不到台账就无法判断链路是否按预期工作。

## What Changes

- **后端只读接口**：`GET /api/broker/overview`（资金 / 持仓市值 / 总资产 / 浮动盈亏）、`GET /api/broker/positions`、`GET /api/broker/orders`（含状态、原因码、decision_id / signal_id）、`GET /api/broker/gate-log`（被风控门禁拒绝的订单：原因码 + 标的 + 时间）。
- **前端 `/broker` 页**（PageShellA：指标条 + 持仓构成图 + 底部 Tab 表）：
  - 指标条：总资产 / 可用资金 / 持仓市值 / 浮动盈亏；
  - 主图表：持仓市值构成（按标的）；
  - Tab 表：持仓 / 订单（全部、挂单、已成交、已拒绝）/ 门禁拒绝；
  - 订单状态用 StatusBadge；空账户如实空态（不伪第二条曲线、不伪数据）。
- 入口：侧边栏折叠图标 + 命令面板。

非目标（Out of scope）：

- 不做手工下单/撤单（执行仍只经日批驱动器）。
- 不做组合级战绩指标（净值双曲线已在战绩页；本页只做执行台账）。
- 不做实盘接入。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `broker-execution`: 新增「台账只读查询」要求。
- `frontend`: 新增「执行台账页」要求。

## Impact

- **后端**：`api.py` 四个只读端点（包装既有 `read_cash` / `list_positions` / orders 查询 / 门禁拒绝过滤，零业务复制）。
- **前端**：`frontend/src/pages/broker/`（新页）+ 路由 + 入口。
- **测试**：API 单测（空账户/字段齐备/拒绝过滤）；前端 Vitest（指标条/构成图/Tab 切换/徽章/空态）；Playwright E2E（stub 种子：资金 + 两持仓 + 四态订单 + 一条门禁拒绝）。
