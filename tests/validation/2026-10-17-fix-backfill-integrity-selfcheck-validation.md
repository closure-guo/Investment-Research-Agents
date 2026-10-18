# 人工验证报告: fix-backfill-integrity-selfcheck（incident 043 待落防护）

**日期**: 2026-10-17
**验证人**: ZCode 验证 runner（单测 + 生产库实跑读数）
**关联**: incident 043「快照库并行回填致索引损坏」防护清单「待落」项——**回填后置校验 + 检测路径**
**E2E 门禁**: 不适用（运维脚本自检，无前端 UI / SSE / 会话切换 / 状态流转）
**执行环境**: worktree `.worktrees/fix-backfill-integrity` @ 基于 main 916ae78d；生产库 `data/market_snapshot.db`（incident 043 重建后的 1,736,952 行）；镜像 `finance-agent-backend:latest`

## 背景与缺口

incident 043（2026-10-16 索引损坏、17 重复键、表重建恢复）在「防护」章列明两条待落项：

1. 回填收尾自动 `PRAGMA quick_check` + 重复键扫描（脚本侧新增），异常即 fail loud；
2. 检测纳入快照日批健康检查**或运维巡检**（当时「无任何自动检测——本次是人工查询偶然发现」）。

## 实现

- `scripts/backfill_market_snapshot.py` 新增 `verify_snapshot_integrity()`：`PRAGMA quick_check`（页级/索引损坏，抛错亦捕获为 ok=False）+ `(trade_date, code)` 重复键扫描；库不存在 → skipped（dry-run/首跑无物可查）；
- **回填收尾自动执行**（非 dry-run）：通过打印一行读数；未通过打印 `✗ 完整性自检未通过…` 并**退出码 1**（fail loud，摘要含 `integrity`）；
- **`--verify-only` 运维巡检入口**：随时单跑同款自检（零写入，与 `--dry-run` 同档豁免宿主机护栏），退出码 0 通过 / 1 未通过；`--start/--end` 相应可选化（非巡检模式仍必填）。

## 验证结果

| # | 场景 | 证据 | 通过 |
|---|---|---|---|
| 1 | **生产库实跑（首次自动化自检）** | 容器内对 `data/market_snapshot.db`（1,736,952 行）：**`quick_check=ok · duplicate_keys=0`**——incident 043 重建后的库经自动校验健康 | ✅ |
| 2 | 健康库 ok 路径 | `test_verify_integrity_ok_on_healthy_db` | ✅ |
| 3 | 重复键检出 | 无约束同名表造 1 组重复 → `duplicate_keys=1`、ok=False | ✅ |
| 4 | **页损坏检出（确定性）** | 覆写第 2 页为零 → quick_check 抛 `database disk image is malformed` → 捕获为 ok=False（先经独立实验实证该造损路径必触发） | ✅ |
| 5 | 库缺失 → skipped | `test_verify_integrity_missing_db_is_skipped` | ✅ |
| 6 | `--verify-only` 退出码 | 健康库 0 / 重复键库 1（输出含读数） | ✅ |
| 7 | 回填收尾 fail loud | `test_main_backfill_runs_selfcheck_and_fails_loud`：自检未过 → 退 1 + 输出含 `完整性`/`malformed` | ✅ |
| 8 | 回归 | 脚本单测 21 项全绿；ruff/mypy 零告警 | ✅ |

**成本实测**（生产库 1.74M 行、bind mount 上）：`quick_check ≈ 29s` + `重复键扫描 ≈ 3s`（净检查 ~32s；`--verify-only` 端到端 36s）。分片并发回填时每片结尾各查一次（8 片 ≈ 8×32s 的尾部开销，可接受；这是 incident 043 缺的正是这道检查）。

## 未达成项 / 披露

1. **未纳入快照日批**（incident 给出「日批健康检查**或**运维巡检」二选一）：本 PR 落「回填收尾自动 + `--verify-only` 巡检」路径；日批为高频路径，挂 30s 级全库快检需先评估时段预算，留给 owner 决策（如需，加在 `daily_market_snapshot` 收尾或周度巡检）。
2. 自检覆盖「页/索引损坏 + 重复键」两类 incident 043 实收形态；不覆盖语义级错值（如价格异常），超出自检边界。
3. 生产实跑为**只读**自检（`--verify-only`）；回填路径中的自动自检由单测钉死，首次真实回填（合并后）将自然命中并留读数。
