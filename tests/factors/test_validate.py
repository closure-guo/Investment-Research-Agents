"""factors/validate.py 单测（add-factor-library-and-monitor Task 3）。

覆盖闸①（可计算性：全样本缺失率 ≤ ``MAX_MISSING_RATE``）与闸②（单因子检验：
``|ICIR| ≥ MIN_ABS_ICIR`` 且 ``|IC 均值| > 0``，E3）、``admit_candidates`` 编排骨架
（结构查重 → 闸①② → 闸③占位 → 闸④⑤占位 → 入库 active；顺序保持、被拒不入库）。

闸③④⑤ 本任务为 ``TODO(T4)``/``TODO(T5)`` 占位桩（恒通过）；本测试只断言编排形状
（闸编号序列、拒绝闸口编号），不断言占位桩内部。

合成面板：固定种子；「强因子」= 用 volume 承载次日收益信号，且 amount = volume ×
(-1)^t（横截面秩与次日收益逐日反号 → IC 序列均值≈0、标准差≈1 → 闸②必拒）。
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from finance_agent.factors.dsl import parse, serialize
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.library import add_factor, list_active_factors, list_events, list_factors
from finance_agent.factors.validate import (
    AdmissionResult,
    GateResult,
    ReasonCode,
    admit_candidates,
    check_computability,
    check_single_factor,
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

    闸③④⑤ 本任务为 T4/T5 占位桩（恒通过）；本断言只钉编排形状（闸编号序列 +
    不被拒 + 落库状态），真实统计判定由 T4/T5 的测试覆盖。
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
