# Tasks: add-rejected-factor-revival

- [x] T1 `factors/constraints.py` 复苏常量（`REVIVAL_COOLDOWN_DAYS=30` / `REVIVAL_DAILY_CAP=5`）+ 单测
- [x] T2 `factors/revival.py`：`run_factor_revival(*, dry_run, db_path, snapshot_db_path, panel)`——筛选（点数≥60 / |滚动ICIR|≥MIN_ABS_ICIR / 冷却外）+ cap 排序 + dry_run 预览 + 提交经 `run_factor_external_import(ledger_source="revival")` + 六字段摘要；单测覆盖 spec 六场景（命中过闸/冷却跳过/再拒重置/零候选零面板/cap 截序/面板不可得上抛）
- [x] T3 ops 注册：`JOB_IDS`（+`factor_revival_daily`）/ `JOBS` / `JOB_LABELS`（"拒绝池复苏"）/ `SCHEDULES`（mon-fri 17:10）/ `scheduler.py` 包装与 add_job；钉数测试 29→30
- [x] T4 定向测试全绿（tests/factors/test_revival.py + tests/outcome/test_ops_jobs.py / test_scheduler.py）+ 全量 `pytest tests/factors tests/outcome` + ruff + mypy
- [x] T5 真实数据 dry-run：`uv run python -c` 直调 `run_factor_revival(dry_run=True)`，输出 checked/eligible/cooldown_skipped 读数（不提交、零面板或如实面板成本）；读数与 `data/factor_library.db` 拒绝池现状自洽（2026-10-29 实测：checked=191 / eligible=0 / cooldown_skipped=50 / submitted=0——50 个结构已过静默线，随冷却期（池 10-28 建，11-27 解锁）陆续进入重提）
- [x] T6 PR 合并 + 主检出同步 + 部署验证（2026-10-29 11:22）：`/api/v1/ops/jobs` 已出现 `factor_revival_daily`（label 拒绝池复苏、mon-fri 17:10、next_fire 当日 17:10）；容器内 dry-run 读数 checked=191 / eligible=0 / cooldown_skipped=50 与实现侧一致。注：本机 bind mount 下 watchfiles 未触发 reload（与 AGENTS.md 原始红线一致），部署经 `docker restart`（0 运行会话/0 运行作业、非红线窗口）。首跑当日 17:10（eligible=0 → 零面板零提交）
- [ ] 交互类判定：纯后端，免 E2E 门禁；archive 前置：tasks 全勾 + 验证通过
