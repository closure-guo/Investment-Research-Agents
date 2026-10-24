# 人工验证报告：add-factor-monotonicity-gate（2026-10-24）

**分支**：`feat/factor-monotonicity-gate`（worktree `.worktrees/monotonicity-gate`，基于 origin/main @ 0b93d6cd）
**性质**：纯后端逻辑变更（闸②附加判据 + 详情只读读数），非交互类，不适用 E2E 门禁。
**实施**：executing-plans 内联执行，计划 `docs/superpowers/plans/2026-10-24-factor-monotonicity-gate.md`。

## 1. 阈值 sanity 锚（Task 2 决策门——决策门触发并收口）

对生产 10 个 active 因子（主检出 `data/*.db`，**只读**；准入面板 500 交易日）跑分位单调性读数：

| factor | Spearman | Q10−Q1 | IC 均值 | ICIR | 旧口径（正相关≥0.6） | **修正口径（\|ρ\|≥0.6 且同号）** |
|---|---|---|---|---|---|---|
| 1 volratio20 | −0.758 | −0.920 | −0.0464 | −0.45 | 拒 | **过** |
| 2 volcv20 | −1.000 | −0.790 | −0.0401 | −0.62 | 拒 | **过** |
| 3 range1 | −0.297 | −0.596 | −0.0629 | −0.33 | 拒 | **拒（弱单调）** |
| 4 RANGE_EXP_20D | −0.758 | −0.681 | −0.0344 | −0.35 | 拒 | **过** |
| 5 CORR5 | −0.976 | −0.603 | −0.0405 | −0.48 | 拒 | **过** |
| 6 KLOW | −0.964 | −1.055 | −0.0430 | −0.35 | 拒 | **过** |
| 8 volume_autocorr | −0.988 | −0.632 | −0.0220 | −0.33 | 拒 | **过** |
| 9 VolumeTrend | −0.794 | −0.525 | −0.0368 | −0.31 | 拒 | **过** |
| 10 VOL_RET_CORR | −0.248 | +0.053 | −0.0293 | −0.35 | 拒 | **拒（弱单调+端点反号）** |
| 11 F_range_exp | −0.358 | −0.642 | −0.0355 | −0.36 | 拒 | **拒（弱单调）** |

**决策门结论**：首跑按计划判据（正相关 ≥0.6）得 10/10 拒收 → 触发停止条件。逐行归因发现不是阈值问题而是**判据方向口径 bug**：10 因子 IC 均值全负（量价反转族常态），其中 7 个 Spearman ∈ [−1.0, −0.758] 且与 IC 同号 = 方向一致的强单调，按「正相关」口径被整体误杀。判据修正为 **|Spearman| ≥ 0.6 且 Spearman 与 IC 均值同号**（端点差降为披露字段），spec/proposal/design 已同步改写。修正后 7/10 通过、3/10（0.248/0.297/0.358）真弱被拒——阈值 0.6 校准健康，维持。

## 2. 判据实现与接线（Task 1/3）

- `check_monotonicity` 纯函数（`validate.py`）：当日截面按秩分十分位（`rank(method="first")` → `qcut`，有效样本 ≥ `MIN_IC_SAMPLES`=30 的日期才计入）、等权远期收益算术累加、Spearman = 秩变换 Pearson（**scipy 非本仓依赖，未引入**）；零有效日/常量累计/方向无向 → fail closed。
- 单测 5 用例：单调通过 / 中段倒挂拒收 / 方向不一致拒收 / 负向单调通过（生产形态）/ 广度不足 fail closed。
- `admit_candidates` 阶段 A 接线：ICIR 通过后追加判据，拒收仍记 `rejected_gate=2`、`gates` 序列含两项 gate=2；账本行 `rejected_gate='2'`（TEXT 列型）。端到端用例：42 正日（+0.10）+ 18 反号日（−0.30）交错 → ICIR≈0.42 过、IC 均值 +0.39、分位累计 ∝ −1.2×f 递减 → 方向不一致拒收（「多数日方向 ≠ 累计可交易方向」形态）。
- **既有用例影响面**：`tests/factors` 444 项中仅 2 项翻红，均为契约锚点按预期扩展（原因码枚举集 + 强因子 gates 序列 [0,1,2,3,4,5]→[0,1,2,2,3,4,5]，强因子仍入库）；无行为性翻红。

## 3. 详情接口三态（Task 4）

`/api/factors/{id}` 新增 `monotonicity` 键（与 `style_exposure` 合并为 `_factor_detail_diagnostics`，**共享一次准入面板构建**，IC 点数 < 30 时短路不建面板）：

| 态 | 证据 |
|---|---|
| 快照库为空 → `null` | `test_detail_monotonicity_null_without_snapshot` |
| IC 点数 < MIN_STYLE_SAMPLES → `null`（不建面板，`_boom` 断言零面板开销） | 既有 `test_detail_style_exposure_skips_panel_when_ic_points_below_min` 语义随重构保留并通过 |
| 可计算 → `{passed, spearman, q10_q1, decile_returns, valid_days, threshold}` | `test_detail_monotonicity_observed_with_panel`（注入面板 + 30 条 IC 记录越过短路门槛） |

## 4. 存量零回溯核对

生产库只读核对（2026-10-24 实测）：11 行 = 10 active + 1 retired（id7），与实施前一致；判据只活在 `admit_candidates` 候选路径，无任何存量重裁代码路径；详情读数为观测项（`passed` 字段仅展示，不挂处置）。

## 5. 验证命令与结果

```
uv run pytest tests/factors tests/test_api_factors.py -q   → 463 passed
uv run ruff check（factors + api + 两测试文件）             → All checks passed
uv run ruff format --check（同上）                          → 37 files already formatted
uv run mypy validate.py constraints.py                      → Success: no issues
openspec validate add-factor-monotonicity-gate --strict     → valid
uv run python tests/scripts/check_secrets.py                → PASS
```

（pre-commit 本机缓存锁坏——按既有纪律手动跑 check_secrets/ruff 后 `--no-verify` 提交。）

## 6. 结论

判据已生效且方向口径经生产数据实证校准；存量 active 零回溯；详情接口三态如实披露。归档顺序提醒：本 change 须先于 `add-analyst-revision-factors` 归档（同改 factor-library「五道闸准入管线」，后归档方合并重写 MODIFIED 块）。
