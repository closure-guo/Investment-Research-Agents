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


def test_build_payload_carries_dsl_contract_single_sourced() -> None:
    from finance_agent.factors.dsl import MAX_AST_DEPTH, MAX_FIELDS, MAX_FREE_PARAMS
    from finance_agent.factors.feedback import CONTRACT_RULES
    from finance_agent.factors.qlib_translate import FIELD_MAPPINGS, OPERATOR_MAPPINGS

    payload = build_payload({}, {})
    contract = payload["contract"]
    assert contract["complexity"] == {
        "max_ast_depth": MAX_AST_DEPTH,
        "max_free_params": MAX_FREE_PARAMS,
        "max_fields": MAX_FIELDS,
    }
    expected_ops = sorted(k for k, m in OPERATOR_MAPPINGS.items() if m.dsl_name)
    expected_fields = sorted(k for k, f in FIELD_MAPPINGS.items() if f.dsl_field)
    assert sorted(contract["operators"]) == expected_ops
    assert sorted(contract["fields"]) == expected_fields
    assert contract["rules"] == list(CONTRACT_RULES)
    joined = " ".join(CONTRACT_RULES).lower()
    assert "epsilon" in joined and "cross-sectional" in joined and "machine" in joined


def test_build_payload_gate_rejections_aggregated() -> None:
    processed = {
        "a": {
            "status": "rejected",
            "expression": "sub(close,ref(close,1))",
            "gate": 2,
            "reason": "WEAK_ICIR",
            "at": "2026-10-21T16:00:00+08:00",
        },
        "b": {
            "status": "rejected",
            "expression": "sub(close,ref(close,2))",
            "gate": 4,
            "reason": "NO_POOL_INCREMENT",
            "at": "2026-10-21T16:00:01+08:00",
        },
        "c": {
            "status": "rejected",
            "expression": "sub(close,ref(close,3))",
            "gate": 2,
            "reason": "WEAK_ICIR",
            "at": "2026-10-21T16:00:02+08:00",
        },
        "d": {
            "status": "admitted",
            "expression": "div(volume,ts_mean(volume,20))",
            "at": "2026-10-21T16:00:03+08:00",
        },
    }
    payload = build_payload(processed, {})
    assert payload["gate_rejections"] == {"2": 2, "4": 1}


def test_cli_prints_contract_presence(tmp_path) -> None:
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    state = tmp_path / "state.json"
    cand = tmp_path / "cand.json"
    out = tmp_path / "explored.json"
    state.write_text(
        json.dumps(
            {
                "processed": {
                    "a": {
                        "status": "admitted",
                        "expression": "div(volume,ts_mean(volume,20))",
                        "at": "2026-10-21T16:00:00+08:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cand.write_text(json.dumps({"candidates": []}), encoding="utf-8")
    proc = subprocess.run(  # noqa: S603 - 固定脚本路径,无外部输入
        [
            sys.executable,
            str(repo / "scripts" / "export_factor_feedback.py"),
            "--state",
            str(state),
            "--candidates",
            str(cand),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo,
    )
    assert "contract=yes" in proc.stdout
