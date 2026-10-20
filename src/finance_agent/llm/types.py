# src/finance_agent/llm/types.py
"""LLM Provider Gateway 类型契约。

设计依据：docs/design/LLM Provider Gateway 设计档案 §6 + 实战增补
（incident 016/017、PR #74）：
- ``reasoning_forced``：方舟 GLM 实测 thinking 强制开启不可关
  （``thinking.type=disabled`` 被端点 400 拒、``reasoning_effort`` 不透传），
  该事实决定 max_tokens 预算策略（reasoning 与正文共享配额）。
- ``reasoning_must_echo_on_tool``：DeepSeek 官方要求工具轮次回传
  ``reasoning_content``（缺失 400），方舟端点行为相反（携带该字段 400）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ToolsCap = Literal["none", "single", "parallel"]
JsonCap = Literal["none", "json_mode", "strict_schema"]
Purpose = Literal[
    "deep",
    "quick",
    "followup",
    "extract",
    "react",
    "pipeline_node",
    "probe",
    "judge",
    "factor_proposal",
    "factor_transcription",
    "report_extraction",
]


@dataclass(frozen=True)
class Capability:
    """provider 能力契约。业务代码只看本结构，不看模型名字符串。"""

    tools: ToolsCap
    tool_choice_required: bool
    streaming: bool
    streaming_tool_calls: bool
    json_schema: JsonCap
    supports_system_role: bool
    # reasoning 字段名（下发），不支持思考的模型为 None
    reasoning_field: str | None
    # 工具调用轮次是否必须回传 reasoning 字段（DeepSeek 类约束）
    reasoning_must_echo_on_tool: bool
    # 思考是否强制开启不可关（方舟 GLM 类行为），影响输出预算派生
    reasoning_forced: bool
    max_context: int
    max_output: int
    extra_body_allowed: bool
    data_regions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelProfile:
    """一次 LLM 调用的原子配置：provider + model + 端点 + 能力 + 默认参数。

    profile 是原子单位——切换 profile 整体切换，不允许半套配置漂移。
    """

    name: str
    provider: str
    model: str
    base_url: str | None
    api_key: str | None
    capability: Capability
    default_params: dict[str, Any] = field(default_factory=dict)
    # provider 特有配置（设计档案 §7.1）：thinking/reasoning_effort 等，
    # 消费只发生在 adapter，业务代码零 provider 分支
    provider_options: dict[str, Any] = field(default_factory=dict)
    fallback: tuple[str, ...] = ()
    # API 形式（add-llm-api-form）：chat_completion / messages / responses，
    # None=litellm 按 model 前缀自动路由，不显式设置 api 参数
    api_form: str | None = None
    # probe 治理（llm-capability-probe spec）：无缓存 probe 事实时要求先探测
    probe_required: bool = False
    # probe 事实与静态能力表冲突的字段（如 "tools: single→none"）
    probe_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalRequest:
    """Agent 核心 → gateway 的归一请求。核心不感知 provider 细节。"""

    messages: list[dict[str, Any]]
    purpose: Purpose
    tools: list[dict[str, Any]] | None = None
    tool_choice: Literal["auto", "required", "none"] = "auto"
    output_schema: type | None = None
    max_tokens: int = 4096
    temperature: float | None = None
    stream: bool = False
    trace: dict[str, Any] | None = None


@dataclass(frozen=True)
class CanonicalEvent:
    """gateway → Agent 核心的归一事件流。"""

    kind: Literal["text", "reasoning", "tool_call", "finished", "error"]
    text: str = ""
    reasoning: str = ""
    tool_call: dict[str, Any] | None = None
    # 归一化 finish_reason: stop/tool_calls/length/content_filter/error/unknown
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    # 仅 trace/debug，不进业务逻辑
    raw: dict[str, Any] | None = None
