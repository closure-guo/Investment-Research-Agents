"""factors/validate.py 单测（add-factor-library-and-monitor Task 3；T4 增补闸③）。

覆盖闸①（可计算性：全样本缺失率 ≤ ``MAX_MISSING_RATE``）与闸②（单因子检验：
``|ICIR| ≥ MIN_ABS_ICIR`` 且 ``|IC 均值| > 0``，E3）、``admit_candidates`` 编排
（结构查重 → 闸①②③④⑤ → 入库 active；顺序保持、被拒不入库），T4 的闸③
置换检验 + BH-FDR（同种子逐位可复现、强信号通过、纯噪声拒收、BH 单调性与手算
对照、50 随机表达式批量注入入库 < 5），T5 的闸④（冗余 + 校验段增量）与闸⑤
（训验切分 + 边界日剔除），以及评审修正新增的 BH 分辨率（K ≤ 100 / 守卫 / K>20
端到端）与按候选位置计多重性用例。

合成面板：固定种子；「强因子」= 用 volume 承载次日收益信号，且 amount = volume ×
(-1)^t（横截面秩与次日收益逐日反号 → IC 序列均值≈0、标准差≈1 → 闸②必拒）。
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

from finance_agent.factors.constraints import (
    BH_FDR_Q,
    DEFAULT_IC_HORIZON,
    MIN_OOS_IR_SAMPLES,
    PERMUTATION_N,
    TRAIN_FRACTION,
)
from finance_agent.factors.dsl import (
    AstNode,
    Call,
    Field,
    Number,
    is_subtree_isomorphic,
    parse,
    serialize,
)
from finance_agent.factors.eval_engine import (
    MarketPanel,
    evaluate,
    forward_returns,
    rank_ic_series,
)
from finance_agent.factors.library import (
    add_factor,
    list_active_factors,
    list_events,
    list_factors,
    list_ic_series,
    record_ic,
)
from finance_agent.factors.validate import (
    AdmissionResult,
    GateResult,
    ReasonCode,
    admit_candidates,
    benjamini_hochberg_adjust,
    check_computability,
    check_out_of_sample,
    check_pool_increment,
    check_significance,
    check_single_factor,
    train_validation_split,
)

_SEED_EXPRESSION = "ts_mean(close,10)"
_PANEL_SEED = 20261013
_PANEL_DATES = 61
_PANEL_CODES = 40


# ── 合成面板与候选构造 ──────────────────────────────────────────────────────


def _signal_panel() -> MarketPanel:
    """固定种子合成面板：volume 真实预测次日收益；amount = volume × (-1)^t（弱）。"""
    rng = np.random.default_rng(_PANEL_SEED)
    signal = rng.uniform(-1.0, 1.0, size=(_PANEL_DATES, _PANEL_CODES))
    noise = rng.uniform(-1.0, 1.0, size=(_PANEL_DATES, _PANEL_CODES))
    returns = np.zeros((_PANEL_DATES, _PANEL_CODES))
    returns[1:] = 0.05 * signal[:-1] + 0.01 * noise[:-1]
    close = 100.0 * np.cumprod(1.0 + returns, axis=0)
    dates = [
        (date(2026, 1, 5) + timedelta(days=offset)).isoformat() for offset in range(_PANEL_DATES)
    ]
    codes = [f"c{index:03d}" for index in range(_PANEL_CODES)]
    weak = signal * np.array([(-1.0) ** step for step in range(_PANEL_DATES)])[:, None]

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=dates, columns=codes)

    return MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(signal),
            "amount": frame(weak),
        }
    )


def _candidate(expression: str, *, category: str = "价量") -> dict:
    """候选 dict（与 propose.FactorProposal 对应字段；ast_json 缺省由编排派生）。"""
    return {
        "expression": expression,
        "hypothesis": "测试用经济学假设：该因子与未来收益存在稳定关系。",
        "category": category,
    }


def _seed_library(tmp_path) -> tuple[str, int]:
    """建库并预置 ts_mean(close,10)（结构查重的命中对象）。"""
    db = str(tmp_path / "factor_library.db")
    factor_id = add_factor(
        expression=_SEED_EXPRESSION,
        hypothesis="测试用经济学假设：十天均线水平与未来收益存在关系。",
        category="动量",
        ast_json=serialize(parse(_SEED_EXPRESSION)),
        db_path=db,
    )
    return db, factor_id


def _empty_db(tmp_path) -> str:
    return str(tmp_path / "factor_library.db")


# ── 原因码契约 ─────────────────────────────────────────────────────────────


def test_reason_codes_are_verbatim():
    """原因码逐字（跨模块契约，Global Constraints）。"""
    assert {code.value for code in ReasonCode} == {
        "LOW_COVERAGE",
        "WEAK_ICIR",
        "NOT_SIGNIFICANT",
        "REDUNDANT_WITH_POOL",
        "NO_POOL_INCREMENT",
        "OOS_FAILED",
        "STRUCTURAL_DUPLICATE",
    }
    assert str(ReasonCode.LOW_COVERAGE) == "LOW_COVERAGE"  # StrEnum：可直接当字符串用


# ── 闸① 可计算性 ────────────────────────────────────────────────────────────


def _frame_with_missing(count: int, *, cells: int = 1000) -> pd.DataFrame:
    """100 × 10 帧，前 ``count`` 个 cell 置 NaN（缺失率 = count/1000）。"""
    values = np.arange(cells, dtype="float64").reshape(100, 10)
    flat = values.reshape(-1).copy()
    flat[:count] = np.nan
    return pd.DataFrame(flat.reshape(100, 10))


def test_check_computability_boundary_passes():
    """缺失率 19.9% 与恰在阈值 20.0% → 通过（「≤ 阈值」语义），detail 带分子分母。"""
    for count in (199, 200):
        result = check_computability(_frame_with_missing(count))
        assert isinstance(result, GateResult)
        assert result.gate == 1
        assert result.passed is True
        assert result.reason is None
        assert result.detail["missing_cells"] == count
        assert result.detail["total_cells"] == 1000
        assert result.detail["missing_rate"] == pytest.approx(count / 1000)
        assert result.detail["threshold"] == pytest.approx(0.2)


def test_check_computability_boundary_rejects():
    """缺失率 20.1% → LOW_COVERAGE（含算子不足窗口的 NaN，按全体 cell 计）。"""
    result = check_computability(_frame_with_missing(201))
    assert result.passed is False
    assert result.reason == ReasonCode.LOW_COVERAGE
    assert result.detail["missing_rate"] == pytest.approx(0.201)


def test_check_computability_all_missing_fails():
    """全 NaN（窗口远大于样本）→ LOW_COVERAGE（缺失率 1.0）。"""
    result = check_computability(pd.DataFrame(np.full((5, 4), np.nan)))
    assert result.passed is False
    assert result.reason == "LOW_COVERAGE"
    assert result.detail["missing_rate"] == pytest.approx(1.0)


def test_check_computability_empty_frame_fails_closed():
    """空面板（0 cell）→ LOW_COVERAGE（拿不到任何数据视为不可计算，不除零）。"""
    result = check_computability(pd.DataFrame())
    assert result.passed is False
    assert result.reason == "LOW_COVERAGE"
    assert result.detail["missing_rate"] == pytest.approx(1.0)
    assert result.detail["total_cells"] == 0


# ── 闸② 单因子检验（E3） ───────────────────────────────────────────────────


def _series_with_mean_and_std(mean: float, std: float) -> pd.Series:
    """均值 mean、样本标准差（ddof=1）std 的两点序列。"""
    half = std / np.sqrt(2.0)
    return pd.Series([mean - half, mean + half])


def test_check_single_factor_passes_at_threshold():
    """|ICIR| = 0.31（> 0.3）且均值 ≠ 0 → 通过；detail 暴露 mean/std/icir/n_samples。"""
    result = check_single_factor(_series_with_mean_and_std(0.031, 0.1))
    assert result.gate == 2
    assert result.passed is True
    assert result.reason is None
    assert result.detail["mean"] == pytest.approx(0.031)
    assert result.detail["std"] == pytest.approx(0.1)
    assert result.detail["icir"] == pytest.approx(0.31)
    assert result.detail["n_samples"] == 2


def test_check_single_factor_rejects_weak_icir():
    """|ICIR| = 0.29（< 0.3）→ WEAK_ICIR。"""
    result = check_single_factor(_series_with_mean_and_std(0.029, 0.1))
    assert result.passed is False
    assert result.reason == "WEAK_ICIR"
    assert result.detail["icir"] == pytest.approx(0.29)


def test_check_single_factor_rejects_zero_mean():
    """IC 均值 = 0（即使 |ICIR| 大）→ WEAK_ICIR（E3：|mean| > 0 硬条件）。"""
    result = check_single_factor(pd.Series([0.2, -0.2, 0.2, -0.2]))
    assert result.passed is False
    assert result.reason == "WEAK_ICIR"
    assert result.detail["mean"] == pytest.approx(0.0)


def test_check_single_factor_counts_nan_pairs_out():
    """NaN 不参与统计（dropna 后判定）——有效样本仅 2 且 ICIR 达标 → 通过。"""
    series = pd.Series([0.031 - 0.1 / np.sqrt(2.0), np.nan, 0.031 + 0.1 / np.sqrt(2.0)])
    result = check_single_factor(series)
    assert result.passed is True
    assert result.detail["n_samples"] == 2


def test_check_single_factor_negative_ic_with_strong_icir_passes():
    """E3 用 |ICIR|：强负 IC（|ICIR| ≥ 0.3 且均值 ≠ 0）同样通过（方向性由闸④⑤把关）。"""
    result = check_single_factor(_series_with_mean_and_std(-0.031, 0.1))
    assert result.passed is True
    assert result.detail["icir"] == pytest.approx(-0.31)


def test_check_single_factor_zero_std_is_zero_icir():
    """std=0（IC 常量）→ ICIR 记 0 → WEAK_ICIR（不产生 0/0、inf 或浮点残差巨值）。"""
    result = check_single_factor(pd.Series([0.05, 0.05, 0.05]))
    assert result.passed is False
    assert result.reason == "WEAK_ICIR"
    assert result.detail["std"] == pytest.approx(0.0)
    assert result.detail["icir"] == pytest.approx(0.0)


def test_check_single_factor_all_nan_rejected():
    """无有效样本 → WEAK_ICIR（icir 0、n_samples 0）。"""
    result = check_single_factor(pd.Series([np.nan, np.nan]))
    assert result.passed is False
    assert result.reason == "WEAK_ICIR"
    assert result.detail["icir"] == pytest.approx(0.0)
    assert result.detail["n_samples"] == 0


# ── admit_candidates 编排 ──────────────────────────────────────────────────


def test_admit_candidates_empty_batch(tmp_path):
    """空批次 → 空结果（不建库、不报错）。"""
    assert admit_candidates([], _signal_panel(), seed=1, db_path=_empty_db(tmp_path)) == []


def test_admit_candidates_rejects_structural_duplicate_before_insert(tmp_path):
    """结构同构（ts_mean(close,11) vs 库内 ts_mean(close,10)）→ 闸 0 拒收、不入库。"""
    db, seeded = _seed_library(tmp_path)
    results = admit_candidates(
        [_candidate("ts_mean(close,11)")], _signal_panel(), seed=1, db_path=db
    )
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, AdmissionResult)
    assert result.factor_id is None
    assert result.status == "rejected"
    assert result.rejected_gate == 0
    assert [gate.gate for gate in result.gates] == [0]
    gate = result.gates[0]
    assert gate.passed is False
    assert gate.reason == "STRUCTURAL_DUPLICATE"
    assert gate.detail["duplicate_of"] == seeded
    assert len(list_factors(db_path=db)) == 1  # 仅预置行，被拒候选不入库


def test_admit_candidates_rejects_low_coverage(tmp_path):
    """窗口远大于样本（缺失率 100%）→ 闸① LOW_COVERAGE、闸后不再评、不入库。"""
    db = _empty_db(tmp_path)
    results = admit_candidates(
        [_candidate("delta(close,200)")], _signal_panel(), seed=1, db_path=db
    )
    result = results[0]
    assert [gate.gate for gate in result.gates] == [0, 1]
    assert result.rejected_gate == 1
    assert result.gates[1].reason == "LOW_COVERAGE"
    assert result.gates[0].passed is True
    assert list_factors(db_path=db) == []


def test_admit_candidates_rejects_weak_icir(tmp_path):
    """IC 序列均值≈0（逐日反号）→ 闸② WEAK_ICIR、不入库。"""
    db = _empty_db(tmp_path)
    results = admit_candidates([_candidate("amount")], _signal_panel(), seed=1, db_path=db)
    result = results[0]
    assert [gate.gate for gate in result.gates] == [0, 1, 2]
    assert result.rejected_gate == 2
    assert result.gates[1].passed is True
    assert result.gates[2].reason == "WEAK_ICIR"
    assert result.gates[2].detail["n_samples"] >= 30
    assert list_factors(db_path=db) == []


def test_admit_candidates_admits_strong_factor_end_to_end(tmp_path):
    """强预测因子走完闸 0–5 → 入库 active；状态机轨迹 created→candidate→active。

    五道闸均为真实统计判定（T4/T5 已实装，占位桩全部删除）：本断言钉编排形状
    （闸编号序列 + 不被拒 + 落库状态），各闸判定细节由本文件各闸专测覆盖。
    """
    db = _empty_db(tmp_path)
    results = admit_candidates([_candidate("volume")], _signal_panel(), seed=1, db_path=db)
    result = results[0]
    assert result.rejected_gate is None
    assert result.status == "active"
    assert [gate.gate for gate in result.gates] == [0, 1, 2, 3, 4, 5]

    rows = list_factors(db_path=db)
    assert len(rows) == 1
    assert rows[0]["id"] == result.factor_id
    assert rows[0]["status"] == "active"
    assert rows[0]["ast_json"] == "volume"  # 缺省 ast_json 由表达式派生
    events = list_events(result.factor_id, db_path=db)
    assert [(event["from_status"], event["to_status"]) for event in events] == [
        (None, "candidate"),
        ("candidate", "active"),
    ]
    assert [row["expression"] for row in list_active_factors(db_path=db)] == ["volume"]


def test_admit_candidates_mixed_batch_preserves_order(tmp_path):
    """混合批次：输出与输入同序，逐条记录被拒闸口；仅通过者入 active。"""
    db, seeded = _seed_library(tmp_path)
    batch = [
        _candidate("ts_mean(close,11)"),  # 闸 0：结构同构
        _candidate("delta(close,200)"),  # 闸 1：缺失率 100%
        _candidate("amount"),  # 闸 2：IC 均值≈0
        _candidate("volume"),  # 全闸通过
    ]
    results = admit_candidates(batch, _signal_panel(), seed=11, db_path=db)
    assert [result.status for result in results] == [
        "rejected",
        "rejected",
        "rejected",
        "active",
    ]
    assert [result.rejected_gate for result in results] == [0, 1, 2, None]
    assert results[0].gates[0].detail["duplicate_of"] == seeded
    assert results[3].factor_id is not None
    assert len(list_factors(db_path=db)) == 2  # 预置行 + 通过者


# ── 闸③ 置换检验 + BH-FDR（E4，T4） ────────────────────────────────────────


def _ic_series(values: list[float]) -> pd.Series:
    """合成 IC 序列（索引为规范交易日；值与列表同序）。"""
    dates = [
        (date(2026, 1, 5) + timedelta(days=offset)).isoformat() for offset in range(len(values))
    ]
    return pd.Series(values, index=dates, dtype="float64")


def _strong_ic_series(*, days: int = 80, mean: float = 0.08, noise: float = 0.01) -> pd.Series:
    """强信号 IC 序列：均值远离 0、逐日波动小（置换零分布不可能达到观测均值）。"""
    rng = np.random.default_rng(4242)
    return _ic_series(list(mean + noise * rng.normal(size=days)))


def _noise_ic_series(*, days: int = 80, noise: float = 0.15) -> pd.Series:
    """纯噪声 IC 序列：均值≈0（单因子日 IC 抽样噪声量级 1/sqrt(40)≈0.16）。"""
    rng = np.random.default_rng(20261014)
    return _ic_series(list(noise * rng.normal(size=days)))


def test_check_significance_same_seed_bitwise_reproducible():
    """同种子两次运行：p 值逐位相等（==，非 approx）；不同种子改变置换抽样。"""
    series_map = {"noise(a)": _noise_ic_series(), "noise(b)": _noise_ic_series(days=60)}
    first = check_significance(series_map, seed=7)
    second = check_significance(series_map, seed=7)
    assert set(first) == set(series_map)
    for key in series_map:
        assert first[key].gate == 3
        assert first[key].detail["p_value"] == second[key].detail["p_value"]  # 逐位
        assert first[key].detail["p_adj"] == second[key].detail["p_adj"]
        assert first[key].passed is second[key].passed
    other = check_significance(series_map, seed=8)
    assert any(
        other[key].detail["p_value"] != first[key].detail["p_value"] for key in series_map
    ), "换种子后置换抽样应改变 p 值（否则 seed 未真正参与）"


def test_check_significance_strong_signal_passes():
    """强信号 IC：置换零分布远达不到观测均值 → p = 1/(1+N)、p_adj ≤ q、通过。"""
    result = check_significance({"strong": _strong_ic_series()}, seed=3)["strong"]
    assert result.passed is True
    assert result.reason is None
    assert result.detail["p_value"] == pytest.approx(1.0 / (1.0 + PERMUTATION_N))
    assert result.detail["p_adj"] == pytest.approx(result.detail["p_value"])
    assert result.detail["n_permutations"] == PERMUTATION_N
    assert result.detail["n_samples"] == 80
    assert result.detail["observed_mean"] == pytest.approx(0.08, abs=0.01)


def test_check_significance_pure_noise_rejected():
    """纯噪声 IC（均值≈0，量级 = 逐日抽样噪声）→ p 大 → NOT_SIGNIFICANT。"""
    result = check_significance({"noise": _noise_ic_series()}, seed=5)["noise"]
    assert result.passed is False
    assert result.reason == ReasonCode.NOT_SIGNIFICANT
    assert result.detail["p_value"] > 0.1
    assert result.detail["p_adj"] == pytest.approx(result.detail["p_value"])  # 单候选不校正


def test_check_significance_all_nan_series_rejected():
    """全 NaN IC 序列（无有效样本）→ p 记 1.0 → NOT_SIGNIFICANT（fail closed）。"""
    result = check_significance({"empty": _ic_series([np.nan, np.nan])}, seed=1)["empty"]
    assert result.passed is False
    assert result.reason == "NOT_SIGNIFICANT"
    assert result.detail["p_value"] == pytest.approx(1.0)
    assert result.detail["n_samples"] == 0


def test_check_significance_empty_map_returns_empty():
    """空批次 → 空结果（BH 无输入）。"""
    assert check_significance({}, seed=1) == {}


def test_benjamini_hochberg_hand_computed_case():
    """手算小样本对照（K=4）：调整 p 值 = 后缀最小值 K·p_(j)/j，排序序上单调不减。

    原 p = {a:0.01, b:0.04, c:0.03, d:0.5} → 排序 (a, c, b, d)；
    K·p/j = (0.04, 0.06, 0.0533…, 0.5)，取后缀最小 → (0.04, 0.0533…, 0.0533…, 0.5)
    （c 被 b 的 0.0533 压住，正是 step-up 上凸包的体现）。
    """
    adjusted = benjamini_hochberg_adjust({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.5})
    assert adjusted["a"] == pytest.approx(0.04)
    assert adjusted["c"] == pytest.approx(4 * 0.04 / 3)
    assert adjusted["b"] == pytest.approx(4 * 0.04 / 3)
    assert adjusted["d"] == pytest.approx(0.5)
    ordered = [adjusted[key] for key in ("a", "c", "b", "d")]
    assert all(left <= right for left, right in pairwise(ordered))


def test_benjamini_hochberg_monotone_over_fifty_p_values():
    """50 个 p 值批量：调整值单调不减、≥ 原值、≤ 1（BH step-up 上凸包）。"""
    rng = np.random.default_rng(99)
    raw = {f"p{index:02d}": float(value) for index, value in enumerate(rng.uniform(size=50))}
    adjusted = benjamini_hochberg_adjust(raw)
    assert set(adjusted) == set(raw)
    ordered_keys = sorted(raw, key=lambda key: raw[key])
    ordered = [adjusted[key] for key in ordered_keys]
    assert all(left <= right for left, right in pairwise(ordered))
    assert all(adjusted[key] >= raw[key] - 1e-12 for key in raw)
    assert all(0.0 <= adjusted[key] <= 1.0 for key in raw)


def test_benjamini_hochberg_empty_and_single():
    """边界：空输入 → 空输出；单候选调整值 = 原 p（K=1，无多重性）。"""
    assert benjamini_hochberg_adjust({}) == {}
    assert benjamini_hochberg_adjust({"only": 0.03})["only"] == pytest.approx(0.03)


# ── 评审修正：BH 分辨率 / K>20 批量 / 位置键多重性（fix 1/8/9） ──────────────


def _spike_ic_series(*, days: int, spike: float = 1.0) -> pd.Series:
    """尖峰 IC 序列（仅最后一日有幅度）：过闸②但置换检验必不显著（超额数 = N）。

    ``[0]×(days−1) + [spike]`` 的 ``|ICIR|`` = 0.316–0.5（≥ 0.3）且均值 > 0 → 过闸②；
    其余日幅度为 0 → 任一符号翻转都不改变 |置换均值|（恒 = 观测均值）→ 超额数 = N、
    p = 1.0（超额数被构造完全钉死，不依赖抽样运气）。
    """
    return _ic_series([0.0] * (days - 1) + [spike])


def _noise_batch(count: int, *, days: int = 80, seed: int = 303) -> dict[str, pd.Series]:
    """``count`` 条互不相同的纯噪声 IC 序列（固定种子；均值≈0 → p ≈ 0.5）。"""
    rng = np.random.default_rng(seed)
    return {
        f"noise-{index:02d}": _ic_series(list(0.15 * rng.normal(size=days)))
        for index in range(count)
    }


def test_check_significance_k50_one_strong_candidate_passes_bh():
    """fix 1：K=50 批量——零超额强候选 p_adj = 50/(N+1) = 0.05 ≤ 0.1 通过。

    回归钉：``PERMUTATION_N`` 分辨率必须覆盖 ``K ≤ q·(N+1)``；旧值 N=200 时
    p 值下界 1/201 → p_adj ≥ 50/201 ≈ 0.249 > 0.1，完美候选也必然被拒。
    """
    series_map = {"strong": _strong_ic_series(), **_noise_batch(49)}
    results = check_significance(series_map, seed=17)
    strong = results["strong"]
    assert strong.passed is True
    assert strong.reason is None
    assert strong.detail["exceedances"] == 0
    assert strong.detail["n_hypotheses"] == 50
    assert strong.detail["p_adj"] == pytest.approx(50.0 / (PERMUTATION_N + 1))
    assert strong.detail["p_adj"] <= BH_FDR_Q
    rejected = [key for key in series_map if key != "strong" and not results[key].passed]
    assert len(rejected) == 49  # 同批噪声候选取 p_adj ≫ q 被拒（BH 只放行强候选）


def test_check_significance_k50_all_noise_all_rejected():
    """fix 1：K=50 全噪声批量 → p_adj 全部 > q（无候选因分辨率不足/校正错误放行）。"""
    series_map = _noise_batch(50, seed=404)
    results = check_significance(series_map, seed=18)
    assert len(results) == 50
    for key, result in results.items():
        assert result.passed is False, key
        assert result.reason == ReasonCode.NOT_SIGNIFICANT
        assert result.detail["n_hypotheses"] == 50
        assert result.detail["p_adj"] > BH_FDR_Q


def test_check_significance_k25_gate2_survivors_only_strong_passes():
    """fix 8：K=25 批量（闸②存活者，逐条先验 ``check_single_factor`` 通过）端到端。

    24 条尖峰序列（超额数 = N、p = 1.0）+ 1 条强信号（0 超额）→ BH 只放行强候选；
    整批在旧 N=200（p 值下界 1/201 而 K > 20.1）下强候选也必被拒——本用例即该回归的
    端到端钉子（K>20 大批次的分辨率要求）。
    """
    series_map: dict[str, pd.Series] = {"strong": _strong_ic_series()}
    for index, days in enumerate((10, 9, 8, 6) * 6):
        series_map[f"spike-{index:02d}"] = _spike_ic_series(days=days)
    assert len(series_map) == 25
    for key, series in series_map.items():  # 批量 = 闸①②存活者（②逐条验证）
        gate2 = check_single_factor(series)
        assert gate2.passed is True, (key, gate2.detail)
    results = check_significance(series_map, seed=19)
    strong = results["strong"]
    assert strong.passed is True
    assert strong.detail["p_adj"] == pytest.approx(25.0 / (PERMUTATION_N + 1))
    assert strong.detail["p_adj"] <= BH_FDR_Q
    spikes = [key for key in series_map if key.startswith("spike-")]
    assert len(spikes) == 24
    for key in spikes:
        assert results[key].passed is False, key
        assert results[key].reason == "NOT_SIGNIFICANT"
        assert results[key].detail["exceedances"] == PERMUTATION_N
        assert results[key].detail["p_adj"] == pytest.approx(1.0)


def test_check_significance_guard_raises_when_resolution_cannot_reject(monkeypatch):
    """fix 1：分辨率不足（p 值下界 > q/K）→ 抛清晰错误（fail loud，消息含两侧数值）。

    N=50 → 下界 1/51 ≈ 0.01961：K=5 时阈值 q/K = 0.02（不触发）；K=6 时 0.01667
    （触发）——边界两侧都钉住。
    """
    monkeypatch.setattr("finance_agent.factors.validate.PERMUTATION_N", 50)
    quiet = {f"n{index}": _ic_series([0.01, -0.01]) for index in range(5)}
    assert len(check_significance(quiet, seed=1)) == 5  # 1/51 ≤ q/5：分辨率仍可拒收
    loud = {f"n{index}": _ic_series([0.01, -0.01]) for index in range(6)}
    with pytest.raises(ValueError) as excinfo:
        check_significance(loud, seed=1)
    message = str(excinfo.value)
    assert "1/(PERMUTATION_N+1)" in message and "BH_FDR_Q/K" in message  # 不等式两侧
    assert "PERMUTATION_N=50" in message and "K=6" in message


def test_check_significance_guard_boundary_default_resolution_covers_k100():
    """fix 1：默认 N=999 覆盖 K ≤ 100（K=100 不触发；K=101 触发守卫）。"""
    at_capacity = {f"c{index:03d}": _ic_series([0.02, -0.02]) for index in range(100)}
    assert len(check_significance(at_capacity, seed=1)) == 100
    over = {f"c{index:03d}": _ic_series([0.02, -0.02]) for index in range(101)}
    with pytest.raises(ValueError, match="分辨率不足"):
        check_significance(over, seed=1)


def test_admit_candidates_duplicate_expressions_are_separate_hypotheses(tmp_path):
    """fix 9：同批逐字重复表达式按候选**位置**各计一条假设（不塌缩）。

    两个 volume（逐字重复）：闸③ 收到 2 条假设（``n_hypotheses == 2``；表达式为键时
    会塌缩成 1），各取独立抽样下标；先入库者为 active，后到者被批内先入库者拦下。
    """
    db = _empty_db(tmp_path)
    results = admit_candidates(
        [_candidate("volume"), _candidate("volume")], _signal_panel(), seed=1, db_path=db
    )
    first = results[0]
    assert first.status == "active"
    gate3 = next(gate for gate in first.gates if gate.gate == 3)
    assert gate3.detail["n_hypotheses"] == 2  # 位置键：重复表达式各占一条假设
    assert gate3.detail["p_adj"] <= BH_FDR_Q
    second = results[1]
    assert second.status == "rejected"
    assert second.rejected_gate is not None
    second_gate3 = next(gate for gate in second.gates if gate.gate == 3)
    assert second_gate3.detail["n_hypotheses"] == 2  # 同批假设数（映射回各自位置）
    assert len([row for row in list_factors(db_path=db) if row["status"] == "active"]) == 1


# ── 50 随机表达式批量注入（spec 场景：入库 < 5） ─────────────────────────────

_NOISE_EXPRESSION_SEED = 20261015
_NOISE_BATCH_SIZE = 50
_RANDOM_OPS: tuple[str, ...] = (
    "ts_mean",
    "ts_std",
    "ts_rank",
    "delta",
    "rank",
    "zscore",
    "add",
    "sub",
    "mul",
    "div",
    "max",
    "min",
    "abs",
    "log",
)
_RANDOM_FIELDS: tuple[str, ...] = ("open", "high", "low", "close", "volume", "amount")


def _random_node(rng: np.random.Generator, depth: int = 0) -> AstNode:
    """随机表达式节点（深度 ≤ 3、自由参数 ≤ 2、字段 ≤ 2——恒在复杂度上限内）。"""
    if depth >= 2 or rng.random() < 0.45:
        return Field(str(rng.choice(_RANDOM_FIELDS)))
    op = str(rng.choice(_RANDOM_OPS))
    if op in ("ts_mean", "ts_std", "ts_rank", "delta"):
        return Call(op, (_random_node(rng, depth + 1), Number(int(rng.integers(2, 9)))))
    if op == "ts_corr":  # 不在 _RANDOM_OPS 内，保留分支以便后续扩展
        return Call(op, (_random_node(rng, depth + 1), _random_node(rng, depth + 1), Number(5)))
    if op in ("rank", "zscore", "abs", "log"):
        return Call(op, (_random_node(rng, depth + 1),))
    return Call(op, (_random_node(rng, depth + 1), _random_node(rng, depth + 1)))


def _random_expressions(count: int, *, seed: int) -> list[str]:
    """生成 ``count`` 条**结构互不同构**的随机表达式（固定种子可复现）。

    同构去重是必须的：``is_subtree_isomorphic`` 忽略数字取值，同形状表达式
    （如 ``ts_mean(close,3)`` 与 ``ts_mean(close,7)``）会在闸 0 直接被查重拒收，
    批量注入就测不到闸③。
    """
    rng = np.random.default_rng(seed)
    expressions: list[str] = []
    nodes: list[AstNode] = []
    for _ in range(count * 200):
        if len(expressions) >= count:
            break
        node = _random_node(rng)
        if any(is_subtree_isomorphic(node, other) for other in nodes):
            continue
        nodes.append(node)
        expressions.append(serialize(node))
    assert len(expressions) == count, "随机表达式生成器未产出足量互不同构样本"
    return expressions


def _noise_market_panel(*, dates: int = 120, codes: int = 60, seed: int = 20261016) -> MarketPanel:
    """纯噪声合成面板（价格随机游走、量额独立；零真实信号、零网络）。"""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0, 0.02, size=(dates, codes))
    close = 100.0 * np.cumprod(1.0 + returns, axis=0)
    volume = rng.lognormal(0.0, 0.5, size=(dates, codes))
    amount = volume * close * 100.0
    day_labels = [
        (date(2026, 1, 5) + timedelta(days=offset)).isoformat() for offset in range(dates)
    ]
    code_labels = [f"n{index:03d}" for index in range(codes)]

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=day_labels, columns=code_labels)

    return MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(volume),
            "amount": frame(amount),
        }
    )


def test_admit_candidates_fifty_random_expressions_admits_under_five(tmp_path):
    """spec 场景：50 个随机表达式注入纯噪声面板（固定种子）→ 入库 active < 5。

    闸③ 的 BH-FDR 只在闸①②存活者上校正（编排契约）；纯噪声面板上 50 条随机
    表达式几乎全部止步闸②（|ICIR| ≥ 0.3 的抽样概率 ≈ 0.1%），入库数断言 < 5。
    """
    expressions = _random_expressions(_NOISE_BATCH_SIZE, seed=_NOISE_EXPRESSION_SEED)
    candidates = [_candidate(expression, category="价量") for expression in expressions]
    results = admit_candidates(
        candidates, _noise_market_panel(), seed=_NOISE_EXPRESSION_SEED, db_path=_empty_db(tmp_path)
    )
    assert len(results) == _NOISE_BATCH_SIZE
    active = [result for result in results if result.status == "active"]
    assert len(active) < 5, [result.factor_id for result in active]
    # 被拒者必带被拒闸口编号（spec：每个被拒因子记录被拒闸口）
    rejected = [result for result in results if result.status == "rejected"]
    assert len(rejected) == _NOISE_BATCH_SIZE - len(active)
    assert all(result.rejected_gate is not None for result in rejected)


# ── 闸④ 池级增量 + 闸⑤ 样本外（E5/E6，T5） ─────────────────────────────────


def _indexed_ic(values: np.ndarray, *, start: str = "2026-03-02") -> pd.Series:
    """ndarray → 规范交易日索引的 IC 序列（升序、逐日）。"""
    dates = [
        (date.fromisoformat(start) + timedelta(days=offset)).isoformat()
        for offset in range(values.size)
    ]
    return pd.Series(values, index=dates, dtype="float64")


def _correlated_ic(base: np.ndarray, *, rho: float, seed: int) -> np.ndarray:
    """与 ``base`` 的 Pearson 相关恰为 ``rho`` 的同长序列（正交化后按比例合成）。"""
    rng = np.random.default_rng(seed)
    a = base - base.mean()
    a = a / np.sqrt((a**2).sum())
    b = rng.normal(size=base.size)
    b = b - b.mean()
    b = b - (b * a).sum() * a  # 与 a 正交
    b = b / np.sqrt((b**2).sum())
    return rho * a + np.sqrt(1.0 - rho**2) * b


def _pool_panel(
    *,
    dates: int = 60,
    codes: int = 40,
    seed: int = 777,
    beta_volume: float = 0.03,
    beta_amount: float = 0.0,
    noise: float = 0.005,
) -> MarketPanel:
    """次日收益由 volume/amount 的横截面 zscore 驱动（系数可调）的合成面板。

    ``beta_amount=0`` 时 amount 与收益无关（加入池只会稀释组合 IR → 闸④拒收）。
    """
    rng = np.random.default_rng(seed)
    volume = rng.normal(size=(dates, codes))
    amount = rng.normal(size=(dates, codes))
    z_volume = (volume - volume.mean(axis=1, keepdims=True)) / volume.std(
        axis=1, ddof=1, keepdims=True
    )
    z_amount = (amount - amount.mean(axis=1, keepdims=True)) / amount.std(
        axis=1, ddof=1, keepdims=True
    )
    returns = np.zeros((dates, codes))
    returns[1:] = (
        beta_volume * z_volume[:-1]
        + beta_amount * z_amount[:-1]
        + noise * rng.normal(size=(dates - 1, codes))
    )
    close = 100.0 * np.cumprod(1.0 + returns, axis=0)
    day_labels = [
        (date(2026, 2, 2) + timedelta(days=offset)).isoformat() for offset in range(dates)
    ]
    code_labels = [f"p{index:03d}" for index in range(codes)]

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=day_labels, columns=code_labels)

    return MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(volume),
            "amount": frame(amount),
        }
    )


def _oos_increment_panel(*, dates: int = 60, codes: int = 40, seed: int = 999) -> MarketPanel:
    """闸④样本外用例面板：amount 信号仅存在于训练段，验证段只由 volume 承载。

    次日收益 ``r_{t+1} = 0.03·(z_volume,t + z_amount,t·[t 在训练段]) + 0.05·ε``
    （验证段 = 日期排序后 30%）。实测：全样本 |IC 相关| ≈ 0.13（冗余闸不误伤）；
    训练段加入 amount 有正增量（+2.46），验证段 amount 无信号（只稀释组合，−1.40）
    → 旧实现（全样本增量）放行、新实现（验证段增量）拒收。
    """
    rng = np.random.default_rng(seed)
    volume = rng.normal(size=(dates, codes))
    amount = rng.normal(size=(dates, codes))
    z_volume = (volume - volume.mean(axis=1, keepdims=True)) / volume.std(
        axis=1, ddof=1, keepdims=True
    )
    z_amount = (amount - amount.mean(axis=1, keepdims=True)) / amount.std(
        axis=1, ddof=1, keepdims=True
    )
    cut = int(dates * TRAIN_FRACTION)
    signal = np.zeros((dates - 1, codes))
    for step in range(dates - 1):
        signal[step] = 0.03 * z_volume[step]
        if step < cut:
            signal[step] = signal[step] + 0.03 * z_amount[step]
    returns = np.zeros((dates, codes))
    returns[1:] = signal + 0.05 * rng.normal(size=(dates - 1, codes))
    close = 100.0 * np.cumprod(1.0 + returns, axis=0)
    day_labels = [
        (date(2026, 2, 2) + timedelta(days=offset)).isoformat() for offset in range(dates)
    ]
    code_labels = [f"g{index:03d}" for index in range(codes)]

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=day_labels, columns=code_labels)

    return MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(volume),
            "amount": frame(amount),
        }
    )
    day_labels = [
        (date(2026, 2, 2) + timedelta(days=offset)).isoformat() for offset in range(dates)
    ]
    code_labels = [f"g{index:03d}" for index in range(codes)]

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=day_labels, columns=code_labels)

    return MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(volume),
            "amount": frame(amount),
        }
    )


def _factor_ic(expression: str, panel: MarketPanel) -> pd.Series:
    """表达式 → 该面板上的 IC 序列（与被测编排同一求值路径）。"""
    return rank_ic_series(
        evaluate(parse(expression), panel), forward_returns(panel.fields["close"])
    )


def _pool_row(factor_id: int, expression: str) -> dict:
    """池行（与 library.list_factors 行同形的最小字段集）。"""
    return {
        "id": factor_id,
        "expression": expression,
        "ast_json": serialize(parse(expression)),
        "category": "价量",
        "status": "active",
    }


def test_check_pool_increment_redundant_with_pool_records_offender(tmp_path):
    """|IC 相关| = 0.92 > 0.7 → REDUNDANT_WITH_POOL，detail 记相关对象 id 与 ρ。"""
    db = _empty_db(tmp_path)
    panel = _pool_panel()
    factor_id = add_factor(
        expression="volume",
        hypothesis="测试用经济学假设：成交额与未来收益存在关系。",
        category="价量",
        status="active",
        ast_json=serialize(parse("volume")),
        db_path=db,
    )
    base = np.random.default_rng(31).normal(size=80)
    pool_ic = _indexed_ic(base)
    for trade_date, value in pool_ic.items():
        record_ic(factor_id, str(trade_date), float(value), db_path=db)
    candidate_ic = _indexed_ic(_correlated_ic(base, rho=0.92, seed=32))
    result = check_pool_increment(
        candidate_ic,
        evaluate(parse("amount"), panel),
        [_pool_row(factor_id, "volume")],
        panel,
        db_path=db,
    )
    assert result.gate == 4
    assert result.passed is False
    assert result.reason == ReasonCode.REDUNDANT_WITH_POOL
    assert result.detail["duplicate_of"] == factor_id
    assert result.detail["correlation"] == pytest.approx(0.92, abs=1e-9)
    assert result.detail["threshold"] == pytest.approx(0.7)


def test_check_pool_increment_redundancy_uses_panel_ic_when_pool_has_no_history(tmp_path):
    """池内因子无落库 IC（本批次刚入库）→ 由面板现算 IC 做冗余比对（ρ=1 命中）。"""
    db = _empty_db(tmp_path)
    panel = _pool_panel()
    factor_id = add_factor(
        expression="volume",
        hypothesis="测试用经济学假设：成交额与未来收益存在关系。",
        category="价量",
        status="active",
        ast_json=serialize(parse("volume")),
        db_path=db,
    )
    assert list_ic_series(factor_id, db_path=db) == []
    result = check_pool_increment(
        _factor_ic("volume", panel),
        evaluate(parse("volume"), panel),
        [_pool_row(factor_id, "volume")],
        panel,
        db_path=db,
    )
    assert result.passed is False
    assert result.reason == "REDUNDANT_WITH_POOL"
    assert result.detail["duplicate_of"] == factor_id
    assert result.detail["correlation"] == pytest.approx(1.0)


def test_check_pool_increment_rejects_no_pool_increment(tmp_path):
    """候选对池组合无增量（噪声因子稀释组合 IR）→ NO_POOL_INCREMENT。"""
    db = _empty_db(tmp_path)
    panel = _pool_panel(beta_amount=0.0)
    result = check_pool_increment(
        _factor_ic("amount", panel),
        evaluate(parse("amount"), panel),
        [_pool_row(1, "volume")],
        panel,
        db_path=db,
    )
    assert result.passed is False
    assert result.reason == ReasonCode.NO_POOL_INCREMENT
    assert result.detail["increment"] <= 0.0
    assert result.detail["ir_pool"] > result.detail["ir_pool_with_candidate"]
    assert result.detail["pool_size"] == 1


def test_check_pool_increment_accepts_positive_increment(tmp_path):
    """候选给池组合带来正 IR 增量 → 通过；detail 必含 increment: float（E8 保种依赖）。

    判定量为**验证段**增量（fix 2）；训练段增量同号 > 0（两段皆为真实信号载荷）。
    """
    db = _empty_db(tmp_path)
    panel = _pool_panel(beta_volume=0.02, beta_amount=0.02)
    result = check_pool_increment(
        _factor_ic("amount", panel),
        evaluate(parse("amount"), panel),
        [_pool_row(1, "volume")],
        panel,
        db_path=db,
    )
    assert result.passed is True
    assert result.reason is None
    increment = result.detail["increment"]
    assert isinstance(increment, float)
    assert increment > 0.0
    assert result.detail["increment_train"] > 0.0
    assert result.detail["ir_pool_with_candidate"] == pytest.approx(
        result.detail["ir_pool"] + increment, abs=1e-12
    )


def test_check_pool_increment_empty_pool_uses_candidate_ir(tmp_path):
    """空池语义：增量 = IR(候选)（定向后，与闸② |ICIR| 同向重复但保留判定）。

    fix-pool-increment-orientation 起「负 IR 必拒」旧语义由符号归一化取代：**真实**镜像
    候选（IC 与值取自同一表达式）按训练段符号定向后同样通过——旧用例拆开 IC 与值
    （IC 取负、值不变）的构造不再是生产可达形态（admit_candidates 恒以 ic = IC(values) 调用）。
    """
    db = _empty_db(tmp_path)
    panel = _pool_panel()
    candidate_ic = _factor_ic("volume", panel)
    passed = check_pool_increment(
        candidate_ic, evaluate(parse("volume"), panel), [], panel, db_path=db
    )
    assert passed.passed is True
    assert passed.detail["pool_size"] == 0
    assert passed.detail["ir_pool"] is None
    assert passed.detail["orientation"] == 1
    assert passed.detail["increment"] == pytest.approx(passed.detail["ir_pool_with_candidate"])
    assert passed.detail["increment"] > 0.0

    mirror_values = evaluate(parse("sub(0, volume)"), panel)
    mirror_ic = rank_ic_series(mirror_values, forward_returns(panel.fields["close"]))
    mirror = check_pool_increment(mirror_ic, mirror_values, [], panel, db_path=db)
    assert mirror.passed is True
    assert mirror.detail["orientation"] == -1
    assert mirror.detail["increment"] == pytest.approx(passed.detail["increment"], rel=1e-9)


def test_check_pool_increment_judges_on_validation_segment(tmp_path):
    """fix 2：闸④判据 = 验证段增量（样本外）——样本内强但样本外平坦 → NO_POOL_INCREMENT。

    面板见 ``_oos_increment_panel``：训练段 volume/amount 交替承载信号（候选样本内
    增量 > 0），验证段只由 volume 承载（amount 无信号）→ 候选只稀释组合 →
    验证段增量 ≤ 0 → 拒收。detail 同时记录训练/验证两段增量（判定取验证段）。
    """
    db = _empty_db(tmp_path)
    panel = _oos_increment_panel()
    result = check_pool_increment(
        _factor_ic("amount", panel),
        evaluate(parse("amount"), panel),
        [_pool_row(1, "volume")],
        panel,
        db_path=db,
    )
    assert result.passed is False
    assert result.reason == ReasonCode.NO_POOL_INCREMENT
    assert result.detail["increment_train"] > 0.0  # 样本内强（旧实现在此放行）
    assert result.detail["increment"] <= 0.0  # 样本外平坦：判定量
    assert result.detail["increment"] == pytest.approx(
        result.detail["ir_pool_with_candidate"] - result.detail["ir_pool"], abs=1e-12
    )


def test_check_pool_increment_insufficient_oos_dates_fails_closed(tmp_path):
    """fix 2：验证段有效日 < ``MIN_OOS_IR_SAMPLES``（IR 不稳）→ 拒收且记原因。"""
    db = _empty_db(tmp_path)
    panel = _pool_panel(dates=12)  # 切点 8 → 验证段仅 4 日（其中末日 fwd 为 NaN）
    result = check_pool_increment(
        _factor_ic("amount", panel),
        evaluate(parse("amount"), panel),
        [_pool_row(1, "volume")],
        panel,
        db_path=db,
    )
    assert result.passed is False
    assert result.reason == ReasonCode.NO_POOL_INCREMENT
    assert result.detail["reason"] == "insufficient_oos_dates"
    assert result.detail["n_val_days"] < MIN_OOS_IR_SAMPLES
    assert result.detail["min_oos_days"] == MIN_OOS_IR_SAMPLES


def test_check_pool_increment_boundary_matches_gate5_split(tmp_path):
    """fix 2：闸④验证段边界 = 闸⑤同一切分（``TRAIN_FRACTION``，切点不跨日）。"""
    db = _empty_db(tmp_path)
    panel = _pool_panel(beta_volume=0.02, beta_amount=0.02)
    candidate_values = evaluate(parse("amount"), panel)
    result = check_pool_increment(
        _factor_ic("amount", panel), candidate_values, [_pool_row(1, "volume")], panel, db_path=db
    )
    fwd = forward_returns(panel.fields["close"])
    train_dates, val_dates = train_validation_split(candidate_values.index.intersection(fwd.index))
    assert result.detail["train_fraction"] == TRAIN_FRACTION
    assert result.detail["train_end"] == train_dates[-1]
    assert result.detail["val_start"] == val_dates[0]
    assert result.detail["val_end"] == val_dates[-1]
    assert 0 < result.detail["n_val_days"] <= len(val_dates)
    assert result.detail["n_train_days"] > 0


def test_check_pool_increment_redundancy_sample_floor_is_five(tmp_path):
    """fix 6：冗余比对的最少共同日阈值 = ``MIN_REDUNDANCY_SAMPLES``（单一来源 = 5）。

    4 点共同日（2 点相关恒 ±1 → 不可信）→ 该对象跳过（``redundancy_skipped``）；
    5 点起进入比对（``redundancy_checked``）。
    """
    db = _empty_db(tmp_path)
    panel = _pool_panel(dates=40, beta_volume=0.02)
    factor_id = add_factor(
        expression="volume",
        hypothesis="测试用经济学假设：成交额与未来收益存在关系。",
        category="价量",
        status="active",
        ast_json=serialize(parse("volume")),
        db_path=db,
    )
    dates = [(date(2026, 3, 2) + timedelta(days=offset)).isoformat() for offset in range(5)]
    candidate_ic = _indexed_ic(np.array([0.01, -0.02, 0.03, -0.01, 0.02]), start=dates[0])
    for trade_date, value in zip(dates[:4], [0.01, -0.02, 0.03, -0.01], strict=True):
        record_ic(factor_id, trade_date, value, db_path=db)
    skipped = check_pool_increment(
        candidate_ic.iloc[:4],
        evaluate(parse("amount"), panel),
        [_pool_row(factor_id, "volume")],
        panel,
        db_path=db,
    )
    assert skipped.detail["redundancy_skipped"] == [factor_id]
    assert skipped.detail["redundancy_checked"] == []
    record_ic(factor_id, dates[4], 0.02, db_path=db)
    checked = check_pool_increment(
        candidate_ic,
        evaluate(parse("amount"), panel),
        [_pool_row(factor_id, "volume")],
        panel,
        db_path=db,
    )
    assert checked.detail["redundancy_checked"] == [factor_id]
    assert checked.detail["redundancy_skipped"] == []


# ── 闸⑤ 样本外复核（E6） ───────────────────────────────────────────────────


def _split_probe(dates: list[str]) -> tuple[list[str], list[str]]:
    """测试侧独立复核切分（不复用被测实现的期望生成）。"""
    ordered = sorted(dates)
    cut = int(len(ordered) * 0.7)
    return ordered[:cut], ordered[cut:]


def _oos_frames(*, dates: int = 200, codes: int = 40, mode: str, seed: int = 55):
    """构造 (factor_values, fwd)：按日期段控制信号强度/符号。"""
    rng = np.random.default_rng(seed)
    factor = rng.normal(size=(dates, codes))
    z = (factor - factor.mean(axis=1, keepdims=True)) / factor.std(axis=1, ddof=1, keepdims=True)
    cut = int(dates * 0.7)
    signal = z.copy()
    if mode == "train_only":
        signal[cut:] = 0.0
    elif mode == "sign_flip":
        signal[cut:] = -z[cut:]
    returns = 0.03 * signal + 0.005 * rng.normal(size=(dates, codes))
    labels = [(date(2026, 4, 1) + timedelta(days=offset)).isoformat() for offset in range(dates)]
    code_labels = [f"o{index:03d}" for index in range(codes)]
    values = pd.DataFrame(factor, index=labels, columns=code_labels)
    fwd = pd.DataFrame(returns, index=labels, columns=code_labels)
    return values, fwd


def _rank_shift_frames(shifts: list[int], *, codes: int = 40) -> tuple[pd.DataFrame, pd.DataFrame]:
    """逐日「秩平移」构造（每日 Spearman 精确已知、无抽样噪声）。

    因子值恒为 ``0..codes−1``（逐日相同；本用例直接调用闸⑤，不经闸①）；
    收益 = ``np.roll(values, −k)`` → 当日 Spearman 恒为 ``1 − 6k(n−k)/(n²−1)``。
    """
    values = np.arange(codes, dtype="float64")
    labels = [
        (date(2026, 6, 1) + timedelta(days=offset)).isoformat() for offset in range(len(shifts))
    ]
    code_labels = [f"q{index:03d}" for index in range(codes)]
    return (
        pd.DataFrame(np.tile(values, (len(shifts), 1)), index=labels, columns=code_labels),
        pd.DataFrame(
            np.stack([np.roll(values, -shift) for shift in shifts]),
            index=labels,
            columns=code_labels,
        ),
    )


def test_train_validation_split_partitions_dates_without_overlap():
    """切分点不跨日：train/val 无交集、并集 = 全区间、均升序、前 70% 为训练段。"""
    dates = [(date(2026, 4, 1) + timedelta(days=offset)).isoformat() for offset in range(61)]
    train, val = train_validation_split(dates)
    assert len(train) == 42 and len(val) == 19
    assert set(train).isdisjoint(val)
    assert sorted(train + val) == sorted(dates)
    assert train == sorted(train) and val == sorted(val)
    assert max(train) < min(val)  # 切点按日期序，不跨日
    assert (train, val) == _split_probe(dates)


def test_check_out_of_sample_passes_when_both_segments_strong():
    """训练/验证两段皆强且同号 → 通过；detail 记切分区间与两段 ICIR。

    fix 4：训练段末端 ``DEFAULT_IC_HORIZON`` 日已剔除（边界泄漏），``train_days``
    仍记切分天数、``purged_train_days`` 记被剔除天数。
    """
    values, fwd = _oos_frames(mode="both_strong")
    result = check_out_of_sample(values, fwd)
    assert result.gate == 5
    assert result.passed is True
    assert result.reason is None
    assert result.detail["icir_val"] > 0.3
    assert result.detail["train_days"] == 140 and result.detail["val_days"] == 60
    assert result.detail["purged_train_days"] == DEFAULT_IC_HORIZON
    assert result.detail["val_start"] > result.detail["train_end"]


def test_check_out_of_sample_purges_boundary_train_dates():
    """fix 4：训练段末端 horizon 日的远期收益跨入验证段 → 剔除后再算训练统计量。

    构造（秩平移，逐日 Spearman 精确）：训练段 14 日 = 7 日 ρ=−0.501 + 6 日 ρ=+0.460
    + 末 1 日 ρ=+1.0（边界日）；验证段 6 日 ρ≈−0.47（强负、|ICIR| ≥ 0.3）。
    **不剔除**时训练均值 = +0.2514/14 > 0 → 与验证段符号相反（错误拒收）；
    **剔除**后训练均值 = −0.7486/13 < 0 → 同号通过。
    """
    shifts = [20] * 7 + [36] * 6 + [0] + [20] * 3 + [24] * 3  # 20 日：train 14 + val 6
    values, fwd = _rank_shift_frames(shifts)
    train_dates, val_dates = train_validation_split(values.index)
    assert (len(train_dates), len(val_dates)) == (14, 6)  # 切点 int(20 × 0.7)
    leaked = rank_ic_series(values.loc[train_dates], fwd.loc[train_dates])
    assert float(leaked.mean()) > 0.0  # 含边界日：训练段均值为正（泄漏会翻转符号）
    result = check_out_of_sample(values, fwd)
    assert result.detail["sign_train"] == -1  # 剔除边界日后：为负（不再被泄漏翻转）
    assert result.detail["sign_val"] == -1
    assert result.detail["mean_train"] < 0.0
    assert result.detail["purged_train_days"] == DEFAULT_IC_HORIZON
    assert result.passed is True


def test_check_out_of_sample_purge_empties_train_fails_closed():
    """fix 4：训练段过短（剔除后为空）→ OOS_FAILED（``insufficient_dates``）。"""
    values, fwd = _rank_shift_frames([0, 20])  # 2 日 → train 1 / val 1；剔除后 train 空
    result = check_out_of_sample(values, fwd)
    assert result.passed is False
    assert result.reason == "OOS_FAILED"
    assert result.detail["reason"] == "insufficient_dates"


def test_check_out_of_sample_rejects_weak_validation_segment():
    """训练段强、验证段无信号 → |ICIR_val| < 0.3 → OOS_FAILED。"""
    values, fwd = _oos_frames(mode="train_only")
    result = check_out_of_sample(values, fwd)
    assert result.passed is False
    assert result.reason == ReasonCode.OOS_FAILED
    assert abs(result.detail["icir_val"]) < 0.3
    assert result.detail["icir_train"] > 0.3


def test_check_out_of_sample_rejects_sign_mismatch():
    """验证段强但符号与训练段相反 → OOS_FAILED（|ICIR| 达标不等于通过）。"""
    values, fwd = _oos_frames(mode="sign_flip")
    result = check_out_of_sample(values, fwd)
    assert result.passed is False
    assert result.reason == "OOS_FAILED"
    assert abs(result.detail["icir_val"]) > 0.3
    assert result.detail["sign_train"] != result.detail["sign_val"]


def test_check_out_of_sample_too_short_fails_closed():
    """样本太短（验证段不足）→ OOS_FAILED（不因数据不足放行）。"""
    values, fwd = _oos_frames(dates=1, mode="both_strong")
    result = check_out_of_sample(values, fwd)
    assert result.passed is False
    assert result.reason == "OOS_FAILED"


# ── fix-pool-increment-orientation：闸④组合方向归一化 ──────────────────────


def _ic_values(expression: str, panel: MarketPanel):
    values = evaluate(parse(expression), panel)
    fwd = forward_returns(panel.fields["close"])
    return rank_ic_series(values, fwd), values


def test_pool_increment_orients_stable_negative_candidate(tmp_path):
    """稳定负向候选（空池）按训练段符号定向：increment = |验证段 IR| > 0。"""
    panel = _signal_panel()
    ic, values = _ic_values("sub(0, ts_mean(volume, 5))", panel)
    result = check_pool_increment(ic, values, [], panel, db_path=_empty_db(tmp_path))
    assert result.passed
    assert result.detail["orientation"] == -1
    assert result.detail["increment"] > 0.0
    assert result.detail["pool_orientations"] == []


def test_pool_increment_mirror_symmetry(tmp_path):
    """镜像对称：正/负向同强度因子的定向增量相等（门槛未放松，只是方向归一）。"""
    panel = _signal_panel()
    pos_ic, pos_values = _ic_values("ts_mean(volume, 5)", panel)
    neg_ic, neg_values = _ic_values("sub(0, ts_mean(volume, 5))", panel)
    pos = check_pool_increment(pos_ic, pos_values, [], panel, db_path=_empty_db(tmp_path))
    neg = check_pool_increment(neg_ic, neg_values, [], panel, db_path=_empty_db(tmp_path))
    assert pos.passed and neg.passed
    assert (pos.detail["orientation"], neg.detail["orientation"]) == (1, -1)
    assert neg.detail["increment"] == pytest.approx(pos.detail["increment"], rel=1e-9)


def test_pool_increment_undetermined_orientation_fails_closed(tmp_path):
    """方向不可定（IC 全 NaN：零值因子）→ 按原向参与，增量不可正 → 拒收。"""
    panel = _signal_panel()
    ic, values = _ic_values("sub(close, close)", panel)
    result = check_pool_increment(ic, values, [], panel, db_path=_empty_db(tmp_path))
    assert not result.passed
    assert result.detail["orientation"] == 0


def test_pool_increment_orients_negative_pool_factor(tmp_path):
    """池内含稳定负向因子：按训练段符号 −1 定向进组合（detail 留痕、组合 IR 取正）。"""
    panel = _signal_panel()
    db = _empty_db(tmp_path)
    pool_id = add_factor(
        expression="sub(0, ts_mean(volume, 5))",
        hypothesis="测试用：负向量化因子（镜像），经训练段定向进入池组合。",
        category="价量",
        status="active",
        ast_json=serialize(parse("sub(0, ts_mean(volume, 5))")),
        db_path=db,
    )
    ic, values = _ic_values("ts_mean(close, 5)", panel)
    result = check_pool_increment(ic, values, list_active_factors(db_path=db), panel, db_path=db)
    assert result.detail["pool_orientations"] == [{"factor_id": pool_id, "orientation": -1}]
    assert result.detail["ir_pool"] is not None and result.detail["ir_pool"] > 0.0


def test_pool_increment_pool_orientation_ignores_stale_stored_ic(tmp_path):
    """review C1 回归：池因子仅存验证段落库 IC（近日入库形态）→ 方向仍由面板训练段现算。

    旧实现取落库 IC 定向：验证段落库序列与训练段交集为空 → orientation=0 → 池因子被
    静默踢出组合（闸④退化为单因子判定）。修复后定向与候选同规则（面板现算）。
    """
    panel = _signal_panel()
    db = _empty_db(tmp_path)
    pool_id = add_factor(
        expression="sub(0, ts_mean(volume, 5))",
        hypothesis="测试用：负向量化因子（镜像），落库 IC 仅覆盖验证段。",
        category="价量",
        status="active",
        ast_json=serialize(parse("sub(0, ts_mean(volume, 5))")),
        db_path=db,
    )
    # 只在最后 15 个交易日（验证段）落库 IC——训练段零覆盖
    for trade_date in panel.fields["close"].index[-15:]:
        record_ic(pool_id, trade_date, 0.05, db_path=db)
    ic, values = _ic_values("ts_mean(close, 5)", panel)
    result = check_pool_increment(ic, values, list_active_factors(db_path=db), panel, db_path=db)
    assert result.detail["pool_orientations"] == [{"factor_id": pool_id, "orientation": -1}]
    assert result.detail["ir_pool"] is not None and result.detail["ir_pool"] > 0.0


def test_pool_increment_all_pool_orientations_undetermined_equals_empty_pool(tmp_path):
    """池非空但方向全部不可定（零值因子）→ 等价空池：ir_pool=None、审计字段如实记录。"""
    panel = _signal_panel()
    db = _empty_db(tmp_path)
    pool_id = add_factor(
        expression="sub(close, close)",
        hypothesis="测试用：零值因子（方向不可定），按契约不参与组合。",
        category="价量",
        status="active",
        ast_json=serialize(parse("sub(close, close)")),
        db_path=db,
    )
    ic, values = _ic_values("ts_mean(volume, 5)", panel)
    result = check_pool_increment(ic, values, list_active_factors(db_path=db), panel, db_path=db)
    assert result.detail["pool_orientations"] == [{"factor_id": pool_id, "orientation": 0}]
    assert result.detail["pool_size"] == 1
    assert result.detail["ir_pool"] is None
    assert result.detail["orientation"] == 1
