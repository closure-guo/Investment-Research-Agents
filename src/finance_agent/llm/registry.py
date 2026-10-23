# src/finance_agent/llm/registry.py
"""provider 能力表（静态默认）。

静态表声明「我们已验证的默认行为」；capability probe（probes.py）是
运行时事实，冲突时以 probe 为准并写 warning（design 决策 7）。
行为依据：DeepSeek 官方文档（2025-12 起 thinking 支持工具调用、要求
回传 reasoning_content）与方舟 plan/v3 实测（incident 016/017、PR #74）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from finance_agent.llm.types import Capability, ModelProfile


class DeepSeekOptions(BaseModel):
    """deepseek provider_options schema（设计档案 §7.1 示例）。

    pydantic v2 默认 ignore 未知 key——本 schema 显式 extra="forbid" 拒绝
    （未知配置项必须显式报错，禁止静默吞掉）。
    """

    model_config = ConfigDict(extra="forbid")

    thinking: Literal["enabled", "disabled"] | None = None
    reasoning_effort: Literal["low", "high", "max"] | None = None


class ArkGLMOptions(BaseModel):
    """ark-glm（方舟 GLM-5.3）provider_options schema。

    docs.bigmodel.cn：GLM-5.3 reasoning_effort 仅 max/high/low 三档，
    官方默认 max；thinking 强制开启（无开关入口，勿配置）。显式
    extra="forbid" 拒绝未知配置项（如 deepseek 专属 thinking）。
    """

    model_config = ConfigDict(extra="forbid")

    reasoning_effort: Literal["max", "high", "low"] | None = None


class KimiOptions(BaseModel):
    """kimi（Kimi Code API，openai/ 前缀接入）provider_options schema。

    Kimi Code 官方文档：4 个模型 ID（k3/k3-256k/kimi-for-coding/
    kimi-for-coding-highspeed）全系为思考模型，effort 档位 low/high/max；
    ``none`` 为官方出口——关闭思考并路由到无思考版（judge 保持
    temperature=0 采样口径的前提）。官方映射别名（medium→high 等）
    不进 schema：配置显式性优先。extra="forbid" 拒绝未知配置项。
    """

    model_config = ConfigDict(extra="forbid")

    reasoning_effort: Literal["none", "low", "high", "max"] | None = None


# provider_options 校验 schema（§7.1 registry 静态三件套之一）
PROVIDER_OPTIONS_SCHEMAS: dict[str, type[BaseModel]] = {
    "deepseek": DeepSeekOptions,
    "ark-glm": ArkGLMOptions,
    "kimi": KimiOptions,
}

# provider 静态默认 options（对齐 legacy deep 分支行为）
# kimi 不设默认档：各模型默认不同（k3=high、kimi-for-coding=max），
# 未显式配置时不携带参数、跟随端点默认（add-kimi-reasoning-effort D4）
DEFAULT_PROVIDER_OPTIONS: dict[str, dict] = {
    "deepseek": {"thinking": "enabled", "reasoning_effort": "max"},
    "ark-glm": {"reasoning_effort": "max"},
}

# 请求级 llm_config 允许覆盖的白名单；未登记 provider 请求级不可覆盖
REQUEST_OVERRIDABLE: dict[str, set[str]] = {
    "deepseek": {"thinking", "reasoning_effort"},
    "ark-glm": {"reasoning_effort"},
    "kimi": {"reasoning_effort"},
}

_NO_REASONING = {
    "reasoning_field": None,
    "reasoning_must_echo_on_tool": False,
    "reasoning_forced": False,
}


def _cap(**overrides: object) -> Capability:
    """BASE_CAP + 覆盖项构造 Capability（避免 ** 展开与显式 kwarg 冲突）。"""
    return Capability(**{**_BASE_CAP, **overrides})  # type: ignore[arg-type]


_BASE_CAP = {
    "tools": "single",
    "tool_choice_required": False,
    "streaming": True,
    "streaming_tool_calls": True,
    "json_schema": "json_mode",
    "supports_system_role": True,
    "max_context": 128000,
    "max_output": 8192,
    "extra_body_allowed": True,
}

_PRESETS: dict[str, ModelProfile] = {
    "deepseek-official": ModelProfile(
        name="deepseek-official",
        provider="deepseek",
        model="deepseek/deepseek-chat",
        base_url=None,
        api_key=None,
        # DeepSeek 官方：思考可关；工具轮次必须回传 reasoning_content
        capability=_cap(
            reasoning_field="reasoning_content",
            reasoning_must_echo_on_tool=True,
            reasoning_forced=False,
            # DeepSeek 官方支持 tool_choice=required（force_tool ReAct 轮）
            tool_choice_required=True,
        ),
        default_params={},
        provider_options=dict(DEFAULT_PROVIDER_OPTIONS["deepseek"]),
        # fallback 示例链（设计档案 §9）：DeepSeek 官方端点不可用时切官方 OpenAI
        fallback=("openai-official",),
    ),
    "ark-glm": ModelProfile(
        name="ark-glm",
        provider="openai",
        model="openai/glm-5.2",
        base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        api_key=None,
        # 方舟 GLM 实测：thinking 强制开启（disabled 被 400 拒）、effort 不透传、
        # 拒收 messages.reasoning_content 字段；reasoning 与正文共享 max_tokens
        capability=_cap(
            reasoning_field="reasoning_content",
            reasoning_must_echo_on_tool=False,
            reasoning_forced=True,
            # 方舟 GLM 支持函数调用 / tool_choice=required（force_tool ReAct 轮）
            tool_choice_required=True,
            # reasoning 与正文共享配额，预算必须覆盖 reasoning 峰值（incident 017）；
            # 官方默认 max_tokens=65536（最大 131072，docs.bigmodel.cn）
            max_output=65536,
        ),
        default_params={"max_tokens": 65536},
    ),
    "openai-compatible": ModelProfile(
        name="openai-compatible",
        provider="openai",
        model="openai/<model>",
        base_url=None,  # 由调用方/环境注入
        api_key=None,
        # 通用 OpenAI 兼容端点（中转/Ollama/vLLM）：不做能力假设，
        # 上线前必须过 capability probe（llm-capability-probe spec）
        capability=_cap(**_NO_REASONING),
        default_params={},
    ),
    "openai-official": ModelProfile(
        name="openai-official",
        provider="openai",
        model="openai/gpt-4o",
        base_url=None,
        api_key=None,
        capability=_cap(**_NO_REASONING, tools="parallel", json_schema="strict_schema"),
        default_params={},
    ),
    "anthropic": ModelProfile(
        name="anthropic",
        provider="anthropic",
        model="anthropic/claude-sonnet-4",
        base_url=None,
        api_key=None,
        # adapter 负责 content block 归一，核心无感
        capability=_cap(**_NO_REASONING),
        default_params={},
    ),
}


def get_profile_preset(name: str) -> ModelProfile:
    """按名取静态 preset；未知名字抛 KeyError（不允许静默回退默认 provider）。"""
    return _PRESETS[name]


def register_preset(profile: ModelProfile) -> None:
    """运行时注册 preset（env 注入的 fallback 成员用；同名覆盖幂等）。

    fallback 链成员的解析路径是「名字 → registry preset」，env 注入的成员必须先
    注册再挂到主 profile 的 ``fallback`` 上，否则链装配时查不到。
    """
    _PRESETS[profile.name] = profile


def list_presets() -> list[str]:
    return list(_PRESETS)
