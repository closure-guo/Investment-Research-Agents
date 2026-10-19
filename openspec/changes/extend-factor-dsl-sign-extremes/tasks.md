# Tasks: extend-factor-dsl-sign-extremes

> 上线顺序 = 任务顺序。TDD：先红后绿。

## T1 DSL 算子注册（factors/dsl.py）

- [x] 先红后绿：`sign`（arity 1）/ `ts_max`/`ts_min`（arity 2，window 位 1）解析；
      窗口形态非法（0 / 2.5 / 非字面量）拒收
- [x] 求值语义用例：sign 三值 + NaN 传播；ts_max/ts_min 窗口含当日 / 前置 NaN / 含 NaN 窗口 → NaN

## T2 求值引擎（factors/eval_engine.py）

- [x] 先红后绿：`ts_max`/`ts_min` = `rolling(window, min_periods=window)`；`sign` = `np.sign` 逐元素
- [x] 与 ts_mean 同族口径对照用例（min_periods=window 满窗非 NaN，含 NaN 窗口传播）

## T3 翻译器映射翻转（factors/qlib_translate.py）

- [x] 先红后绿：`Max($close, 20)` → `ts_max(close,20)`、`Min` → `ts_min`、`Sign($close - Ref($close,1))` → `sign(sub(close,ref(close,1)))`
- [x] 既有「同名不同义拒收」用例迁移（Max/Min 翻转后由 Skew（missing）钉住；harvest 混合批样本同步换 Skew）
- [x] 映射表审计用例更新（expected statuses 含 Max/Min/Sign 翻转）

## T4 prompt 同步 + 发布

- [x] `factor_transcription.md`：算子清单 + 拒译反例改写；Langfuse 定向发布（production **v2** 实证：含 Max/Min/Sign、反例已删）

## T5 重放实测（判据）

- [ ] 生产状态副本 + 临时库：`--statuses untranslatable --limit 40` 重放
- [ ] 读数：LLM 拒译族 21 条的转译恢复数 + 恢复族闸② |ICIR| 分布
- [ ] 读数落档 → 验证报告 `tests/validation/2026-10-19-extend-factor-dsl-sign-extremes-validation.md`

## 收口

- [x] `uv run pytest tests/factors ...` 全绿（469）；ruff / mypy 干净
- [x] `openspec validate extend-factor-dsl-sign-extremes --strict` 通过
- [ ] 验证报告 + PR；sync + archive 在验证通过后执行
