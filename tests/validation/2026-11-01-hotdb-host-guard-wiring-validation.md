# 验证报告: 热库宿主直跑护栏共享化 + 全脚本接线（harden-sqlite-availability task 1）

**日期**: 2026-11-01
**执行人**: agent（ZCode 会话）
**关联**: issue #515（delta task 1）、PR #549/#550（迁移）、issue #344（护栏前身）

## 变更定性

后端运维纪律改造（无交互面）：共享护栏模块 `src/finance_agent/data/hotdb_guard.py` +
8 个 scripts 接线。无 UI/SSE/会话切换变更，E2E 不适用（CI stub 套件照常回归）。

## 语义升级（#344 → #515）

| | #344 时代（bind mount） | #515 迁卷后（本变更） |
|---|---|---|
| 拒绝条件 | 非容器 + 库在主检出 data/ + **容器读方在跑** | 非容器 + 库在主检出 data/（**容器状态不再参与**） |
| 风险面 | 跨挂载 WAL 争用 → 容器读库全不可用 | 宿主 data/ 只是回滚副本：直读=读旧数据、直写=写进永远不可见的过期副本 |
| --allow-host-write | 确认无容器读的时段 | 确知在操作过期回滚副本 |

删除了 `docker compose ps` 探活（best-effort 且已无判定意义）。

## 接线清单（8 脚本）

backfill_market_snapshot（本地护栏实现删除改共享 import）、backfill_delisted（补 repo_root/db 解析）、
backfill_predictions_from_reports、backfill_rejected_factors、backfill_risk_free_rates、
backfill_index_closes、import_external_factors（因子库+快照库双库）、track_record_rebuild、
cohort_eval_readout（双库只读——读回滚副本=读数全错，一并拦）。

src/ 侧 ~24 处 `data/*.db` 引用为 env 可覆写的容器内默认路径，形态正确不动。

## 验证结果

| 项 | 证据 | 结果 |
|---|---|---|
| 护栏单测 | tests/scripts/test_hotdb_guard.py：7 例（宿主+主检出 data/ 拒绝 / 容器内放行 / env 兜底 / 库在别处放行 / dry-run+绕过旗标 / 提示命令含脚本与参数 / cwd 兜底） | 全过 |
| 旧语义测试翻新 | test_backfill_market_snapshot 护栏 4 例 + test_backfill_delisted 2 例改为迁卷语义（含「无容器读方」分支翻转为拒绝） | 全过 |
| 宿主拒绝冒烟（实跑） | predictions / risk_free / index_closes / track_record_rebuild 四脚本宿主直跑 → 全部 exit 2 + 提示容器内命令 | 通过 |
| 既有脚本回归 | tests/scripts/ 165 passed（1 failed = test_index_is_fresh，main 基线复现的既有时钟炸弹族，与本变更无关） | 通过 |
| lint/format | ruff check + ruff format --check 全量 | 全过 |
| mypy | 98 = 既有基线（#544），无新增 | 基线持平 |

## 边界说明

- 测试经 conftest 隔离到 tmp_path，不受护栏影响（护栏只拦主检出 data/ 面的库路径）。
- 迁移/回滚 shell 脚本为文件级拷贝，不经 sqlite 层，不在护栏范围（其自身的宿主纪律见脚本头注）。

## 结论

- [x] 全部通过
