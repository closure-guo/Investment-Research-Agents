# Tasks: add-factor-monotonicity-gate

- [x] `MIN_DECILE_MONOTONICITY` 常量落 `constraints.py`；对现有 10 个 active 因子先跑一次分位单调性读数做阈值 sanity 锚（多数 < 0.6 则先复核口径再上线）——**决策门触发并收口**：旧「正相关」口径 10/10 误杀（7 个强负向单调与 IC 同号），判据修正为 |Spearman| ≥ 0.6 且与 IC 均值同号；修正后 7 过/3 拒，阈值维持 0.6（读数表见验证报告 §1）
- [x] `check_monotonicity` 纯函数（十分位 / 等权累计 / Spearman（秩变换 Pearson，零 scipy）/ 端点差）+ 单测钉死场景（单调通过 / 中段倒挂拒收 / 方向不一致拒收 / 负向单调通过 / 广度不足日 fail closed）
- [x] 闸②接线：ICIR 通过后追加判据，`WEAK_MONOTONICITY` 拒收（`rejected_gate=2`），`detail.monotonicity` 落读数；账本行拒收闸口 = 2（原因码经 gates/事件史自动携带，账本列集不含 reason）
- [x] 存量不回溯（判据只活在 `admit_candidates` 候选路径，生产库只读核对 10 active + 1 retired 不变）+ 详情接口单调性观测读数（`_factor_detail_diagnostics` 与风格暴露共享一次面板构建；不可计算返回 null）
- [x] `openspec validate add-factor-monotonicity-gate --strict` 通过；ruff / ruff format / mypy 干净；`tests/factors` + `tests/test_api_factors.py` 463 全绿；人工验证报告 `tests/validation/2026-10-24-add-factor-monotonicity-gate-validation.md`
