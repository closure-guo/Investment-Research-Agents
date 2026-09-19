## 1. 实现（TDD 先红后绿）

- [x] 1.1 红灯：`TestDerivedRootAlias`（numerical 别名根 PASS / 规范根不变）+ `TestComputationalRegistryCoverage::test_alias_root_recomputes`（计算型走别名查注册表）
- [x] 1.2 `_ROOT_ALIASES` + `_apply_root_alias` 接入 `_resolve_field_ref` 与 `_verify_computational`；analysts context 提示改 `derived_series.`
- [x] 1.3 `tests/test_citation.py` 全绿（56+ 例）+ ruff/mypy 改动文件

## 2. 收口

- [x] 2.1 `openspec validate --strict` + 归档 + spec sync（主规范 53/53）
- [x] 2.2 验证记录并入 `tests/validation/2026-09-14-close-citation-coverage-gaps-validation.md`（验证期发现并修复）
