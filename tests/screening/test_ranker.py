"""screening/ranker.py L2 横截面打分排序单测（add-screening-ranker Task 1）。

计划 docs/superpowers/plans/2026-10-13-add-screening-ranker.md 关键设计决策：

- R1 z-score 口径：逐因子在**通过 L1 的标的集合**上做截面 z-score（ddof=1，与
  ``eval_engine.cross_section_zscore`` 同口径）；因子值 NaN → 该标的该因子
  ``zscore=0.0``（贡献 0，且不因缺失被剔除）；截面全 NaN 或 std=0 → 全体 zscore=0.0；
- R2 权重口径：``weight_i = ic_mean_i / Σ_j |ic_mean_j|``；
  ``Σ_j |ic_mean_j| < 1e-12`` → ``ValueError("无有效 IC 信息，拒绝打分")``；
  ``contribution_i = weight_i × zscore_i``，``composite = Σ contribution_i``
  （按因子传入顺序累加——勾稽 Σcontribution == composite 由此天然成立，容差 1e-9）；
- R3 as_of 语义：只用面板 ``as_of`` 当日行（无跨日聚合/无前视）；``as_of`` 不在
  面板 → ``ValueError``；
- R4 IC 统计来源：``recent_ic_stats`` 取 ``list_ic_series(fid, limit=60)``，空序列
  → ``(0.0, 0.0)``（IC 均值 0 的因子权重 0，等价于不参与）。

确定性（spec 场景「重复执行一致」）：同输入两次执行逐项相等；排序 tie-break =
composite 降序 → symbol 升序。期望值全部在测试内独立手算（不复用被测函数生成）。
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import pytest

from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.library import add_factor, list_factors, record_ic
from finance_agent.screening.ranker import (
    MIN_IC_SAMPLES_FOR_SCORING,
    Candidate,
    factor_weights,
    has_scorable_factors,
    ic_sample_counts,
    liquidity_fallback_rank,
    rank_universe,
    recent_ic_stats,
    select_scorable_factors,
)

_DATES = ("2026-03-02", "2026-03-03", "2026-03-04")
_AS_OF = _DATES[-1]


# ── 小面板 / 因子行构造 ─────────────────────────────────────────────────────


def _matrix(values: dict[str, float], codes: list[str], dates: tuple[str, ...]) -> pd.DataFrame:
    """code→标量映射 → date×code 帧（各日同值：无跨日算子的表达式下等价）。"""
    row = [float(values[code]) for code in codes]
    return pd.DataFrame([row for _ in dates], index=list(dates), columns=codes)


def _panel(
    *,
    close: dict[str, float],
    volume: dict[str, float] | None = None,
    amount: dict[str, float] | None = None,
    dates: tuple[str, ...] = _DATES,
) -> MarketPanel:
    """小面板：指定各 code 在 as_of 当日的值（前几日同值；测试只用当日截面）。"""
    codes = sorted(close)
    volume = dict.fromkeys(codes, 1.0) if volume is None else volume
    amount = dict.fromkeys(codes, 1.0) if amount is None else amount
    return MarketPanel(
        fields={
            "open": _matrix(close, codes, dates),
            "high": _matrix(close, codes, dates),
            "low": _matrix(close, codes, dates),
            "close": _matrix(close, codes, dates),
            "volume": _matrix(volume, codes, dates),
            "amount": _matrix(amount, codes, dates),
        }
    )


def _factor(factor_id: int, expression: str, *, status: str = "active") -> dict:
    """因子库行 dict 的最小形态（id + expression 为 ranker 消费字段）。"""
    return {
        "id": factor_id,
        "expression": expression,
        "hypothesis": "测试用经济学假设：该因子与未来收益存在稳定关系（≥30 字长度达标）。",
        "category": "builtin",
        "status": status,
    }


def _sum_contributions(candidate: Candidate) -> float:
    """按 factor_contribs 元组顺序累加贡献（与实现同序，用于勾稽断言）。"""
    total = 0.0
    for contrib in candidate.factor_contribs:
        total += contrib.contribution
    return total


# ── 手算基线：两因子三标的 ──────────────────────────────────────────────────


def test_hand_computed_scores_and_contributions() -> None:
    """close/volume 两因子三标的：zscore/weight/contribution/composite 逐项手算对照。

    手算：close = (1,2,3) → mean 2、std(ddof=1) 1 → z = (−1,0,1)；
    volume = (10,20,30) → z = (−1,0,1)；ic_stats = (0.03, 0.01) →
    Σ|ic| = 0.04 → w = (0.75, 0.25)；composite(a,b,c) = (−1, 0, 1)。
    """
    panel = _panel(close={"a": 1.0, "b": 2.0, "c": 3.0}, volume={"a": 10.0, "b": 20.0, "c": 30.0})
    factors = [_factor(1, "close"), _factor(2, "volume")]
    ic_stats = {1: (0.03, 0.50), 2: (0.01, 0.20)}

    candidates = rank_universe(
        panel=panel,
        as_of=_AS_OF,
        passed_codes=["a", "b", "c"],
        factors=factors,
        ic_stats=ic_stats,
    )

    assert [candidate.symbol for candidate in candidates] == ["c", "b", "a"]
    assert [candidate.rank for candidate in candidates] == [1, 2, 3]
    assert all(candidate.trade_date == _AS_OF for candidate in candidates)
    expected_composites = {"c": 1.0, "b": 0.0, "a": -1.0}
    for candidate in candidates:
        expected = expected_composites[candidate.symbol]
        assert candidate.composite_score == pytest.approx(expected, abs=1e-12)
        # factor_contribs 顺序 = factors 传入顺序；逐字段手算
        close_contrib, volume_contrib = candidate.factor_contribs
        assert close_contrib.factor_id == 1
        assert volume_contrib.factor_id == 2
        sign = expected  # z 与 composite 同号（此构造下两因子 z 相同）
        assert close_contrib.zscore == pytest.approx(sign, abs=1e-12)
        assert volume_contrib.zscore == pytest.approx(sign, abs=1e-12)
        assert close_contrib.weight == pytest.approx(0.75, abs=1e-12)
        assert volume_contrib.weight == pytest.approx(0.25, abs=1e-12)
        assert close_contrib.contribution == pytest.approx(0.75 * sign, abs=1e-12)
        assert volume_contrib.contribution == pytest.approx(0.25 * sign, abs=1e-12)
        # spec 场景：Σ contribution == composite（容差 1e-9）
        assert abs(_sum_contributions(candidate) - candidate.composite_score) <= 1e-9


def test_reconciliation_holds_in_every_path() -> None:
    """勾稽（Σcontrib == composite，≤1e-9）在常规 / 全 NaN 因子 / std=0 / 负权重下恒成立。"""
    cases: list[tuple[MarketPanel, list[dict], dict[int, tuple[float, float]]]] = [
        (
            _panel(close={"a": 1.0, "b": 2.0, "c": 3.0}, volume={"a": 30.0, "b": 20.0, "c": 10.0}),
            [_factor(1, "close"), _factor(2, "volume")],
            {1: (0.03, 0.5), 2: (0.01, 0.2)},
        ),
        (
            # 全 NaN 因子（volume 全缺）：zscore 全 0，贡献 0
            _panel(
                close={"a": 1.0, "b": 2.0, "c": 3.0},
                volume={"a": float("nan"), "b": float("nan"), "c": float("nan")},
            ),
            [_factor(1, "close"), _factor(2, "volume")],
            {1: (0.03, 0.5), 2: (0.01, 0.2)},
        ),
        (
            # std=0 截面：close 全相等 → 该因子全体 zscore 0
            _panel(close={"a": 5.0, "b": 5.0, "c": 5.0}, volume={"a": 10.0, "b": 20.0, "c": 30.0}),
            [_factor(1, "close"), _factor(2, "volume")],
            {1: (0.03, 0.5), 2: (0.01, 0.2)},
        ),
        (
            # 负 IC 权重（方向×强度）
            _panel(close={"a": 1.0, "b": 2.0, "c": 3.0}, volume={"a": 10.0, "b": 20.0, "c": 30.0}),
            [_factor(1, "close"), _factor(2, "volume")],
            {1: (0.03, 0.5), 2: (-0.01, 0.2)},
        ),
        (
            # 部分 NaN 单元
            _panel(
                close={"a": 1.0, "b": float("nan"), "c": 3.0},
                volume={"a": 10.0, "b": 20.0, "c": 30.0},
            ),
            [_factor(1, "close"), _factor(2, "volume")],
            {1: (0.03, 0.5), 2: (0.01, 0.2)},
        ),
    ]
    for panel, factors, ic_stats in cases:
        candidates = rank_universe(
            panel=panel,
            as_of=_AS_OF,
            passed_codes=["a", "b", "c"],
            factors=factors,
            ic_stats=ic_stats,
        )
        assert len(candidates) == 3
        for candidate in candidates:
            assert abs(_sum_contributions(candidate) - candidate.composite_score) <= 1e-9
            for contrib in candidate.factor_contribs:
                assert math.isfinite(contrib.zscore)
                assert math.isfinite(contrib.contribution)


def test_repeated_runs_are_bitwise_identical() -> None:
    """spec 场景「重复执行一致」：同输入两次执行逐项相等（含排序与贡献分解）。"""
    panel = _panel(close={"a": 3.0, "b": 1.0, "c": 2.0}, volume={"a": 10.0, "b": 30.0, "c": 20.0})
    factors = [_factor(1, "close"), _factor(2, "volume")]
    ic_stats = {1: (0.03, 0.5), 2: (0.01, 0.2)}
    kwargs = {
        "panel": panel,
        "as_of": _AS_OF,
        "passed_codes": ["a", "b", "c"],
        "factors": factors,
        "ic_stats": ic_stats,
    }

    first = rank_universe(**kwargs)
    second = rank_universe(**kwargs)

    assert first == second  # dataclass 逐值比较（含 factor_contribs 元组）
    assert [(c.symbol, c.rank, c.composite_score, c.factor_contribs) for c in first] == [
        (c.symbol, c.rank, c.composite_score, c.factor_contribs) for c in second
    ]


def test_nan_factor_value_gets_zero_zscore_and_stays_in_list() -> None:
    """因子值 NaN → 该标的该因子 zscore=0（贡献 0），且不被剔除（R1）。

    手算：close(a=1, b=2, c=NaN) → 有效集合 {1,2}：mean 1.5、std(ddof=1)=1/√2 →
    z_a=−1/√2、z_b=+1/√2、z_c=0；volume=(10,20,30) → z=(−1,0,1)；
    w=(0.75,0.25) → composite_c = 0.25×1 = 0.25。
    """
    panel = _panel(
        close={"a": 1.0, "b": 2.0, "c": float("nan")},
        volume={"a": 10.0, "b": 20.0, "c": 30.0},
    )
    candidates = rank_universe(
        panel=panel,
        as_of=_AS_OF,
        passed_codes=["a", "b", "c"],
        factors=[_factor(1, "close"), _factor(2, "volume")],
        ic_stats={1: (0.03, 0.5), 2: (0.01, 0.2)},
    )

    by_symbol = {candidate.symbol: candidate for candidate in candidates}
    assert set(by_symbol) == {"a", "b", "c"}  # 缺失不剔除
    close_contrib_c = by_symbol["c"].factor_contribs[0]
    assert close_contrib_c.zscore == 0.0
    assert close_contrib_c.contribution == 0.0
    assert by_symbol["c"].composite_score == pytest.approx(0.25, abs=1e-12)
    assert by_symbol["a"].factor_contribs[0].zscore == pytest.approx(
        -1.0 / math.sqrt(2.0), abs=1e-12
    )
    assert by_symbol["b"].factor_contribs[0].zscore == pytest.approx(
        1.0 / math.sqrt(2.0), abs=1e-12
    )


def test_all_nan_factor_cross_section_gives_zero_zscores() -> None:
    """截面全 NaN 因子 → 全体 zscore=0、贡献 0，综合分只由其余因子构成。"""
    panel = _panel(
        close={"a": 1.0, "b": 2.0, "c": 3.0},
        volume={"a": float("nan"), "b": float("nan"), "c": float("nan")},
    )
    candidates = rank_universe(
        panel=panel,
        as_of=_AS_OF,
        passed_codes=["a", "b", "c"],
        factors=[_factor(1, "close"), _factor(2, "volume")],
        ic_stats={1: (0.03, 0.5), 2: (0.01, 0.2)},
    )

    for candidate in candidates:
        close_contrib, volume_contrib = candidate.factor_contribs
        # 全 NaN 因子（volume）：zscore/贡献全 0
        assert volume_contrib.zscore == 0.0
        assert volume_contrib.contribution == 0.0
        # 综合分只由 close 因子构成
        assert candidate.composite_score == pytest.approx(close_contrib.contribution, abs=1e-12)
    close_z = {c.symbol: c.factor_contribs[0].zscore for c in candidates}
    assert close_z == {"a": pytest.approx(-1.0), "b": pytest.approx(0.0), "c": pytest.approx(1.0)}


def test_constant_cross_section_gives_all_zero_zscores() -> None:
    """std=0 截面 → 全体 zscore=0、composite=0（不产生 inf/NaN）。"""
    panel = _panel(close={"a": 5.0, "b": 5.0, "c": 5.0}, volume={"a": 7.0, "b": 7.0, "c": 7.0})
    candidates = rank_universe(
        panel=panel,
        as_of=_AS_OF,
        passed_codes=["a", "b", "c"],
        factors=[_factor(1, "close"), _factor(2, "volume")],
        ic_stats={1: (0.03, 0.5), 2: (0.01, 0.2)},
    )

    for candidate in candidates:
        assert candidate.composite_score == 0.0
        for contrib in candidate.factor_contribs:
            assert contrib.zscore == 0.0
            assert contrib.contribution == 0.0


def test_zero_ic_information_raises() -> None:
    """Σ|ic_mean| < 1e-12 → ValueError（fail loud，不做任意排序）。"""
    panel = _panel(close={"a": 1.0, "b": 2.0}, volume={"a": 10.0, "b": 20.0})
    factors = [_factor(1, "close"), _factor(2, "volume")]

    with pytest.raises(ValueError, match="无有效 IC 信息"):
        rank_universe(
            panel=panel,
            as_of=_AS_OF,
            passed_codes=["a", "b"],
            factors=factors,
            ic_stats={1: (0.0, 0.5), 2: (0.0, 0.2)},
        )
    with pytest.raises(ValueError, match="无有效 IC 信息"):
        rank_universe(
            panel=panel,
            as_of=_AS_OF,
            passed_codes=["a", "b"],
            factors=factors,
            ic_stats={1: (1e-13, 0.5), 2: (0.0, 0.2)},
        )
    with pytest.raises(ValueError, match="无有效 IC 信息"):
        factor_weights(factors, {})


def test_as_of_absent_from_panel_raises() -> None:
    """as_of 不在面板日期 → ValueError（消息含可用日期范围，供调用方定位）。"""
    panel = _panel(close={"a": 1.0, "b": 2.0}, volume={"a": 10.0, "b": 20.0})
    with pytest.raises(ValueError, match="as_of") as excinfo:
        rank_universe(
            panel=panel,
            as_of="2026-03-05",
            passed_codes=["a", "b"],
            factors=[_factor(1, "close")],
            ic_stats={1: (0.03, 0.5)},
        )
    assert f"{_DATES[0]}..{_DATES[-1]}" in str(excinfo.value)


def test_top_n_truncates_after_sorting() -> None:
    """top_n 截断发生在全部排序之后：保留最高分者，rank 从 1 连续。"""
    panel = _panel(close={"a": 1.0, "b": 2.0, "c": 3.0}, volume={"a": 1.0, "b": 1.0, "c": 1.0})
    candidates = rank_universe(
        panel=panel,
        as_of=_AS_OF,
        passed_codes=["a", "b", "c"],
        factors=[_factor(1, "close")],
        ic_stats={1: (0.03, 0.5)},
        top_n=2,
    )

    assert [c.symbol for c in candidates] == ["c", "b"]
    assert [c.rank for c in candidates] == [1, 2]
    assert candidates[0].composite_score == pytest.approx(1.0, abs=1e-12)
    assert candidates[1].composite_score == pytest.approx(0.0, abs=1e-12)


def test_tie_break_is_symbol_ascending() -> None:
    """同分 tie-break：composite 降序 → symbol 升序（确定性，不依赖输入顺序）。"""
    panel = _panel(close={"a": 2.0, "b": 2.0, "c": 2.0}, volume={"a": 1.0, "b": 1.0, "c": 1.0})
    candidates = rank_universe(
        panel=panel,
        as_of=_AS_OF,
        passed_codes=["c", "b", "a"],
        factors=[_factor(1, "close")],
        ic_stats={1: (0.03, 0.5)},
    )

    assert [c.symbol for c in candidates] == ["a", "b", "c"]
    assert [c.rank for c in candidates] == [1, 2, 3]
    assert all(c.composite_score == 0.0 for c in candidates)


def test_codes_outside_passed_list_are_excluded_and_do_not_affect_zscores() -> None:
    """打分集合 = passed_codes；z-score 也在该集合上做（b 不参与均值/标准差）。"""
    panel = _panel(
        close={"a": 1.0, "b": 100.0, "c": 3.0},
        volume={"a": 1.0, "b": 1.0, "c": 1.0},
    )
    candidates = rank_universe(
        panel=panel,
        as_of=_AS_OF,
        passed_codes=["a", "c"],
        factors=[_factor(1, "close")],
        ic_stats={1: (0.03, 0.5)},
    )

    assert [c.symbol for c in candidates] == ["c", "a"]
    # 手算：{1,3} → mean 2、std(ddof=1)=√2 → z = (∓1/√2)；而非全样本 (∓1, ±1)
    by_symbol = {c.symbol: c for c in candidates}
    z_a = by_symbol["a"].factor_contribs[0].zscore
    z_c = by_symbol["c"].factor_contribs[0].zscore
    assert z_a == pytest.approx(-1.0 / math.sqrt(2.0), abs=1e-12)
    assert z_c == pytest.approx(1.0 / math.sqrt(2.0), abs=1e-12)
    assert by_symbol["a"].composite_score == pytest.approx(-1.0 / math.sqrt(2.0), abs=1e-12)


def test_passed_code_absent_from_panel_raises() -> None:
    """L1 通过标的不在面板列 → ValueError（fail loud，不静默造 0 分候选）。"""
    panel = _panel(close={"a": 1.0, "b": 2.0}, volume={"a": 1.0, "b": 1.0})
    with pytest.raises(ValueError, match="passed_codes 不在面板"):
        rank_universe(
            panel=panel,
            as_of=_AS_OF,
            passed_codes=["a", "z"],
            factors=[_factor(1, "close")],
            ic_stats={1: (0.03, 0.5)},
        )


def test_duplicate_passed_codes_raise() -> None:
    """passed_codes 重复 → ValueError（同一标的不得产出两条候选/两个 rank）。"""
    panel = _panel(close={"a": 1.0, "b": 2.0}, volume={"a": 1.0, "b": 1.0})
    with pytest.raises(ValueError, match="重复"):
        rank_universe(
            panel=panel,
            as_of=_AS_OF,
            passed_codes=["a", "a"],
            factors=[_factor(1, "close")],
            ic_stats={1: (0.03, 0.5)},
        )


def test_invalid_top_n_raises() -> None:
    """top_n 必须为正整数（0/负数 → ValueError）。"""
    panel = _panel(close={"a": 1.0, "b": 2.0}, volume={"a": 1.0, "b": 1.0})
    for bad in (0, -1):
        with pytest.raises(ValueError, match="top_n"):
            rank_universe(
                panel=panel,
                as_of=_AS_OF,
                passed_codes=["a", "b"],
                factors=[_factor(1, "close")],
                ic_stats={1: (0.03, 0.5)},
                top_n=bad,
            )


def test_factor_missing_from_ic_stats_gets_zero_weight() -> None:
    """ic_stats 缺该因子 → ic_mean=0 → 权重 0（贡献 0，但 zscore 仍记录在案）。"""
    panel = _panel(close={"a": 1.0, "b": 2.0, "c": 3.0}, volume={"a": 10.0, "b": 20.0, "c": 30.0})
    candidates = rank_universe(
        panel=panel,
        as_of=_AS_OF,
        passed_codes=["a", "b", "c"],
        factors=[_factor(1, "close"), _factor(2, "volume")],
        ic_stats={1: (0.03, 0.5)},  # 因子 2 无 IC 信息
    )

    for candidate in candidates:
        close_contrib, volume_contrib = candidate.factor_contribs
        assert volume_contrib.weight == 0.0
        assert volume_contrib.contribution == 0.0
        assert volume_contrib.zscore == pytest.approx(close_contrib.zscore, abs=1e-12)
        assert candidate.composite_score == pytest.approx(close_contrib.contribution, abs=1e-12)


def test_negative_ic_mean_flips_weight_direction() -> None:
    """IC 均值符号决定权重方向（方向×强度）：负 IC 因子以负权重进入合成。

    手算：w = (0.75, −0.25)；z_close = z_volume = (−1, 0, 1) →
    composite = (−0.75+0.25, 0, 0.75−0.25) = (−0.5, 0, +0.5)。
    """
    panel = _panel(close={"a": 1.0, "b": 2.0, "c": 3.0}, volume={"a": 10.0, "b": 20.0, "c": 30.0})
    candidates = rank_universe(
        panel=panel,
        as_of=_AS_OF,
        passed_codes=["a", "b", "c"],
        factors=[_factor(1, "close"), _factor(2, "volume")],
        ic_stats={1: (0.03, 0.5), 2: (-0.01, 0.2)},
    )

    for candidate in candidates:
        close_contrib, volume_contrib = candidate.factor_contribs
        assert close_contrib.weight == pytest.approx(0.75, abs=1e-12)
        assert volume_contrib.weight == pytest.approx(-0.25, abs=1e-12)
        assert volume_contrib.contribution == pytest.approx(-0.25 * close_contrib.zscore, abs=1e-12)
    by_symbol = {c.symbol: c for c in candidates}
    assert by_symbol["a"].composite_score == pytest.approx(-0.5, abs=1e-12)
    assert by_symbol["b"].composite_score == pytest.approx(0.0, abs=1e-12)
    assert by_symbol["c"].composite_score == pytest.approx(0.5, abs=1e-12)


def test_factor_weights_formula_and_guards() -> None:
    """factor_weights 逐项：权重 = ic_mean/Σ|ic_mean|；Σ|w| = 1；缺失 → 0；坏值 fail loud。"""
    factors = [_factor(1, "close"), _factor(2, "volume"), _factor(3, "amount")]
    weights = factor_weights(factors, {1: (0.06, 0.5), 2: (-0.02, 0.2), 3: (0.02, 0.3)})

    assert weights[1] == pytest.approx(0.6, abs=1e-12)
    assert weights[2] == pytest.approx(-0.2, abs=1e-12)
    assert weights[3] == pytest.approx(0.2, abs=1e-12)
    assert sum(abs(value) for value in weights.values()) == pytest.approx(1.0, abs=1e-12)

    weights_missing = factor_weights(factors, {1: (0.06, 0.5)})
    assert weights_missing == {
        1: pytest.approx(1.0, abs=1e-12),
        2: 0.0,
        3: 0.0,
    }
    with pytest.raises(ValueError, match="非有限"):
        factor_weights(factors, {1: (float("nan"), 0.5), 2: (0.02, 0.2), 3: (0.02, 0.3)})
    with pytest.raises(ValueError, match="无有效 IC 信息"):
        factor_weights([], {})


def test_scores_use_as_of_day_row_only() -> None:
    """跨日算子的因子值取 ``as_of`` 当日行（无跨日聚合、无前视）。

    手算（delta(close,1) 在 as_of 行）：a=4−2=2、b=2−3=−1、c=2−2=0 →
    mean=1/3、样本方差=7/3 → z = (x−1/3)/√(7/3)。
    """
    dates = ("2026-03-02", "2026-03-03", "2026-03-04")
    close = pd.DataFrame(
        {"a": [1.0, 2.0, 4.0], "b": [4.0, 3.0, 2.0], "c": [2.0, 2.0, 2.0]},
        index=list(dates),
    )
    panel = MarketPanel(
        fields={
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": close * 0.0 + 1.0,
            "amount": close * 0.0 + 1.0,
        }
    )
    candidates = rank_universe(
        panel=panel,
        as_of=dates[-1],
        passed_codes=["a", "b", "c"],
        factors=[_factor(1, "delta(close,1)")],
        ic_stats={1: (0.05, 1.0)},
    )

    expected = {
        "a": (2.0 - 1.0 / 3.0) / math.sqrt(7.0 / 3.0),
        "b": (-1.0 - 1.0 / 3.0) / math.sqrt(7.0 / 3.0),
        "c": (0.0 - 1.0 / 3.0) / math.sqrt(7.0 / 3.0),
    }
    assert [c.symbol for c in candidates] == ["a", "c", "b"]  # a 最高；c(−0.218) > b(−0.873)
    for candidate in candidates:
        assert candidate.composite_score == pytest.approx(expected[candidate.symbol], abs=1e-12)


# ── recent_ic_stats（R4：库内近窗口 IC 均值/ICIR） ──────────────────────────


def _iso_days(count: int, start: str = "2025-09-01") -> list[str]:
    """count 个连续 ISO 日期（字典序 = 日期序，供 list_ic_series 排序）。"""
    first = date.fromisoformat(start)
    return [(first + timedelta(days=offset)).isoformat() for offset in range(count)]


def _hand_stats(values: list[float]) -> tuple[float, float]:
    """独立手算 ``(mean, icir)``（ddof=1；常量序列 ICIR 记 0，与非有限样本同纪律）。"""
    count = len(values)
    mean = sum(values) / count
    if count < 2:
        return mean, 0.0
    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    if variance == 0.0:
        return mean, 0.0
    return mean, mean / math.sqrt(variance)


def test_recent_ic_stats_reads_last_window_from_library(tmp_path) -> None:
    """recent_ic_stats：近 window 日 IC 均值与 ICIR（独立手算对照；空序列 → (0,0)）。"""
    db = str(tmp_path / "factor_library.db")
    factor_a = add_factor(
        expression="close",
        hypothesis="测试假设文本（≥30 字长度达标）",
        category="builtin",
        db_path=db,
    )
    factor_b = add_factor(
        expression="volume",
        hypothesis="测试假设文本（≥30 字长度达标）",
        category="builtin",
        db_path=db,
    )
    days = _iso_days(70)
    recorded = [0.001 * index for index in range(1, 71)]
    for day, value in zip(days, recorded, strict=True):
        record_ic(factor_a, day, value, db_path=db)

    factors = list_factors(db_path=db)
    stats = recent_ic_stats(factors, db_path=db)

    last_60 = recorded[-60:]
    expected_mean, expected_icir = _hand_stats(last_60)
    assert stats[factor_a] == pytest.approx((expected_mean, expected_icir), abs=1e-12)
    assert stats[factor_b] == (0.0, 0.0)

    stats_10 = recent_ic_stats(factors, window=10, db_path=db)
    assert stats_10[factor_a] == pytest.approx(_hand_stats(recorded[-10:]), abs=1e-12)


def test_recent_ic_stats_constant_series_icir_zero(tmp_path) -> None:
    """常量 IC 序列 → ICIR 逐位记 0（不产生 inf / 5.9e15 量级假象）。

    反例钉死（评审 finding）：DB 往返后的 ``[0.1, 0.1, 0.1]`` 其
    ``fsum / count`` 均值与样本值差一个 ulp（0.1000…02 ≠ 0.1），若只按
    ``std == 0.0`` 判定会漏检并算出 ~5.9e15 的 ICIR；本用例要求
    ``max == min`` 守卫命中 → ICIR 逐位等于 0.0。
    """
    db = str(tmp_path / "factor_library.db")
    factor_id = add_factor(
        expression="close",
        hypothesis="测试假设文本（≥30 字长度达标）",
        category="builtin",
        db_path=db,
    )
    for day in _iso_days(5):
        record_ic(factor_id, day, 0.02, db_path=db)
    # 浮点反例：0.1 的三元常量序列（mean 非逐位等于样本值 → 旧守卫漏检）
    float_factor = add_factor(
        expression="volume",
        hypothesis="测试假设文本（≥30 字长度达标）",
        category="builtin",
        db_path=db,
    )
    for day in _iso_days(3):
        record_ic(float_factor, day, 0.1, db_path=db)

    stats = recent_ic_stats(list_factors(db_path=db), db_path=db)
    assert stats[factor_id] == pytest.approx((0.02, 0.0), abs=1e-15)
    assert stats[float_factor][1] == 0.0  # 逐位零：非「近似为零的大数」通过
    assert stats[float_factor] == pytest.approx((0.1, 0.0), abs=1e-15)


def test_recent_ic_stats_rejects_invalid_window(tmp_path) -> None:
    """window 必须为正整数。"""
    db = str(tmp_path / "factor_library.db")
    with pytest.raises(ValueError, match="window"):
        recent_ic_stats([], window=0, db_path=db)


# ── 降级通道（add-screening-empty-library-fallback T1） ─────────────────────


def test_has_scorable_factors_mirrors_ranker_refusal_condition() -> None:
    """谓词镜像 L2 拒绝条件：无因子/全零 IC → False；任一 |IC 均值| 非零 → True。"""
    factors = [_factor(1, "close"), _factor(2, "volume")]
    assert has_scorable_factors([], {}) is False
    assert has_scorable_factors(factors, {1: (0.0, 0.0), 2: (0.0, 0.0)}) is False
    assert has_scorable_factors(factors, {}) is False  # IC 记录缺失按 0 计
    assert has_scorable_factors(factors, {1: (0.0, 0.0), 2: (0.01, 0.3)}) is True
    assert has_scorable_factors(factors, {1: (-0.02, -0.5)}) is True  # 负 IC 同样可打分
    # 非有限 IC 属数据错误：谓词判可打分 → 交回打分路径 fail loud（降级不吞数据错误）
    assert has_scorable_factors(factors, {1: (math.nan, 0.0)}) is True
    assert has_scorable_factors(factors, {1: (math.inf, 0.0)}) is True


def test_has_scorable_factors_boundary_pinned() -> None:
    """谓词边界钉死：Σ|IC| 恰等于阈值 → 可打分（>= 语义，与 factor_weights 的 < 拒绝对偶）。"""
    factors = [_factor(1, "close")]
    assert has_scorable_factors(factors, {1: (1e-12, 0.0)}) is True
    assert has_scorable_factors(factors, {1: (0.5e-12, 0.0)}) is False


def test_liquidity_fallback_rank_by_mean_amount_desc_with_symbol_tiebreak() -> None:
    """近 N 日日均成交额降序；同额按 symbol 升序；score=日均成交额、contribs 为空。"""
    amounts = {
        "b": [50.0, 70.0],
        "a": [10.0, 20.0],
        "c": [1.0, 1.0],
        "d": [15.0, 15.0],
    }
    candidates = liquidity_fallback_rank(
        amounts_by_code=amounts,
        passed_codes=["b", "a", "c", "d"],
        as_of=_AS_OF,
        top_n=10,
    )
    # d 与 a 同额（15.0）→ symbol 升序 a 在前
    assert [candidate.symbol for candidate in candidates] == ["b", "a", "d", "c"]
    assert [candidate.rank for candidate in candidates] == [1, 2, 3, 4]
    assert candidates[0].composite_score == pytest.approx(60.0)
    assert candidates[2].composite_score == pytest.approx(15.0)
    assert all(candidate.trade_date == _AS_OF for candidate in candidates)
    assert all(candidate.factor_contribs == () for candidate in candidates)


def test_liquidity_fallback_rank_top_n_truncation_and_determinism() -> None:
    amounts = {f"c{index}": [float(index)] for index in range(5)}
    first = liquidity_fallback_rank(
        amounts_by_code=amounts, passed_codes=list(amounts), as_of=_AS_OF, top_n=3
    )
    second = liquidity_fallback_rank(
        amounts_by_code=amounts, passed_codes=list(amounts), as_of=_AS_OF, top_n=3
    )
    assert [candidate.symbol for candidate in first] == ["c4", "c3", "c2"]
    assert first == second  # 全确定性：两次调用逐项相等


def test_liquidity_fallback_rank_excludes_nonfinite_and_missing_windows() -> None:
    """窗口含 NaN/±inf 或空窗口 → 剔除（fail-closed 不猜，与 L1 DATA_MISSING 同纪律）。"""
    amounts = {
        "ok": [1.0, 3.0],
        "nan": [1.0, math.nan],
        "inf": [1.0, math.inf],
        "empty": [],
    }
    candidates = liquidity_fallback_rank(
        amounts_by_code=amounts,
        passed_codes=["ok", "nan", "inf", "empty"],
        as_of=_AS_OF,
        top_n=10,
    )
    assert [candidate.symbol for candidate in candidates] == ["ok"]


def test_liquidity_fallback_rank_empty_and_guards() -> None:
    assert liquidity_fallback_rank(amounts_by_code={}, passed_codes=[], as_of=_AS_OF) == []
    with pytest.raises(ValueError, match="top_n"):
        liquidity_fallback_rank(
            amounts_by_code={"a": [1.0]}, passed_codes=["a"], as_of=_AS_OF, top_n=0
        )
    with pytest.raises(ValueError, match="重复"):
        liquidity_fallback_rank(amounts_by_code={"a": [1.0]}, passed_codes=["a", "a"], as_of=_AS_OF)


# ── IC 样本门槛（add-screening-ic-bootstrap-guard） ─────────────────────────


def test_select_scorable_factors_excludes_thin_samples() -> None:
    """样本 < 10 条的因子被排除；达标的保留；两者按传入顺序切分。"""
    factors = [_factor(1, "close"), _factor(2, "volume"), _factor(3, "high")]
    kept, excluded = select_scorable_factors(factors, {1: 10, 2: 9, 3: 0})

    assert [row["id"] for row in kept] == [1]
    assert [row["id"] for row in excluded] == [2, 3]
    assert MIN_IC_SAMPLES_FOR_SCORING == 10


def test_select_scorable_factors_boundary_and_guards() -> None:
    """边界 = 阈值本身纳入（>= 语义）；min_samples 必须正整数。"""
    factors = [_factor(1, "close")]
    kept, excluded = select_scorable_factors(factors, {1: 9}, min_samples=9)
    assert [row["id"] for row in kept] == [1] and excluded == []
    kept, excluded = select_scorable_factors(factors, {1: 8}, min_samples=9)
    assert kept == [] and [row["id"] for row in excluded] == [1]
    with pytest.raises(ValueError, match="min_samples"):
        select_scorable_factors(factors, {1: 1}, min_samples=0)


def test_ic_sample_counts_reads_recorded_series(tmp_path) -> None:
    """计数 = 库内实际 RankIC 记录条数（缺记录 → 0）。"""
    db = tmp_path / "lib.db"
    factor_id = add_factor(
        expression="close",
        hypothesis="测试用经济学假设：该因子与未来收益存在稳定关系（≥30 字长度达标）。",
        category="builtin",
        status="active",
        db_path=db,
    )
    for day in ("2026-03-02", "2026-03-03", "2026-03-04"):
        record_ic(factor_id, day, 0.01, db_path=db)

    assert ic_sample_counts([{"id": factor_id}], db_path=db) == {factor_id: 3}
    assert ic_sample_counts([{"id": factor_id + 99}], db_path=db) == {factor_id + 99: 0}
