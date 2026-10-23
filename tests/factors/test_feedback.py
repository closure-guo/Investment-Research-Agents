"""跨会话因子记忆桥（feedback.py）单测。"""

from __future__ import annotations

import json

import pytest

from finance_agent.factors.feedback import (
    build_payload,
    canonical_expression,
    export_feedback,
    load_candidates,
    load_state,
)


def test_canonical_epsilon_guard_collapses() -> None:
    plain = "div(close,ts_mean(volume,20))"
    guarded = "div(close,add(ts_mean(volume,20),0.000000000001))"
    assert canonical_expression(plain) == canonical_expression(guarded)


def test_canonical_commutative_add_sorted() -> None:
    assert canonical_expression("add(close,volume)") == canonical_expression("add(volume,close)")


def test_canonical_invalid_returns_none() -> None:
    assert canonical_expression("not_a_real_expr(") is None


def test_build_payload_dedupes_by_canonical_and_orders_admitted_first() -> None:
    processed = {
        "mom20": {
            "status": "rejected",
            "expression": "sub(div(close,ref(close,20)),1)",
            "gate": 2,
            "reason": "WEAK_ICIR",
            "at": "2026-10-21T16:00:00+08:00",
        },
        "MOM_20D": {
            "status": "rejected",
            "expression": "sub(div(close,ref(close,20)),1)",
            "gate": 2,
            "reason": "WEAK_ICIR",
            "at": "2026-10-20T10:00:00+08:00",
        },
        "volratio20": {
            "status": "admitted",
            "expression": "sub(div(volume,ts_mean(volume,20)),1)",
            "at": "2026-10-19T15:35:31+08:00",
        },
        "obv_odd": {
            "status": "untranslatable",
            "stage": "translator",
            "detail": "复杂度超限：自由参数 4 > 上限 3",
            "at": "2026-10-21T09:00:00+08:00",
        },
    }
    candidates = {
        "mom20": {"name": "mom20", "description": "Twenty-day momentum"},
        "MOM_20D": {"name": "MOM_20D", "description": "20-day momentum"},
        "volratio20": {"name": "volratio20", "description": "Volume ratio"},
        "obv_odd": {"name": "obv_odd", "description": "OBV variant"},
    }
    payload = build_payload(processed, candidates, limit=10)

    names = [e["name"] for e in payload["explored"]]
    assert names[0] == "volratio20"  # admitted 优先
    assert "MOM_20D" not in names  # 同 canonical 表达式去重
    assert payload["counts"]["admitted"] == 1
    assert payload["counts"]["rejected"] == 1
    assert payload["counts"]["untranslatable"] == 1
    unt = next(e for e in payload["explored"] if e["status"] == "untranslatable")
    assert unt["expression"] is None
    assert unt["reason"].startswith("untranslatable:translator")


def test_build_payload_limit_truncates() -> None:
    processed = {
        f"f{i}": {
            "status": "rejected",
            "expression": f"sub(div(close,ref(close,{i + 1})),1)",
            "gate": 2,
            "reason": "WEAK_ICIR",
            "at": "2026-10-21T16:00:00+08:00",
        }
        for i in range(5)
    }
    payload = build_payload(processed, {}, limit=2)
    assert payload["returned"] == 2
    assert len(payload["explored"]) == 2
    assert payload["total_explored"] == 5


def test_export_feedback_roundtrip(tmp_path) -> None:
    state = {
        "updated_at": "x",
        "processed": {
            "a": {
                "status": "rejected",
                "expression": "sub(div(close,ref(close,5)),1)",
                "gate": 2,
                "reason": "WEAK_ICIR",
                "at": "2026-10-21T16:00:00+08:00",
            }
        },
    }
    candidates = {"candidates": [{"name": "a", "description": "5-day momentum"}]}
    state_path = tmp_path / "state.json"
    cand_path = tmp_path / "cand.json"
    out = tmp_path / "explored.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    cand_path.write_text(json.dumps(candidates), encoding="utf-8")

    payload = export_feedback(state_path=state_path, candidates_path=cand_path, output_path=out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["returned"] == 1
    assert written["explored"][0]["name"] == "a"
    assert payload["explored"][0]["desc"] == "5-day momentum"


def test_load_state_fails_loud_on_missing_processed(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_state(bad)
    with pytest.raises(ValueError):
        load_candidates(bad)
