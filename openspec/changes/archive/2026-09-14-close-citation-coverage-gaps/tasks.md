## 1. 图通道（③，真 bug 优先）

- [x] 1.1 TDD 红：门禁测试——`compute_metrics` 全分支产出键 ⊆ `AnalysisState` 声明 ⊆ 图 `channels`（新增于 `tests/test_graph_5layer.py`，推广 incident 027 守卫）
- [x] 1.2 `AnalysisState` 补声明 `derived_series` / `price_levels`（注释标明三处消费方与 027 教训）→ 门禁转绿
- [x] 1.3 真实运行验证：三类 sanity 校验与两处 context 节按设计生效（含 `price_check` 结果、派生值表、价位参考节的实据）；结论落验证报告

## 2. 引用校验派生键覆盖（②）

- [x] 2.1 TDD 红：覆盖门禁测试——`compute_metrics` 产出键 ⊆ `_COMPUTATIONAL_RECALC` ∪ 豁免表（构造全分支 state）
- [x] 2.2 注册 8 个未注册派生键（复用 `_recompute_snapshot`）→ 门禁转绿；补注册键的可重算性单测（至少一条：`quarterly_trend` 子路径 PASS/FAIL）

## 3. 比较型差值重算（①）

- [x] 3.1 TDD 红：差值型三场景（通过 / 超容差 FAIL / direction 未申报显式降级+缺口）
- [x] 3.2 `_verify_comparative` 增加差值型分支（双端重算 + 参考系 max(|a|,|b|) + 符号校验）→ 转绿；既有方向型语义与 D3 行为不回归

## 4. 收口

- [x] 4.1 全量回归（pytest 非 live + ruff + mypy 改动文件）
- [x] 4.2 `openspec validate --strict` + 验证报告落 `tests/validation/` + archive/spec sync
- [x] 4.3 metrics.md §3 两条 follow-up（比较型重算注册 / 未注册趋零）状态更新；如 ③ 改变真实行为，评估是否需要在 docs/evals 记录观测项
