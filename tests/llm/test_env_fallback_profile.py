"""env 注入 fallback 成员（LLM_FALLBACK_*）解析与链装配。

背景：2026-10 生产 provider（opencode zen）多次 5xx/连接错误，7 天内 9 次会话失败。
registry 的 preset fallback 链只服务命名 preset，env 配置（LLM_*）没有 fallback 成员。
本组用例钉死：LLM_FALLBACK_* → 动态注册 preset → 主 profile.fallback 指向它；judge 不受影响；
半套配置（缺端点）不生效。
"""

from __future__ import annotations

import pytest

from finance_agent.llm.gateway import fallback_attempt_plan
from finance_agent.llm.registry import get_profile_preset
from finance_agent.llm.resolver import resolve_profile

_PRIMARY = {
    "LLM_MODEL": "openai/deepseek-v4.1-flash",
    "LLM_BASE_URL": "https://primary.invalid/v1",
    "LLM_API_KEY": "primary-key",
}
_FALLBACK = {
    "LLM_FALLBACK_MODEL": "openai/k3-256k",
    "LLM_FALLBACK_BASE_URL": "https://fallback.invalid/coding/v1",
    "LLM_FALLBACK_API_KEY": "fallback-key",
}


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_env_fallback_is_registered_and_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**_PRIMARY, **_FALLBACK})

    profile = resolve_profile(purpose="deep")

    assert profile.fallback == ("env-fallback:openai/k3-256k",)
    member = get_profile_preset(profile.fallback[0])
    assert member.model == "openai/k3-256k"
    assert member.base_url == "https://fallback.invalid/coding/v1"
    assert member.api_key == "fallback-key"


def _clear_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉 .env 可能注入的 fallback 变量（负例必须显式清空，避免读宿主环境）。"""
    for key in ("LLM_FALLBACK_MODEL", "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_no_fallback_env_keeps_empty_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_fallback(monkeypatch)
    _set(monkeypatch, _PRIMARY)

    assert resolve_profile(purpose="deep").fallback == ()


def test_partial_fallback_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """只有 model 没有端点 = 半套配置，不得注册成链成员（避免打错网关）。"""
    _clear_fallback(monkeypatch)
    _set(monkeypatch, {**_PRIMARY, "LLM_FALLBACK_MODEL": "openai/k3-256k"})

    assert resolve_profile(purpose="deep").fallback == ()


def test_judge_ignores_fallback_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(
        monkeypatch,
        {
            "JUDGE_MODEL": "openai/glm-5.3-flash",
            "JUDGE_BASE_URL": "https://judge.invalid/v1",
            "JUDGE_API_KEY": "judge-key",
            **_FALLBACK,
        },
    )

    assert resolve_profile(purpose="judge").fallback == ()


def test_fallback_attempt_plan_includes_env_member(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**_PRIMARY, **_FALLBACK})

    plan = fallback_attempt_plan(purpose="deep")

    assert [attempt.profile for attempt in plan.attempts] == [
        "env:openai/deepseek-v4.1-flash",
        "env-fallback:openai/k3-256k",
    ]
    assert plan.attempts[1].preset == "env-fallback:openai/k3-256k"
