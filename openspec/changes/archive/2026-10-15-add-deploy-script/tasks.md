# Tasks: add-deploy-script

## 1. 脚本实现（先写失败测试）
- [x] 1.1 `scripts/deploy.py`：三段式（前置检查/拉取/探针）+ `--dry-run`/`--stash`/`--repo` 参数；纯函数拆分（`classify_working_tree_changes` / `plan_runtime_file_actions` / `check_running_sessions` / `probe`）便于单测
      —— 单测（tmp git 仓库实证，对齐 tests/test_prompt_history.py 先例）：WIP 交集分类（空/重叠/`--stash` 预判两态）/ untracked 同名处置计划 / 运行时库（`data/` 前缀）丢弃 vs 非运行时中止 / running 门禁（可达有 running / 可达无 running / 不可达三态）/ dry-run 零变更 / 幂等（已最新只探针）
- [x] 1.2 探针与重启：`urllib` 探活 + `docker compose restart backend frontend`（失败重试一次语义）+ 退出码约定（0/1/2）
- [x] 1.3 docstring 记录四类坑的处置法与用法（含 `--dry-run` 推荐先跑）

## 2. 验证
- [x] 2.1 单测全绿（`uv run pytest tests/test_deploy_script.py -q`）+ ruff/format
- [x] 2.2 **真实栈实证**：对主检出执行一次真实部署（`--dry-run` 先看计划 → 真实执行）——当时主检出应已最新 → 走「跳过拉取只探针」路径；再构造一次「有 WIP 但无交集」的直拉路径（或如实记录仅覆盖到的路径）
- [x] 2.3 既有测试零修改（红线）
- [x] 2.4 验证报告落 `tests/validation/2026-10-15-add-deploy-script-validation.md`
