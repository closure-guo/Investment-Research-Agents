# Tasks: add-factor-monotonicity-gate

- [ ] `MIN_DECILE_MONOTONICITY` 常量落 `constraints.py`；对现有 10 个 active 因子先跑一次分位单调性读数做阈值 sanity 锚（多数 < 0.6 则先复核口径再上线）
- [ ] `check_monotonicity` 纯函数（十分位 / 等权累计 / Spearman / 端点差）+ 单测钉死三个场景（中段倒挂拒收 / 端点同向通过 / 广度不足日跳过）
- [ ] 闸②接线：ICIR 通过后追加判据，`WEAK_MONOTONICITY` 拒收（`rejected_gate=2`），`detail.monotonicity` 落读数；账本行携带原因码
- [ ] 存量不回溯回归测试（active 状态不变）+ 详情接口单调性观测读数（不可计算返回 null）
- [ ] `openspec validate add-factor-monotonicity-gate --strict` 通过；ruff / mypy / `tests/factors` 全绿
