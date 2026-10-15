# Proposal: fix-execution-signal-source

## Why

执行链日批驱动器（`execution/driver.py`）的决策取源绑死在 `decision_log` 表——该表实际为**死表**（生产库仅 1 行、`record_decision` 零调用方），真实决策全部落在 `predictions`（观点记录，`track_record.ingest` 生产写入，2026-10-15 实测 172 行）。后果：

1. 驱动器①段（决策 → 信号）在生产上恒零产出（`paper_execute_daily` 摘要 `signals_created=0`），执行链（Signal→组合→门禁→paper 下单）整段休眠——链路「决策落库」与「执行」之间断开；
2. 快照守卫先于信号生成：快照不可用（如东财行情域封禁期，2026-10-13~15 实测快照库空）时连信号也不落库，链路可观测性为零（信号转换本不依赖行情）。

对照事实（2026-10-15 生产库实测）：`decision_log` 1 行；`predictions` 172 行（open 54：审批通过 52 / reject 2；direction long 13 / neutral 134 / short 25）；`rationale_snapshot` 172/172 可解析且承载 `action` 与 `fund_manager_decision`。

## What Changes

- 驱动器①段取源改为 `predictions`：`status='open'` 且决策日早于执行日的观点 → 信号转换（`signal_from_decision`，`source="predictions"`；action 走既有信号契约映射：buy→long、hold/watch→flat、sell→flat）。
- 审批过滤：仅 `rationale_snapshot.fund_manager_decision == "approve"` 的观点可转换（未审批/被拒观点零信号，摘要 `skipped_unapproved` 留痕）。
- 存量窗口：默认近 5 个自然日（`window_days` 参数），窗口外旧观点不回填执行（摘要 `skipped_backlog` 留痕）——防历史存量一次性砸入下单。
- 信号生成移到快照守卫之前：快照不可用时信号照常落库（订单侧仍跳过，`no_snapshot_data`）。
- 摘要统一键集：+`skipped_backlog` / `skipped_unapproved`；快照不可用摘要不再收窄（含真实 `signals_created` 与 `skipped_symbols`）。

## Capabilities

- **Modified Capabilities**: `trade-execution`（MODIFIED「信号到订单的日批执行」）

## Impact

- 代码：`src/finance_agent/execution/driver.py`。
- 测试：`tests/execution/test_driver.py` 造数口径重写（decision_log → predictions）并新增 4 项（未审批过滤 / 存量窗口 / 无快照信号照常 / 脏数据不阻断）。
- 运维：`paper_execute_daily` 摘要键集变化（+2 键；消费方为 ops 历史展示与日批读数）。
