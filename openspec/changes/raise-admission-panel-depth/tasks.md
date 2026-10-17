# Tasks: raise-admission-panel-depth

> 上线顺序 = 任务顺序。TDD：先红后绿。**合并前置 = 历史回填完成 + 覆盖率核验**（D3）。

## T1 常量与口径注记（factors/constraints.py + monthly.py）

- [ ] 先红后绿：`ADMISSION_PANEL_LOOKBACK_DAYS == 250`；面板取数取最近 250 日（260 日种子 → 250）
- [ ] 边界：249 日可用 → fail loud（含补历史指引）；250 日可用 → 通过
- [ ] 口径注记更新（热身占比 8%+基线 6.1%；SE 0.086 → 0.063）；monitor 口径断言不动（回归钉死）

## T2 测试边界随动

- [ ] `test_monthly.py`：常量断言 150 → 250；面板深度用例种子 160 → 260；浅面板边界 149 → 249
- [ ] `test_external_import.py`：浅面板 fail loud（90 日）语义不变（回归）

## T3 复跑与证据（250 日面板）

- [ ] 批 A（10 条）+ 批 B（22 条）重跑：闸口读数与 150 日面板对照（重点：20 日窗因子闸①由
      贴线拒 → 通过；闸②读数分布变化）
- [ ] 收割批重跑（临时状态文件，避免幂等短路）：新面板读数落档
- [ ] 读数落档 → 验证报告 `tests/validation/`（含新旧对照与面板深度标注）

## 收口

- [ ] `uv run pytest tests/factors -q` 全绿；ruff / mypy 零新增
- [ ] `openspec validate raise-admission-panel-depth --strict` 通过
- [ ] 验证报告 + PR；sync + archive 在验证通过后执行
