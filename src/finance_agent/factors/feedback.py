"""跨会话因子记忆桥（收割侧 → 生成侧）。

RD-Agent 的 feedback 只在**同一 trace**（会话）内传递（`trace.hist`）；新会话的历史为空，
于是同一经典因子会换名字反复提案（实测 `mom20`/`mom_20d`/`MOM_20D`/`momentum_20d` 四条同式）。
本模块把本系统已探索/已裁决的因子导出成生成侧可读的清单。

产物：``data/factor_research/explored_factors.json``（消费方：distro 内
``rdagent/scenarios/qlib/proposal/explored_feedback.py``，经环境变量
``FACTOR_FEEDBACK_PATH`` 指向同一路径）。

口径：
- 表达式按 DSL 规范化（``1e-6`` 以下常数视为 0）去重——epsilon 守卫变体不应各占一条；
- 排序：admitted → rejected（闸②优先）→ untranslatable，再按时间倒序；
- 条目数由 ``limit`` 截断（默认 80），供 prompt 预算使用；
- 任何字段缺失如实留空，不编造。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.factors.dsl import Call, Number, parse, serialize

DEFAULT_OUTPUT_PATH = "data/factor_research/explored_factors.json"
DEFAULT_LIMIT = 80
_MAX_DESC = 160

_STATUS_ORDER = {"admitted": 0, "rejected": 1, "untranslatable": 2, "transcription_error": 3}


def canonical_expression(expression: str) -> str | None:
    """DSL 表达式规范化（epsilon 归零 + 可交换算子定序）；解析失败 → None。"""
    try:
        node = parse(expression)
    except Exception:
        return None
    return serialize(_canon(node))


def _canon(node: Any) -> Any:
    if isinstance(node, Number):
        if isinstance(node.value, float) and abs(node.value) < 1e-6:
            return Number(0)
        return node
    if isinstance(node, Call):
        args = tuple(_canon(arg) for arg in node.args)
        if node.op in {"add", "mul"}:
            args = tuple(sorted(args, key=serialize))
        # epsilon 守卫归一：x+0 / x-0 / x*1 / x/1 → x
        if node.op == "add":
            rest = [a for a in args if not (isinstance(a, Number) and a.value == 0)]
            if len(rest) == 1:
                return rest[0]
        if node.op == "mul":
            rest = [a for a in args if not (isinstance(a, Number) and a.value == 1)]
            if len(rest) == 1:
                return rest[0]
        if node.op in {"sub", "div"} and len(args) == 2:
            right = args[1]
            if isinstance(right, Number) and right.value == (0 if node.op == "sub" else 1):
                return args[0]
        return Call(node.op, args)
    return node


def _reason(verdict: Mapping[str, Any]) -> tuple[int | None, str | None]:
    gate = verdict.get("gate")
    reason = verdict.get("reason")
    if gate is None and verdict.get("status") == "untranslatable":
        stage = verdict.get("stage")
        detail = str(verdict.get("detail") or "").strip()
        return None, f"untranslatable:{stage}" + (f"（{detail[:80]}）" if detail else "")
    return (int(gate) if isinstance(gate, int) else None), (str(reason) if reason else None)


def build_payload(
    processed: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """状态 + 候选表 → 生成侧清单载荷（按规范化表达式去重、排序、截断）。"""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, verdict in processed.items():
        if not isinstance(verdict, Mapping):
            continue
        status = str(verdict.get("status") or "")
        if status not in _STATUS_ORDER:
            continue
        expression = verdict.get("expression")
        key = canonical_expression(str(expression)) if expression else None
        if key is None:
            key = f"name:{name}"
        if key in seen:
            continue
        seen.add(key)
        candidate = candidates.get(name) or {}
        description = " ".join(str(candidate.get("description") or "").split())
        gate, reason = _reason(verdict)
        entries.append(
            {
                "name": name,
                "desc": description[:_MAX_DESC],
                "status": status,
                "gate": gate,
                "reason": reason,
                "expression": key if key.startswith("name:") is False else None,
                "at": str(verdict.get("at") or ""),
            }
        )

    entries.sort(key=lambda e: str(e["at"]), reverse=True)
    entries.sort(
        key=lambda e: (
            _STATUS_ORDER.get(str(e["status"]), 9),
            e["gate"] if isinstance(e["gate"], int) else 99,
        )
    )
    trimmed = entries[: max(limit, 0)]
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "total_explored": len(entries),
        "returned": len(trimmed),
        "counts": {
            status: sum(1 for e in entries if e["status"] == status) for status in _STATUS_ORDER
        },
        "explored": trimmed,
    }


def load_state(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    processed = payload.get("processed") if isinstance(payload, dict) else None
    if not isinstance(processed, dict):
        raise ValueError(f"收割状态缺少 processed 对象：{path}")
    return processed


def load_candidates(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"候选文件缺少 candidates 数组：{path}")
    return {str(c["name"]): c for c in items if isinstance(c, dict) and c.get("name")}


def write_payload(payload: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(target)
    return target


def export_feedback(
    *,
    state_path: str | Path,
    candidates_path: str | Path,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """一步导出（CLI 与测试共用）：返回写出载荷。"""
    processed = load_state(state_path)
    candidates = load_candidates(candidates_path)
    payload = build_payload(processed, candidates, limit=limit)
    write_payload(payload, output_path)
    return payload
