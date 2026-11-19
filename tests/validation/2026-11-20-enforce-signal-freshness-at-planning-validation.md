# 人工验证报告: enforce-signal-freshness-at-planning（T3 生产实跑）

**日期**: 2026-11-20（取证窗口 2026-11-18 ~ 2026-11-19）
**验证人**: Closure（agent 辅助取证）
**关联 delta**: openspec/changes/enforce-signal-freshness-at-planning/
**关联 issue**: #596
**生产版本**: 6d963d34（PR #597 squash，随 11-18 08:56 镜像重建入栈；11-19 22:07 重启复用同镜像）

## 背景

PR #597 合并时容器内守护链在跑，部署顺延至 11-18 晨镜像重建。本报告记录部署后两个完整执行日
（11-18 16:10/16:12 + 11-19 22:34/22:34 两 lane 四批）的生产验证证据。

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果（生产四批） | 通过 |
|---|---|---|---|---|
| S1 来源状态重验：resolved/非 open 来源信号跳过 + `STALE_SOURCE` 留痕 | 是（test_driver STALE_SOURCE 场景） | 旧来源信号不执行，skip 留痕带 decision_id/signal_id | 11-18 16:10 daily 批：0 订单，skip 全为 `STALE_SOURCE`（10-15~10-20 遗留信号，decision_id+signal_id 逐条带）；11-18 16:12 micro 批 4 条同型；11-19 22:34 两批同型复现（daily 31KB 明细被 summary 存储截断，micro 完整可见） | ✅ |
| S2 newest-wins：同标的旧信号让位最新 + `SUPERSEDED_BY_NEWER` + 胜者 signal_id | 是（test_driver SUPERSEDED 场景） | 旧信号跳过并记录 winner | 11-19 22:34 micro 批：002224.SZ / 001277.SZ / 002852.SZ / 002880.SZ 四标的，`stale_signal_id`(11-12/11-13) → `winner_signal_id`(11-19) 逐条成对；daily 批同机制运行 | ✅ |
| S3 存量自清：600780.SH 两颗 flat 地雷（11-06 resolved short 来源 + 11-12 duplicate 来源）在修复部署后首个执行日批清场 | 部分（issue 预告场景） | 两颗地雷不再触发卖单，11-12 buy 新仓不再被击穿 | signals 表中 600780.SH:2026-11-06 / :2026-11-12 两颗 flat 信号在 11-18、11-19 两批均**零卖单**（orders 表 11-16+ 仅存 11-17 修复前那笔 34,400@5.83 filled）；对照修复前 11-17 22:15 批同一地雷实际成交击穿新仓 | ✅ |
| S4 合规信号不受影响（回归护栏） | 是（test_driver 合规信号回归场景） | 新鲜 open 来源信号正常入信号池 | 11-19 22:34 daily 批 `signals_created=25`（11-18 晚决策全部正常入池；方向全 flat，无仓可出 → 0 订单为预期行为）；micro 批 winner 信号（002224/001277 11-19 期）正常入池 | ✅ |

## 异常记录

- 无本 delta 缺陷。附注：daily lane 的 `skipped_details` 在 11-18/11-19 两批均超 31KB 被 ops summary 存储层截断（`truncated: true`），明细取证以 micro lane 完整 summary + execution.db 行级核对补足；summary 截断为既有存储行为，非本 delta 引入。

## 证据位置

- ops runs：779（11-18 16:10 daily）/ 780（11-18 16:12 micro）/ 822（11-19 22:34 daily）/ 823（11-19 22:34 micro）
- 行级取证：execution.db signals（600780 三信号）+ orders（11-16+ 仅 1 单）只读拷贝
- 回归：2026-11-20 01:2x `uv run pytest tests/execution/` 270 passed；ruff/format 过；mypy 98 = 基线零新增

## 结论

- [x] 全部通过，可 archive
