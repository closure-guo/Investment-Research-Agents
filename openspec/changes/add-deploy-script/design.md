# Design: add-deploy-script

## Context

四次手动部署的实测处置法（全部来自 2026-10-14/15 会话）：

| 坑 | 现象 | 实测处置 |
|---|---|---|
| WIP 重叠 | `pull` 报 "Your local changes would be overwritten" | `merge-tree` dry-run 预判 → 无冲突才 `stash push` → `pull` → `stash pop`（冲突则先中止） |
| untracked 同名 | `pull` 报 "untracked working tree files would be overwritten" | 挪开为 `<name>.deploy-bak-<ts>`（保留证据，pull 后可人工处置） |
| 运行时库误跟踪 | 应用写入 `data/*.db`（已跟踪+已修改）→ `pull` 中止 | `git checkout -- <path>` 丢弃（运行时库可再生成）；非 `data/` 前缀 → 中止人工裁决 |
| reload 无验证 | 新代码是否生效靠人工 curl | 探针：后端 `/api/sessions` 200 + 前端 `/` 200；失败重启一次再探 |

## Decisions

- **Python（非 bash）**：纯函数可测（分类/计划）+ 与仓库既有 `scripts/*.py` 一致；测试用 tmp git 仓库实证（对齐 `tests/test_prompt_history.py` 先例）。
- **默认拒绝、显式放行**：WIP 重叠默认中止（列出文件）；`--stash` 才自动 stash/pop（且先 `merge-tree` 预判，有冲突仍中止）；运行时库丢弃仅限 `data/` 前缀（白名单式），其余一律中止。
- **running 门禁是硬前置**：后端可达且任一会话 `running` → 中止（红线纪律）；后端不可达 → 视为栈已停（无运行中会话），继续但报告。
- **探针不引入新依赖**：`urllib.request` 探活（stdlib）；容器重启用 `docker compose restart backend frontend`（幂等）。
- **幂等/可重入**：已最新 → 只跑探针并报告（重复执行零副作用）；`--dry-run` 全检查零变更。
- **退出码**：0 成功；1 前置检查拒绝（含原因与人工指令）；2 拉取/探针失败。

## Alternatives Considered

- **bash 脚本**：放弃——分类/预判逻辑难以单测；Windows/Git-Bash 兼容面大（本机为 MSYS 环境）。
- **并入 `docker compose up -d --build`**：放弃——后端真源是主检出 `src/` 挂载（镜像重建对后端无效），重建只在依赖变更时需要；脚本以「推进 + reload + 探针」为最小正确集，镜像重建保持手动。
- **CI 自动部署**：放弃（非目标）——需要部署凭据与 runner 决策。

## Risks

- 主检出被并发会话使用：脚本会改动其工作树（stash/pop、运行时文件处置）——**前置检查全部只读**，任何变更动作都有报告留痕；`--dry-run` 供先看后做。
- 探针只覆盖两个端点：新端点生效与否不逐一验证（避免把「新增端点清单」耦合进脚本）；需要时人工加探。
