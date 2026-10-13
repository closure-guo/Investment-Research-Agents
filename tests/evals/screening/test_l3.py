"""±L3 粗筛 keep/drop 前向对照单测（add-screening-evaluation Task 3；合成面板，零网络）。

spec causal-ablation「L3 粗筛增量变体」：keep 组 = ``triage_decisions.verdict=="keep"``
且入池；drop 组 = ``verdict=="drop"``；两组 20 日（本单测缩窗）前向收益 + 踩雷率 +
配对 CI；**仅当 drop 显著低于 keep 方可陈述具有排雷价值**（措辞判定在此钉死）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from evals.screening import l3
from finance_agent.execution.config import default_matching_config
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.screening.candidate_llm import TriageDecision
from finance_agent.screening.ranker import Candidate
from finance_agent.screening.store import (
    record_l2_candidates,
    record_screening_batch,
    record_triage_decisions,
)

DATES = tuple(f"2026-04-{day:02d}" for day in range(1, 13))  # d0..d11
POOL_DATES = DATES[:3]

SLIPPAGE_RATE = 0.002

CLOSES: dict[str, list[float]] = {
    "K": [10.0, 10.0, 10.0, 10.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
    "Q": [10.0, 10.0, 10.0, 10.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0],
    "P": [10.0] * 12,  # keep 但未入池（门槛未达）→ 两组都不得计入
}


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


def _seed(tmp_path: Path) -> Path:
    """库造数：K keep 入池、Q drop、P keep 未入池（三池日）。"""
    db_path = tmp_path / "screening.db"
    for pool_date in POOL_DATES:
        record_l2_candidates(
            pool_date,
            [
                Candidate(
                    symbol="K",
                    trade_date=pool_date,
                    composite_score=3.0,
                    factor_contribs=(),
                    rank=1,
                ),
                Candidate(
                    symbol="Q",
                    trade_date=pool_date,
                    composite_score=2.0,
                    factor_contribs=(),
                    rank=2,
                ),
                Candidate(
                    symbol="P",
                    trade_date=pool_date,
                    composite_score=1.0,
                    factor_contribs=(),
                    rank=3,
                ),
            ],
            db_path=db_path,
        )
        record_screening_batch(
            pool_date,
            status="ok",
            started_at="2026-04-01T00:00:00",
            finished_at="2026-04-01T00:01:00",
            l2_top=3,
            l3_keep=2,
            pool_rows=[
                {
                    "symbol": "K",
                    "composite_score": 3.0,
                    "rank": 1,
                    "consecutive_days": 3,
                    "l3_verdict": "keep",
                    "l3_reason": "合成",
                    "factor_contribs": [],
                }
            ],
            db_path=db_path,
        )
        record_triage_decisions(
            pool_date,
            [
                TriageDecision(symbol="K", verdict="keep", reason="合成 keep"),
                TriageDecision(symbol="Q", verdict="drop", reason="合成 drop"),
                TriageDecision(symbol="P", verdict="keep", reason="合成 keep（未入池）"),
            ],
            db_path=db_path,
        )
    return db_path


def test_keep_drop_compare_hand_computed(tmp_path: Path):
    payload = l3.l3_keep_drop_compare(
        pool_dates=POOL_DATES,
        panel=_panel(),
        db_path=_seed(tmp_path),
        horizon=5,
        config=default_matching_config(),
    )
    assert payload["pool_dates"] == list(POOL_DATES)
    assert payload["horizon"] == 5

    # 手算：d0/d1/d2 入场 d1/d2/d3（开盘 10 → 成交 10.02）；出场 d6/d7/d8
    keep_rets = [_ret(10.0, 12.0), _ret(10.0, 13.0), _ret(10.0, 14.0)]
    drop_rets = [_ret(10.0, 8.0), _ret(10.0, 7.0), _ret(10.0, 6.0)]
    assert payload["keep"]["n"] == 3  # P（keep 未入池）不计入
    assert payload["keep"]["mean"] == pytest.approx(sum(keep_rets) / 3)
    assert payload["keep"]["blowup_rate"] == 0.0
    assert payload["drop"]["n"] == 3
    assert payload["drop"]["mean"] == pytest.approx(sum(drop_rets) / 3)
    assert payload["drop"]["blowup_rate"] == 1.0  # 全部 ≤ -10%
    assert payload["blowup_threshold"] == l3.BLOWUP_THRESHOLD

    # 配对（池日）均值差（keep − drop）显著为正 → 具有排雷价值
    day_diffs = [
        keep_ret - drop_ret for keep_ret, drop_ret in zip(keep_rets, drop_rets, strict=True)
    ]
    assert payload["diff"]["paired_days"] == 3
    assert payload["diff"]["diff_mean"] == pytest.approx(sum(day_diffs) / 3)
    lo, hi = payload["diff"]["ci95"]
    assert lo > 0
    assert payload["conclusion"] == "具有排雷价值"
    assert payload["diff"]["conclusion"] == "具有排雷价值"


def test_groups_require_keep_and_pool_membership(tmp_path: Path):
    """keep 组 = keep ∩ 入池：未入池的 keep 不进任何组（对照只看粗筛增量）。"""
    groups = l3.build_l3_keep_drop_groups(POOL_DATES, db_path=_seed(tmp_path))
    assert groups["keep"] == {pool_date: ["K"] for pool_date in POOL_DATES}
    assert groups["drop"] == {pool_date: ["Q"] for pool_date in POOL_DATES}


def test_l3_value_conclusion_wording():
    """措辞判定：CI 不含 0 且 drop < keep → 排雷价值；含 0 → 无显著差异。"""
    assert l3.l3_value_conclusion((0.005, 0.09)) == "具有排雷价值"
    assert l3.l3_value_conclusion((-0.01, 0.06)) == "无显著差异"
    assert l3.l3_value_conclusion((-0.09, -0.02)) == "显著反向（keep 组低于 drop 组）"
    assert l3.l3_value_conclusion(None) == "样本不足，无法判定"


def test_l3_compare_insufficient_ci_when_single_paired_day(tmp_path: Path):
    payload = l3.l3_keep_drop_compare(
        pool_dates=POOL_DATES[:1],
        panel=_panel(),
        db_path=_seed(tmp_path),
        horizon=5,
        config=default_matching_config(),
    )
    assert payload["diff"]["ci95"] is None
    assert payload["conclusion"] == "样本不足，无法判定"
