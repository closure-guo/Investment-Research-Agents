"""PolicyRouter：按 purpose 的硬性能力过滤 + fallback 链选择（纯函数）。

设计依据：docs/design/LLM Provider Gateway 设计档案 §9。
规则：
- REQUIRED_CAPS 声明每个 purpose 的硬性能力要求；未登记目的无硬性要求。
- react 目的下 tools=none 出局，除非 allow_action_fallback（action 文本协议
  显式降级，降级事实记入 trace，禁止静默）。
- fallback 链成员能力偏序 >= primary（tools: none<single<parallel；
  json_schema: none<json_mode<strict_schema），链长上限 2。
- purpose=quick 按 capability.max_output 升序（低延迟代理指标）；其余保序。
- 无候选满足 → UnsupportedCapabilityError（消息列出要求与各候选缺口）。
"""

from __future__ import annotations

from dataclasses import dataclass

from finance_agent.llm.errors import UnsupportedCapabilityError
from finance_agent.llm.types import ModelProfile, Purpose

# 各 purpose 的硬性能力要求：{capability 字段: 允许值集合}
REQUIRED_CAPS: dict[str, dict[str, set[str]]] = {
    "react": {"tools": {"single", "parallel"}},
    "pipeline_node": {"json_schema": {"json_mode", "strict_schema"}},
    # 因子提案（add-factor-proposal-agent）：结构化合同硬要求；双档（quick/deep）
    # 候选由调用方按序传入，本表只做能力过滤（保序 → broad 档 primary=quick）
    "factor_proposal": {"json_schema": {"json_mode", "strict_schema"}},
    # 因子转译（add-factor-harvest-bridge）：结构化载荷硬要求（二值裁决 JSON）
    "factor_transcription": {"json_schema": {"json_mode", "strict_schema"}},
    # 投喂研报抽取（add-research-report-dataplane）：结构化 JSON 合同
    "report_extraction": {"json_schema": {"json_mode", "strict_schema"}},
    # 外资二手快讯事件抽取（add-foreign-broker-sentiment）：结构化 JSON 合同
    "foreign_broker_event": {"json_schema": {"json_mode", "strict_schema"}},
}

# 能力偏序 rank（越大越强）；fallback 链成员 rank 不得低于 primary
_TOOL_RANK = {"none": 0, "single": 1, "parallel": 2}
_JSON_RANK = {"none": 0, "json_mode": 1, "strict_schema": 2}

_FALLBACK_CHAIN_LIMIT = 2


@dataclass(frozen=True)
class RouterResult:
    """路由结果：primary + 能力等价（偏序 >=）的 fallback 链 + 审计 trace。"""

    primary: ModelProfile
    fallback_chain: list[ModelProfile]
    trace: dict


def _cap_rank(profile: ModelProfile) -> tuple[int, int]:
    """能力偏序 rank：(tools_rank, json_rank)。"""
    return (
        _TOOL_RANK.get(profile.capability.tools, 0),
        _JSON_RANK.get(profile.capability.json_schema, 0),
    )


def _cap_summary(profile: ModelProfile) -> dict[str, str]:
    return {
        "tools": profile.capability.tools,
        "json_schema": profile.capability.json_schema,
    }


def select_profile(
    *,
    purpose: Purpose,
    candidates: list[ModelProfile],
    allow_action_fallback: bool = False,
) -> RouterResult:
    """按 purpose 硬性要求过滤候选并选出 primary + fallback 链（纯函数，无 IO）。"""
    required = REQUIRED_CAPS.get(purpose, {})
    passed: list[ModelProfile] = []
    degraded: list[ModelProfile] = []
    rejections: list[str] = []
    for cand in candidates:
        lack = [
            f"{field}={getattr(cand.capability, field)}"
            for field, allowed in required.items()
            if getattr(cand.capability, field) not in allowed
        ]
        # react 等工具目的 + allow_action_fallback：tools=none 显式降级放行，
        # 但排序劣后于全达标候选（有能力达标者不得让位于降级候选）
        if (
            lack == ["tools=none"]
            and allow_action_fallback
            and "tools" in required
            and cand.capability.tools == "none"
        ):
            degraded.append(cand)
            continue
        if lack:
            rejections.append(f"{cand.name}: 缺少 {', '.join(lack)}")
        else:
            passed.append(cand)
    is_degraded_primary = not passed
    passed = passed + degraded

    if not passed:
        req_desc = (
            ", ".join(f"{field}∈{sorted(allowed)}" for field, allowed in required.items()) or "无"
        )
        raise UnsupportedCapabilityError(
            f"purpose={purpose} 要求 [{req_desc}]，无候选满足；"
            f"候选缺口：{'; '.join(rejections) if rejections else '候选为空'}"
        )

    # quick 目的：低延迟优先（max_output 升序作代理）；其余保持输入序
    if purpose == "quick":
        ordered = sorted(passed, key=lambda p: p.capability.max_output)
    else:
        ordered = passed

    primary = ordered[0]
    primary_rank = _cap_rank(primary)
    chain = [
        cand
        for cand in ordered[1:]
        if all(c >= p for c, p in zip(_cap_rank(cand), primary_rank, strict=True))
    ][:_FALLBACK_CHAIN_LIMIT]

    trace: dict = {
        "profile": primary.name,
        "provider": primary.provider,
        "model": primary.model,
        "capability": _cap_summary(primary),
        "fallback_chain": [c.name for c in chain],
    }
    if is_degraded_primary and primary.capability.tools == "none":
        trace["degradation"] = "tools=none (action text-protocol fallback)"
    elif degraded:
        trace["degradation_noted_candidates"] = [c.name for c in degraded if c is not primary]
    return RouterResult(primary=primary, fallback_chain=chain, trace=trace)
