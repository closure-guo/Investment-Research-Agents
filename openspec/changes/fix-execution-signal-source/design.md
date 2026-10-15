# Design: fix-execution-signal-source

## Context

执行链五段编排（add-execution-chain-driver）的①段取源为 `decision_log`，其设计前提「唯一生产写入方 `api._persist_decision_log` 只在审批通过时落行」在实现演进中失效：真实决策改由 `track_record.ingest` 写入 `predictions`（会话完成后落观点记录），`decision_log` 再无写入方（库内 1 行，2026-09-03）。快照守卫又先于①段，导致快照故障期整条执行链（含纯转换的信号段）零产出。

## Goals / Non-Goals

- Goals：①段接回真实决策真源；信号转换与行情解耦（快照故障期可观测）；防历史存量一次性回填；幂等锚点不变。
- Non-Goals：不改②-⑤段编排与撮合口径；不给 `decision_log` 补写方；不引入做空信号（A 股信号路径既有边界）；不接滞回判定（`generate_signal` 的 hysteresis 属后续接线，本 delta 维持 `signal_from_decision` 纯转换）。

## Decisions

### D1 取源 = `predictions`（`status='open'`），`decision_log` 弃用

`predictions` 是决策的生产落点（含 `session_id` / `langfuse_trace_id` 追溯、审批结论与 action 冻结在 `rationale_snapshot`）。规格语义「审批通过的 TradeDecision」在实现层即「open 观点 + `fund_manager_decision == approve`」。

备选：给 `decision_log` 接线（补写方）——否决：双写双源，`predictions` 已是 track-record 单一真源，且 `decision_log` 无审批结论字段。

### D2 action 从 `rationale_snapshot` 解出（而非 `direction` 字段）

`predictions.direction` ∈ {long, short, neutral} 是**结算口径**（`direction_for_action`：buy→long、sell→short、其余→neutral），与信号枚举 {long, flat} 不同；`rationale_snapshot.action`（buy/hold/watch/sell）是信号契约的原生映射输入（`_ACTION_DIRECTION`），且 sell 的 flat 语义（exit）由契约既有分支承接。

缺 action / 快照不可解析/非对象 → 单条跳过（`skipped_symbols` 留痕），不阻断整批（与既有脏数据处理同款）。

### D3 存量窗口默认 5 个自然日

首次接线时 `predictions` 有 54 行 open（最老 2026-09-06）——全量回填会让 25 个交易日前的观点在今日开盘成交，违背「决策日次一交易日开盘」口径。窗口 = `[as_of − window_days, as_of)`（自然日，默认 5）；窗口外留痕 `skipped_backlog` 不回填。`window_days` 参数化供 ops 显式放宽（补跑场景）。

备选：不设窗口、以「首跑标记」区分存量——否决：需新增状态位且不可观测（窗口+留痕是零状态、可解释的口径）。

### D4 信号生成先于快照守卫

信号转换（决策→Signal）不依赖行情；快照只服务②-⑤（目标权重、门禁状态位、撮合价）。故①段先执行：快照不可用时信号照常落库、摘要 `no_snapshot_data`（不撮合、零订单、持仓零变动），订单待快照恢复后的执行日撮合。`as_of` 与最新快照均不可得时，①段窗口基准取当日（`signal_id` 仅作幂等命名，撮合仍待快照）。

备选：维持守卫在前——否决：快照管道故障期信号全不可见（2026-10-13~15 东财封禁期即实例：整条链零产出且无任何信号留痕）。

### D5 幂等锚点不变

信号唯一键沿用 `decision_id`（值现为 `prediction_id`），订单 `order_id` 由 `signal_id` 派生——重试 / 手动补跑 / 重复执行三态下不重复生成、不重复动账（既有机制，零改动）。

## Risks / Trade-offs

- 窗口内未执行的旧观点随窗口滑动永久错过（10-10 的观点自 10-16 起出窗）：口径选择（拒绝陈旧回填），`skipped_backlog` 可观测；如需补跑显式放宽 `window_days`。
- `flat` 信号对应标的无持仓 → 不产生订单（既有语义），该信号常驻待执行集直至出窗——无副作用（每轮零订单），`/broker` 页可见（信号已落库、订单为空）。
- 观点扫描 `list_predictions(limit=1000)` 倒序取最新：open 超过 1000 行时更早的行本轮不处理（含 backlog 计数低估）——信号生成幂等，后续轮次不丢不重（既有「已知边界」模式，模块 docstring 已更新）。
