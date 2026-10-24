# Factor Monotonicity Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 闸②在 ICIR 之外附加分层单调性判据（`WEAK_MONOTONICITY`），并为因子详情接口增加存量因子的单调性观测读数。

**Architecture:** 新纯函数 `check_monotonicity(values, fwd, ic)` 复用闸②的 GateResult 形态，在 `admit_candidates` 阶段 A 的 `check_single_factor` 通过后追加执行（拒收仍记 `rejected_gate=2`）；详情接口把风格暴露与单调性合并为一次共享面板构建的诊断载荷。存量 active 因子零回溯（判据只活在候选路径）。

**Tech Stack:** Python 3.14 / pandas / pytest（后端纯逻辑，无前端改动、无 E2E 门禁）。

## Global Constraints

- 判定 = **|Spearman| ≥ `MIN_DECILE_MONOTONICITY`（0.6）且 Spearman 符号与 IC 均值一致**（负向因子按其方向单调同样通过；端点差 Q10−Q1 为披露字段——Task 2 sanity 锚实证修正，spec 已同步）。
- 拒收原因码 **`WEAK_MONOTONICITY`**，`rejected_gate=2`（不新增闸编号）。
- 十分位按**当日截面因子值秩**（`rank(method="first")` 后 `qcut` 10 桶）划分；当日两帧共同有效样本 **< `MIN_IC_SAMPLES`（30）→ 该日跳过**（与 `rank_ic_series` 同一有效性定义，不新增常量）。
- 分位累计收益 = 各分位等权远期收益按日**算术累加**（全准入面板段，不引入新切分）。
- 任一不可计算（零有效日 / 累计收益常量 / 端点差为 0 / 符号无向）→ **fail closed 拒收**。
- 存量 active 因子**不回溯翻案**；详情读数为观测项，不挂任何处置。
- 既有常量不动：`MIN_ABS_ICIR=0.3`、`MAX_MISSING_RATE=0.2`、`MIN_IC_SAMPLES=30`。
- 工作目录 = worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\monotonicity-gate`（分支 `feat/factor-monotonicity-gate`）；**生产库只读**（脚本指向主检出 `data/*.db`，测试一律 tmp fixture，MUST NOT 写生产库）。

---

### Task 1: `MIN_DECILE_MONOTONICITY` 常量 + `check_monotonicity` 纯函数

**Files:**
- Modify: `src/finance_agent/factors/constraints.py`（常量区，约 line 52 `MIN_ABS_ICIR` 之后）
- Modify: `src/finance_agent/factors/validate.py`（闸②区块之后新增函数；`ReasonCode` 暂不加，Task 3 加）
- Test: `tests/factors/test_validate.py`（文件末尾追加）

**Interfaces:**
- Produces: `check_monotonicity(factor_values: pd.DataFrame, fwd_returns: pd.DataFrame, ic: pd.Series) -> GateResult`；`GateResult.gate == 2`、`detail = {"spearman", "q10_q1", "decile_returns"(list[float]×10), "valid_days", "threshold"}`、失败 `reason="WEAK_MONOTONICITY"`（Task 3 起枚举可用前先写字符串字面量亦可——为避免占位，Task 1 直接与 Task 3 的 `ReasonCode.WEAK_MONOTONICITY` 同批提交，见下）。
- 本任务与 Task 3 同一提交周期内完成 `ReasonCode` 扩展（TDD 粒度拆开跑测试，最后一次性 commit）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/factors/test_validate.py` 末尾）

```python
# ── 闸②附加判据：分层单调性（add-factor-monotonicity-gate） ─────────────────


def _mono_frames(per_code_return: list[float]) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """构造 values/fwd/ic：40 码、值 = 码序（十分位各 4 码）、次日收益逐码给定。

    ``per_code_return[i]`` = 码 i 的次日收益（全 20 日同型，逐日累加放大分位差异）；
    ic 序列取同号常量（本纯函数只消费 ic 的均值符号）。
    """
    dates = [(date(2026, 1, 5) + timedelta(days=offset)).isoformat() for offset in range(21)]
    codes = [f"c{index:03d}" for index in range(len(per_code_return))]
    values = pd.DataFrame(
        np.tile(np.arange(len(per_code_return), dtype=float), (21, 1)), index=dates, columns=codes
    )
    returns = np.vstack(
        [
            np.tile(np.asarray(per_code_return, dtype=float), (20, 1)),
            np.full((1, len(per_code_return)), np.nan),
        ]
    )  # 末行 NaN = 最后一日无远期收益 → 有效日 = 20
    fwd = pd.DataFrame(returns, index=dates, columns=codes)
    ic = pd.Series([0.2, 0.25, 0.15, 0.2, 0.22], name="rank_ic")
    return values, fwd, ic


def test_monotone_factor_passes_monotonicity() -> None:
    values, fwd, ic = _mono_frames([float(i) for i in range(40)])
    gate = check_monotonicity(values, fwd, ic)
    assert gate.gate == 2
    assert gate.passed is True
    assert gate.reason is None
    assert gate.detail["spearman"] >= 0.99
    assert gate.detail["q10_q1"] > 0
    assert len(gate.detail["decile_returns"]) == 10
    assert gate.detail["valid_days"] == 20  # 首日无远期收益行 → 有效日 = 20
    assert gate.detail["threshold"] == MIN_DECILE_MONOTONICITY


def test_mid_inversion_rejected_by_monotonicity() -> None:
    # 端点方向正确（Q10 > Q1）但中段倒挂 → Spearman 负 → 拒收
    per_code = [1.0] * 4 + [9.0] * 8 + [2.0] * 12 + [6.0] * 8 + [8.0] * 8
    values, fwd, ic = _mono_frames(per_code)
    gate = check_monotonicity(values, fwd, ic)
    assert gate.passed is False
    assert gate.reason == "WEAK_MONOTONICITY"
    assert gate.detail["q10_q1"] > 0  # 端点差方向本身正确——死于 Spearman


def test_sign_mismatch_rejected_by_monotonicity() -> None:
    values, fwd, _ic = _mono_frames([float(i) for i in range(40)])
    negative_ic = pd.Series([-0.2, -0.25, -0.15, -0.2, -0.22], name="rank_ic")
    gate = check_monotonicity(values, fwd, negative_ic)
    assert gate.passed is False
    assert gate.reason == "WEAK_MONOTONICITY"


def test_insufficient_daily_breadth_fails_closed() -> None:
    values, fwd, ic = _mono_frames([float(i) for i in range(40)])
    values.iloc[:, 10:] = np.nan  # 每日仅 10 票有效 < MIN_IC_SAMPLES(30) → 零有效日
    gate = check_monotonicity(values, fwd, ic)
    assert gate.passed is False
    assert gate.reason == "WEAK_MONOTONICITY"
    assert gate.detail["valid_days"] == 0
```

imports 追加（文件头部对应 import 区）：`from finance_agent.factors.constraints import MIN_DECICILE...` 不存在——实际写 `MIN_DECILE_MONOTONICITY`；validate 导入区加 `check_monotonicity`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd .worktrees/monotonicity-gate && uv run pytest tests/factors/test_validate.py -k monotonicity -x -q`
Expected: FAIL（`ImportError: cannot import name 'check_monotonicity'`）

- [ ] **Step 3: 最小实现**

`constraints.py`（`MIN_ABS_ICIR` 块后）：

```python
MIN_DECILE_MONOTONICITY: float = 0.6
```

docstring 清单同步加一行（口径说明区，紧跟 `MIN_ABS_ICIR` 条目后）：

```python
- ``MIN_DECILE_MONOTONICITY``：闸②附加判据「分层单调性」——十分位累计收益与
  分位序的 Spearman 秩相关下限，且端点差（Q10−Q1）符号须与 IC 均值一致
  （add-factor-monotonicity-gate；首月观测期不调，调整须过 metrics.md 口径修订）；
```

`validate.py`：`ReasonCode` 增 `WEAK_MONOTONICITY = "WEAK_MONOTONICITY"`；`check_single_factor` 之后新增：

```python
def check_monotonicity(
    factor_values: pd.DataFrame, fwd_returns: pd.DataFrame, ic: pd.Series
) -> GateResult:
    """闸②附加判据：分层单调性（add-factor-monotonicity-gate）。

    逐日按因子值截面秩（``rank(method="first")`` → ``qcut`` 10 桶，当日两帧共同
    有效样本 ≥ ``MIN_IC_SAMPLES`` 的日期才计入——与 ``rank_ic_series`` 同一有效性
    定义）构建十分位等权组合，各分位远期收益按日算术累加为分位累计收益；判定 =
    分位序(1..10)与累计收益的 Spearman ≥ ``MIN_DECILE_MONOTONICITY`` **且**端点差
    （Q10−Q1）符号与 IC 均值一致。任一不可计算（零有效日 / 累计收益常量 →
    Spearman NaN / 端点差为 0 / IC 均值无向）→ fail closed 拒收
    （``WEAK_MONOTONICITY``）。秩并列按列序破平（面板构造确定 → 逐位可复现）。
    """
    decile_returns = [0.0] * 10
    valid_days = 0
    common = factor_values.index.intersection(fwd_returns.index).sort_values()
    for trade_date in common:
        factor_row = factor_values.loc[trade_date]
        return_row = fwd_returns.loc[trade_date]
        mask = factor_row.notna() & return_row.notna()
        if int(mask.sum()) < MIN_IC_SAMPLES:
            continue
        ranks = factor_row[mask].rank(method="first")
        buckets = pd.qcut(ranks, 10, labels=False)
        aligned = return_row[mask]
        for bucket in range(10):
            selector = buckets == bucket
            if bool(selector.any()):
                decile_returns[bucket] += float(aligned[selector].mean())
        valid_days += 1
    q10_q1 = decile_returns[-1] - decile_returns[0]
    if valid_days > 0 and len(set(decile_returns)) > 1:
        spearman = float(
            pd.Series(range(1, 11)).corr(pd.Series(decile_returns), method="spearman")
        )
    else:
        spearman = math.nan
    ic_mean = _ic_stats(ic)[0]
    sign_ok = _sign(q10_q1) != 0 and _sign(q10_q1) == _sign(ic_mean)
    passed = math.isfinite(spearman) and spearman >= MIN_DECILE_MONOTONICITY and sign_ok
    return GateResult(
        gate=GATE_SINGLE_FACTOR,
        passed=passed,
        reason=None if passed else ReasonCode.WEAK_MONOTONICITY,
        detail={
            "spearman": spearman,
            "q10_q1": q10_q1,
            "decile_returns": decile_returns,
            "valid_days": valid_days,
            "threshold": MIN_DECILE_MONOTONICITY,
        },
    )
```

`validate.py` 头部 constraints 导入追加 `MIN_DECILE_MONOTONICITY`、`MIN_IC_SAMPLES`（若未导入）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/factors/test_validate.py -k monotonicity -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/factors/constraints.py src/finance_agent/factors/validate.py tests/factors/test_validate.py
git commit -m "feat(factors): check_monotonicity 纯函数 + MIN_DECILE_MONOTONICITY 常量（闸②附加判据，TDD）"
```

---

### Task 2: 阈值 sanity 锚（生产 10 因子一次性读数，决策门）

**Files:**
- 无仓库产物（一次性脚本，读数记入 Task 5 的验证报告）

**Interfaces:**
- Consumes: Task 1 的 `check_monotonicity`；主检出 `data/factor_library.db`（只读）与 `data/market_snapshot.db`。

- [ ] **Step 1: 跑读数脚本**

```bash
cd .worktrees/monotonicity-gate && uv run python - <<'EOF'
import sqlite3
import pandas as pd
from finance_agent.factors.constraints import MIN_IC_SAMPLES
from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import evaluate, forward_returns, rank_ic_series
from finance_agent.factors.monthly import _panel_from_snapshot
from finance_agent.factors.validate import check_monotonicity

SNAP = r"D:\WorkSpace\finance_analysis_agent\data\market_snapshot.db"
LIB = r"D:\WorkSpace\finance_analysis_agent\data\factor_library.db"
con = sqlite3.connect(LIB)
rows = con.execute("SELECT id, expression FROM factors WHERE status='active'").fetchall()
panel = _panel_from_snapshot(SNAP)
fwd = forward_returns(panel.fields["close"])
below = 0
for fid, expr in rows:
    values = evaluate(parse(expr), panel)
    ic = rank_ic_series(values, fwd)
    gate = check_monotonicity(values, fwd, ic)
    d = gate.detail
    print(f"factor {fid}: spearman={d['spearman']:.3f} q10_q1={d['q10_q1']:+.4f} "
          f"valid_days={d['valid_days']} passed={gate.passed}")
    if not (d['spearman'] >= 0.6):
        below += 1
print(f"--- below-threshold: {below}/{len(rows)}")
EOF
```

Expected: 每行输出 spearman 读数。**决策门**：若 `below ≥ 6`（多数存量因子 Spearman < 0.6）→ **停止后续任务**，向 owner 报告读数与口径复核建议（阈值或十分位口径有问题）；否则把逐因子读数抄录进验证报告并继续。

- [ ] **Step 2: 无 commit（信息性产物）**

---

### Task 3: 闸②接线（admit_candidates 阶段 A）

**Files:**
- Modify: `src/finance_agent/factors/validate.py`（模块 docstring 闸②行 + `admit_candidates` 阶段 A，约 line 820-850）
- Test: `tests/factors/test_validate.py`

**Interfaces:**
- Consumes: Task 1 的 `check_monotonicity` / `ReasonCode.WEAK_MONOTONICITY`。
- Produces: 编排行为——`check_single_factor` 通过后追加单调性判定；失败 → `AdmissionResult(None, STATUS_REJECTED, (dedup, gate1, gate2, gate2m), GATE_SINGLE_FACTOR)`；成功 → `gates` 含 4 项进 pending。

- [ ] **Step 1: 写失败测试**

```python
def test_monotonicity_rejection_wired_into_gate2(tmp_path) -> None:
    """端到端：少数强正信号日 + 多数温和反号日 → |ICIR| 过闸但累计分位倒挂 → 闸②拒收。

    构造：60 日 × 40 码；10 个「强日」（系数 +0.10）+ 50 个「温和反号日」（系数
    −0.03）。逐日 IC = ±1（零噪声线性）→ |ICIR| 大、均值非零 → 闸② ICIR 过；
    分位累计 = f(分位) × (10×0.10 − 50×0.03) = −0.5×f → 递减 → Spearman=−1 → 拒收。
    """
    rng = np.random.default_rng(20261024)
    days, codes_n = 60, 40
    signal = rng.uniform(-1.0, 1.0, size=(days, codes_n))
    coeffs = np.array([0.10] * 10 + [-0.03] * 50)
    returns = np.zeros((days, codes_n))
    returns[1:] = coeffs[1:, None] * signal[:-1]
    close = 100.0 * np.cumprod(1.0 + returns, axis=0)
    dates = [
        (date(2026, 1, 5) + timedelta(days=offset)).isoformat() for offset in range(days)
    ]
    codes = [f"c{index:03d}" for index in range(codes_n)]

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=dates, columns=codes)

    panel = MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(signal),
            "amount": frame(signal),
        }
    )
    results = admit_candidates(
        [{"expression": "volume", "hypothesis": "单调性接线用例", "category": "价量"}],
        panel,
        seed=42,
        db_path=tmp_path / "lib.db",
    )
    result = results[0]
    assert result.factor_id is None
    assert result.rejected_gate == 2
    mono = [gate for gate in result.gates if gate.reason == "WEAK_MONOTONICITY"]
    assert len(mono) == 1
    assert mono[0].detail["spearman"] < 0
    # 账本行拒收闸口 = 2（原因码经 gates/事件史自动携带；账本列集不含 reason）
    from finance_agent.factors.library import list_ledger_entries

    entries = list_ledger_entries(db_path=tmp_path / "lib.db")
    assert entries[0]["rejected_gate"] == 2 and entries[0]["admitted"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/factors/test_validate.py -k wired_into_gate2 -x -q`
Expected: FAIL（当前无单调性判定 → 该候选大概率走到闸③/④/⑤ 或因 ICIR 通过而入库 → `rejected_gate != 2`）

- [ ] **Step 3: 接线实现**

`admit_candidates` 阶段 A，`gate2 = check_single_factor(ic)` 通过分支之后、`pending.append` 之前插入：

```python
        gate2 = check_single_factor(ic)
        if not gate2.passed:
            outcomes[position] = AdmissionResult(
                None, STATUS_REJECTED, (dedup, gate1, gate2), GATE_SINGLE_FACTOR
            )
            continue
        gate2m = check_monotonicity(values, fwd, ic)
        if not gate2m.passed:
            outcomes[position] = AdmissionResult(
                None, STATUS_REJECTED, (dedup, gate1, gate2, gate2m), GATE_SINGLE_FACTOR
            )
            continue
        pending.append(
            _PendingCandidate(
                ...,
                gates=[dedup, gate1, gate2, gate2m],
            )
        )
```

（只改两处：插入 gate2m 判定块；`gates=[...]` 列表追加 `gate2m`。其余逐字不动。）

模块 docstring「闸 2 单因子检验」行追加：`；附加分层单调性判据（Spearman ≥ MIN_DECILE_MONOTONICITY 且端点差与 IC 均值同号 → 否则 WEAK_MONOTONICITY，add-factor-monotonicity-gate）`。

- [ ] **Step 4: 回归 + 新测试**

Run: `uv run pytest tests/factors -q`
Expected: 全绿（既有「强因子」面板的日度关系单调 → 单调性通过；50 随机表达式批只会更难入库）。若个别既有用例因单调性拒收翻红：**先读读数判断是否「噪声日主导」形态**——是则用例属合理收紧，改用例期望并注明；不得放宽判据。

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/factors/validate.py tests/factors/test_validate.py
git commit -m "feat(factors): 闸②接线分层单调性判据——WEAK_MONOTONICITY 拒收仍记 gate=2"
```

---

### Task 4: 详情接口单调性观测读数（共享一次面板构建）

**Files:**
- Modify: `src/finance_agent/api.py`（`_factor_detail_payload` 约 line 4252-4267、`_factor_style_exposure_payload` 重构为 `_factor_detail_diagnostics`）
- Test: `tests/test_api_factors.py`

**Interfaces:**
- Consumes: Task 1 `check_monotonicity`；既有 `compute_style_exposure` / `style_exposure_payload` / `MIN_STYLE_SAMPLES` 短路模式。
- Produces: 详情响应新增键 `monotonicity: {"passed", "spearman", "q10_q1", "decile_returns", "valid_days", "threshold"} | null`；`style_exposure` 语义不变。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_api_factors.py`）

```python
def test_detail_monotonicity_null_without_snapshot(_factors_db) -> None:
    """详情含 monotonicity 键；快照库为空 → 诚实 None。"""
    llm_id, _ = _seed_library()
    body = TestClient(app).get(f"{_FACTORS}/{llm_id}").json()
    assert "monotonicity" in body
    assert body["monotonicity"] is None


def test_detail_monotonicity_observed_with_panel(_factors_db, monkeypatch) -> None:
    """IC 点数过门槛 + 可注入面板 → monotonicity 为观测读数（含全部 detail 字段）。"""
    import numpy as np
    import pandas as pd
    import finance_agent.factors.monthly as monthly
    from datetime import date as _date, timedelta as _timedelta
    from finance_agent.factors.eval_engine import MarketPanel

    # 种子因子表达式 = "close"（直接取 close 帧，规避 ts_rank 热身期/常量截面干扰）
    factor_id = add_factor(
        expression="close", hypothesis="单调性观测读数用例", category="价量",
        status="candidate", source="llm",
    )
    set_status(factor_id, "active", detail={"reason": "ADMITTED"})
    for offset in range(30):  # 越过 MIN_STYLE_SAMPLES(30) 短路门槛
        record_ic(factor_id, (_date(2026, 1, 5) + _timedelta(days=offset)).isoformat(), 0.1)

    days, codes_n = 41, 40
    codes = [f"c{i:03d}" for i in range(codes_n)]
    dates = [(_date(2026, 1, 5) + _timedelta(days=o)).isoformat() for o in range(days)]
    # 收益截面随码序递增 + 逐日码相位抖动（IC 逐日非常量，风格暴露相关可定义）
    t_axis = np.arange(days)[:, None]
    i_axis = np.arange(codes_n)[None, :]
    daily = np.linspace(0.0005, 0.0025, codes_n)[None, :] * np.ones((days, 1))
    daily = daily + 0.0008 * np.sin(t_axis / 5.0 + i_axis / 7.0)
    close = 100.0 * np.cumprod(1.0 + daily, axis=0)

    def _frame(matrix: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(matrix, index=dates, columns=codes)

    panel = MarketPanel(fields={
        "open": _frame(close), "high": _frame(close), "low": _frame(close),
        "close": _frame(close), "volume": _frame(close), "amount": _frame(close),
    })
    monkeypatch.setattr(monthly, "_panel_from_snapshot", lambda *_a, **_k: panel)

    body = TestClient(app).get(f"{_FACTORS}/{factor_id}").json()
    mono = body["monotonicity"]
    assert mono is not None
    assert {"passed", "spearman", "q10_q1", "decile_returns", "valid_days", "threshold"} <= set(mono)
```

（`add_factor` / `set_status` / `record_ic` 已在该测试文件头部导入——`_seed_library` 同款用法。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_api_factors.py -k monotonicity -x -q`
Expected: FAIL（`KeyError: 'monotonicity'`）

- [ ] **Step 3: 实现——重构共享诊断载荷**

`_factor_detail_payload` 中 `detail["style_exposure"] = _factor_style_exposure_payload(row)` 替换为 `detail.update(_factor_detail_diagnostics(row))`；删除 `_factor_style_exposure_payload`，新增：

```python
def _factor_detail_diagnostics(row: dict[str, Any]) -> dict[str, Any]:
    """风格暴露 + 分层单调性诊断（共享一次准入面板构建；add-factor-monotonicity-gate）。

    任一不可计算（面板不可得/深度不足/表达式不可求值/IC 点数不足共同样本门槛）→
    对应键 None（前端如实展示「数据不足」，MUST NOT 用 0/虚构冒充）。IC 点数不足
    时不构建面板（500 日 × 全市场面板实测数分钟——同 add-factor-ic-visibility 短路）。
    """
    try:
        from finance_agent.factors.style_exposure import MIN_STYLE_SAMPLES

        if len(list_ic_series(int(row["id"]), limit=FACTOR_IC_WINDOW)) < MIN_STYLE_SAMPLES:
            return {"style_exposure": None, "monotonicity": None}
        from finance_agent.factors.dsl import parse as dsl_parse
        from finance_agent.factors.eval_engine import (
            evaluate as dsl_evaluate,
            forward_returns,
            rank_ic_series,
        )
        from finance_agent.factors.monthly import _panel_from_snapshot
        from finance_agent.factors.validate import check_monotonicity

        panel = _panel_from_snapshot(None)
        fwd = forward_returns(panel.fields["close"])
        values = dsl_evaluate(dsl_parse(str(row["expression"])), panel)
        factor_ic = rank_ic_series(values, fwd)
        monotonicity = check_monotonicity(values, fwd, factor_ic)
        return {
            "style_exposure": style_exposure_payload(
                compute_style_exposure(factor_ic, panel, fwd=fwd)
            ),
            "monotonicity": {"passed": monotonicity.passed, **monotonicity.detail},
        }
    except (OSError, ValueError) as exc:  # 面板不可得/深度不足/表达式不可求值 → 诚实置空
        _logger.warning("因子诊断读数不可计算 factor_id=%s: %s", row.get("id"), exc)
        return {"style_exposure": None, "monotonicity": None}
```

- [ ] **Step 4: 回归**

Run: `uv run pytest tests/test_api_factors.py tests/factors/test_style_exposure.py -q`
Expected: 全绿（`test_detail_style_exposure_null_without_snapshot` / `test_detail_style_exposure_skips_panel_when_ic_points_below_min` 语义原样保留——短路路径返回双 None、不触碰 `_panel_from_snapshot`）。

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/api.py tests/test_api_factors.py
git commit -m "feat(api): 因子详情单调性观测读数——与风格暴露共享一次面板构建"
```

---

### Task 5: 验证与收尾

**Files:**
- Modify: `openspec/changes/add-factor-monotonicity-gate/tasks.md`（勾选）
- Create: `tests/validation/2026-10-24-add-factor-monotonicity-gate-validation.md`

- [ ] **Step 1: 全量验证**

```bash
cd .worktrees/monotonicity-gate
uv run pytest tests/factors tests/test_api_factors.py -q
uv run ruff check src/finance_agent/factors src/finance_agent/api.py tests/factors tests/test_api_factors.py
uv run ruff format --check src/finance_agent/factors src/finance_agent/api.py tests/factors tests/test_api_factors.py
uv run mypy src/finance_agent/factors/validate.py src/finance_agent/factors/constraints.py
openspec validate add-factor-monotonicity-gate --strict
```

Expected: pytest 全绿；ruff/format 零告警（mypy 若报**存量**无关错误须对照 main 基线，只保证新增代码零新增错误）。

- [ ] **Step 2: 人工验证报告**（`tests/validation/2026-10-24-add-factor-monotonicity-gate-validation.md`）

内容：①Task 2 的 10 因子 sanity 锚逐行读数 + 决策门结论；②闸②接线后对既有「强因子/噪声因子」用例的影响面（翻红清单或「零翻红」）；③详情接口三态（空快照 None / IC 不足短路 / 可算读数）实测输出；④结论段（判据生效、存量零回溯——`GET /api/factors` 列表状态与实施前一致）。

- [ ] **Step 3: 勾选 delta tasks.md 并 commit**

```bash
git add openspec/changes/add-factor-monotonicity-gate/tasks.md tests/validation/2026-10-24-add-factor-monotonicity-gate-validation.md
git commit -m "docs(validation): 分层单调性判据验证报告 + delta tasks 勾选"
```

- [ ] **Step 4: 推送开 PR**

```bash
git push -u origin feat/factor-monotonicity-gate
gh pr create --title "feat(factors): 闸②附加分层单调性判据（add-factor-monotonicity-gate）" --body-file <(见下方模板)
```

PR body 要点：Why（对标 G2 缺口）/ What（WEAK_MONOTONICITY、rejected_gate=2、存量不回溯、详情观测读数）/ sanity 锚读数表 / 验证清单 / 注明与 add-analyst-revision-factors 的归档顺序（本 PR 先归档）。
