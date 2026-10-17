# 人工验证报告: fix-backfill-in-container（issue #344）

**日期**: 2026-10-17
**验证人**: ZCode 验证 runner（单测 + 宿主机/容器双侧实跑）
**关联 issue**: #344（宿主机快照回填与容器读库互斥：bind mount 上 SQLite WAL 争用）
**同族**: incident 043（并行回填索引损坏）——本修复落实其「回填运维纪律」条
**E2E 门禁**: 不适用（运维脚本 + compose 挂载，无前端 UI / SSE / 会话切换 / 状态流转）
**执行环境**: worktree `.worktrees/fix-backfill-in-container` @ 基于 main 4e95ebfc；真实栈（backend 运行中）；镜像 `finance-agent-backend:latest`

## 缺口与修复

**缺口**（issue #344 实证，2026-10-15）：`data/` 经 bind mount 同时暴露给宿主机与容器；宿主机进程写 WAL 期间容器进程打开同一库（`PRAGMA journal_mode=WAL` 需短暂独占/共享内存语义）即 `disk I/O error`——回填期间（约 1 小时）容器内一切读快照路径不可用（paper_execute/选股面板/factor_decay_monitor 500）。同库同侧多进程无此问题（容器内快照日批写 5569 行与读并发正常）。

**修复**（issue 建议方向 1 + 3 合并，落地为「通道 + 护栏 + 纪律」）：

| 件 | 内容 |
|---|---|
| 容器内执行通道 | `docker-compose.yml` backend 增 `./scripts:/app/scripts:ro` 只读挂载 → `docker compose exec -T backend python scripts/backfill_market_snapshot.py …` 读写同侧，无跨边界 WAL |
| 宿主机护栏 | 脚本新增 `_host_write_guard()`：非容器内 + 目标库落在主检出 `data/` + backend 容器运行中 → **拒绝**（退 2）并给出可直接复制的容器内命令；`--dry-run`（零写入）与 `--allow-host-write`（显式绕过，限无容器读时段）放行 |
| 运维纪律 | 脚本文档串固化：分片并发 ≤8；运行期间不硬杀进程（incident 043 教训）；收尾查 `/api/snapshot/status` |

## 验证结果

| # | 场景 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| 1 | 宿主机 + 容器读方在跑 + 库在 `data/` | 拒绝退 2 + 容器内命令 | 实跑：输出含 `docker compose exec -T backend python scripts/backfill_market_snapshot.py --start … --db data/market_snapshot.db`，exit=2，库文件零创建 | ✅ |
| 2 | 容器内（`/.dockerenv` 检测） | 放行 | 实跑 `docker run`（scripts 只读挂载）：护栏直过、进入正常回填路径 | ✅ |
| 3 | `--allow-host-write` / `--dry-run` / 库在 data/ 外 / 无容器读方 | 放行 | 单测判定矩阵 6 分支全钉（`test_host_guard_decisions_matrix` + main 级 3 例） | ✅ |
| 4 | 容器内有完整依赖 | `--help` 与模块导入可用 | 实跑：脚本在容器内 argparse/import finance_agent+pandas 正常，新版 flag 可见 | ✅ |
| 5 | compose 配置生效 | mount 且只读 | `docker compose config`：`source …/scripts → /app/scripts (read_only: true)` | ✅ |
| 6 | 回归 | 既有 10 例不破 | `tests/scripts/test_backfill_market_snapshot.py` 15 项全绿；`tests/data/` 全绿（合计 262） | ✅ |

**静态检查**：ruff、mypy 零告警。

## 部署注意（PR 合并后）

新增 volume **需容器重建**才生效（restart 不挂载）：合并后在主检出执行 `docker compose up -d backend`（deploy.py 对 `docker-compose` 类 incoming 会打印 `up -d --build` 提示，见 fix-deploy-load-new-code）。生效后 `docker compose exec backend ls /app/scripts` 可见脚本。

## 未达成项 / 披露

1. **生产实跑未命中**：本日无回填任务在跑；护栏与通道由单测 + 双侧实跑钉死，首次真实回填应走容器内命令（届时补记读数）。
2. 护栏的容器读方判定为 best-effort（`docker compose ps --services --status running`）；docker 不可用/解析失败按「无读方」放行（宁可漏拦不误拦）——栈已停时宿主机直跑本就安全。
3. 本修复不改变回填产物语义（脚本逻辑零变更，仅入口护栏 + 挂载通道）；staging 库方案（issue 方向 2）未采纳：成本高于收益且引入合并窗口。
