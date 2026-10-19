"""recover-factor-supply-losses 复跑测量（2026-10-19，零 LLM / 临时库）。

用法::

    uv run python tests/validation/2026-10-19-recover-factor-supply-losses-replay.py \\
        --state <main>/data/factor_research/harvest_state.json \\
        --candidates <main>/data/factor_research/candidates.json \\
        --snapshot-db <main>/data/market_snapshot.db \\
        --r2-db tmp/replay_r2.db --r1-db tmp/replay_r1.db \\
        --out reports/recover-factor-supply-losses-replay.json

两块读数（各自独立空库起步，忠实于「重放当刻生产库为空」的语义）：

- **R2**：``stage=translator`` 的不可翻译候选 → 新翻译器（负数常量重排 + 深度 8）复译
  → 可恢复者组批过五道闸（读数 = 这些候选在现行面板上的闸口裁决）；
- **R1**：已拒候选（``status=rejected``）→ 存库 DSL 表达式重放复检（面板 150→250 与
  代码演进后的裁决 diff；无变化亦为读数）。

LLM 重试实测（transcription_error 两条）由 ``scripts/replay_factor_harvest.py`` 实网执行，
不在本脚本内（本脚本零 LLM）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from finance_agent.factors import qlib_translate
from finance_agent.factors.external import run_factor_external_import


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidates_by_name(candidates_file: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json(candidates_file)
    items = payload.get("candidates") if isinstance(payload, dict) else None
    return {str(c.get("name")): c for c in (items or []) if isinstance(c, dict) and c.get("name")}


def _hypothesis(candidate: dict[str, Any] | None, name: str, note: str) -> str:
    if not candidate:
        return f"[RD-Agent/{name}] {note}"
    description = str(candidate.get("description") or "").strip()
    formulation = str(candidate.get("formulation") or "").strip()
    return f"[RD-Agent/{name}] {description}；formulation: {formulation}；{note}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="供给线损耗复跑测量（R1 已拒复检 / R2 复译恢复）")
    parser.add_argument("--state", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--snapshot-db", default=None)
    parser.add_argument("--r2-db", required=True)
    parser.add_argument("--r1-db", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    state = _load_json(Path(args.state)).get("processed") or {}
    candidates = _candidates_by_name(Path(args.candidates))

    # ── R2：translator 阶段不可翻译 → 复译 ────────────────────────────────────
    translator_untranslatable = {
        name: verdict
        for name, verdict in state.items()
        if verdict.get("status") == "untranslatable"
        and verdict.get("stage") == "translator"
        and verdict.get("qlib_expression")
    }
    r2_verdicts: list[dict[str, Any]] = []
    r2_payload: list[dict[str, Any]] = []
    for name, verdict in sorted(translator_untranslatable.items()):
        result = qlib_translate.translate_qlib_expression(str(verdict["qlib_expression"]))
        r2_verdicts.append(
            {
                "name": name,
                "old_detail": verdict.get("detail"),
                "qlib_expression": verdict["qlib_expression"],
                "recovered": result.translatable,
                "dsl_expression": result.dsl_expression,
                "blockers": list(result.blockers),
            }
        )
        if result.translatable:
            r2_payload.append(
                {
                    "expression": result.dsl_expression,
                    "hypothesis": _hypothesis(
                        candidates.get(name), name, "复译恢复（recover-factor-supply-losses R2）"
                    ),
                    "category": "外部",
                }
            )
    r2_summary = (
        run_factor_external_import(
            r2_payload,
            db_path=args.r2_db,
            snapshot_db_path=args.snapshot_db,
            ledger_source="external",
        )
        if r2_payload
        else {"admitted": 0, "rejected": 0, "errors": 0, "rejections": []}
    )
    r2_rejected = {str(r.get("expression")): r for r in r2_summary["rejections"]}

    # ── R1：已拒候选复检 ─────────────────────────────────────────────────────
    rejected = {
        name: verdict for name, verdict in state.items() if verdict.get("status") == "rejected"
    }
    r1_payload = [
        {
            "expression": verdict["expression"],
            "hypothesis": _hypothesis(candidates.get(name), name, "重放复检 R1"),
            "category": "外部",
        }
        for name, verdict in sorted(rejected.items())
        if verdict.get("expression")
    ]
    r1_summary = run_factor_external_import(
        r1_payload,
        db_path=args.r1_db,
        snapshot_db_path=args.snapshot_db,
        ledger_source="external",
    )
    r1_rejected_new = {str(r.get("expression")): r for r in r1_summary["rejections"]}
    r1_diff: list[dict[str, Any]] = []
    for name, verdict in sorted(rejected.items()):
        expression = str(verdict.get("expression"))
        new = r1_rejected_new.get(expression)
        if new is None:
            outcome = "admitted（复检通过！）"
            new_gate, new_reason = None, None
        else:
            new_gate, new_reason = new.get("gate"), new.get("reason")
            if str(new_gate) == str(verdict.get("gate")) and new_reason == verdict.get("reason"):
                outcome = "same"
            else:
                outcome = "changed"
        r1_diff.append(
            {
                "name": name,
                "expression": expression,
                "old_gate": verdict.get("gate"),
                "old_reason": verdict.get("reason"),
                "new_gate": new_gate,
                "new_reason": new_reason,
                "outcome": outcome,
                "new_detail": (new or {}).get("detail"),
            }
        )

    # ── 打印 ────────────────────────────────────────────────────────────────
    print("=" * 72)
    print(f"R2 复译恢复：{len(translator_untranslatable)} 条 translator 阶段不可翻译")
    for row in r2_verdicts:
        mark = "恢复" if row["recovered"] else "仍拒"
        print(f"  [{mark}] {row['name']}: {row['dsl_expression'] or row['blockers']}")
    print("R2 过闸摘要：" + json.dumps({k: v for k, v in r2_summary.items() if k != "rejections"}))
    for row in r2_verdicts:
        if not row["recovered"]:
            continue
        new = r2_rejected.get(str(row.get("dsl_expression")))
        if new is None:
            print(f"  {row['name']}: admitted")
        else:
            print(
                f"  {row['name']}: gate={new.get('gate')} reason={new.get('reason')} "
                f"detail={json.dumps(new.get('detail'), ensure_ascii=False)}"
            )
    print("=" * 72)
    print(f"R1 已拒复检：{len(rejected)} 条")
    for row in r1_diff:
        print(
            f"  {row['name']}: old(gate={row['old_gate']},{row['old_reason']}) "
            f"-> new(gate={row['new_gate']},{row['new_reason']}) [{row['outcome']}]"
        )
    print("R1 过闸摘要：" + json.dumps({k: v for k, v in r1_summary.items() if k != "rejections"}))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        artifact = {
            "state_file": args.state,
            "candidates_file": args.candidates,
            "snapshot_db": args.snapshot_db,
            "r2": {
                "verdicts": r2_verdicts,
                "summary": {k: v for k, v in r2_summary.items() if k != "rejections"},
                "rejections": r2_summary["rejections"],
            },
            "r1": {
                "diff": r1_diff,
                "summary": {k: v for k, v in r1_summary.items() if k != "rejections"},
                "rejections": r1_summary["rejections"],
            },
        }
        out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"产物：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
