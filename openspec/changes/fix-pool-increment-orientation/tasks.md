# Tasks: fix-pool-increment-orientation

> TDD：先红后绿。

## T1 闸④方向归一化（factors/validate.py）

- [ ] 先红后绿：稳定负向候选（空池）→ 定向后 `detail.orientation == -1` 且 increment > 0
- [ ] 正向候选行为逐位不变（既有闸④用例全绿）
- [ ] 方向不可定（IC 均值 NaN/0）→ 按原向参与（fail closed）
- [ ] 池因子定向：池内含负向因子时 `pool_orientations` 记 −1，组合 IR 取正

## T2 复跑测量

- [ ] 全量已拒/不可翻译候选重放（临时库）→ 新增入库清单（预期仅 max_ret_5d 族）
- [ ] 读数落档 → `tests/validation/2026-10-19-fix-pool-increment-orientation-validation.md`

## 收口

- [ ] `uv run pytest tests/factors -q` 全绿；ruff / mypy 干净
- [ ] `openspec validate fix-pool-increment-orientation --strict` 通过
- [ ] 验证报告 + PR；sync + archive 在验证通过后执行
