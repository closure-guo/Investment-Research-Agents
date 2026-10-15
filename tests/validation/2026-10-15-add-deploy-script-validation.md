# add-deploy-script 实施验证报告

**日期**: 2026-10-15
**范围**: 一键部署脚本（主检出推进 + 容器 reload + 健康探针）
**交付**: `scripts/deploy.py`（新增）+ `tests/test_deploy_script.py`（35 例）

## 一、实施摘要

本仓库部署为「主检出推进 + 容器 reload」，2026-10-14/15 会话内手动执行四次、每次被不同坑拦下（WIP 重叠 / untracked 同名 / 运行时库误跟踪 / reload 无验证）。本 delta 把四类处置法固化为三段式脚本：**前置检查（只读）→ 拉取 → 探针**。

- **前置检查**：① running 会话门禁（红线）；② 已最新 → 跳过拉取；③ WIP 交集（默认中止列文件；`--stash` 先 `merge-tree` 预判无冲突才 stash→pull→pop）；④ 运行时文件按类处置（`data/` 下**数据库文件**：untracked → 挪 `.deploy-bak-<ts>`、已跟踪已修改 → 丢弃；其余同类冲突 → 中止）。
- **拉取**：`git pull --ff-only`（禁 merge 提交）。
- **探针**：后端 `/api/sessions` + 前端 `/` 均 200；不健康 → `docker compose restart backend frontend` 一次 → 复探；仍不健康 → 退出码 2。
- `--dry-run` 零变更；幂等（已最新只探针）；退出码 0/1/2。

**实施期裁决（收窄白名单）**：运行时白名单由「`data/` 前缀」收窄为「`data/` 下名含 `.db`」——`data/` 下仍可能有不可再生非库文件（如 `data/cohort/*.json` 已跟踪），误丢弃代价高于部署失败；此类文件按普通 WIP 处理（③/blocked）。新增断言 `test_data_prefix_non_db_file_not_in_runtime_plan` 钉死。

## 二、验证证据

| 项 | 命令 | 结果 |
|---|---|---|
| 单测 | `uv run pytest tests/test_deploy_script.py -q` | **35 passed**（TDD：先红后绿；tmp git 仓库真实 git 实证 + 网络/docker 全注入 fake） |
| 变异验证 | 临时移除 `core.quotepath=false` | 非 ASCII 路径用例转红（挪开 0、本地被静默覆盖）→ 恢复转绿（防「测试空转」） |
| lint | ruff check / format | 全过 |
| 回归抽查 | `pytest tests/ -q -k deploy` | 50 passed（含既有 deploy_prompts 测试，无收集破坏） |

**真实栈实证（tasks 2.2，主检出零变更）**：

```
[deploy] ① running 门禁: 通过（后端可达，无 running 会话）
[deploy] ② 版本: HEAD=fc85db4 origin/main=fc85db4（已是最新）
[deploy] ③ WIP 交集: 无（可直拉）（本地未提交改动 10 个文件）
[deploy] ④ 运行时文件: 挪开 0 / 丢弃 0 / 阻断 0
[deploy] 提交: fc85db4 → fc85db4（已是最新 origin/main，跳过拉取）
[deploy] 探针第 1 轮: 后端 /api/sessions=200；前端 /=200
[deploy] ✓ 部署完成：两探针均 200   （EXIT=0）
```

执行后复核：主检出 HEAD/10 个 WIP 文件/工作树逐项不变（**零变更路径实证**）；`--dry-run` 亦实测零变更。**拉取路径**（HEAD≠origin/main 的 stash/挪开/丢弃全链）由 tmp 仓库用例覆盖（真实 git），并将在下次 main 前进后的真实部署中首次实战。

## 三、既有/周边改动清单

- 新增两文件；产品代码、既有测试、compose 配置**零修改**（红线）。
- 文档入口未动（proposal 明确不碰 README——主检出 README 属他人 WIP）；AGENTS.md 常用命令是否登记 `scripts/deploy.py --dry-run` 留后续裁决。

## 四、遗留

1. 拉取路径的真实栈实战（下次 main 前进时用本脚本执行一次即覆盖）。
2. 已知边角：incoming 新增路径在本地为**目录**时 ④ 不识别 → 走 pull 失败以退出码 2 + git 原文报告（不静默损坏）。
3. 文档入口（AGENTS.md/README）登记待裁决。

## 五、结论

部署从「易错的手工序列」变为「可 dry-run、可复跑、带探针的一条命令」；四类历史坑各有自动化处置与单测钉死；真实栈零变更路径（门禁/版本/WIP/运行时文件/探针）全链实证。
