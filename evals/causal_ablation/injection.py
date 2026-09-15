"""注入法污染矩阵：8 类污染的确定性构造 + 机制开关注册（spec causal-ablation）。"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any

POLLUTION_TYPES: tuple[str, ...] = (
    "value_error",
    "direction_error",
    "unit_error",
    "period_shift",
    "mirror_narrative",
    "fabricated_event",
    "stale_macro",
    "illegal_price",
)
INJECTION_POINTS: tuple[str, ...] = ("context", "analyst_output", "decision")


@dataclass(frozen=True)
class MechanismSwitch:
    """机制开关点：off_value/on_value 为配置项（env 或属性）的取值。"""

    id: str
    kind: str  # "env" | "callable"
    key: str
    off_value: Any
    on_value: Any


MECHANISM_SWITCHES: dict[str, MechanismSwitch] = {
    "A1": MechanismSwitch("A1", "callable", "compute_metrics_injection", False, True),
    "A2": MechanismSwitch("A2", "callable", "semantic_header", False, True),
    "A3": MechanismSwitch("A3", "callable", "verify_citations", False, True),
    "A4": MechanismSwitch("A4", "callable", "surgical_repair", False, True),
    "A5": MechanismSwitch("A5", "callable", "price_sanity_check", False, True),
    "A6": MechanismSwitch("A6", "callable", "text_echo_match", False, True),
    "A7": MechanismSwitch("A7", "callable", "macro_freshness_mark", False, True),
}

# 污染类型 → (注入点, 因果负责机制)
_POLLUTION_ROUTING: dict[str, tuple[str, str]] = {
    "value_error": ("context", "A3"),
    "direction_error": ("analyst_output", "A3"),
    "unit_error": ("analyst_output", "A3"),
    "period_shift": ("analyst_output", "A2"),
    "mirror_narrative": ("context", "A2"),
    "fabricated_event": ("analyst_output", "A6"),
    "stale_macro": ("context", "A7"),
    "illegal_price": ("decision", "A5"),
}

# 成本分型：校验侧污染可对已有产物离线重放（零 LLM）；输入侧/决策层须真跑全管线
COST_CLASS: dict[str, str] = {
    "value_error": "real_run",
    "direction_error": "offline_replay",
    "unit_error": "offline_replay",
    "period_shift": "offline_replay",
    "mirror_narrative": "real_run",
    "fabricated_event": "offline_replay",
    "stale_macro": "offline_replay",
    "illegal_price": "real_run",
}


@dataclass(frozen=True)
class InjectionCase:
    case_id: str
    pollution_type: str
    injection_point: str
    mechanism_id: str
    payload: dict[str, Any] = field(default_factory=dict)


def build_injection_cases(
    pollution_type: str,
    *,
    ticker: str,
    base_values: dict[str, Any],
    n: int = 4,
    seed: int = 0,
) -> list[InjectionCase]:
    """确定性构造 n 个污染单元（同 seed 同结果）。"""
    if pollution_type not in POLLUTION_TYPES:
        raise ValueError(f"未知污染类型: {pollution_type!r}（须为 {POLLUTION_TYPES}）")
    point, mechanism = _POLLUTION_ROUTING[pollution_type]
    rng = random.Random(f"{seed}:{ticker}:{pollution_type}")  # noqa: S311 — 可复现 fixture，非加密用途
    cases: list[InjectionCase] = []
    for i in range(n):
        payload = _payload(pollution_type, base_values, rng, i)
        cases.append(
            InjectionCase(
                case_id=f"{ticker}-{pollution_type}-{i}",
                pollution_type=pollution_type,
                injection_point=point,
                mechanism_id=mechanism,
                payload=payload,
            )
        )
    return cases


def _payload(
    pollution_type: str, base_values: dict[str, Any], rng: random.Random, idx: int
) -> dict[str, Any]:
    if pollution_type == "value_error":
        field_name = "revenue" if idx % 2 == 0 else "price"
        original = float(base_values[field_name])
        factor = 1.0 + rng.choice([-1.0, 1.0]) * rng.uniform(0.05, 0.50)
        return {"set": {field_name: original * factor}, "field": field_name}
    if pollution_type == "direction_error":
        return {"set": {"growth": -float(base_values["growth"])}, "field": "growth"}
    if pollution_type == "unit_error":
        return {"set": {"revenue": float(base_values["revenue"]) * 1.0e8}, "field": "revenue"}
    if pollution_type == "period_shift":
        return {"set": {"period_label": "2026Q3", "period_value": 0.0}, "field": "period_label"}
    if pollution_type == "mirror_narrative":
        return {"reverse_sequence": True, "field": "series_direction"}
    if pollution_type == "fabricated_event":
        return {"news_append": ["某公司宣布重大战略合作（注入）"], "field": "news"}
    if pollution_type == "stale_macro":
        return {"set": {"macro_as_of": "2026-05-01"}, "field": "macro_as_of"}
    if pollution_type == "illegal_price":
        entry = float(base_values["entry"])
        return {"set": {"stop_loss": round(entry * 1.1, 4), "entry": entry}, "field": "stop_loss"}
    raise ValueError(f"未实现污染类型: {pollution_type}")


def apply_injection(snapshot: dict[str, Any], case: InjectionCase) -> dict[str, Any]:
    """返回污染后的新快照（深拷贝；不得修改入参）。"""
    out = copy.deepcopy(snapshot)
    for key, value in case.payload.get("set", {}).items():
        out[key] = value
    if "news_append" in case.payload:
        out["news"] = list(out.get("news", [])) + list(case.payload["news_append"])
    if case.payload.get("reverse_sequence"):
        series = out.get("close_series")
        if isinstance(series, list):
            out["close_series"] = list(reversed(series))
    return out
