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


def _gate_floor_processed() -> dict[str, dict]:
    """真实漏斗比例的合成状态：闸② 395 / 闸④ 84 / admitted 12 / untranslatable 30。"""
    processed: dict[str, dict] = {}
    for i in range(395):
        processed[f"g2_{i}"] = {
            "status": "rejected",
            "expression": f"sub(div(close,ref(close,{i + 2})),1)",
            "gate": 2,
            "reason": "WEAK_ICIR",
            "at": "2026-10-27T16:00:00+08:00",
        }
    for i in range(84):
        processed[f"g4_{i}"] = {
            "status": "rejected",
            "expression": f"mul(sub(div(volume,ts_mean(volume,20)),1),{i + 1})",
            "gate": 4,
            "reason": "REDUNDANT_WITH_POOL",
            "at": "2026-10-26T16:00:00+08:00",
        }
    for i in range(12):
        processed[f"adm_{i}"] = {
            "status": "admitted",
            "expression": f"div(volume,ts_mean(volume,{i + 5}))",
            "at": "2026-10-20T16:00:00+08:00",
        }
    for i in range(30):
        processed[f"unt_{i}"] = {
            "status": "untranslatable",
            "stage": "translator",
            "detail": "复杂度超限",
            "at": "2026-10-27T09:00:00+08:00",
        }
    return processed


def test_build_payload_gate_floor_breaks_gate2_monopoly() -> None:
    """闸② 不得独占 rejected 档：每个出现过拒收的闸口须有保底出场（闸④ 现状恒 0）。"""
    payload = build_payload(_gate_floor_processed(), {}, limit=80)
    gates = [e["gate"] for e in payload["explored"] if e["status"] == "rejected"]
    assert sum(1 for g in gates if g == 4) >= 5
    assert sum(1 for g in gates if g == 2) < 48  # rejected 档预算 = 80 - 12 admitted - 20 配额
    assert payload["returned"] == 80  # 总量不被保底机制缩水


def test_build_payload_gate_floor_underfull_gate_fully_shown() -> None:
    """累计拒收不足保底数的闸口全量出场，空位由余量池使用（不缩水 payload）。"""
    processed = {
        f"g2_{i}": {
            "status": "rejected",
            "expression": f"sub(div(close,ref(close,{i + 2})),1)",
            "gate": 2,
            "reason": "WEAK_ICIR",
            "at": "2026-10-27T16:00:00+08:00",
        }
        for i in range(100)
    }
    processed["g6_a"] = {
        "status": "rejected",
        "expression": "sub(close,ref(close,1))",
        "gate": 6,
        "reason": "NO_POOL_INCREMENT",
        "at": "2026-10-27T16:00:00+08:00",
    }
    processed["g6_b"] = {
        "status": "rejected",
        "expression": "sub(close,ref(close,3))",
        "gate": 6,
        "reason": "NO_POOL_INCREMENT",
        "at": "2026-10-27T15:00:00+08:00",
    }
    payload = build_payload(processed, {}, limit=20)
    g6 = [e for e in payload["explored"] if e["gate"] == 6]
    assert len(g6) == 2
    assert payload["returned"] == 20


def test_build_payload_gate_floor_configurable() -> None:
    """spec「默认每闸 ≥5 条，可配置」：gate_floor 可显式调低，保底数随之生效。"""
    processed = _gate_floor_processed()
    payload = build_payload(processed, {}, limit=80, gate_floor=2)
    gates = [e["gate"] for e in payload["explored"] if e["status"] == "rejected"]
    assert sum(1 for g in gates if g == 4) >= 2
    assert payload["returned"] == 80
    default = build_payload(processed, {}, limit=80)
    default_g4 = sum(1 for e in default["explored"] if e["status"] == "rejected" and e["gate"] == 4)
    assert sum(1 for g in gates if g == 4) < default_g4  # 调低保底 → 闸④ 出场减少


def _stage_mix_processed() -> dict[str, dict]:
    """同日双 stage 的 untranslatable：translator 300 条 / llm 60 条（LLM 段=最大损耗桶）。"""
    processed: dict[str, dict] = {}
    for i in range(300):
        processed[f"tr_{i}"] = {
            "status": "untranslatable",
            "stage": "translator",
            "detail": "复杂度超限：自由参数 4 > 上限 3",
            "at": f"2026-10-25T16:{i % 60:02d}:{i % 60:02d}+08:00",
        }
    for i in range(60):
        processed[f"ll_{i}"] = {
            "status": "untranslatable",
            "stage": "llm",
            "detail": "缺少按日期的横截面算子",
            "at": f"2026-10-25T17:{i % 60:02d}:{i % 60:02d}+08:00",
        }
    return processed


def test_build_payload_untranslatable_stage_representativeness() -> None:
    """单一失败类别（stage）不得独占 untranslatable 档：translator/llm 均须出场。"""
    payload = build_payload(_stage_mix_processed(), {}, limit=80)
    stages = [
        e["reason"].split("（", 1)[0].removeprefix("untranslatable:")
        for e in payload["explored"]
        if e["status"] == "untranslatable"
    ]
    assert set(stages) == {"translator", "llm"}
    assert 0 < stages.count("llm") < 20


def test_build_payload_quota_allocation_readout_consistent() -> None:
    """配额分配读数须与 explored 清单实际构成逐项一致（可程序核对）。"""
    processed = _stage_mix_processed()
    processed["g2_0"] = {
        "status": "rejected",
        "expression": "sub(div(close,ref(close,2)),1)",
        "gate": 2,
        "reason": "WEAK_ICIR",
        "at": "2026-10-27T16:00:00+08:00",
    }
    processed["g4_0"] = {
        "status": "rejected",
        "expression": "mul(sub(div(volume,ts_mean(volume,20)),1),7)",
        "gate": 4,
        "reason": "REDUNDANT_WITH_POOL",
        "at": "2026-10-26T16:00:00+08:00",
    }
    payload = build_payload(processed, {}, limit=80)
    alloc = payload["quota_allocation"]
    rej_gates = [str(e["gate"]) for e in payload["explored"] if e["status"] == "rejected"]
    assert alloc["rejected_by_gate"] == {g: rej_gates.count(g) for g in set(rej_gates)}
    unt_stages = [
        e["reason"].split("（", 1)[0].removeprefix("untranslatable:")
        for e in payload["explored"]
        if e["status"] == "untranslatable"
    ]
    assert alloc["untranslatable_by_stage"] == {s: unt_stages.count(s) for s in set(unt_stages)}
