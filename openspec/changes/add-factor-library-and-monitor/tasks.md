# Tasks: add-factor-library-and-monitor

## 1. 求值引擎与库 schema
- [x] 1.1 DSL 向量化求值引擎（消费 market-snapshot）+ 与手算基准对拍单测
      → `src/finance_agent/factors/eval_engine.py`（T1，`f4c621e0`）；`tests/factors/test_eval_engine.py`（33 例）
- [x] 1.2 因子库 schema（状态机 candidate/active/degraded/retired）+ 状态迁移单测
      → `src/finance_agent/factors/library.py`（T2，`a61d98b7`）；`tests/factors/test_library.py`

## 2. 五道闸
- [x] 2.1 闸①②（可计算性/RankIC/ICIR）+ 正反单测
      → `validate.py::check_computability` / `check_single_factor` + 编排骨架（T3，`8ebc4858`）
- [x] 2.2 闸③（置换检验 + BH-FDR，固定种子可复现）+ 噪声因子批量注入单测（50 个纯噪声因子入库率 < 10%）
      → T4（`052b84f4`）：`check_significance` + `benjamini_hochberg_adjust`；50 随机表达式注入纯噪声面板
      （120 日 × 60 票，固定种子）实测 **0 条入库**（全部止步闸②），< 10% ✓；同种子 p 值逐位相等
- [x] 2.3 闸④⑤（池级组合增量 + 样本外复核）+ 冗余因子拒收单测
      → T5（`eca7f599`）：`check_pool_increment`（冗余 |ρ| > 0.7 先于增量；`detail.increment` 供 E8 保种排序）
      + `check_out_of_sample`（训验 70/30 按日期切分，切点不跨日）

## 3. 保种与监控
- [x] 3.1 分桶 top-k 保种 + 桶间配额单测
      → `library.py::apply_bucket_quota`（T2，`a61d98b7`）；编排入库后调用（T3 `8ebc4858`）
- [x] 3.2 衰减监控日批（滚动 IC 降级规则）+ 降级事件审计落库单测
      → T6（`4514c173`）：`factors/monitor.py::run_factor_monitor` + `run_factor_decay_monitor`
      （快照库 → 面板）；ops 注册 `factor_decay_monitor`（15:45 mon-fri，`_with_retry`）

## 4. 验证
- [x] 4.1 全量测试通过
      → `uv run pytest tests/ -q -m "not live"`：`1 failed, 5076 passed, 6 skipped, 12 deselected in 782.16s`
      ——唯一失败为**既有 llm env flake**（`test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default`；
      隔离复跑通过、`git stash` 撤改动后 `tests/llm` 同样失败、宿主 `.env` 第 40 行 `LLM_MAX_CONTEXT=256000`
      经 `api` import 期 `load_dotenv` 泄漏；报告 §3.6）。本 change 全部用例（`tests/factors` 240 例）通过。
- [x] 4.2 5 有效 + 5 噪声因子对照验收，报告落 `tests/validation/`
      → 脚本 `tests/validation/2026-10-13-add-factor-library-and-monitor-acceptance.py`（固定种子、零网络）
      + artifact `.json` + 报告 `tests/validation/2026-10-13-add-factor-library-and-monitor-validation.md`：
      5 经典因子全部 `active`、5 噪声因子全部被拒（闸② `WEAK_ICIR`）
- [x] 4.3 既有测试无修改通过（红线）
      → 既有测试**断言零改动**；仅按计划 Global Constraints 明文允许的最小更新改 ops 注册表计数断言
      6→7（`tests/outcome/test_ops_jobs.py` / `tests/outcome/test_scheduler.py`，change 1 先例，报告 §3.4 披露），
      另 `tests/factors/test_validate.py` 模块 docstring 随 T4/T5 实装更新（纯文字，断言未动）；
      T3 端到端 fixture 未被调整（真闸下原样通过，报告 §3.7）
