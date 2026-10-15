# 人工验证报告: fix-execution-signal-source

**日期**: 2026-10-15
**验证人**: ZCode 验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: openspec/changes/fix-execution-signal-source/
**E2E 门禁**: 不适用（纯后端执行链变更，无前端 UI / SSE / 会话切换 / 状态流转；前端仅作为读数界面）
**执行环境**: 主检出 `D:\WorkSpace\finance_analysis_agent` @ c28b111c（#341+#342 合并后经 `scripts/deploy.py` 真实拉取 + `docker compose restart backend`；容器内已确认新代码：`_SIGNAL_SOURCE == "predictions"`）
**复现入口**: `uv run pytest tests/execution/test_driver.py`（11 项）；生产读数经 `GET /api/v1/ops/jobs`（job_runs 摘要）与 `/broker`、`/track-record` 页面（浏览器自动化）

## 验证结果

| # | Scenario（delta spec） | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 1 | 决策到持仓闭环 | 审批通过买入观点 → Signal 落库（含 decision_id）→ 按目标权重买入成交 → 持仓/资金/订单三处断言 | 单测 `test_decision_to_position_closed_loop`（观点造数走 `init_predictions`+`insert_prediction`，与 ingest 同形；手算 20,000 股/10.02/50.10 佣金/余额 799,549.90）。**生产实跑未命中**：库内无 open 买入观点（今日全 flat，见 §未达成项） | ✅（单测） |
| 2 | 幂等重跑 | 同决策不重复生成 Signal、不产生重复订单；`signals_created=0` | 单测 `test_rerun_is_idempotent`；**生产实跑命中**：20:16:59 尝试落 11 信号后在快照读取处中断（宿主机回填 WAL 争用），20:18:12 重跑 → `signals_created=0 · orders_placed=0`（按 decision_id 去重） | ✅ |
| 3 | flat 信号平仓 | 持仓 500 股 + 卖出观点 → 清仓、资金入账 | 单测 `test_flat_signal_liquidates_position`（19.96/9.99 费/1,009,970.01）。**生产实跑未命中**：paper 账户无持仓（首次入金，0 订单） | ✅（单测） |
| 4 | 快照不可用跳过 | 订单侧跳过（no_snapshot_data），信号转换照常落库 | 单测 `test_missing_snapshot_still_converts_decisions_to_signals` + `test_missing_snapshot_without_as_of_uses_today_for_signal_window`；**生产前史命中**：16:10 旧码日批 `no_snapshot_data`（旧行为零信号），本次修复后语义见单测 | ✅ |
| 5 | 未审批决策不执行 | FM reject 观点零信号；`skipped_unapproved` 留痕 | 单测 `test_fm_rejected_prediction_never_generates_signal`（生产库 2 行 reject 观点在窗口外，计入 backlog 桶） | ✅ |
| 6 | 存量窗口限制回填范围 | 窗口外旧观点不回填；`skipped_backlog` 留痕；放宽窗口可执行 | 单测 `test_backlog_window_excludes_stale_decisions`；**生产读数**：54 open = 38 backlog（<10-10）+ 11 信号（10-13/10-14）+ 5 同日顺延（10-15） | ✅ |

## §6 生产实跑读数（2026-10-15 晚，浏览器自动化驱动 /ops 补跑）

**① 仿真执行（paper_execute_daily）摘要**（job_runs，2026-10-15T20:18:12）：

```
{"trade_date": "2026-10-15", "signals_created": 0, "orders_placed": 0, "rejected": 0,
 "pending": 0, "skipped_reason": null, "skipped_symbols": [], "skipped_backlog": 38, "skipped_unapproved": 0}
```

（`signals_created=0` 为幂等重跑读数——11 条信号已由 20:16:59 中断的那次尝试落库。）

**② 信号明细（execution.db，11 条，全部来自真实观点记录）**：

```
600519.SH:2026-10-15 flat p_1641871f110e (2026-10-13)   002916.SZ:2026-10-15 flat p_08650ac9d55e (2026-10-14)
600015.SH:2026-10-15 flat p_731a842c12b4 (2026-10-14)   600026.SH:2026-10-15 flat p_6763ad41193b (2026-10-14)
600029.SH:2026-10-15 flat p_94c747725085 (2026-10-14)   600515.SH:2026-10-15 flat p_7647667b592a (2026-10-14)
600845.SH:2026-10-15 flat p_2b9fd0f0daf3 (2026-10-14)   601058.SH:2026-10-15 flat p_15e23046d844 (2026-10-14)
601066.SH:2026-10-15 flat p_08dbe076f3c5 (2026-10-14)   300033.SZ:2026-10-15 flat p_460d83ec17b5 (2026-10-14)
601818.SH:2026-10-15 flat p_73f42e9468a7 (2026-10-14)
```

**③ 浏览器读数**：

- `/ops` 日批运维：16:10 仿真执行卡片 → `成功 · 2026-10-15T20:18:12 · trade_date=2026-10-15 · signals_created=0 · orders_placed=0 · rejected=0`
- `/broker` 执行台账：总资产 ¥1,000,000.00 / 可用资金 ¥1,000,000.00 / 持仓市值 ¥0.00 / 订单页签「暂无订单」/ 门禁拒绝页签空——与「全 flat 观点 + 零持仓 → 零订单」的设计语义一致
- `/track-record` 决策战绩：当日 9 条真实决策在列（光大银行 看空 / 中信建投 中性 / 宝信软件 中性 / 海南机场 看空 / 南方航空 看空 / 中远海能 中性 …，均 T+20 进行中）

**④ 中断-恢复实证（非计划内）**：20:15:58 与 20:16:59 两次尝试在宿主机 16 进程快照回填期间撞 SQLite WAL 争用（`disk I/O error`；容器经 bind mount 读宿主机 WAL 库的已知限制）。第二次已落 11 信号、在后续读快照处中断；第三次干净运行按幂等收敛——**中断恢复不重复、不丢信号**（决策→信号段先于快照守卫的设计在故障面同样成立）。

## 未达成项 / 披露

1. **买入路径（scenario 1）与平仓路径（scenario 3）本日无生产命中**：库内 54 行 open 观点无一为买入（direction=long 的 13 行均已结算），且 paper 账户零持仓——当日实跑正确产出为零订单。两路径由单测（手算断言）覆盖；生产验证待首个买入决策出现（次日 16:10 日批将执行今晚新落决策，其中仍无买入）。
2. 本次实跑前，宿主机快照回填（60 交易日历史，选股面板前置）与容器读库互斥导致 2 次 `disk I/O error`——已记录为运维限制（见 issue），不影响本 delta 的收敛结论。
3. `skipped_backlog=38` 含 2 行 FM-reject 观点（09-07/09-08，日期桶先于审批桶判定）——桶序为设计口径（design.md D3）。
