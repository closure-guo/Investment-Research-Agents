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


def test_build_payload_reserves_untranslatable_quota() -> None:
    """untranslatable 必须有保底配额且排在 rejected 之前（否则被 limit+渲染预算结构性饿死）。"""
    processed: dict[str, dict] = {}
    for i in range(200):
        processed[f"rej{i}"] = {
            "status": "rejected",
            "expression": f"sub(div(close,ref(close,{i + 2})),1)",
            "gate": 2,
            "reason": "WEAK_ICIR",
            "at": "2026-10-21T16:00:00+08:00",
        }
    for i in range(50):
        processed[f"unt{i}"] = {
            "status": "untranslatable",
            "stage": "llm",
            "detail": "缺少按日期的横截面算子：无法计算等权市场收益与最终 cs_zscore " + "x" * 100,
            "at": f"2026-10-2{5 - (i // 25)}T16:0{i % 10}:00+08:00",
        }
    payload = build_payload(processed, {}, limit=80)

    statuses = [e["status"] for e in payload["explored"]]
    assert payload["returned"] == 80
    assert statuses.count("untranslatable") == 20  # 默认配额
    assert statuses.count("rejected") == 60
    # 配额条目紧随 admitted 之后、rejected 之前（渲染器单遍截断，位置决定可见性）
    first_rejected = statuses.index("rejected")
    assert statuses[first_rejected - 20 : first_rejected] == ["untranslatable"] * 20
    # 紧凑化：desc 与 reason 收紧，让配额能挤进 9000 字符渲染预算
    for entry in payload["explored"]:
        if entry["status"] != "untranslatable":
            continue
        assert entry["expression"] is None
        assert len(entry["desc"]) <= 60
        assert entry["reason"].startswith("untranslatable:llm")
        assert len(entry["reason"]) <= len("untranslatable:llm（）") + 40


def test_build_payload_untranslatable_quota_spills_back_to_rejected() -> None:
    """untranslatable 不足配额时，空出的槽位归还 rejected（不缩水 payload）。"""
    processed = {
        f"rej{i}": {
            "status": "rejected",
            "expression": f"sub(div(close,ref(close,{i + 2})),1)",
            "gate": 2,
            "reason": "WEAK_ICIR",
            "at": "2026-10-21T16:00:00+08:00",
        }
        for i in range(10)
    }
    processed["unt0"] = {
        "status": "untranslatable",
        "stage": "translator",
        "detail": "复杂度超限",
        "at": "2026-10-21T09:00:00+08:00",
    }
    payload = build_payload(processed, {}, limit=5)
    statuses = [e["status"] for e in payload["explored"]]
    assert statuses == ["untranslatable", "rejected", "rejected", "rejected", "rejected"]


def test_build_payload_untranslatable_quota_spans_multiple_days() -> None:
    """单一收割批次不得垄断配额：按日轮转选取，早期批次的机制族仍须可见。"""
    processed: dict[str, dict] = {}
    for i in range(40):
        processed[f"new{i}"] = {
            "status": "untranslatable",
            "stage": "llm",
            "detail": "缺少横截面算子",
            "at": f"2026-10-25T16:{i:02d}:00+08:00",
        }
    processed["old_resid_rev"] = {
        "status": "untranslatable",
        "stage": "llm",
        "detail": "缺少横截面 OLS 与 PCA",
        "at": "2026-10-21T09:00:00+08:00",
    }
    payload = build_payload(processed, {}, limit=10, untranslatable_quota=6)
    names = [e["name"] for e in payload["explored"]]
    assert payload["returned"] == 6
    assert "old_resid_rev" in names  # 旧批次仍进入配额
    assert names.count("old_resid_rev") == 1
    # 选取后仍按时间倒序呈现
    assert [e["at"] for e in payload["explored"]] == sorted(
        (e["at"] for e in payload["explored"]), reverse=True
    )


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
