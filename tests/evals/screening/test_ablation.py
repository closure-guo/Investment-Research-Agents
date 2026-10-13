"""选股消融三变体单测（add-screening-evaluation Task 2；合成面板，零网络）。

覆盖（计划 Task 2 + spec causal-ablation 场景「随机变体可复现」）：

- ``random_selection`` 固定种子 42 两次执行的标的序列完全一致（输入序无关）；
- 三变体（因子打分 / 固定种子随机 / 等权指数）共享同一时段与 horizon；
- 配对 bootstrap 已知分布手算对照（逐日差恒定 → CI 退化到该常数）；
- 结论措辞：CI 含 0 → 逐字「无显著差异」（渲染断言在 ``test_report.py``）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from evals.screening import ablation
from finance_agent.execution.config import default_matching_config
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.screening.ranker import Candidate
from finance_agent.screening.store import record_l2_candidates

DATES = tuple(f"2026-02-{day:02d}" for day in range(1, 9))  # d0..d7
D0, D1, D2 = DATES[0], DATES[1], DATES[2]

SLIPPAGE_RATE = 0.002


def _fill_price(open_price: float) -> float:
    return round(open_price * (1 + SLIPPAGE_RATE), 2)


def _ret(open_price: float, close_price: float) -> float:
    return close_price / _fill_price(open_price) - 1.0


def _panel() -> MarketPanel:
    """合成面板：A 恒定（对照臂），B 每日翻倍（恒定比率 → 逐日收益差恒定），C 缓降。

    high/low 偏离 close → 不触发一字板（B 的日涨幅远超 4.8%，仅用于比率构造）。
    """
    closes = {
        "A": [10.0] * 8,
        "B": [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 320.0, 640.0],
        "C": [10.0, 10.0, 10.0, 10.0, 9.0, 8.0, 7.0, 6.0],
    }
    close_frame = pd.DataFrame(closes, index=list(DATES)).sort_index().sort_index(axis=1)
    fields = {
        "close": close_frame,
        "open": close_frame.copy(),
        "high": close_frame * 1.02,
        "low": close_frame * 0.98,
        "amount": pd.DataFrame(
            {symbol: [1000.0] * len(DATES) for symbol in closes}, index=list(DATES)
        ).sort_index(axis=1),
    }
    return MarketPanel(fields=fields)


# 三变体共享同一时段：d0 入场 d1（出场 d4）、d1 入场 d2（出场 d5），horizon=3
FACTOR = {D0: ["B"], D1: ["B"]}
RANDOM = {D0: ["A"], D1: ["A"]}
EQUAL_WEIGHT = {D0: ["A", "B", "C"], D1: ["A", "B", "C"]}


def test_random_selection_seed_42_reproducible():
    """spec 场景：固定种子 42 两次执行标的序列完全一致。"""
    universe = {
        D0: [f"6000{index:02d}" for index in range(20)],
        D1: [f"0000{index:02d}" for index in range(15)],
        D2: [f"3000{index:02d}" for index in range(11)],
    }
    first = ablation.random_selection((D0, D1, D2), universe, seed=42, top_n=5)
    second = ablation.random_selection((D0, D1, D2), universe, seed=42, top_n=5)
    assert first == second
    for pool_date in (D0, D1, D2):
        assert len(first[pool_date]) == 5
        assert set(first[pool_date]) <= set(universe[pool_date])

    # 输入序无关（抽样前先规范化）：universe 逆序 + pool_dates 逆序 → 同结果
    shuffled = {pool_date: list(reversed(symbols)) for pool_date, symbols in universe.items()}
    assert ablation.random_selection((D2, D0, D1), shuffled, seed=42, top_n=5) == first

    # top_n 超候选数 → 全量（不改语义）；候选重复先去重
    small = {pool_date: ["600000", "600001", "600000"] for pool_date in (D0, D1, D2)}
    selected = ablation.random_selection((D0,), small, seed=42, top_n=5)
    assert sorted(selected[D0]) == ["600000", "600001"]


def test_random_selection_fail_loud():
    universe = {D0: ["A", "B"]}
    with pytest.raises(ValueError):
        ablation.random_selection((D0, D1), universe, seed=42, top_n=1)  # 缺池日覆盖
    with pytest.raises(ValueError):
        ablation.random_selection((D0,), universe, seed=42, top_n=0)
    with pytest.raises(ValueError):
        ablation.random_selection(("2026-2-1",), universe, seed=42, top_n=1)


def test_factor_top_selection_rank_order_and_tie_break():
    candidates = {
        D0: [
            {"symbol": "C", "composite_score": 1.0},
            {"symbol": "A", "composite_score": 2.0},
            {"symbol": "B", "composite_score": 2.0},
        ]
    }
    assert ablation.factor_top_selection(candidates, top_n=2) == {D0: ["A", "B"]}
    assert ablation.factor_top_selection(candidates, top_n=9) == {D0: ["A", "B", "C"]}


def test_build_ablation_variants_from_db(tmp_path: Path):
    """库读入口：三变体同一时段；随机臂种子固定 → 全链路两次一致。"""
    db_path = tmp_path / "screening.db"
    rows = {
        D0: [("A", 3.0), ("B", 1.0), ("C", 2.0)],
        D1: [("B", 2.0), ("A", 1.0)],
    }
    for trade_date, items in rows.items():
        record_l2_candidates(
            trade_date,
            [
                Candidate(
                    symbol=s,
                    trade_date=trade_date,
                    composite_score=score,
                    factor_contribs=(),
                    rank=i + 1,
                )
                for i, (s, score) in enumerate(items)
            ],
            db_path=db_path,
        )
    variants = ablation.build_ablation_variants((D0, D1), db_path=db_path, top_n=2, seed=42)
    assert variants["factor"] == {D0: ["A", "C"], D1: ["B", "A"]}
    assert variants["equal_weight"] == {D0: ["A", "B", "C"], D1: ["A", "B"]}
    assert len(variants["random"][D0]) == 2
    assert set(variants["random"][D0]) <= {"A", "B", "C"}
    assert set(variants["random"][D1]) <= {"A", "B"}

    again = ablation.build_ablation_variants((D0, D1), db_path=db_path, top_n=2, seed=42)
    assert again == variants  # spec 场景：同一时段重复执行两次消融 → 随机臂逐位一致


def test_compare_variants_shared_window_and_hand_computed_diff():
    """三变体同窗口逐数对照；恒定逐日差 → 配对 CI 退化到手算常数。"""
    result = ablation.compare_ablation_variants(
        variants={"factor": FACTOR, "random": RANDOM, "equal_weight": EQUAL_WEIGHT},
        panel=_panel(),
        horizon=3,
        config=default_matching_config(),
    )
    assert result["horizon"] == 3
    assert result["pool_dates"] == [D0, D1]
    assert set(result["variants"]) == {"factor", "random", "equal_weight"}
    assert result["variants"]["factor"]["n"] == 2
    assert result["variants"]["random"]["n"] == 2
    assert result["variants"]["equal_weight"]["n"] == 6

    # 手算：B 每日翻倍 → 每日比率恒定；A 恒定 → 「因子臂 − 随机臂」逐日差恒等
    assert _ret(20.0, 160.0) == pytest.approx(_ret(10.0, 80.0))  # 恒定比率前提成立
    constant_diff = _ret(10.0, 80.0) - _ret(10.0, 10.0)
    factor_random = result["diffs"]["factor_minus_random"]
    assert factor_random["paired_days"] == 2
    assert factor_random["diff_mean"] == pytest.approx(constant_diff)
    lo, hi = factor_random["ci95"]
    assert (lo, hi) == pytest.approx((constant_diff, constant_diff))
    assert factor_random["bootstrap_B"] == 10000
    assert factor_random["seed"] == 42
    assert factor_random["conclusion"] == "显著改进"

    # 因子臂 vs 等权指数：逐日差不恒定 → 真 bootstrap CI（数值可回归、两次一致）
    factor_equal = result["diffs"]["factor_minus_equal_weight"]
    assert factor_equal["paired_days"] == 2
    expected_days = [
        _ret(10.0, 80.0) - (_ret(10.0, 10.0) + _ret(10.0, 80.0) + _ret(10.0, 9.0)) / 3,
        _ret(20.0, 160.0) - (_ret(10.0, 10.0) + _ret(20.0, 160.0) + _ret(10.0, 8.0)) / 3,
    ]
    assert factor_equal["diff_mean"] == pytest.approx(sum(expected_days) / 2)
    lo, hi = factor_equal["ci95"]
    assert lo <= hi
    assert factor_equal["conclusion"] in {"显著改进", "显著退步", "无显著差异"}
    repeat = ablation.compare_ablation_variants(
        variants={"factor": FACTOR, "random": RANDOM, "equal_weight": EQUAL_WEIGHT},
        panel=_panel(),
        horizon=3,
        config=default_matching_config(),
    )
    assert repeat["diffs"] == result["diffs"]


def test_compare_variants_fail_loud_on_window_mismatch():
    with pytest.raises(ValueError):
        ablation.compare_ablation_variants(
            variants={"factor": FACTOR, "random": RANDOM, "equal_weight": {D0: ["A"]}},
            panel=_panel(),
            horizon=3,
            config=default_matching_config(),
        )
    with pytest.raises(ValueError):
        ablation.compare_ablation_variants(
            variants={"factor": FACTOR, "random": RANDOM},
            panel=_panel(),
            horizon=3,
            config=default_matching_config(),
        )
    with pytest.raises(ValueError):
        ablation.compare_ablation_variants(
            variants={"factor": FACTOR, "random": RANDOM, "equal_weight": EQUAL_WEIGHT},
            panel=_panel(),
            horizon=0,
            config=default_matching_config(),
        )


def test_conclusion_for_variant_increment_wording():
    assert ablation.conclusion_for_variant_increment((-0.01, 0.02)) == "无显著差异"
    assert ablation.conclusion_for_variant_increment((0.0, 0.0)) == "无显著差异"
    assert ablation.conclusion_for_variant_increment((0.01, 0.05)) == "显著改进"
    assert ablation.conclusion_for_variant_increment((-0.05, -0.01)) == "显著退步"
    assert ablation.conclusion_for_variant_increment(None) == "样本不足，无法判定"
