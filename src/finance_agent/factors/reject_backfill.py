"""存量拒绝候选回填（add-rejected-factor-pool T5）。

从收割状态文件（``harvest.py`` 的 processed 映射）把历史 gate-rejected（带 DSL
表达式）候选按**原裁决原样**导入拒绝池——不重新过闸、不重新裁决（事件 detail
携带状态文件中记录的拒收闸口与原因）。无 DSL 的 ``untranslatable`` /
``transcription_error`` 记录不在导入面内（拒绝池只收「过转译、死在闸门」族）。

幂等：同构命中走 ``record_rejected_factor`` 的 upsert 路径（重复执行零新增）。
``ast_json`` 缺失的旧记录由库层现算规范串。``hypothesis`` / ``category`` 重建：
``candidates_path`` 给定且按 name 命中时沿用 harvest 同款形态（``[RD-Agent/名]
描述`` + 机理分桶），否则用 ``[backfill/名]`` + 兜底桶（拒绝池行不参与分桶保种，
category 仅诊断用）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from finance_agent.factors.dsl import DSLParseError
from finance_agent.factors.harvest import CATEGORY_FALLBACK, classify_category
from finance_agent.factors.library import record_rejected_factor


def _descriptions_from_candidates(candidates_path: str | Path | None) -> dict[str, str]:
    """候选文件 → ``{name: description}``；文件缺失/形态不符 → 空表（不阻断回填）。"""
    if candidates_path is None:
        return {}
    raw = json.loads(Path(candidates_path).read_text(encoding="utf-8"))
    entries = raw.get("candidates") if isinstance(raw, Mapping) else None
    if not isinstance(entries, list):
        return {}
    out: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, Mapping) and entry.get("name"):
            out[str(entry["name"])] = str(entry.get("description") or "").strip()
    return out


def backfill_rejected_from_state(
    state_path: str | Path,
    *,
    db_path: str | Path | None = None,
    candidates_path: str | Path | None = None,
) -> dict[str, int]:
    """收割状态文件 → 拒绝池（原裁决原样导入）。

    返回摘要：``scanned``（processed 总条数）/ ``imported``（新建行）/
    ``updated``（同构 upsert 更新）/ ``skipped_no_expression``（rejected 但无 DSL）/
    ``skipped_unparseable``（表达式不可解析的脏行）/ ``skipped_other``
    （非 rejected 状态与非 Mapping 条目）。
    """
    scanned = 0
    imported = 0
    updated = 0
    skipped_no_expression = 0
    skipped_unparseable = 0
    skipped_other = 0
    payload = json.loads(Path(state_path).read_text(encoding="utf-8"))
    processed = payload.get("processed") if isinstance(payload, Mapping) else None
    if not isinstance(processed, dict):
        raise ValueError(f"状态文件缺少 processed 映射: {state_path}")
    descriptions = _descriptions_from_candidates(candidates_path)
    for name, verdict in processed.items():
        scanned += 1
        if not isinstance(verdict, Mapping) or str(verdict.get("status")) != "rejected":
            skipped_other += 1
            continue
        expression = str(verdict.get("expression") or "").strip()
        if not expression:
            skipped_no_expression += 1
            continue
        description = descriptions.get(str(name))
        hypothesis = f"[RD-Agent/{name}] {description}" if description else f"[backfill/{name}]"
        category = classify_category(name, description) if description else CATEGORY_FALLBACK
        raw_detail = verdict.get("detail")
        detail_dict: dict[str, Any] | None
        if isinstance(raw_detail, Mapping):
            detail_dict = dict(raw_detail)
        elif raw_detail:
            detail_dict = {"detail": str(raw_detail)}
        else:
            detail_dict = None
        gate = verdict.get("gate")
        try:
            result = record_rejected_factor(
                expression=expression,
                hypothesis=hypothesis,
                category=category,
                ast_json=verdict.get("ast_json"),
                source="backfill",
                gate=int(gate) if gate is not None else None,
                reason=str(verdict["reason"]) if verdict.get("reason") is not None else None,
                detail=detail_dict,
                db_path=db_path,
            )
        except DSLParseError:
            skipped_unparseable += 1
            continue
        if result["action"] == "created":
            imported += 1
        else:
            updated += 1
    return {
        "scanned": scanned,
        "imported": imported,
        "updated": updated,
        "skipped_no_expression": skipped_no_expression,
        "skipped_unparseable": skipped_unparseable,
        "skipped_other": skipped_other,
    }
