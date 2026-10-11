"""辩论论点锚点校验（add-debate-argument-anchors + add-anchor-value-grounding）。

只判「有没有锚、锚存不存在、文本数字可否溯源」——锚是否支持结论由 judge 承担
（忠实性 = 可追溯性（程序）+ 支持性（judge)）。零 LLM；fail-open（调用方不以其
结果改路由）。
"""

from __future__ import annotations

import re

# 复用 citation 的私有助手（计划强制：解析/回声语义单一实现，勿在本模块复制）
from finance_agent.citation import _norm_text, _resolve_field_ref, collect_text_sources
from finance_agent.models import DebateArgument, DebateMessage

# 数值 token：左邻 ASCII 字母/数字的排除（MA5/R1），右邻 ASCII 字母的排除
# （2024Q1）；CJK 相邻保留（「上涨5%」「10亿」是数值断言）。Python re \w 含
# CJK，必须用显式 ASCII 类（price-validator #259 M2 标识符教训）。
_NUM_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?(?![A-Za-z])")
_YEAR_TOKEN_RE = re.compile(r"^(?:19|20)\d{2}$")
# 年份豁免量纲：后随这些字符的 4 位整数不是年份（「2024亿」）
_YEAR_EXEMPT_CHARS = "%亿万元倍"


def _numeric_tokens(text: str) -> list[float]:
    """论点文本的数值断言 token（排除标识符/年份形态，见 add-anchor-value-grounding D4）。"""
    tokens: list[float] = []
    for m in _NUM_TOKEN_RE.finditer(text or ""):
        raw = m.group(0)
        nxt = text[m.end()] if m.end() < len(text) else ""
        if _YEAR_TOKEN_RE.match(raw.lstrip("-")) and nxt not in _YEAR_EXEMPT_CHARS:
            continue
        tokens.append(float(raw))
    return tokens


def _as_float(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None
    return None


def _number_matches(token: float, value: float) -> bool:
    # 宽容匹配（D3）：原值 / ×100 百分数 / 两位舍入 / 绝对值，相对容差 0.5%——
    # 方向正确性归 judge，程序只判数字可溯源
    candidates = [value, value * 100, round(value, 2), round(value * 100, 2)]
    candidates += [abs(c) for c in candidates]
    tol = max(0.005, 0.005 * abs(value))
    return any(abs(token - c) <= tol for c in candidates)


def _is_field_shaped(anchor: str, state: dict) -> bool:
    """field 形态：含 "." 且首段为 state 现存根键（零词表维护，D5）。"""
    root = anchor.split(".", 1)[0] if "." in anchor else ""
    return bool(root) and root in state


def _anchor_status(anchor: str, kind: str, state: dict, sources: list[str]) -> tuple[str, str]:
    """返回 (status, matched_via)：field_ref / echo / 空（未命中）。"""
    if kind in ("data", "inference") and _resolve_field_ref(anchor, state) is not None:
        return "resolved", "field_ref"
    if kind in ("event", "inference"):
        a_norm = _norm_text(anchor)
        if a_norm and any(a_norm in s or s in a_norm for s in (_norm_text(x) for x in sources)):
            return "resolved", "echo"
    return "unresolved", ""


def _argument_status(
    arg: DebateArgument,
    statuses: list[str],
    matched_via: list[str],
    state: dict,
) -> str:
    if arg.kind == "unspecified":
        return "unspecified"
    if not arg.anchors:
        return "missing" if arg.kind in ("data", "event") else "none"
    resolved_values = [
        v
        for a, via in zip(arg.anchors, matched_via, strict=True)
        if via == "field_ref" and (v := _as_float(_resolve_field_ref(a, state))) is not None
    ]
    if resolved_values:
        tokens = _numeric_tokens(arg.text)
        if tokens and not any(_number_matches(t, v) for t in tokens for v in resolved_values):
            return "value_mismatch"
    return "resolved" if "resolved" in statuses else "unresolved"


def check_argument_anchors(msg: DebateMessage, state: dict) -> list[dict]:
    """逐论点锚点校验；返回可 JSON 序列化的检查记录列表（供 state channel 落盘）。

    消费方按锚点状态门控时必须读 `status`：`unspecified` 条目的 `anchor_statuses`
    虽被计算但非权威（恒为 unresolved），不代表锚点缺失或未解析。
    `matched_via` 与 `anchors` 平行记录每锚点解析途径；`echo_only_field_refs`
    收录「field 形态（含 . 且首段为 state 根键）却仅经回声命中」的 inference 锚点
    ——锚点声明形态与证据来源错位的确定性信号（add-anchor-value-grounding）。
    """
    sources = collect_text_sources(state)
    checks: list[dict] = []
    for i, arg in enumerate(msg.key_arguments, start=1):
        pairs = [_anchor_status(a, arg.kind, state, sources) for a in arg.anchors]
        statuses = [p[0] for p in pairs]
        matched_via = [p[1] for p in pairs]
        echo_only_field_refs = [
            a
            for a, via in zip(arg.anchors, matched_via, strict=True)
            if arg.kind == "inference" and via == "echo" and _is_field_shaped(a, state)
        ]
        checks.append(
            {
                "role": msg.role,
                "round": msg.round,
                "index": i,
                "kind": arg.kind,
                "anchors": list(arg.anchors),
                "anchor_statuses": statuses,
                "matched_via": matched_via,
                "echo_only_field_refs": echo_only_field_refs,
                "status": _argument_status(arg, statuses, matched_via, state),
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
        "value_mismatch": sum(1 for c in checks if c.get("status") == "value_mismatch"),
        "field_ref_echo_only": sum(len(c.get("echo_only_field_refs") or []) for c in checks),
    }
