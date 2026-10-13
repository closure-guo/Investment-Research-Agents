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

## 5. 评审修正（review fixes，2026-10-13）
- [x] 5.1 BH-FDR 分辨率：`PERMUTATION_N` 200 → 999（覆盖 K ≤ 100）+ `check_significance`
      分辨率守卫（`1/(N+1) > q/K` → fail loud）；测试 K=50 强/全噪声、守卫边界、K=100↔101
- [x] 5.2 闸④判据改**验证段**（与闸⑤同一 `TRAIN_FRACTION` 切分）；detail 记训练/验证两段增量；
      验证段有效日 < `MIN_OOS_IR_SAMPLES=10` → `NO_POOL_INCREMENT` + `insufficient_oos_dates`
- [x] 5.3 置换口径替代（逐日符号随机化）获控制器批准，口径写入 `validate.py` 模块 docstring
- [x] 5.4 闸⑤剔除训练段末端 `DEFAULT_IC_HORIZON` 日（边界泄漏），`purged_train_days` 落 detail
- [x] 5.5 delta spec 查重措辞改为 candidate/active/degraded（retired 允许重提）；
      `openspec validate add-factor-library-and-monitor --strict` 通过
- [x] 5.6 `MIN_REDUNDANCY_SAMPLES=5` 移入 `constraints.py` 单一来源
- [x] 5.7 `test_validate.py` 陈旧 docstring（占位桩表述）更新
- [x] 5.8 K>20 端到端批量用例（K=25 闸②存活者，仅零超额强候选过闸③）
- [x] 5.9 BH 多重性按候选**位置**计（零填充位置键；逐字重复各计一条假设），`n_hypotheses` 落 detail
- [x] 5.10 验收重跑 + 报告「评审修正」章节 + 计划「评审修正记录」；
      验证：`uv run pytest tests/factors tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py -q`
      → 303 passed；ruff / mypy（factors）全绿；verdict 不变、artifact 已刷新
