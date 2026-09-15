"""辩论论点锚点校验（add-debate-argument-anchors）。

只判「有没有锚、锚存不存在」——锚是否支持结论由 judge 承担（忠实性 = 可追溯性
（程序）+ 支持性（judge)）。零 LLM；fail-open（调用方不以其结果改路由）。
"""

from __future__ import annotations

# 复用 citation 的私有助手（计划强制：解析/回声语义单一实现，勿在本模块复制）
from finance_agent.citation import _norm_text, _resolve_field_ref, collect_text_sources
from finance_agent.models import DebateArgument, DebateMessage


def _anchor_status(anchor: str, kind: str, state: dict, sources: list[str]) -> str:
    if kind in ("data", "inference") and _resolve_field_ref(anchor, state) is not None:
        return "resolved"
    if kind in ("event", "inference"):
        a_norm = _norm_text(anchor)
        if a_norm and any(a_norm in s or s in a_norm for s in (_norm_text(x) for x in sources)):
            return "resolved"
    return "unresolved"


def _argument_status(arg: DebateArgument, statuses: list[str]) -> str:
    if arg.kind == "unspecified":
        return "unspecified"
    if not arg.anchors:
        return "missing" if arg.kind in ("data", "event") else "none"
    return "resolved" if "resolved" in statuses else "unresolved"


def check_argument_anchors(msg: DebateMessage, state: dict) -> list[dict]:
    """逐论点锚点校验；返回可 JSON 序列化的检查记录列表（供 state channel 落盘）。

    消费方按锚点状态门控时必须读 `status`：`unspecified` 条目的 `anchor_statuses`
    虽被计算但非权威（恒为 unresolved），不代表锚点缺失或未解析。
    """
    sources = collect_text_sources(state)
    checks: list[dict] = []
    for i, arg in enumerate(msg.key_arguments, start=1):
        statuses = [_anchor_status(a, arg.kind, state, sources) for a in arg.anchors]
        checks.append(
            {
                "role": msg.role,
                "round": msg.round,
                "index": i,
                "kind": arg.kind,
                "anchors": list(arg.anchors),
                "anchor_statuses": statuses,
                "status": _argument_status(arg, statuses),
                "anchored": "resolved" in statuses,
            }
        )
    return checks


def anchor_stats(checks: list[dict]) -> dict:
    """覆盖统计（零 LLM）；total=0 由调用方决定是否记 null。"""
    return {
        "total": len(checks),
        "anchored": sum(1 for c in checks if c.get("anchored")),
        "unanchored_inference": sum(1 for c in checks if c.get("status") == "none"),
        "unresolved": sum(1 for c in checks if c.get("status") == "unresolved"),
        "missing_required": sum(1 for c in checks if c.get("status") == "missing"),
        "unspecified": sum(1 for c in checks if c.get("status") == "unspecified"),
    }
