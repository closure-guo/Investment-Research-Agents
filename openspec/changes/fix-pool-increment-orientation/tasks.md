# Tasks: fix-pool-increment-orientation

> TDD：先红后绿。

## T1 闸④方向归一化（factors/validate.py）

- [x] 先红后绿：稳定负向候选（空池）→ 定向后 `detail.orientation == -1` 且 increment > 0
- [x] 正向候选行为逐位不变（既有闸④用例全绿；review 复核增量逐位相同）
- [x] 方向不可定（IC 均值 NaN/0）→ 按原向参与（fail closed）
- [x] 池因子定向：池内含负向因子时 `pool_orientations` 记 −1，组合 IR 取正；**review C1 修复**：定向改面板现算（落库 IC 只覆盖验证段会静默踢出组合）+ 陈旧落库 IC 回归用例

## T2 复跑测量

- [x] 全量重放（78 条，两轮）：**均入 3 条**（volratio20/volcv20/range1）；max_ret_5d 过②③倒在④冗余（dup_of=range1）
- [x] 读数落档 → `tests/validation/2026-10-19-fix-pool-increment-orientation-validation.md`

## 收口

- [x] `uv run pytest tests/factors -q` 全绿（403）；ruff / mypy 干净
- [x] `openspec validate fix-pool-increment-orientation --strict` 通过
- [ ] sync + archive + PR（验证通过后执行）
