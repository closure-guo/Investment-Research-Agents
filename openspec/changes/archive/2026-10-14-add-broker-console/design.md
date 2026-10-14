# Design: add-broker-console

## Context

`execution/store.py` 已提供：`read_cash` / `list_positions` / orders 表（status/reason/fill_price/fee/decision_id/signal_id/attempts）与 `paper_equity`（日终净值）；`add-execution-chain-driver` 落库门禁拒绝后，「门禁日志」即「orders 中 status=rejected 的记录」——**零 schema 变更**。

页面形态：PageShellA（指标条 + 主图表 + 底部 Tab 表）——「资金与持仓一眼看全、明细按 Tab 深入」正合该壳。

## Decisions

- **API 四端点只读**：overview 聚合（cash + 持仓市值按最新快照 close_raw 计算；快照缺失时持仓市值/浮盈记 null 并标注，MUST NOT 填 0 冒充）、positions、orders（status 过滤）、gate-log（= rejected 订单视图，含原因码与 decision_id/signal_id）。
- **主图表 = 持仓市值构成**（按标的饼图）：与战绩页净值曲线不重复；空仓时显示空态而非空图。
- **Tab 表**：持仓（标的/数量/成本/现价/浮盈）、订单（时间/标的/方向/数量/状态/成交价/原因码）、门禁拒绝（时间/标的/方向/数量/原因码/关联决策）。订单状态映射 StatusBadge：pending→`running`、filled→`active`、rejected→`failed`、expired→`retired`。
- **空账户如实**：资金 = 初始 100 万、无持仓无订单 → 指标条显示资金、图表空态、表格空态；MUST NOT 显示伪数据。
- **入口同源**：侧边栏折叠图标（`aria-label="执行台账"`）+ 命令面板。

## Alternatives Considered

- **并入战绩页**：放弃——战绩页是「观点→收益」的评估面；执行台账是「订单→持仓→资金」的操作面，语义与刷新频率不同（7 页面愿景亦分列）。
- **展示净值曲线**：放弃——与战绩页重复，且战绩页已含双曲线口径。
- **提供手工下单按钮**：放弃——执行只经日批驱动器（可审计、可重放）；手工入口会绕过信号/门禁契约。

## Risks

- 当前无真实成交（执行链 2026-10-14 才接线、快照受封禁）：页面多为空态——预期行为，空态文案如实说明。
- 持仓市值依赖快照 close_raw：封禁期快照缺失 → 市值/浮盈为 null 并显式标注（与「不填 0」纪律一致）。
