"""日报零买入告警测试（2026-11-06 零买入修复 Task 1）。

契约：``zero_buy_warning(mix)`` 连续零买入 ≥3 天返回中文告警句，否则 None；
``collect_daily_facts`` 注入 ``facts["decision_mix"]``（分布 + streak + 告警句），
供 LLM 日报与降级渲染两条路径都能看见「决策分布退化」。
"""

from __future__ import annotations

import json
import sqlite3

from finance_agent.outcome.daily_brief import collect_daily_facts, render_fallback, zero_buy_warning
from finance_agent.outcome.track_record.model import init_predictions


def test_warns_after_three_zero_buy_days():
    mix = {"zero_buy_streak": 3, "total": {"buy": 0, "sell": 20, "watch": 10, "other": 0}}
    msg = zero_buy_warning(mix)
    assert msg is not None
    assert "连续 3 个交易日零买入" in msg


def test_silent_below_threshold():
    assert zero_buy_warning({"zero_buy_streak": 2, "total": {}}) is None


def test_custom_threshold_overrides_default():
    assert zero_buy_warning({"zero_buy_streak": 2, "total": {}}, threshold=2) is not None


def _seed_predictions(path, rows) -> None:
    init_predictions(path)
    con = sqlite3.connect(path)
    con.executemany(
        "INSERT INTO predictions "
        "(prediction_id, source_type, symbol, direction, rationale_snapshot, status, "
        "created_at, resolved_at, raw_return, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                f"p{i}",
                "live",
                "600519",
                "short",
                json.dumps({"action": act}, ensure_ascii=False),
                "open",
                ts,
                None,
                None,
                ts,
            )
            for i, (ts, act) in enumerate(rows)
        ],
    )
    con.commit()
    con.close()


def test_collect_facts_carries_decision_mix_and_warning(tmp_path):
    sessions = tmp_path / "sessions.db"
    _seed_predictions(
        sessions,
        [
            ("2026-11-02T18:00:00", "watch"),
            ("2026-11-03T18:00:00", "sell"),
            ("2026-11-04T18:00:00", "sell"),
        ],
    )
    facts = collect_daily_facts(
        "2026-11-04",
        sessions_db_path=sessions,
        screening_db_path=tmp_path / "missing-screening.db",
        execution_db_path=tmp_path / "missing-execution.db",
        factor_db_path=tmp_path / "missing-factor.db",
    )
    mix = facts["decision_mix"]
    assert mix["zero_buy_streak"] >= 3
    assert "连续" in (mix["zero_buy_warning"] or "")


def test_collect_facts_no_warning_when_buy_recent(tmp_path):
    sessions = tmp_path / "sessions.db"
    _seed_predictions(
        sessions,
        [
            ("2026-11-02T18:00:00", "watch"),
            ("2026-11-03T18:00:00", "buy"),
            ("2026-11-04T18:00:00", "sell"),
        ],
    )
    facts = collect_daily_facts(
        "2026-11-04",
        sessions_db_path=sessions,
        screening_db_path=tmp_path / "missing-screening.db",
        execution_db_path=tmp_path / "missing-execution.db",
        factor_db_path=tmp_path / "missing-factor.db",
    )
    mix = facts["decision_mix"]
    assert mix["zero_buy_streak"] == 1
    assert "zero_buy_warning" not in mix


def test_fallback_renderer_surfaces_warning():
    facts = {
        "date": "2026-11-04",
        "warnings": [],
        "screening": {},
        "predictions": {},
        "execution": {},
        "factors": {},
        "failures": [],
        "decision_mix": {
            "zero_buy_streak": 3,
            "total": {"buy": 0, "sell": 2, "watch": 1, "other": 0},
            "zero_buy_warning": "决策分布异常：连续 3 个交易日零买入",
        },
    }
    markdown = render_fallback(facts, "boom")
    assert "连续零买入天数：3" in markdown
    assert "决策分布异常：连续 3 个交易日零买入" in markdown
