# Tasks: add-screening-ranker

## 1. 打分纯函数
- [x] 1.1 横截面 z-score + IC 加权合成 + 确定性单测（同输入同输出）
      → `src/finance_agent/screening/ranker.py`（T1，`9a9d6247`）：`rank_universe` /
      `factor_weights` / `recent_ic_stats`（R1–R4）；确定性 = 无随机/无 LLM + 显式 tie-break
      （composite 降序 → symbol 升序），两次执行逐项相等被单测钉死；`tests/screening/test_ranker.py`（21 例）
- [x] 1.2 factor_contribs 贡献分解 + 勾稽单测（Σcontribution = composite_score，容差 1e-9）
      → 同提交：`composite_score` 与 `factor_contribs` 同序累加（勾稽逐位成立）；单测覆盖
      常规 / 全 NaN 因子 / std=0 截面 / 负权重 / 缺失单元路径；验收对 3 日全部 300 条候选实测最大差 0.0

## 2. builtin 因子包
- [x] 2.1 五个手工因子的 DSL 表达与注册路径（免提案、过准入闸）+ 注册单测
      → `src/finance_agent/factors/builtin.py`（T2，`05a26261`）：`BUILTIN_FACTORS` 五条
      （名称逐字 momentum_20/reversal_5/turnover_proxy/volatility_20/size_proxy，
      `category=source="builtin"`）+ `register_builtins`（逐条 `admit_candidates`，幂等：
      结构查重命中即 `STRUCTURAL_DUPLICATE` 不重复入库）；`tests/factors/test_builtin.py`（9 例）：
      表达式逐条过 DSL 默认复杂度上限、强信号面板 ≥1 active、闸②同阈值（纯噪声面板反转因子
      实测 ICIR≈0.119 ≈ spec 场景 0.12，builtin 与 proposal 判定逐位一致）

## 3. 验证
- [x] 3.1 全量测试通过
      → `uv run pytest tests/ -q -m "not live"`：`1 failed, 5118 passed, 6 skipped, 12 deselected
      in 869.20s (0:14:29)`——唯一失败为**既有 llm env flake**
      （`test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default`；
      隔离复跑 `1 passed in 0.05s`、本 change 不触 `llm/`、passed 增量 42 = 12 既有评审修正用例
      + 30 本 change 用例，归因见验证报告 §0.1/§2）。
      （`uv run pytest tests/screening tests/factors -q`：`337 passed in 11.18s`；
      ruff / mypy 改动文件全绿，见验证报告 §0）
- [x] 3.2 三个交易日 Top-100 抽 10 只手算核对，报告落 `tests/validation/`
      → 脚本 `tests/validation/2026-10-13-add-screening-ranker-acceptance.py`（固定种子、零网络）
      + artifact `.json` + 报告 `tests/validation/2026-10-13-add-screening-ranker-validation.md`：
      3 日 × Top-100 × 抽 10 只（共 30 只）**全部手算一致**（最大逐项差 6.2e-15 ≪ 1e-9）；
      勾稽 300/300 候选逐位相等；两次运行 stdout/artifact 逐位可复现。
      **live 2026-09 真实快照核对待补**：本机东财 IP 封禁（issue #325）不可得 → 合成面板 +
      独立手算替代（报告 §1.3/§2），不得读作真实市场有效性证据
- [x] 3.3 既有测试无修改通过（红线）
      → T1/T2 提交 diff 均为纯新增文件（`git show --stat 9a9d6247 05a26261`）；既有 337 例
      （screening+factors）零改动通过，全量回归仅既有 llm env flake（见 3.1 与报告 §2）
