"""PolicyRouter 纯选择函数测试（harden-llm-gateway-governance spec Requirement 1）。

覆盖 spec 两 Scenario：
- Scenario 1：react 目的下 tools=none 候选不得成为 primary 也不得进 fallback 链
- Scenario 2：parallel-tools primary 的 fallback 链不得包含 single-tools 候选
"""

from __future__ import annotations

import dataclasses

import pytest

from finance_agent.llm.errors import UnsupportedCapabilityError
from finance_agent.llm.router import REQUIRED_CAPS, select_profile
from finance_agent.llm.types import Capability, ModelProfile


def _profile(
    name: str,
    *,
    tools: str = "single",
    json_schema: str = "json_mode",
    max_output: int = 8192,
    provider: str = "openai",
) -> ModelProfile:
    cap = Capability(
        tools=tools,  # type: ignore[arg-type]
        tool_choice_required=True,
        streaming=True,
        streaming_tool_calls=True,
        json_schema=json_schema,  # type: ignore[arg-type]
        supports_system_role=True,
        reasoning_field=None,
        reasoning_must_echo_on_tool=False,
        reasoning_forced=False,
        max_context=128000,
        max_output=max_output,
        extra_body_allowed=True,
    )
    return ModelProfile(
        name=name,
        provider=provider,
        model=f"openai/{name}",
        base_url=None,
        api_key=None,
        capability=cap,
    )


def test_required_caps_table() -> None:
    assert REQUIRED_CAPS["react"] == {"tools": {"single", "parallel"}}
    assert REQUIRED_CAPS["pipeline_node"] == {"json_schema": {"json_mode", "strict_schema"}}


def test_react_filters_tools_none_from_primary_and_chain() -> None:
    """Scenario 1：tools=none 候选在 react 目的下整体出局。"""
    weak = _profile("weak", tools="none")
    strong = _profile("strong", tools="parallel")
    result = select_profile(purpose="react", candidates=[weak, strong])
    assert result.primary.name == "strong"
    assert all(p.name != "weak" for p in result.fallback_chain)


def test_parallel_primary_chain_excludes_single_tools() -> None:
    """Scenario 2：偏序约束——链成员能力 rank 必须 >= primary。"""
    parallel = _profile("parallel", tools="parallel")
    single = _profile("single", tools="single")
    another_parallel = _profile("parallel2", tools="parallel")
    result = select_profile(purpose="react", candidates=[parallel, single, another_parallel])
    assert result.primary.name == "parallel"
    assert [p.name for p in result.fallback_chain] == ["parallel2"]


def test_allow_action_fallback_permits_tools_none_with_degradation_note() -> None:
    weak = _profile("weak", tools="none")
    strong = _profile("strong", tools="parallel")
    result = select_profile(purpose="react", candidates=[weak], allow_action_fallback=True)
    assert result.primary.name == "weak"
    assert result.trace["degradation"] == "tools=none (action text-protocol fallback)"
    # 混合候选时 strong 仍应优先于 weak
    result2 = select_profile(purpose="react", candidates=[weak, strong], allow_action_fallback=True)
    assert result2.primary.name == "strong"


def test_no_candidate_satisfies_raises_unsupported_capability() -> None:
    weak = _profile("weak", tools="none")
    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        select_profile(purpose="react", candidates=[weak])
    msg = str(exc_info.value)
    assert "react" in msg
    assert "tools" in msg


def test_quick_orders_by_max_output_ascending() -> None:
    big = _profile("big", max_output=16384)
    small = _profile("small", max_output=4096)
    mid = _profile("mid", max_output=8192)
    result = select_profile(purpose="quick", candidates=[big, mid, small])
    assert [p.name for p in [result.primary, *result.fallback_chain]] == [
        "small",
        "mid",
        "big",
    ]


def test_non_quick_purpose_preserves_input_order() -> None:
    a = _profile("a", max_output=16384)
    b = _profile("b", max_output=4096)
    result = select_profile(purpose="deep", candidates=[a, b])
    assert result.primary.name == "a"
    assert [p.name for p in result.fallback_chain] == ["b"]


def test_chain_capped_at_two() -> None:
    cands = [_profile(f"m{i}", tools="parallel") for i in range(4)]
    result = select_profile(purpose="react", candidates=cands)
    assert result.primary.name == "m0"
    assert [p.name for p in result.fallback_chain] == ["m1", "m2"]


def test_pipeline_node_requires_json_capability() -> None:
    no_json = _profile("no_json", json_schema="none")
    json_ok = _profile("json_ok", json_schema="json_mode")
    strict = _profile("strict", json_schema="strict_schema")
    with pytest.raises(UnsupportedCapabilityError):
        select_profile(purpose="pipeline_node", candidates=[no_json])
    result = select_profile(purpose="pipeline_node", candidates=[no_json, json_ok, strict])
    assert result.primary.name == "json_ok"
    assert [p.name for p in result.fallback_chain] == ["strict"]


def test_trace_contains_profile_provider_model_and_chain() -> None:
    a = _profile("a", tools="parallel", json_schema="strict_schema")
    b = _profile("b", tools="parallel", json_schema="strict_schema")
    result = select_profile(purpose="react", candidates=[a, b])
    trace = result.trace
    assert trace["profile"] == "a"
    assert trace["provider"] == "openai"
    assert trace["model"] == "openai/a"
    assert trace["capability"] == {"tools": "parallel", "json_schema": "strict_schema"}
    assert trace["fallback_chain"] == ["b"]


def test_unknown_purpose_no_hard_requirement() -> None:
    """未登记目的 → 无硬性过滤，保序直通。"""
    any_profile = _profile("plain", tools="none", json_schema="none")
    result = select_profile(purpose="judge", candidates=[any_profile])
    assert result.primary.name == "plain"


def test_router_result_is_frozen_dataclass() -> None:
    result = select_profile(purpose="react", candidates=[_profile("a")])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.primary = _profile("b")  # type: ignore[misc]


# ── add-factor-proposal-agent（T2 追加）：factor_proposal 用途注册与双档路由 ──


def test_factor_proposal_required_caps_registered() -> None:
    """新用途注册：JSON 结构化合同为硬要求（因子提案必须结构化输出）。"""
    assert REQUIRED_CAPS["factor_proposal"] == {"json_schema": {"json_mode", "strict_schema"}}


def test_factor_proposal_rejects_json_none_and_raises_when_exhausted() -> None:
    """Scenario（llm-policy-router delta）：不满足能力的候选出局；链耗尽 typed error。"""
    no_json = _profile("no_json", json_schema="none")
    json_ok = _profile("json_ok", json_schema="json_mode")
    with pytest.raises(UnsupportedCapabilityError):
        select_profile(purpose="factor_proposal", candidates=[no_json])
    result = select_profile(purpose="factor_proposal", candidates=[json_ok, no_json])
    assert result.primary.name == "json_ok"
    assert all(p.name != "no_json" for p in result.fallback_chain)


def test_factor_proposal_preserves_input_order_for_tier_priority() -> None:
    """双档路由依赖保序：候选序 [quick, deep] → primary=quick（不按 max_output 重排）。"""
    quick = _profile("quick", json_schema="json_mode", max_output=16384)
    deep = _profile("deep", json_schema="json_mode", max_output=4096)
    result = select_profile(purpose="factor_proposal", candidates=[quick, deep])
    assert result.primary.name == "quick"
    assert [p.name for p in result.fallback_chain] == ["deep"]
