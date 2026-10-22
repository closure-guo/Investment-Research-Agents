# Tasks: add-screening-ic-bootstrap-guard

- [x] 先红后绿：单因子 IC 样本 < 10 条被排除、≥10 条参与（`select_scorable_factors` 边界）
- [x] 先红后绿：全部因子样本不足 → `selection_mode=fallback` 且精选池照常产出（非空）
- [x] 先红后绿：成熟因子 + 新因子混合 → 仍走 `factor`，新因子不出现在贡献分解
- [x] `uv run pytest tests/screening -q` 全绿；`uv run ruff check` 通过
- [x] `openspec validate add-screening-ic-bootstrap-guard --strict` 通过
- [x] 验证报告落 `tests/validation/2026-10-22-add-screening-ic-bootstrap-guard-validation.md`
- [ ] sync + archive
