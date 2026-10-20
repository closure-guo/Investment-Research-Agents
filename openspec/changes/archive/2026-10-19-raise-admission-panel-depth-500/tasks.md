# Tasks: raise-admission-panel-depth-500

> 上线顺序 = 任务顺序。TDD：先红后绿。**合并前置 = 500 日面板可构造 + 覆盖率核验**。

## T1 常量与口径注记

- [x] `ADMISSION_PANEL_LOOKBACK_DAYS == 500`；面板取数取最近 500 日（520 日种子 → 500）
- [x] 边界：499 日可用 → fail loud（含补历史指引）；500 日可用 → 通过
- [x] 口径注记更新（闸① 20 日窗 11.1% / 60 日窗 18.4%；闸② SE 0.063 → 0.045）；monitor 口径断言不动

## T2 测试边界随动

- [x] `test_monthly.py`：常量断言 250 → 500；面板深度用例种子 260 → 520；浅面板边界 249 → 499
- [x] `test_external_import.py`：浅面板 fail loud（90 日）语义不变（回归）

## T3 实测与重放

- [x] 250 vs 500 面板闸①/闸②对照（5/20/20_vol/60 日窗）
- [x] 近门槛（\|ICIR\| 0.2–0.35）重放：500 日面板完整五道闸，逐条留档；生产因子库保持不变
- [x] 读数落档 → `tests/validation/2026-10-20-raise-admission-panel-depth-500-validation.md`

## 收口

- [x] `uv run pytest tests/factors -q` 全绿；ruff / mypy 零新增
- [x] `openspec validate raise-admission-panel-depth-500 --strict` 通过
- [x] 验证报告 + PR；sync + archive 在验证通过后执行
