# Tasks: extend-factor-dsl-ref

> 上线顺序 = 任务顺序。TDD：先红后绿。

## T1 DSL 解析（factors/dsl.py）

- [x] 先红后绿：`ref(close, 5)` 解析为 `Call("ref", (Field("close"), Number(5)))`；序列化 round-trip 相等
- [x] 先红后绿：窗口形态拒收（`ref(close, 0)` / `ref(close, -5)` / `ref(close, x)` → `DSLParseError`）
- [x] `OPS` / `ARITY` / `WINDOW_ARG_INDEX` 三处登记；既有算子行为零变化（枚举 16 逐字钉死）

## T2 求值（factors/eval_engine.py）

- [x] 先红后绿：`ref(close, 2)` 逐标的后移 2 期（前 2 行 NaN、不跨标的）；与 `delta` 同源机制
- [x] `_WINDOW_OPS` 登记；模块 docstring 语义注记（shift 无 min_periods）

## T3 翻译器（tests/scripts/spike/qlib_translate.py）

- [x] `Ref` 映射 `missing` → `equivalent`（→ `ref`；备注声明无热身期口径差、负/零窗口拒收）；docstring 更新
- [x] `test_qlib_translate.py` 断言翻转（Ref 可翻译；Quantile+Ref 组合只剩 Quantile 阻塞；映射表状态）
      并新增 Ref 零/负窗口拒收用例

## T4 复跑与证据（翻译批）

- [x] 本批 10 候选重跑：翻译 **8/10**（10-16 为 4/10；余 2 条为复杂度深度上限）+ 五道闸读数
      （0 入库；闸②×6、闸④×2；ClosePosition 由 LOW_COVERAGE 转闸②——high/low 回填正向印证）
- [x] 新候选批（22 因子，持续研发会话提取 + 逐条转写）：翻译 **11/22**（阻塞=深度×7、Sum×2、
      Sqrt×1、滚动 Max×1）；过闸 0 入库（闸②×11）
- [x] 产物落档（裁决表 + 映射表 + 通道摘要 → `reports/`）→ 验证报告
      `tests/validation/2026-10-17-extend-factor-dsl-ref-validation.md`（含 32 条全量裁决表）

## 收口

- [x] `uv run pytest tests/factors tests/screening tests/test_api_factors.py tests/test_api_screening.py -q` 全绿（505）；
      `ruff` / `mypy` 零新增
- [x] `openspec validate extend-factor-dsl-ref --strict` 通过
- [x] 验证报告 + PR；sync + archive 在验证通过后执行
