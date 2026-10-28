"""factors/reject_backfill.py 单测（add-rejected-factor-pool T5）。

覆盖：存量收割状态文件 gate-rejected 候选按**原裁决原样**导入拒绝池；无 DSL
（``untranslatable`` / ``transcription_error``）跳过；同构重复命中 upsert 幂等；
缺失 ``ast_json`` 现算。数据库全部落在 ``tmp_path``（零网络、零共享状态）。
"""

from __future__ import annotations

import json

from finance_agent.factors.dsl import parse, serialize
from finance_agent.factors.library import list_events, list_factors
from finance_agent.factors.reject_backfill import backfill_rejected_from_state


def _write_state(tmp_path, processed: dict) -> object:
    state = tmp_path / "harvest_state.json"
    state.write_text(
        json.dumps({"updated_at": "2026-10-28T16:00:00", "processed": processed}),
        encoding="utf-8",
    )
    return state


def test_backfill_imports_gate_rejected_with_original_verdict(tmp_path):
    state = _write_state(
        tmp_path,
        {
            "vol_shock": {
                "status": "rejected",
                "expression": "ts_mean(volume,5)",
                "qlib_expression": "Mean(volume,5)",
                "gate": 4,
                "reason": "REDUNDANT_WITH_POOL",
                "detail": {"duplicate_of": 3},
                "attempts": 1,
                "at": "2026-10-20T16:00:00",
            },
        },
    )
    db = str(tmp_path / "factor_library.db")
    summary = backfill_rejected_from_state(state, db_path=db)
    assert summary["scanned"] == 1
    assert summary["imported"] == 1
    rows = list_factors(status="rejected", db_path=db)
    assert len(rows) == 1
    assert rows[0]["expression"] == "ts_mean(volume,5)"
    assert rows[0]["source"] == "backfill"
    # ast_json 缺失 → 现算规范串
    assert rows[0]["ast_json"] == serialize(parse("ts_mean(volume,5)"))
    events = list_events(int(rows[0]["id"]), db_path=db)
    assert [e["event"] for e in events] == ["created"]
    # 原裁决原样：原闸口编号与原因码，不重新过闸
    assert events[0]["detail"]["gate"] == 4
    assert events[0]["detail"]["reason"] == "REDUNDANT_WITH_POOL"
    assert events[0]["detail"]["duplicate_of"] == 3


def test_backfill_skips_no_expression_and_other_statuses(tmp_path):
    state = _write_state(
        tmp_path,
        {
            "no_expr": {
                "status": "rejected",
                "gate": None,
                "reason": None,
                "detail": "不可翻译（确定性映射）：算子不在白名单",
                "attempts": 1,
                "at": "t",
            },
            "untrans": {"status": "untranslatable", "stage": "translator"},
            "tx_err": {"status": "transcription_error", "detail": "输出截断"},
            "admitted_one": {"status": "admitted", "expression": "close"},
        },
    )
    db = str(tmp_path / "factor_library.db")
    summary = backfill_rejected_from_state(state, db_path=db)
    assert summary["imported"] == 0
    assert summary["skipped_no_expression"] == 1
    assert summary["skipped_other"] == 3
    assert list_factors(include_rejected=True, db_path=db) == []


def test_backfill_idempotent_rerun_zero_new_rows(tmp_path):
    state = _write_state(
        tmp_path,
        {
            "vol_shock": {
                "status": "rejected",
                "expression": "volume",
                "gate": 2,
                "reason": "WEAK_ICIR",
                "detail": {},
                "at": "t",
            },
        },
    )
    db = str(tmp_path / "factor_library.db")
    first = backfill_rejected_from_state(state, db_path=db)
    assert first["imported"] == 1
    second = backfill_rejected_from_state(state, db_path=db)
    assert second["imported"] == 0
    assert second["updated"] == 1
    assert len(list_factors(status="rejected", db_path=db)) == 1
    events = list_events(int(list_factors(status="rejected", db_path=db)[0]["id"]), db_path=db)
    assert [e["event"] for e in events] == ["created", "re_rejected"]


def test_backfill_uses_candidates_file_for_hypothesis_and_category(tmp_path):
    candidates = tmp_path / "candidates.json"
    candidates.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "name": "vol_shock",
                        "description": "[Volume Shock] 成交量冲击与次日收益",
                        "formulation": "volume / ts_mean(volume,20)",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    state = _write_state(
        tmp_path,
        {
            "vol_shock": {
                "status": "rejected",
                "expression": "volume",
                "gate": 2,
                "reason": "WEAK_ICIR",
                "detail": {},
                "at": "t",
            },
        },
    )
    db = str(tmp_path / "factor_library.db")
    backfill_rejected_from_state(state, db_path=db, candidates_path=candidates)
    rows = list_factors(status="rejected", db_path=db)
    assert rows[0]["hypothesis"] == "[RD-Agent/vol_shock] [Volume Shock] 成交量冲击与次日收益"
    # harvest 同款机理分桶（volume 命中「量与流动性」）
    assert rows[0]["category"] == "量与流动性"


def test_backfill_without_candidates_file_uses_fallback(tmp_path):
    state = _write_state(
        tmp_path,
        {
            "mystery": {
                "status": "rejected",
                "expression": "close",
                "gate": 3,
                "reason": "NOT_SIGNIFICANT",
                "detail": {},
                "at": "t",
            },
        },
    )
    db = str(tmp_path / "factor_library.db")
    backfill_rejected_from_state(state, db_path=db)
    rows = list_factors(status="rejected", db_path=db)
    assert rows[0]["hypothesis"] == "[backfill/mystery]"
    assert rows[0]["category"] == "外部-其他"


def test_cli_script_smoke(tmp_path):
    """CLI ``--state``/``--db`` 显式必填；输出摘要 JSON。"""
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    state = _write_state(
        tmp_path,
        {
            "vol_shock": {
                "status": "rejected",
                "expression": "volume",
                "gate": 2,
                "reason": "WEAK_ICIR",
                "detail": {},
                "at": "t",
            },
        },
    )
    db = tmp_path / "cli.db"
    proc = subprocess.run(  # noqa: S603 - 固定脚本路径,无外部输入
        [
            sys.executable,
            str(repo_root / "scripts" / "backfill_rejected_factors.py"),
            "--state",
            str(state),
            "--db",
            str(db),
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=True,
    )
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    assert summary["imported"] == 1
    assert len(list_factors(status="rejected", db_path=str(db))) == 1
