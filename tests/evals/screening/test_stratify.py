"""因子来源分层考核单测（add-screening-evaluation Task 3；合成面板，零网络）。

spec evaluation「因子来源分层考核」：仅 builtin 打分 / 仅 LLM 因子打分两个分层，
各自经 ``rank_universe``（同一 IC 统计口径）重跑 → Top-N 前向收益对照登记。
两分层唯一差异 = 因子子集（单变量纪律）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from evals.screening import stratify
from finance_agent.execution.config import default_matching_config
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.library import add_factor, list_active_factors, record_ic
from finance_agent.screening.ranker import recent_ic_stats

DATES = tuple(f"2026-05-{day:02d}" for day in range(1, 7))  # d0..d5
D1, D2 = DATES[1], DATES[2]
SLIPPAGE_RATE = 0.002

# A 单调上行（动量高分）、B 单调下行（负动量高分）、C 横盘（并列基准）
CLOSES: dict[str, list[float]] = {
    "A": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
    "B": [10.0, 9.0, 8.0, 7.0, 6.0, 5.0],
    "C": [10.0] * 6,
}

BUILTIN_FACTOR = {"id": 1, "expression": "delta(close,1)", "source": "builtin", "status": "active"}
LLM_FACTOR = {
    "id": 2,
    "expression": "sub(0, delta(close,1))",
    "source": "proposal",
    "status": "active",
}
IC_STATS: dict[int, tuple[float, float]] = {1: (0.5, 1.0), 2: (0.5, 1.0)}


def _fill_price(open_price: float) -> float:
    return round(open_price * (1 + SLIPPAGE_RATE), 2)


def _ret(open_price: float, close_price: float) -> float:
    return close_price / _fill_price(open_price) - 1.0


def _panel() -> MarketPanel:
    close_frame = pd.DataFrame(CLOSES, index=list(DATES)).sort_index().sort_index(axis=1)
    return MarketPanel(
        fields={
            "close": close_frame,
            "open": close_frame.copy(),
            "high": close_frame * 1.02,
            "low": close_frame * 0.98,
            "amount": pd.DataFrame(
                {symbol: [1000.0] * len(DATES) for symbol in CLOSES}, index=list(DATES)
            ).sort_index(axis=1),
        }
    )


def _run(*, factors: list[dict], ic_stats: dict[int, tuple[float, float]]):
    return stratify.stratify_by_factor_source(
        pool_dates=(D1, D2),
        panel=_panel(),
        passed_codes_by_date={D1: ["A", "B", "C"], D2: ["A", "B", "C"]},
        factors=factors,
        ic_stats=ic_stats,
        top_n=1,
        horizon=2,
        config=default_matching_config(),
    )


def test_factor_source_strata_split_by_source():
    strata = stratify.factor_source_strata(
        [
            BUILTIN_FACTOR,
            LLM_FACTOR,
            {"id": 3, "expression": "ts_mean(close,2)", "source": "llm", "status": "active"},
        ]
    )
    assert [row["id"] for row in strata["builtin"]] == [1]
    assert [row["id"] for row in strata["llm"]] == [2, 3]  # 非 builtin 一律归 LLM 挖掘分层


def test_stratify_hand_computed_two_strata():
    payload = _run(factors=[BUILTIN_FACTOR, LLM_FACTOR], ic_stats=IC_STATS)
    assert payload["pool_dates"] == [D1, D2]
    assert payload["horizon"] == 2
    assert payload["top_n"] == 1

    # 手算：builtin（动量正号）每日选 A（入场 d2/d3、出场 d4/d5）；LLM（动量负号）每日选 B
    builtin_rets = [_ret(12.0, 14.0), _ret(13.0, 15.0)]
    llm_rets = [_ret(8.0, 6.0), _ret(7.0, 5.0)]
    assert payload["strata"]["builtin"]["n"] == 2
    assert payload["strata"]["builtin"]["mean"] == pytest.approx(sum(builtin_rets) / 2)
    assert payload["strata"]["llm"]["n"] == 2
    assert payload["strata"]["llm"]["mean"] == pytest.approx(sum(llm_rets) / 2)

    diff = payload["diff"]
    assert diff["paired_days"] == 2
    assert diff["diff_mean"] == pytest.approx(
        ((llm_rets[0] - builtin_rets[0]) + (llm_rets[1] - builtin_rets[1])) / 2
    )
    lo, hi = diff["ci95"]
    assert hi < 0
    assert payload["conclusion"] == "LLM 因子分层显著劣于 builtin"


def test_stratify_from_library_db(tmp_path: Path):
    """默认路径：active 因子从库读取、IC 统计同源（两分层同一 ic_stats 口径）。"""
    library_db = tmp_path / "library.db"
    builtin_id = add_factor(
        expression="delta(close,1)",
        hypothesis="动量 builtin",
        category="builtin",
        status="active",
        source="builtin",
        db_path=library_db,
    )
    llm_id = add_factor(
        expression="sub(0, delta(close,1))",
        hypothesis="反转 LLM 挖掘",
        category="动量",
        status="active",
        source="proposal",
        db_path=library_db,
    )
    for factor_id in (builtin_id, llm_id):
        for trade_date in DATES[:3]:
            record_ic(factor_id, trade_date, 0.2, db_path=library_db)

    active = list_active_factors(db_path=library_db)
    payload = stratify.stratify_by_factor_source(
        pool_dates=(D1, D2),
        panel=_panel(),
        passed_codes_by_date={D1: ["A", "B", "C"], D2: ["A", "B", "C"]},
        library_db_path=library_db,
        top_n=1,
        horizon=2,
        config=default_matching_config(),
    )
    assert [row["id"] for row in payload["strata"]["builtin"]["factors"]] == [builtin_id]
    assert [row["id"] for row in payload["strata"]["llm"]["factors"]] == [llm_id]
    # 因子引用固定投影：不携带库行 created_at/updated_at（artifact 跨次运行可复现）
    assert set(payload["strata"]["builtin"]["factors"][0]) == {"id", "expression", "source"}
    injected = _run(factors=active, ic_stats=recent_ic_stats(active, db_path=library_db))
    assert payload["strata"]["builtin"]["mean"] == pytest.approx(
        injected["strata"]["builtin"]["mean"]
    )
    assert payload["strata"]["llm"]["mean"] == pytest.approx(injected["strata"]["llm"]["mean"])


def test_stratify_missing_passed_codes_or_empty_library_fail_loud(tmp_path: Path):
    with pytest.raises(ValueError):
        stratify.stratify_by_factor_source(
            pool_dates=(D1, D2),
            panel=_panel(),
            passed_codes_by_date={D1: ["A", "B", "C"]},
            factors=[BUILTIN_FACTOR, LLM_FACTOR],
            ic_stats=IC_STATS,
            top_n=1,
            horizon=2,
            config=default_matching_config(),
        )
    empty_db = tmp_path / "empty_library.db"
    with pytest.raises(ValueError):
        stratify.stratify_by_factor_source(
            pool_dates=(D1, D2),
            panel=_panel(),
            passed_codes_by_date={D1: ["A", "B", "C"], D2: ["A", "B", "C"]},
            library_db_path=empty_db,
            top_n=1,
            horizon=2,
            config=default_matching_config(),
        )


def test_stratify_empty_stratum_reports_zero_n_without_fabricated_conclusion():
    payload = _run(factors=[BUILTIN_FACTOR], ic_stats={1: (0.5, 1.0)})
    assert payload["strata"]["builtin"]["n"] == 2
    assert payload["strata"]["llm"]["n"] == 0
    assert payload["strata"]["llm"]["mean"] is None
    assert payload["strata"]["llm"]["factors"] == []
    assert payload["diff"]["ci95"] is None
    assert payload["conclusion"] == "样本不足，无法判定"


def test_conclusion_for_source_strata_wording():
    assert stratify.conclusion_for_source_strata((0.01, 0.05)) == "LLM 因子分层显著优于 builtin"
    assert stratify.conclusion_for_source_strata((-0.05, -0.01)) == "LLM 因子分层显著劣于 builtin"
    assert stratify.conclusion_for_source_strata((-0.01, 0.02)) == "无显著差异"
    assert stratify.conclusion_for_source_strata(None) == "样本不足，无法判定"
