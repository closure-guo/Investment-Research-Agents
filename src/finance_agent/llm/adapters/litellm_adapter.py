# src/finance_agent/llm/adapters/litellm_adapter.py
"""litellm adapter：唯一允许 import/配置 litellm 的地方（delta Task 1.4）。

运行时防护收口（design 决策 4）：
- ``disable_streaming_logging``：incident 016 —— litellm 流式每个 chunk 向
  全局 100 线程池提交 logging，worker 内 asyncio.run 新建 ProactorEventLoop，
  Windows/Py3.14 ``_fallback_socketpair`` 并发竞态令线程永久卡在 accept()
  （100 worker 全灭、退出 join 挂死）。项目 Langfuse 走自研 SDK，零损失。
- ``drop_params``：Task 8 起移除全局静默 drop（设计档案 §8：关键参数不静默
  丢弃）。未知/白名单外参数由 litellm 原生报错（显式失败）；白名单内非关键
  参数仅在 capability 明确不支持时由 ``_drop_unsupported`` 剔除。回滚保险：
  环境变量 ``LLM_DROP_PARAMS_STRICT=1`` 可临时恢复旧全局 drop 行为。
- litellm-langfuse 兼容补丁：1.85.x 与 langfuse 4.x 深度不兼容
  （version 属性、sdk_integration 参数等多处不匹配），noop 其 logger。

业务模块（llm.py / harness/litellm_client.py）经 ``ensure_litellm_runtime``
触发初始化，不各自设置 —— 防止防护配置漂移。
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from finance_agent.llm.errors import LLMError
    from finance_agent.llm.types import Capability, ModelProfile

logger = logging.getLogger(__name__)

_INIT_LOCK = threading.Lock()
_initialized = False


def _apply_langfuse_compat_patch() -> None:
    """litellm-langfuse 兼容补丁（自 finance_agent/llm.py 迁入，行为不变）。"""
    try:
        import importlib.metadata

        import langfuse

        if not hasattr(langfuse, "version"):
            _lf_ver = importlib.metadata.version("langfuse")
            langfuse.version = type("version", (), {"__version__": _lf_ver})()  # type: ignore[attr-defined]
    except Exception:  # noqa: S110 -- 补丁失败不阻断初始化
        pass

    def _lf_noop(self, *a, **kw):  # noqa: ARG001
        pass

    def _lf_noop_init(self, *a, **kw):  # noqa: ARG001
        self.langfuse_sdk_version = "4.13.0"
        self.Langfuse = None
        self.langfuse_client = None

    for _cls_path in (
        "litellm.integrations.langfuse.langfuse.LangFuseLogger",
        "litellm.integrations.langfuse.langfuse_prompt_management.LangfusePromptManagement",
    ):
        try:
            _parts = _cls_path.rsplit(".", 1)
            _mod = __import__(_parts[0], fromlist=[_parts[1]])
            _cls = getattr(_mod, _parts[1])
            _cls.__init__ = _lf_noop_init
            for _method in ("log_event_on_langfuse", "_log_langfuse_v2", "_log_langfuse_v1"):
                if hasattr(_cls, _method):
                    setattr(_cls, _method, _lf_noop)
        except Exception:  # noqa: S110
            pass


def ensure_litellm_runtime() -> None:
    """幂等初始化 litellm 运行时防护（线程安全，可并发首次调用）。"""
    global _initialized
    if _initialized:
        return
    with _INIT_LOCK:
        if _initialized:
            return
        import litellm

        # Task 8：移除全局静默 drop（设计档案 §8）；回滚开关：线上出现
        # 端点拒收非关键参数的事故时，设 LLM_DROP_PARAMS_STRICT=1 临时恢复旧行为
        if os.environ.get("LLM_DROP_PARAMS_STRICT") == "1":
            litellm.drop_params = True
        else:
            litellm.drop_params = False
        # incident 016 死锁防护（NOT RECOMMENDED 标记仅影响 litellm 自身
        # 用量回调——项目未注册任何 litellm callback，零功能损失）
        litellm.disable_streaming_logging = True
        _apply_langfuse_compat_patch()
        _initialized = True


# ── 消息序列化收口（delta Task 2.1）─────────────────────────────────


def capability_for_model(model: str) -> Capability:
    """按 model 名推断 Capability（基于 registry preset，不猜行为细节）。

    存量调用方（harness LiteLLMClient）只有 model 字符串；新代码应经
    resolver.resolve_profile 拿完整 ModelProfile。推断规则保守：
    已知 glm/deepseek 映射对应 preset，其余按 openai-compatible 中性假设。
    """
    from finance_agent.llm.registry import get_profile_preset

    lower = model.lower()
    if "glm" in lower:
        return get_profile_preset("ark-glm").capability
    if "deepseek" in lower:
        return get_profile_preset("deepseek-official").capability
    return get_profile_preset("openai-compatible").capability


def _normalize_arguments_str(raw: object) -> object:
    """tool_calls.arguments 规范化为合法 JSON 字符串。

    GLM 等模型偶发输出单引号 Python 字面量（"{'q': 'x'}"），严格端点
    回传 400。合法 JSON 原样返回；Python 字面量经 literal_eval 重 dumps；
    解析失败保留原值（不因清洗破坏请求）。
    """
    import ast
    import json

    if not isinstance(raw, str):
        return raw
    try:
        json.loads(raw)
        return raw
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        return json.dumps(ast.literal_eval(raw), ensure_ascii=False)
    except (ValueError, SyntaxError):
        return raw


def sanitize_messages_for_profile(messages: list[dict], capability: Capability) -> list[dict]:
    """按 capability 清洗回传消息。

    - reasoning 字段：``reasoning_must_echo_on_tool`` 为 False 时剥除
      （方舟拒收该字段 400）；为 True 时保留（DeepSeek 官方要求回传）。
    - arguments 规范化对全部 provider 生效（合法 JSON 是通用要求，
      已合法的值原样返回，无副作用）。
    """
    cleaned: list[dict] = []
    for m in messages:
        m = dict(m)
        if not capability.reasoning_must_echo_on_tool:
            m.pop("reasoning_content", None)
        if m.get("tool_calls"):
            m["tool_calls"] = [
                {
                    **tc,
                    "function": {
                        **tc.get("function", {}),
                        "arguments": _normalize_arguments_str(
                            tc.get("function", {}).get("arguments")
                        ),
                    },
                }
                for tc in m["tool_calls"]
            ]
        cleaned.append(m)
    return cleaned


def sanitize_request_messages(messages: list[dict], capability: Capability) -> list[dict]:
    """gateway 各入口复用命名的薄包装（delta Task 5.1-C.1）。

    逻辑唯一实现仍是 ``sanitize_messages_for_profile``，此处仅提供
    gateway 语义命名（请求前消息清洗），不复制实现。
    """
    return sanitize_messages_for_profile(messages, capability)


# ── 工具增量合并收口（delta Task 5.1-C.1，设计 §8 职责 3）─────────────


class ToolCallAccumulator:
    """流式 tool_calls 增量按 index 聚合（自 harness/litellm_client.py:270-287 移植）。

    litellm 流式 delta 的 tool_calls 片段形如
    ``{id, function: {name, arguments}, index}``（attrs，测试可用同构 dict）；
    arguments 片段跨 chunk 拼接，id/name 首次出现即固定。
    """

    def __init__(self) -> None:
        self._calls: dict[int, dict] = {}

    def add(self, delta: Any) -> None:
        """聚合单个 tool_call 增量片段。"""
        if isinstance(delta, dict):
            idx = delta.get("index", 0)
            tc_id = delta.get("id") or ""
            func = delta.get("function") or {}
        else:
            idx = getattr(delta, "index", 0)
            tc_id = getattr(delta, "id", None) or ""
            func = getattr(delta, "function", None) or {}
        if isinstance(func, dict):
            name = func.get("name", "")
            args = func.get("arguments", "")
        else:
            name = getattr(func, "name", None) or ""
            args = getattr(func, "arguments", None) or ""

        if idx not in self._calls:
            self._calls[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
        call = self._calls[idx]
        if tc_id:
            call["id"] = tc_id
        if name:
            call["function"]["name"] = name
        if args:
            call["function"]["arguments"] += args

    @property
    def calls(self) -> dict[int, dict]:
        """已聚合的 tool_call dict（按 index 键控）。"""
        return self._calls


def finalize_tool_calls(acc: ToolCallAccumulator) -> list[dict]:
    """聚合结果 → 标准 tool_calls 列表（自 harness :46-57 / :411-430 移植）。

    按 index 排序输出 ``{id, function: {name, arguments: <json str>}}``；
    arguments 先经 ``_normalize_arguments_str``（单引号字面量重序列化），
    再做嵌套 ``{"arguments": X}`` 解包（X 为 dict 直接用、为 str 保留），
    空值归为 ``{}``，非法 JSON 不因清洗破坏请求。
    """
    import json

    results: list[dict] = []
    for idx in sorted(acc.calls.keys()):
        raw = acc.calls[idx]
        args_raw = raw["function"]["arguments"] or ""
        normalized = _normalize_arguments_str(args_raw)
        args_str: str = str(normalized) if normalized else "{}"
        try:
            parsed = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, ValueError):
            parsed = {}
        # 嵌套 {"arguments": X} 解包（模型偶发格式，_normalize_tool_args 语义）
        if isinstance(parsed, dict) and len(parsed) == 1 and "arguments" in parsed:
            inner = parsed["arguments"]
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except (json.JSONDecodeError, ValueError):
                    inner = None
            if isinstance(inner, dict):
                parsed = inner
            elif isinstance(inner, str):
                args_str = inner
                parsed = None
        if parsed is not None:
            args_str = json.dumps(parsed, ensure_ascii=False)
        results.append(
            {
                "id": raw.get("id") or f"call_{idx}",
                "function": {"name": raw["function"]["name"], "arguments": args_str},
            }
        )
    return results


def classify_outcome(finish_reason: str | None, *, saw_text_delta: bool) -> None:
    """finish_reason 归一化分型（delta Task 2.2）。

    正常（stop/tool_calls 或有正文 delta 的缺失 reason）返回 None；
    异常分类抛 typed error：length → OutputTruncated（预算复核/repair）、
    content_filter → ContentFiltered（不重试）、无 reason 且无正文 →
    EmptyLLMOutput（重试）——截断/空输出不再伪装成下游 JSONDecodeError。
    """
    from finance_agent.llm.errors import ContentFiltered, EmptyLLMOutput, OutputTruncated

    if finish_reason == "length":
        raise OutputTruncated("finish_reason=length：输出被截断（复核 max_tokens 或 repair）")
    if finish_reason == "content_filter":
        raise ContentFiltered("finish_reason=content_filter：内容被端点过滤，不重试")
    if not saw_text_delta and finish_reason not in ("tool_calls",):
        raise EmptyLLMOutput(
            f"无正文 delta 且 finish_reason={finish_reason!r}：模型思考后即止（incident 017），建议重试"
        )
    return None


def derive_output_budget(capability: Capability, requested: int | None = None) -> int:
    """输出预算派生（delta Task 2.3）：替换 16384 硬编码。

    显式 requested 优先（调用方明确意图）；否则用 capability.max_output
    （preset 已编码：方舟 GLM 因 reasoning_forced 共享配额给 16384，
    其余默认 8192）。reasoning 与正文共享配额的端点预算必须覆盖
    reasoning 峰值（incident 017：4096 下正文被挤空/截断）。
    """
    if requested is not None:
        return requested
    return capability.max_output


_RESUME_INSTRUCTION = "你正在续写一份分析报告，直接无缝继续输出剩余部分，不要重复以上已输出的内容。"


def _estimate_tokens(text: str) -> int:
    """正文 token 估算（续写预算派生用）：仅需近似值。"""
    return max(1, len(text) // 4)


def build_resume_kwargs(
    request_kwargs: dict[str, Any],
    prior_text: str,
    progress_annotation: str | None = None,
) -> dict[str, Any]:
    """构造断点续写请求 kwargs（delta Task 1.1，design D1/D2）。

    基于原 request_kwargs 克隆（外层 dict 与 messages 列表新建，内嵌
    消息对象引用共享；本函数不突变内嵌对象）：messages 末尾合成 1 条
    user 续写指令消息，content 布局为「进度标注（非 None 时在前）→
    已生成内容尾部（prior_text 尾部 4000 字符，design D1 尾部注入基线，
    供模型无缝续写）→ 续写指令」；max_tokens 取剩余配额
    max(1, 原预算 - _estimate_tokens(完整 prior_text))（design D2：
    第一次生成已消耗的配额）。其余 key（model/api_key/endpoint/超时）
    原样保留。
    """
    out = dict(request_kwargs)
    out["messages"] = list(request_kwargs.get("messages", []))
    parts: list[str] = []
    if progress_annotation:
        parts.append(progress_annotation)
    parts.append(f"已生成内容尾部：\n{prior_text[-4000:]}")
    parts.append(_RESUME_INSTRUCTION)
    out["messages"] = out["messages"] + [{"role": "user", "content": "\n\n".join(parts)}]
    base_budget = int(request_kwargs.get("max_tokens") or 4096)
    out["max_tokens"] = max(1, base_budget - _estimate_tokens(prior_text))
    return out


def normalize_exception(exc: Exception) -> LLMError:
    """litellm 异常 → typed error 归一化（delta Task 2.4）。

    调用方只见 LLMError 家族：retryable 标志驱动重试策略
    （ContentFiltered/AuthError 等不重试，RateLimit/Timeout 重试）。
    未知异常包装为 UnknownLLMError 保留上下文，不静默吞掉。
    """
    import litellm as _litellm

    from finance_agent.llm.errors import (
        AuthError,
        ContentFiltered,
        ContextOverflow,
        LLMError,
        LLMTimeoutError,
        ModelNotFound,
        ProviderInternalError,
        RateLimitError,
        UnknownLLMError,
    )

    ex = _litellm.exceptions
    if isinstance(exc, ex.AuthenticationError):
        return AuthError(f"鉴权失败（检查 api_key）：{exc}")
    if isinstance(exc, ex.RateLimitError):
        return RateLimitError(f"限流：{exc}")
    if isinstance(exc, (ex.Timeout, ex.APIConnectionError)):
        return LLMTimeoutError(f"请求超时/连接失败：{exc}")
    if isinstance(exc, (ex.InternalServerError, ex.ServiceUnavailableError)):
        return ProviderInternalError(f"provider 内部错误（可重试）：{exc}")
    if isinstance(exc, ex.NotFoundError):
        return ModelNotFound(f"模型/端点不存在：{exc}")
    if isinstance(exc, ex.ContextWindowExceededError):
        return ContextOverflow(f"上下文超窗：{exc}")
    if isinstance(exc, ex.ContentPolicyViolationError):
        return ContentFiltered(f"内容被端点过滤：{exc}")
    if isinstance(exc, LLMError):
        return exc
    return UnknownLLMError(f"{type(exc).__name__}: {exc}")


def guard_params_supported(
    capability: Capability,
    *,
    tools: list | None = None,
    tool_choice: str = "auto",
    response_format: str | None = None,
) -> None:
    """关键参数能力守卫（delta Task 2.5）：不支持显式抛错，不静默 drop。

    覆盖 tools / tool_choice=required / response_format=json_schema。
    （全局 drop_params 白名单化收口在 Task 5.1，此处先建立显式守卫。）
    """
    from finance_agent.llm.errors import UnsupportedCapabilityError

    if tools and capability.tools == "none":
        raise UnsupportedCapabilityError(
            f"provider 不支持工具调用（capability.tools=none），请求携带 {len(tools)} 个工具；"
            "请降级 action 文本协议或更换 profile"
        )
    if tool_choice == "required" and not capability.tool_choice_required:
        raise UnsupportedCapabilityError("provider 不支持 tool_choice=required")
    if response_format == "json_schema" and capability.json_schema != "strict_schema":
        raise UnsupportedCapabilityError(
            f"provider 不支持 strict schema（capability.json_schema={capability.json_schema}）"
        )


def _provider_options_key(profile: ModelProfile) -> str | None:
    """识别 provider_options 归属的 registry schema key（§7.1）。

    - deepseek 官方 prefix → ``deepseek``
    - ark-glm：provider=openai 且（name 含 ``ark``——preset 名 ark-glm——
      或 model 含 ``glm``——env/request 分支构造的 env:openai/glm-5.3）
      → ``ark-glm``
    - kimi：provider=openai 且（model 含 ``kimi`` 或 k3 前缀段——Kimi Code
      4 个官方模型 ID：k3/k3-256k/kimi-for-coding/kimi-for-coding-highspeed）
      → ``kimi``
    - 其余 provider → None（schema 查无 → 不消费）
    """
    name = (profile.name or "").lower()
    model = (profile.model or "").lower()
    if profile.provider == "deepseek":
        return "deepseek"
    if profile.provider == "openai" and ("ark" in name or "glm" in model):
        return "ark-glm"
    if profile.provider == "openai" and ("kimi" in model or model.split("/")[-1].startswith("k3")):
        return "kimi"
    return None


def apply_provider_options(profile: ModelProfile) -> dict[str, Any]:
    """provider_options 唯一消费点：校验并转为请求 kwargs（设计档案 §7.1）。

    registry schema 校验（非法值/未知 key → pydantic ValidationError）。
    - deepseek（capability.extra_body_allowed 时）：
      ``extra_body.thinking.type``（thinking 显式设置时携带）、
      ``reasoning_effort``（显式设置时携带）、``suppress_temperature: True``
      （thinking=="enabled" 时携带，adapter→gateway 内部契约标志，deepseek
      thinking 模式拒收 temperature，对齐 legacy deep 分支）。
    - ark-glm（provider=openai + name 含 ark / model 含 glm）：
      仅产出 ``reasoning_effort`` 请求参数（官方 max/high/low 三档）；
      thinking/suppress_temperature 是 deepseek 专属，不透传。
    - kimi（provider=openai + model 含 kimi / k3 前缀段）：与 ark-glm 同
      extra_body 机制（litellm openai 路由拒收顶层 reasoning_effort）；
      档位 none/low/high/max（none=关思考，路由无思考版）。
    其他 provider / 空 provider_options → ``{}``。
    """
    options = dict(getattr(profile, "provider_options", None) or {})
    if not options:
        return {}
    from finance_agent.llm.registry import PROVIDER_OPTIONS_SCHEMAS

    key = _provider_options_key(profile)
    if key is None or key not in PROVIDER_OPTIONS_SCHEMAS:
        return {}
    validated = PROVIDER_OPTIONS_SCHEMAS[key].model_validate(options)
    out: dict[str, Any] = {}
    effort = getattr(validated, "reasoning_effort", None)
    if key in ("ark-glm", "kimi"):
        # 实证（方舟 GLM-5.3）：顶层 reasoning_effort 被 litellm openai 路由
        # 判 UnsupportedParamsError 拒绝；必须放 extra_body 才透传到端点
        # （OpenAI 兼容扩展字段）。ark-glm 三档 max/high/low；
        # kimi 四档 none/low/high/max（none=关思考）。
        if effort is not None:
            out["extra_body"] = {"reasoning_effort": effort}
        if key == "kimi" and effort is not None:
            # 实证（Kimi Code，2026-10-04）：端点采样温度锁死——思考档仅接受
            # temperature=1、无思考档（effort=none）仅接受 0.6，其他值一律
            # 400 "invalid temperature"。经内部契约抑制 temperature，交给
            # 端点固定值（#216 降级重试是兜底而非常态）。
            out["suppress_temperature"] = True
        return out
    if effort is not None:
        out["reasoning_effort"] = effort
    if key == "deepseek":
        if not profile.capability.extra_body_allowed:
            return {}
        thinking = getattr(validated, "thinking", None)
        if thinking is not None:
            out["extra_body"] = {"thinking": {"type": thinking}}
        # 内部契约标志：告知 gateway 不发送 temperature（非 litellm 参数）
        if thinking == "enabled":
            out["suppress_temperature"] = True
    return out


# API 形式 → litellm completion 的 ``api`` 参数（add-llm-api-form）：
# chat_completion→/chat/completions、messages→/v1/messages、responses→/responses。
# 仅在显式设置时返回 kwargs；空/未知 → {}（litellm 按 model 前缀自动路由）。
_API_FORM_TO_LITELLM_API = {
    "chat_completion": "chat",
    "messages": "messages",
    "responses": "responses",
}


def apply_api_form_kwargs(profile: ModelProfile) -> dict[str, Any]:
    api_form = getattr(profile, "api_form", None)
    if not api_form:
        return {}
    litellm_api = _API_FORM_TO_LITELLM_API.get(api_form)
    if litellm_api is None:
        return {}  # 上游已校验，防御性兜底
    return {"api": litellm_api}


# 非关键参数白名单（Task 8）：仅这些参数允许按 capability 剔除；
# 白名单外未知参数一律透传，由 litellm/端点原生报错（显式失败）。
_SOFT_DROP_WHITELIST = {"temperature", "top_p", "frequency_penalty", "presence_penalty"}


def _drop_unsupported(kwargs: dict[str, Any]) -> dict[str, Any]:
    """白名单内且端点明确不支持的非关键参数 → 剔除 + warning。

    触发信号（任一命中即认为端点拒收 temperature）：
    - capability 启发：``capability_for_model`` 按模型名映射 registry preset
      （方舟 GLM 类 thinking 强制端点）；存量调用方只有 model 字符串时的兜底。
    - 请求级 thinking：kwargs 自带 ``extra_body.thinking.type=enabled``——
      自定义模型名命中不了名字启发，但 thinking 端点同样拒收 temperature，
      按请求自身信号剔除（#77：请求级 thinking 绕过抑制缺口）。
    top_p/frequency_penalty/presence_penalty 暂无 capability 信号，透传。

    剔除事实 SHALL 落 trace（spec llm-provider-gateway「非关键参数白名单」：
    「按白名单显式剔除并记 trace warning」）：经 update_current_span 写到当前
    generation 观测（未配置 Langfuse 时 no-op，观测故障不阻断业务）。只记进程
    日志则 trace 上看不出「本次调用丢了 temperature」，降级事实不可审计。
    """
    model = kwargs.get("model")
    if not isinstance(model, str):
        return kwargs
    extra_body = kwargs.get("extra_body")
    thinking_type = None
    if isinstance(extra_body, dict):
        thinking = extra_body.get("thinking")
        if isinstance(thinking, dict):
            thinking_type = thinking.get("type")
    request_thinking_forced = thinking_type == "enabled"
    if "temperature" in kwargs and (
        capability_for_model(model).reasoning_forced or request_thinking_forced
    ):
        logger.warning("参数 temperature 被 adapter 白名单剔除(端点不支持)：model=%s", model)
        from finance_agent.langfuse_tracing import update_current_span

        update_current_span(
            metadata={
                "degradation": "drop_params",
                "field": "temperature",
                "model": model,
                # 触发来源可审计：名字启发 vs 请求级 thinking
                "trigger": "request_thinking" if request_thinking_forced else "capability",
            },
            level="WARNING",
        )
        kwargs.pop("temperature")
    return kwargs


def _with_default_timeout(kwargs: dict[str, Any]) -> dict[str, Any]:
    """调用方未传 timeout 时注入默认值（incident 016/017 卡死防护）。

    请求无超时会令流式 chunk 停滞时调用永久挂起（016 线程死锁、
    017 思考后即止两类事故均表现为主管线卡死）。显式 timeout 不覆盖。
    """
    if "timeout" not in kwargs:
        kwargs["timeout"] = float(os.environ.get("LLM_TIMEOUT_SECONDS", "300"))
    return kwargs


# incident 021：opencode zen/go 网关自 2026-09 起对缺 x-opencode-session 头的请求
# 直接 400（"cannot be routed efficiently"），离线 judge 链路全体失败。该头是路由/
# 提示缓存的优化信号（非凭据），文档要求每会话稳定。本项目请求为一次性调用，取
# 进程级稳定 id 即可满足存在性与稳定性。
_OPCODE_SESSION_ID = uuid.uuid4().hex


def _maybe_opencode_session(kwargs: dict[str, Any]) -> dict[str, Any]:
    """api_base 指向 opencode.ai 时注入 x-opencode-session（见 incident 021）。

    仅 opencode zen/go 网关需要该头；其余端点（方舟 glm 等）不注入，避免行为漂移。
    ``base_url`` 是 litellm 的 ``api_base`` 别名（能力探测/设置页测试用该形态），
    两个键都识别——只认 api_base 会让 probe 路径漏头 400。
    """
    base = str(kwargs.get("api_base") or kwargs.get("base_url") or "")
    if "opencode.ai" not in base:
        return kwargs
    headers = dict(kwargs.get("extra_headers") or {})
    headers.setdefault("x-opencode-session", _OPCODE_SESSION_ID)
    kwargs["extra_headers"] = headers
    return kwargs


def _is_temperature_rejected(exc: BaseException) -> bool:
    """端点以 temperature 相关错误拒绝（思考型模型常见：仅允许 temperature=1）。"""
    return "temperature" in str(exc).lower()


def _record_temperature_degradation(model: Any) -> None:
    """降级事实落 trace（观测失败不阻断）+ 进程日志（update-temperature-degrade-fallback）。"""
    logger.warning("端点拒绝 temperature（思考型模型常见限制），剔除后降级重试: model=%s", model)
    try:
        from finance_agent.langfuse_tracing import update_current_span

        update_current_span(metadata={"degradation": "temperature_dropped_endpoint_rejected"})
    except Exception:  # noqa: S110 -- 观测失败不阻断业务
        pass


def raw_completion(**kwargs: Any) -> Any:
    """adapter 内暴露 litellm.completion（gateway/probes 唯一入口）。

    确保 litellm 的 import 与运行时防护只存在于 adapter；业务/gateway/
    probe 经本函数调用，保证门禁（adapters 外禁 import litellm）可执行。
    """
    import litellm

    ensure_litellm_runtime()
    final_kwargs = _with_default_timeout(_drop_unsupported(_maybe_opencode_session(dict(kwargs))))
    try:
        return litellm.completion(**final_kwargs)
    except Exception as exc:
        if "temperature" in final_kwargs and _is_temperature_rejected(exc):
            _record_temperature_degradation(final_kwargs.get("model"))
            final_kwargs.pop("temperature", None)
            return litellm.completion(**final_kwargs)
        raise


def raw_stream(**kwargs: Any) -> Any:
    import litellm

    ensure_litellm_runtime()
    kwargs.setdefault("stream_options", {"include_usage": True})
    kwargs = _maybe_opencode_session(dict(kwargs))
    final_kwargs = _with_default_timeout(_drop_unsupported(kwargs))
    try:
        return litellm.completion(**final_kwargs, stream=True)
    except Exception as exc:
        if "temperature" in final_kwargs and _is_temperature_rejected(exc):
            _record_temperature_degradation(final_kwargs.get("model"))
            final_kwargs.pop("temperature", None)
            return litellm.completion(**final_kwargs, stream=True)
        raise


async def raw_acompletion(**kwargs: Any) -> Any:
    """adapter 内暴露异步 litellm.acompletion（harness 唯一入口）。"""
    import litellm

    ensure_litellm_runtime()
    if kwargs.get("stream"):
        # OpenAI 兼容端点（glm 等）流式默认不带 usage：必须显式 include_usage，
        # 否则 generation 无 token 用量 → Langfuse cost 无法计算
        kwargs.setdefault("stream_options", {"include_usage": True})
    kwargs = _maybe_opencode_session(dict(kwargs))
    final_kwargs = _with_default_timeout(_drop_unsupported(kwargs))
    try:
        return await litellm.acompletion(**final_kwargs)
    except Exception as exc:
        if "temperature" in final_kwargs and _is_temperature_rejected(exc):
            _record_temperature_degradation(final_kwargs.get("model"))
            final_kwargs.pop("temperature", None)
            return await litellm.acompletion(**final_kwargs)
        raise
