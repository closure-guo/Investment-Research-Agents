# Proposal: add-deploy-script

## Why

本仓库的部署是「主检出推进 + 容器 reload」——**手动流程**。2026-10-14/15 的会话里同一流程手动执行了四次，每次都被不同的坑拦下：

1. **WIP 重叠**：主检出有他人未提交改动，与 incoming diff 有文件交集时 `git pull` 中止（需 stash→pull→pop 或逐文件核对）；
2. **untracked 同名**：incoming 新增的文件在本地已存在为 untracked → pull 中止（需挪开）；
3. **运行时库误跟踪**：`data/*.db` 被应用写入成「已跟踪+已修改」→ pull 中止（需判定可否安全丢弃）；
4. **reload 无验证**：pull 之后新代码是否生效（后端 --reload / 前端 vite）没有探针，靠人工 curl。

`ci-pipeline` delta 明确把 CD 划出范围（「不做镜像构建/推送/部署自动化」）；但上述四类坑已有稳定处置法，固化为一键脚本可把「部署」从易错的手工序列变成可复跑、可 dry-run、带健康探针的一条命令。

## What Changes

- 新增 `scripts/deploy.py`：**前置检查 → 拉取 → 探针**三段式。
  - **前置检查（只读，全过才继续）**：① running 会话门禁（`GET /api/sessions` 任一 `running` → 中止，红线）；② 已最新 → 跳过拉取只跑探针；③ **WIP 冲突分析**（本地已修改 ∩ incoming diff：空 → 直拉；非空 → 默认中止并列出文件，`--stash` 时经 `merge-tree` 预判无冲突才 stash→pull→pop）；④ **运行时文件处置**（`data/` 下 incoming 触碰的 untracked → 挪至 `.deploy-bak-<ts>`；已跟踪+已修改的运行时库 → 丢弃（可再生成，报告留痕）；非运行时文件 → 中止）。
  - **拉取**：`git pull --ff-only`（禁 merge 提交，对齐仓库纪律）。
  - **探针**：后端 `/api/sessions` 200 + 前端 `/` 200；不健康 → `docker compose restart backend frontend` 一次 → 复探；仍不健康 → 非零退出 + 报告。
- `--dry-run`：跑全部前置检查 + 打印计划，零变更。
- 文档：脚本 docstring 记四类坑的处置法与用法（不触碰 README——主检出 README 属他人 WIP 文件）。

非目标（Out of scope）：

- 不做镜像构建/推送（compose 镜像重建仍手动；本脚本只覆盖「主检出推进 + reload + 探针」）。
- 不做 CI 自动部署（部署仍由人触发；CI 内跑需凭据与 runner 决策，另议）。
- 不做回滚（`git pull` 失败即中止在旧态；回滚语义另议）。

## Capabilities

### New Capabilities
- `deploy`: 主检出推进 + 容器 reload + 健康探针的一键部署契约（前置检查/失败中止/幂等可重入）。

## Impact

- **新增**：`scripts/deploy.py`（stdlib + subprocess；纯函数可测）+ `tests/test_deploy_script.py`（tmp git 仓库实证 + 纯函数用例）。
- **不改**：产品代码、既有测试、compose 配置。
