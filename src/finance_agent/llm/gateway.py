# src/finance_agent/llm/gateway.py
"""LLM Gateway 统一入口（delta 4.4，设计档案 §4/§14）。

本模块提供 trace 契约 metadata 构造与统一 complete 入口骨架；业务代码
应经 gateway（后续 5.1 薄壳转调），而不是直接 import litellm。
当前阶段实现 build_trace_metadata（可验收）+ complete_text 最小实现，
streaming/with_tools 在 5.1 薄壳转调时补全。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from finance_agent.llm.adapters.litellm_adapter import (
    ensure_litellm_runtime,
    guard_params_supported,
)
from finance_agent.llm.errors import (
    AuthError,
    ContentFilteredError,
    LLMTimeoutError,
    ModelNotFoundError,
    OutputContractError,
    OutputTruncatedError,
    UnsupportedCapabilityError,
)
from finance_agent.llm.registry import get_profile_preset, list_presets
from finance_agent.llm.resolver import resolve_profile
from finance_agent.llm.router import select_profile
from finance_agent.llm.types import ModelProfile, Purpose


def _raw_completion_with_timeout(request_kwargs: dict[str, Any], timeout_seconds: float):
    """raw_completion 半僵连接兜底（incident 016/017 同族）。

    litellm 请求级 timeout 对挂死连接不触发——把阻塞调用放进 daemon 线程，
    主侧队列超时强制终止；超时抛 retryable LLMTimeoutError（调用方重试）。
    """
    import queue as _q
    import threading as _t

    q: _q.Queue = _q.Queue()

    def _call() -> None:
        try:
            from finance_agent.llm.adapters.litellm_adapter import raw_completion

            q.put(("ok", raw_completion(**request_kwargs)))
        except BaseException as exc:  # noqa: BLE001 -- 异常经队列回传
            q.put(("err", exc))

    _t.Thread(target=_call, daemon=True).start()
    try:
        status, item = q.get(timeout=timeout_seconds)
    except _q.Empty:
        from finance_agent.llm.errors import LLMTimeoutError

        raise LLMTimeoutError(
            f"非流式调用超过 {timeout_seconds}s 未返回（半僵连接），终止本次生成"
        ) from None
    if status == "err":
        raise item
    return item


def build_trace_metadata(
    profile: ModelProfile,
    *,
    purpose: Purpose,
    finish_reason: str | None = None,
    repair_count: int = 0,
    fallback_from: str | None = None,
    degradation: str | None = None,
    max_tokens_source: str | None = None,
    usage_estimated: bool | None = None,
) -> dict:
    """构造 generation trace 契约字段（design 档案 §14）。

    ``max_tokens_source``（"requested"|"capability"）与 ``usage_estimated``
    为可选预算治理观测键：仅显式传入时落入 dict，既有消费者不受影响。
    """
    meta = {
        "profile": profile.name,
        "provider": profile.provider,
        "model": profile.model,
        "purpose": purpose,
        "capability": {
            "tools": profile.capability.tools,
            "json_schema": profile.capability.json_schema,
        },
        "finish_reason": finish_reason,
        "repair_count": repair_count,
        "fallback_from": fallback_from,
        "degradation": degradation,
    }
    if max_tokens_source is not None:
        meta["max_tokens_source"] = max_tokens_source
    if usage_estimated is not None:
        meta["usage_estimated"] = usage_estimated
    return meta


def _maybe_resume_text(finish: str | None, answer: str | None) -> bool:
    """判定是否触发续写（llm-output-resume Task 1.2）：仅 finish=length 且正文非空。"""
    return finish == "length" and bool(answer)


_STATUS_ICON = {"done": "✅ 已完成", "in_progress": "⏳", "pending": "⬜ 未开始"}


def _build_progress_annotation(answer: str | None, top_fields: list[str] | None) -> str | None:
    """从已生成正文 + 顶层字段清单生成进度标注文本；不可解析返回 None。

    只报「已闭合数量级」事实（in_progress 字段不附目标总数），
    交付给 build_resume_kwargs 的 progress_annotation。
    """
    from finance_agent.llm.contracts import partial_json_progress

    if not answer or not top_fields:
        return None
    prog = partial_json_progress(answer, top_fields)
    if prog is None:
        return None
    lines = ["当前输出进度："]
    for f in top_fields:
        state = prog.get(f, "pending")
        icon = _STATUS_ICON[state]
        if state == "in_progress":
            # 只报已闭合事实，不编造目标数量（"/5" 解析器无法得知）
            lines.append(f"- {f}: {icon} 断点位于已输出尾部")
        else:
            lines.append(f"- {f}: {icon}")
    return "\n".join(lines)


def _start_trace_observation(trace: dict[str, Any] | None, profile, messages):
    """按 trace dict 开启 Langfuse generation 观测（失败不阻断，返回 None）。"""
    if not trace:
        return None, None
    from finance_agent.langfuse_tracing import get_langfuse

    lf = get_langfuse()
    if lf is None:
        return None, None
    try:
        gen_cm = lf.start_as_current_observation(
            as_type="generation",
            name=trace.get("name") or f"litellm:{profile.model}",
            model=profile.model,
            input={"messages": messages},
            metadata=trace.get("metadata") or {},
        )
        return gen_cm, gen_cm.__enter__()
    except Exception:  # noqa: S110 -- 观测失败不阻断业务
        return None, None


def _usage_details(resp) -> dict:
    """resp.usage → Langfuse usage_details（无 usage 时空 dict）。"""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return {}
    return {
        "input": getattr(usage, "prompt_tokens", 0) or 0,
        "output": getattr(usage, "completion_tokens", 0) or 0,
    }


def _canonical_usage(usage) -> dict[str, int] | None:
    """usage 对象 → CanonicalEvent.usage（provider 中立键名，缺省 None）。

    键名取 prompt_tokens/completion_tokens/total_tokens：消费方（harness ReAct 循环）
    据此取 prompt_tokens 校准上下文预算——预算约束的是请求上下文，不含输出。
    无任何 token 计数时返回 None（消费方据此保持 usage_estimated 估算态）。
    """
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    total = getattr(usage, "total_tokens", None)
    if prompt is None and completion is None and total is None:
        return None
    p = int(prompt or 0)
    c = int(completion or 0)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": int(total or (p + c))}


def _extract_with_tools_output(resp: Any) -> dict:
    """从 completion resp 提取结构化 generation output（自 legacy.py 移植）。

    返回 ``{answer, reasoning}``，非空 tool_calls 时追加 ``tool_calls`` 字段
    （``[{name, arguments}]``，arguments 裁剪）。answer 不回退 reasoning
    （工具调用场景 content 通常为空，回退会误导 trace）。
    """
    from finance_agent.langfuse_tracing import truncate_for_trace

    message = None
    choices = getattr(resp, "choices", [])
    if choices:
        message = getattr(choices[0], "message", None)
    output_text = (getattr(message, "content", "") or "") if message else ""
    reasoning_text = (getattr(message, "reasoning_content", "") or "") if message else ""
    tool_calls: list[dict] = []
    if message is not None:
        for tc in getattr(message, "tool_calls", None) or []:
            func = getattr(tc, "function", None)
            tc_name = (getattr(func, "name", "") or "") if func else ""
            tc_args = (getattr(func, "arguments", "") or "") if func else ""
            tool_calls.append({"name": tc_name, "arguments": truncate_for_trace(tc_args)})
    out: dict = {
        "answer": truncate_for_trace(output_text),
        "reasoning": truncate_for_trace(reasoning_text),
    }
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def complete_text(
    messages: list[dict[str, Any]],
    *,
    purpose: Purpose = "deep",
    max_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    top_fields: list[str] | None = None,
    llm_config: dict[str, Any] | None = None,
    preset: str | None = None,
    temperature: float | None = None,
    trace: dict[str, Any] | None = None,
    timeout_seconds: float = 300.0,
) -> tuple[str, dict]:
    """统一非流式 complete 入口（5.1-C：trace 观测 + sanitize + raw_* 元数据）。

    返回 ``(text, metadata)``：text 为 message.content（缺失空串，不做
    reasoning 回退）；metadata 除 trace 契约字段（build_trace_metadata）外
    附带 ``raw_content`` / ``raw_reasoning``（非 trace 字段，供 legacy 薄壳
    实现「content 为空回退 reasoning」旧行为）。
    ``timeout_seconds``：半僵连接兜底（incident 016/017 同族——请求级 300s
    超时对挂死连接不触发），非流式调用经 daemon 线程泵 + 队列超时强制终止
    （report/nlp/web_fetcher 全经此路径）。

    ``trace``（可选）开启 Langfuse generation 观测（name/metadata），
    观测失败不阻断业务；output 结构 ``{answer, reasoning}`` + usage_details。
    ``preset``：显式命名 preset（透传 resolve_profile，fallback 链成员用）。

    断点续写（llm-output-resume Task 5）：finish_reason=length 且正文非空时
    以「已生成正文尾部(+进度标注) + 剩余配额」构造续写请求二次调用拼接，
    返回 metadata 附 ``resume_count=1`` 且 finish_reason 取续写段；续写仍
    length → ``OutputTruncatedError``。``top_fields``（可选）：调用方 schema
    顶层字段清单，None 则不做进度标注（仅尾部续写）。
    """
    profile = resolve_profile(purpose=purpose, llm_config=llm_config, preset=preset)
    ensure_litellm_runtime()
    guard_params_supported(profile.capability, tools=tools, tool_choice="auto")
    from finance_agent.llm.adapters.litellm_adapter import (
        apply_api_form_kwargs,
        apply_provider_options,
        derive_output_budget,
        normalize_exception,
        sanitize_request_messages,
    )

    messages = sanitize_request_messages(messages, profile.capability)
    _gen_cm, _gen = _start_trace_observation(trace, profile, messages)

    budget = derive_output_budget(profile.capability, requested=max_tokens)
    # provider_options 消费（§7.1）：merge 在 default_params 之后（可覆盖）；
    # ``suppress_temperature`` 是 adapter→gateway 内部契约标志：deepseek
    # thinking=enabled 时端点拒收 temperature（对齐 legacy deep 分支），不发。
    provider_kwargs = apply_provider_options(profile)
    suppress_temperature = bool(provider_kwargs.pop("suppress_temperature", False))
    request_kwargs: dict[str, Any] = {
        "model": profile.model,
        "messages": messages,
        "max_tokens": budget,
        **(profile.default_params or {}),
        **provider_kwargs,
        **apply_api_form_kwargs(profile),
    }
    if profile.api_key:
        request_kwargs["api_key"] = profile.api_key
    if profile.base_url:
        request_kwargs["api_base"] = profile.base_url
    if not suppress_temperature and temperature is not None:
        request_kwargs["temperature"] = temperature
    _resumed = False
    try:
        resp = _raw_completion_with_timeout(request_kwargs, timeout_seconds)
        message = resp.choices[0].message
        raw_content = message.content or ""
        raw_reasoning = getattr(message, "reasoning_content", "") or ""
        # 断点续写（llm-output-resume Task 5）：finish_reason=length 且正文非空
        # → 以「已生成正文尾部(+进度标注) + 剩余配额」构造续写请求二次调用拼接
        # （resume 上限 1）；续写仍 length → OutputTruncatedError（观测 truncated
        # 后上抛）。续写段异常经既有 except 收口 normalize 上抛。
        if _maybe_resume_text(resp.choices[0].finish_reason, raw_content):
            from finance_agent.llm.adapters.litellm_adapter import build_resume_kwargs
            from finance_agent.llm.contracts import partial_json_progress

            ann = None
            if top_fields:
                prog = partial_json_progress(raw_content, top_fields)
                if prog is not None:
                    ann = _build_progress_annotation(raw_content, top_fields)
            resume_kwargs = build_resume_kwargs(
                request_kwargs, prior_text=raw_content, progress_annotation=ann
            )
            try:
                resp2 = _raw_completion_with_timeout(resume_kwargs, timeout_seconds)
                raw_content += resp2.choices[0].message.content or ""
                # 续写完成后统一按 content 处理（正文已拼接，reasoning 不再回退）
                raw_reasoning = raw_content
                _resumed = True
                if _gen is not None:
                    from contextlib import suppress

                    with suppress(Exception):  # 观测失败不阻断
                        _gen.update(
                            metadata={
                                **((trace or {}).get("metadata") or {}),
                                "resume_count": 1,
                            }
                        )
                if resp2.choices[0].finish_reason == "length":
                    raise OutputTruncatedError(
                        "续写仍 finish_reason=length：输出被截断（resume 上限 1）"
                    )
                resp = resp2
            except OutputTruncatedError:
                if _gen is not None:
                    from contextlib import suppress

                    with suppress(Exception):  # 观测失败不阻断
                        _gen.update(
                            metadata={
                                **((trace or {}).get("metadata") or {}),
                                "resume_count": 1,
                                "truncated": True,
                            },
                            level="ERROR",
                        )
                raise
    except Exception as exc:  # noqa: BLE001
        if _gen is not None:
            from contextlib import suppress

            with suppress(Exception):  # 观测失败不阻断
                _gen.update(metadata={"error_type": type(exc).__name__}, level="ERROR")
        _close_observation(_gen_cm)
        raise normalize_exception(exc) from exc
    # Langfuse output.answer 与 legacy call_llm 对齐：content 为空时用
    # reasoning 作为 answer（legacy trace 行为），但返回 text 不做回退。
    _finalize_observation(
        _gen,
        raw_content or raw_reasoning,
        raw_reasoning,
        getattr(resp, "usage", None),
        metadata=(trace or {}).get("metadata"),
    )
    _close_observation(_gen_cm)
    text = raw_content
    metadata = build_trace_metadata(
        profile,
        purpose=purpose,
        finish_reason=resp.choices[0].finish_reason,
        max_tokens_source="requested" if max_tokens is not None else "capability",
        # usage 真值存在时非估算（设计档案 §12：估算必须标 usage_estimated=true）
        usage_estimated=getattr(resp, "usage", None) is None,
    )
    metadata["raw_content"] = raw_content
    metadata["raw_reasoning"] = raw_reasoning
    if _resumed:
        # 续写成功可追溯：resume_count=1 落返回 metadata（与观测侧一致）
        metadata["resume_count"] = 1
    return text, metadata


# fallback 链触发错误（spec llm-policy-router Requirement 2）：不可经同
# profile 重试解决的（合同耗尽/内容过滤/鉴权/模型缺失/能力不支持）依链
# 切换 profile；网络瞬时错误不在此处理（流路径内部自有重试）。节点路径
# （nodes/_llm_utils）复用同一集合，另加本路径的合同 repair 耗尽错误。
FALLBACK_TRIGGER_ERRORS = (
    OutputContractError,
    ContentFilteredError,
    AuthError,
    ModelNotFoundError,
    UnsupportedCapabilityError,
)
_MAX_FALLBACK_ATTEMPTS = 3


@dataclass(frozen=True)
class FallbackAttempt:
    """fallback 链的一次尝试：profile 名 + 该次尝试的配置选择依据。

    index=0 是请求级 primary：llm_config 可透传（primary 命中命名 preset 时
    改用 preset 名，避免链执行中途重解析漂移）；index>0 是链成员，只按命名
    preset 选中——请求级配置描述 primary 的端点，不得带到成员上。
    """

    profile: str
    index: int
    llm_config: dict[str, Any] | None
    preset: str | None


@dataclass(frozen=True)
class FallbackPlan:
    """依序尝试计划：attempts（链长上限内）+ router 审计 trace。"""

    attempts: list[FallbackAttempt]
    router_trace: dict


def fallback_attempt_plan(
    *, purpose: Purpose = "deep", llm_config: dict[str, Any] | None = None
) -> FallbackPlan:
    """算 fallback 链的依序尝试计划（非流式执行器与节点路径共用，避免口径漂移）。

    primary = ``resolve_profile(purpose, llm_config)``；候选 = primary + 其
    registry fallback 名 → ``select_profile`` 做能力偏序校验（链成员能力 >=
    primary）与排序 → 请求级 primary 打头，链成员按路由序去重，截到
    ``_MAX_FALLBACK_ATTEMPTS``。
    """
    primary = resolve_profile(purpose=purpose, llm_config=llm_config)
    candidates: list[ModelProfile] = [primary]
    for name in primary.fallback:
        candidates.append(get_profile_preset(name))
    routed = select_profile(purpose=purpose, candidates=candidates)
    # 路由 primary 与请求解析 primary 一致时直接用其链；不一致仍以
    # 请求级 primary 打头（请求语义优先），链成员按路由序去重。
    ordered: list[ModelProfile] = [primary]
    for cand in [routed.primary, *routed.fallback_chain]:
        if all(cand.name != p.name for p in ordered):
            ordered.append(cand)
    ordered = ordered[:_MAX_FALLBACK_ATTEMPTS]

    preset_names = set(list_presets())
    attempts: list[FallbackAttempt] = []
    for idx, prof in enumerate(ordered):
        preset = prof.name if prof.name in preset_names else None
        attempts.append(
            FallbackAttempt(
                profile=prof.name,
                index=idx,
                llm_config=llm_config if idx == 0 and preset is None else None,
                preset=preset,
            )
        )
    return FallbackPlan(attempts=attempts, router_trace=routed.trace)


def _attempt_trace(
    trace: dict[str, Any] | None, *, previous: str | None, path: list[str]
) -> dict[str, Any] | None:
    """把切换事实并入本次尝试的 trace metadata（每次切换各自成观测记录）。

    spec「每次切换 MUST 在 trace 记录 fallback_from」：多跳链里中间那跳也必须
    可见，故每次尝试携带自己的切换事实（``fallback_from`` = 触发切换的上一
    成员，``fallback_path`` = 含本次在内的完整尝试路径）。首次尝试不改写
    metadata；未开启观测（trace 为空）原样返回。
    """
    if not trace or previous is None:
        return trace
    metadata = dict(trace.get("metadata") or {})
    metadata["fallback_from"] = previous
    metadata["fallback_path"] = list(path)
    return {**trace, "metadata": metadata}


def complete_text_with_fallback(
    messages: list[dict[str, Any]],
    *,
    purpose: Purpose = "deep",
    llm_config: dict[str, Any] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    trace: dict[str, Any] | None = None,
) -> tuple[str, dict]:
    """带 fallback 链的非流式 complete 入口（harden Task 4）。

    按 ``fallback_attempt_plan`` 依序尝试：捕获 ``FALLBACK_TRIGGER_ERRORS``
    → 链未耗尽换下一成员重试（每次切换并入该次尝试的 trace metadata）；
    成功后返回 metadata 合并 ``fallback_from``（触发切换的上一成员）、
    ``fallback_path``（完整尝试路径）与 ``router_trace``；链耗尽上抛最后
    错误；其他异常立即传播。总尝试次数 ≤3。
    """
    plan = fallback_attempt_plan(purpose=purpose, llm_config=llm_config)
    last_error: Exception | None = None
    attempted: list[str] = []
    for att in plan.attempts:
        fallback_from = attempted[-1] if attempted else None
        path = [*attempted, att.profile]
        try:
            text, metadata = complete_text(
                messages,
                purpose=purpose,
                max_tokens=max_tokens,
                llm_config=att.llm_config,
                preset=att.preset,
                temperature=temperature,
                trace=_attempt_trace(trace, previous=fallback_from, path=path),
            )
        except FALLBACK_TRIGGER_ERRORS as exc:
            last_error = exc
            attempted.append(att.profile)
            if att.index + 1 < len(plan.attempts):
                continue
            raise
        if att.index > 0:
            metadata["fallback_from"] = fallback_from
            metadata["fallback_path"] = path
            metadata["router_trace"] = plan.router_trace
        return text, metadata
    raise last_error if last_error is not None else RuntimeError("fallback 链为空")


def _close_observation(gen_cm) -> None:
    """退出 Langfuse observation CM（失败不阻断）。"""
    if gen_cm is None:
        return
    from contextlib import suppress

    with suppress(Exception):
        gen_cm.__exit__(None, None, None)


def complete_with_tools(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
    purpose: Purpose = "quick",
    max_tokens: int | None = None,
    temperature: float | None = None,
    llm_config: dict[str, Any] | None = None,
    trace: dict[str, Any] | None = None,
):
    """统一非流式带工具 complete 入口（5.1-C Task 3）。

    resolve_profile → guard → sanitize → budget → apply_provider_options →
    raw_completion，**返回原始 resp 对象**（调用方检查 ``.tool_calls``）。
    Langfuse 观测 output 含 ``{answer, reasoning, tool_calls?}`` + usage_details；
    主观测不可用时降级经 ``open_span`` 记录（对齐 legacy 降级路径）。

    计划内语义修正（5.1-C，零生产调用方）：deepseek thinking+tools 保持
    开启（registry provider_options 默认），不再像 legacy 显式 disabled。
    """
    from contextlib import suppress

    profile = resolve_profile(purpose=purpose, llm_config=llm_config)
    ensure_litellm_runtime()
    guard_params_supported(profile.capability, tools=tools, tool_choice=tool_choice)
    from finance_agent.llm.adapters.litellm_adapter import (
        apply_api_form_kwargs,
        apply_provider_options,
        derive_output_budget,
        normalize_exception,
        raw_completion,
        sanitize_request_messages,
    )

    messages = sanitize_request_messages(messages, profile.capability)
    _gen_cm, _gen = _start_trace_observation(trace, profile, messages)

    budget = derive_output_budget(profile.capability, requested=max_tokens)
    provider_kwargs = apply_provider_options(profile)
    suppress_temperature = bool(provider_kwargs.pop("suppress_temperature", False))
    request_kwargs: dict[str, Any] = {
        "model": profile.model,
        "messages": messages,
        "max_tokens": budget,
        **{k: v for k, v in (profile.default_params or {}).items() if k != "max_tokens"},
        **provider_kwargs,
        **apply_api_form_kwargs(profile),
    }
    if profile.api_key:
        request_kwargs["api_key"] = profile.api_key
    if profile.base_url:
        request_kwargs["api_base"] = profile.base_url
    if tools:
        request_kwargs["tools"] = tools
        if tool_choice:
            request_kwargs["tool_choice"] = tool_choice
    if not suppress_temperature and temperature is not None:
        request_kwargs["temperature"] = temperature

    def _do_call():
        try:
            return raw_completion(**request_kwargs)
        except Exception as exc:  # noqa: BLE001
            raise normalize_exception(exc) from exc

    if trace and _gen is None:
        # 降级路径（对齐 legacy）：主观测不可用 → open_span（自带 no-op 兜底）
        from finance_agent.langfuse_tracing import open_span

        with open_span(
            name=trace.get("name") or f"litellm:{profile.model}",
            input={"messages": messages},
            # prompt metadata 与主观测（_start_trace_observation）同源下发
            metadata=trace.get("metadata") or {},
            # 与主路径同型（generation）：span 观测会静默丢弃 usage_details，
            # token 用量只有 generation 落库（langfuse 4.13 真机实测）
            as_type="generation",
            model=profile.model,
        ) as obs:
            resp = _do_call()
            if obs is not None:
                with suppress(Exception):  # trace 失败不影响业务
                    # 字段与主路径对齐：output + usage_details（token 用量可比较）
                    obs.update(
                        output=_extract_with_tools_output(resp),
                        usage_details=_usage_details(resp),
                    )
            _close_observation(_gen_cm)
            return resp

    resp = _do_call()
    if _gen is not None:
        with suppress(Exception):
            _gen.update(output=_extract_with_tools_output(resp), usage_details=_usage_details(resp))
    _close_observation(_gen_cm)
    return resp


def complete_stream(
    messages: list[dict[str, Any]],
    *,
    purpose: Purpose = "deep",
    max_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    top_fields: list[str] | None = None,
    llm_config: dict[str, Any] | None = None,
    preset: str | None = None,
    trace: dict[str, Any] | None = None,
    temperature: float | None = None,
    chunk_timeout: float = 120.0,
):
    """统一流式 complete 入口（delta 5.1）：yield CanonicalEvent。

    Agent 核心消费归一事件流（reasoning/text/finished/error），不感知
    provider 细节。对接 litellm 同步流 chunk（choices[0].delta:
    reasoning_content -> reasoning；content -> text）。

    ``preset``（可选）：显式命名 preset 选中 profile（registry），供
    fallback 链成员按 preset 名切换使用（与非流式入口 ``complete_text``
    同语义：请求级 llm_config 优先于 preset）。

    断点续写（llm-output-resume delta）：finish_reason=length 且正文非空时
    以「已生成正文尾部 + 进度标注 + 剩余预算」构造续写请求二次流式，续写段
    text 事件直接续发，最终 finished 事件携带续写段 reason；续写仍 length →
    error 事件（OutputTruncatedError）+ 观测 truncated=true。非 length 时
    零额外调用。``top_fields``（可选）：调用方 schema 顶层字段清单，None 则
    不做进度标注（仅尾部续写）。

    ``trace``（可选）开启 Langfuse generation 观测：``{"name","metadata"}``。
    观测收口在 gateway（自 legacy.call_llm_stream 移植），调用方传入
    观测元数据，core 不直接触 Langfuse；不传则不观测（纯净接口）。
    """
    from finance_agent.llm.types import CanonicalEvent

    profile = resolve_profile(purpose=purpose, llm_config=llm_config, preset=preset)
    ensure_litellm_runtime()
    guard_params_supported(profile.capability, tools=tools, tool_choice="auto")
    from finance_agent.llm.adapters.litellm_adapter import (
        apply_api_form_kwargs,
        apply_provider_options,
        classify_outcome,
        derive_output_budget,
        normalize_exception,
        raw_stream,
    )

    # Langfuse observation（可选）：观测收口（B1），结束前 update 根 span
    # output（incident #67：root span output 必须在 span exit/flush 前写）。
    _lf = None
    _gen_cm = None
    _gen = None
    if trace:
        from finance_agent.langfuse_tracing import get_langfuse

        _lf = get_langfuse()
        if _lf is not None:
            try:
                _gen_cm = _lf.start_as_current_observation(
                    as_type="generation",
                    name=trace.get("name") or f"litellm:{profile.model}",
                    model=profile.model,
                    input={"messages": messages},
                    metadata=trace.get("metadata") or {},
                )
                _gen = _gen_cm.__enter__()
            except Exception:  # noqa: S110 -- 观测失败不阻断业务
                _gen = None
                _gen_cm = None

    budget = derive_output_budget(profile.capability, requested=max_tokens)
    # provider_options 消费（§7.1）：merge 在 default_params 之后（可覆盖）；
    # ``suppress_temperature`` 是 adapter→gateway 内部契约标志：deepseek
    # thinking=enabled 时端点拒收 temperature（对齐 legacy deep 分支），不发。
    provider_kwargs = apply_provider_options(profile)
    suppress_temperature = bool(provider_kwargs.pop("suppress_temperature", False))
    request_kwargs: dict[str, Any] = {
        "model": profile.model,
        "messages": messages,
        "max_tokens": budget,
        "api_base": profile.base_url,
        **{k: v for k, v in (profile.default_params or {}).items() if k != "max_tokens"},
        **provider_kwargs,
        **apply_api_form_kwargs(profile),
    }
    if profile.api_key:
        # keyless 本地端点（如 Ollama）api_key 为 None：不发送空 key
        request_kwargs["api_key"] = profile.api_key
    if tools:
        # tools 非空才携带：tools=None 下发会让方舟等式端点短思考后直接
        # finish=length（drop_params 白名单化后不再静默丢弃 None）——
        # evals 全节点 answer=0/truncation 的根因。
        request_kwargs["tools"] = tools
    if not suppress_temperature and temperature is not None:
        request_kwargs["temperature"] = temperature
    # 累积变量须在 try 外初始化：raw_stream 抛异常时（端点拒连/超时），
    # except 分支的 _finalize_observation 仍要引用它们——若初始化在 try 内
    # raw_stream 之后，异常时变量未绑定 → NameError 盖住原始异常
    # （evals Item 9-16 全败的根因，'cannot access local variable _answer'）。
    _answer = ""
    _reasoning = ""
    _last_usage = None
    try:
        stream = raw_stream(**request_kwargs)
        saw_text = False
        finish = None
        # per-chunk 超时保护（incident 016/017 卡死族）：同步阻塞迭代无法被
        # 中断，用 daemon 线程把 chunk 泵进队列、主侧带超时取——半僵流
        # （chunk 永不到达且 litellm 请求级 timeout 不触发）不再挂死进程。
        import queue as _queue
        import threading as _threading

        _DONE = object()
        _q: _queue.Queue = _queue.Queue()

        def _pump() -> None:
            try:
                for chunk in stream:
                    _q.put(chunk)
            except BaseException as exc:  # noqa: BLE001 -- 异常经队列回传主侧
                _q.put(exc)
            else:
                _q.put(_DONE)

        _threading.Thread(target=_pump, daemon=True).start()
        while True:
            try:
                item = _q.get(timeout=chunk_timeout)
            except _queue.Empty:
                from finance_agent.llm.errors import LLMTimeoutError

                raise LLMTimeoutError(
                    f"流式 chunk 超过 {chunk_timeout}s 未到达（半僵流），终止本次生成"
                ) from None
            if item is _DONE:
                break
            if isinstance(item, BaseException):
                raise item
            chunk = item
            # usage 帧（OpenAI 兼容端点 include_usage）：choices 为空，必须先取
            # usage 再跳过——镜像异步主循环的 `if not choices: continue` 护栏。
            # 否则 choices[0] 抛 IndexError → normalize_exception 归一为 error
            # 事件，整次调用失败（Δ3T3 审查实测）。
            if getattr(chunk, "usage", None):
                _last_usage = chunk.usage
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.delta
            finish = getattr(choice, "finish_reason", None) or finish
            if delta and hasattr(delta, "reasoning_content") and delta.reasoning_content:
                rc = str(delta.reasoning_content)
                _reasoning += rc
                yield CanonicalEvent(kind="reasoning", reasoning=rc)
            if delta and getattr(delta, "content", None):
                ct = str(delta.content)
                _answer += ct
                saw_text = True
                yield CanonicalEvent(kind="text", text=ct)
        _finalize_observation(
            _gen, _answer, _reasoning, _last_usage, metadata=(trace or {}).get("metadata")
        )
        _finish = finish
        if _maybe_resume_text(_finish, _answer):
            # 断点续写（llm-output-resume Delta Task 4）：finish=length 且正文
            # 非空 → 以「已生成正文尾部(+进度标注) + 剩余预算」构造续写请求二次
            # 流式，续写段 text 事件直接续发（无 error/finished 前插），最终
            # finished 事件携带续写段 reason。续写段内层循环与主循环共享
            # _answer/_reasoning/_last_usage 累积（直接 for 迭代，避开主泵线程）。
            from finance_agent.llm.adapters.litellm_adapter import build_resume_kwargs
            from finance_agent.llm.contracts import partial_json_progress
            from finance_agent.llm.errors import OutputTruncatedError

            ann = None
            if top_fields:
                prog = partial_json_progress(_answer, top_fields)
                if prog is not None:
                    ann = _build_progress_annotation(_answer, top_fields)
            resume_kwargs = build_resume_kwargs(
                request_kwargs, prior_text=_answer, progress_annotation=ann
            )
            try:
                stream2 = raw_stream(**resume_kwargs)
                _finish = None
                for chunk2 in stream2:
                    # 续写段 usage 累积（镜像异步续写循环 `last_usage = chunk2.usage`）：
                    # finished 事件报告的是**最终**一段 provider usage。
                    if getattr(chunk2, "usage", None):
                        _last_usage = chunk2.usage
                    choices2 = getattr(chunk2, "choices", None) or []
                    if not choices2:
                        continue
                    choice2 = choices2[0]
                    d2 = choice2.delta
                    if (
                        getattr(choice2, "finish_reason", None)
                        and getattr(choice2, "finish_reason", None) != _finish
                    ):
                        _finish = getattr(choice2, "finish_reason", None) or _finish
                    if d2 and getattr(d2, "reasoning_content", None):
                        _reasoning += str(d2.reasoning_content)
                    if d2 and getattr(d2, "content", None):
                        ct = str(d2.content)
                        _answer += ct
                        yield CanonicalEvent(kind="text", text=ct)
            except Exception as exc2:  # noqa: BLE001 -- 续写段异常与主循环同风格收口
                _finalize_observation(
                    _gen,
                    _answer,
                    _reasoning,
                    _last_usage,
                    metadata=(trace or {}).get("metadata"),
                )
                err2 = normalize_exception(exc2)
                if _gen is not None:
                    from contextlib import suppress

                    with suppress(Exception):  # noqa: S110 -- 观测失败不阻断
                        _gen.update(
                            metadata={
                                **((trace or {}).get("metadata") or {}),
                                "truncated": True,
                            },
                            level="ERROR",
                        )
                yield CanonicalEvent(
                    kind="error",
                    finish_reason=type(err2).__name__,
                    raw={"error": str(err2)},
                )
                return
            # 续写段观测收口：resume_count=1 落观测（spec 续写可追溯）
            if _gen is not None:
                from contextlib import suppress

                with suppress(Exception):  # noqa: S110 -- 观测失败不阻断
                    _gen.update(
                        metadata={**((trace or {}).get("metadata") or {}), "resume_count": 1}
                    )
            if _maybe_resume_text(_finish, _answer):
                # 续写仍截断：resume 上限 1，上抛 OutputTruncatedError + truncated
                exc3 = OutputTruncatedError(
                    "续写仍 finish_reason=length：输出被截断（resume 上限 1）"
                )
                if _gen is not None:
                    from contextlib import suppress

                    with suppress(Exception):  # noqa: S110 -- 观测失败不阻断
                        _gen.update(
                            metadata={
                                **((trace or {}).get("metadata") or {}),
                                "resume_count": 1,
                                "truncated": True,
                            },
                            level="ERROR",
                        )
                yield CanonicalEvent(
                    kind="error",
                    finish_reason=type(exc3).__name__,
                    raw={"error": str(exc3)},
                )
                return
            finish = _finish
        try:
            classify_outcome(finish, saw_text_delta=saw_text)
        except Exception as exc:  # noqa: BLE001
            yield CanonicalEvent(
                kind="error", finish_reason=type(exc).__name__, raw={"error": str(exc)}
            )
            return
        yield CanonicalEvent(
            kind="finished", finish_reason=finish, usage=_canonical_usage(_last_usage)
        )
    except Exception as exc:  # noqa: BLE001
        _finalize_observation(
            _gen, _answer, _reasoning, _last_usage, metadata=(trace or {}).get("metadata")
        )
        err = normalize_exception(exc)
        if _gen is not None:
            from contextlib import suppress

            with suppress(Exception):  # noqa: S110 -- 观测失败不阻断
                _gen.update(metadata={"error_type": type(err).__name__}, level="ERROR")
        yield CanonicalEvent(
            kind="error", finish_reason=type(err).__name__, raw={"error": str(err)}
        )
    finally:
        if _gen_cm is not None and _gen is not None:
            import sys as _sys
            from contextlib import suppress

            # GeneratorExit：调用方提前终止流，OTel detach token 跨 Context 必抛
            # ValueError 并被 opentelemetry 内部刷 "Failed to detach context" 噪音。
            # 改 _gen.end() 优雅收口（同步生成器同 async 版）。
            if _sys.exc_info()[0] is GeneratorExit:
                with suppress(Exception):  # noqa: S110 -- 观测失败不阻断业务
                    _gen.end()
            else:
                with suppress(Exception):  # noqa: S110 -- 观测失败不阻断业务
                    _gen_cm.__exit__(None, None, None)


def _finalize_observation(
    gen,
    answer: str,
    reasoning: str,
    last_usage,
    tool_calls: list | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """写 Langfuse generation output/usage（自 legacy 移植；无观测则 no-op）。

    incident #67：root span output 必须在 span exit/flush 前写，否则被丢弃。
    ``tool_calls``（可选）：``[{name, arguments}]`` 结构（自 harness 移植）。
    ``metadata``（可选）：显式经 update 写入——langfuse 4.13 OTel 导出会把
    start_as_current_observation(metadata=) 挤掉（观测 metadata 被 resource/
    scope 属性覆盖），业务标记（如 judge environment）必须在 update 时落。
    """
    if gen is None:
        return
    try:
        from finance_agent.langfuse_tracing import truncate_for_trace

        _ud = {}
        if last_usage is not None:
            _ud = {
                "input": getattr(last_usage, "prompt_tokens", 0) or 0,
                "output": getattr(last_usage, "completion_tokens", 0) or 0,
            }
        output: dict[str, Any] = {
            "answer": truncate_for_trace(answer),
            "reasoning": truncate_for_trace(reasoning),
        }
        if tool_calls:
            output["tool_calls"] = [
                {"name": c["function"]["name"], "arguments": c["function"]["arguments"]}
                for c in tool_calls
            ]
        update_kwargs: dict[str, Any] = {"output": output, "usage_details": _ud}
        if metadata:
            update_kwargs["metadata"] = metadata
        gen.update(**update_kwargs)
    except Exception:  # noqa: S110 -- 观测失败不阻断业务
        pass


async def complete_stream_async(
    messages: list[dict[str, Any]],
    *,
    purpose: Purpose = "deep",
    max_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
    top_fields: list[str] | None = None,
    temperature: float | None = None,
    llm_config: dict[str, Any] | None = None,
    trace: dict[str, Any] | None = None,
    chunk_timeout: float = 60.0,
    max_retries: int = 3,
    retry_delay: float = 1.0,
):
    """异步流式 complete 入口（delta 5.1-C Task 2）：yield CanonicalEvent。

    自 harness/litellm_client.chat_stream 移植的行为合同：per-chunk 超时
    （``asyncio.wait_for``）、可重试错误指数退避重试、重试耗尽/不可重试
    直接 raise（保 Agent 主循环捕获合同）、tool_calls 增量按 index 聚合
    终态产出单条 tool_call 事件。空输出分类不在本入口（loop 自有重试）。

    断点续写（llm-output-resume Task 5）：finish=length 且正文非空 → 以
    「已生成正文尾部(+进度标注) + 剩余配额」二次异步流式续发，事件序
    text…text…finished（finished 携带续写段 reason）；续写仍 length →
    OutputTruncatedError。finish=length 且正文为空 → 直接抛
    OutputTruncatedError（修复「length 被静默当 finished(None)」缺陷）。
    ``top_fields``（可选）：调用方 schema 顶层字段清单，None 则不做进度
    标注（仅尾部续写）。续写段迭代同样受 ``chunk_timeout`` 逐 chunk 保护。
    """
    import asyncio

    from finance_agent.llm.types import CanonicalEvent

    profile = resolve_profile(purpose=purpose, llm_config=llm_config)
    ensure_litellm_runtime()
    guard_params_supported(profile.capability, tools=tools, tool_choice=tool_choice)
    from finance_agent.llm.adapters.litellm_adapter import (
        ToolCallAccumulator,
        apply_api_form_kwargs,
        apply_provider_options,
        derive_output_budget,
        finalize_tool_calls,
        normalize_exception,
        raw_acompletion,
        sanitize_request_messages,
    )

    messages = sanitize_request_messages(messages, profile.capability)

    # Langfuse observation（可选）：观测收口，结束前 update 根 span output
    _lf = None
    _gen_cm = None
    _gen = None
    if trace:
        from finance_agent.langfuse_tracing import get_langfuse

        _lf = get_langfuse()
        if _lf is not None:
            try:
                _gen_cm = _lf.start_as_current_observation(
                    as_type="generation",
                    name=trace.get("name") or f"litellm:{profile.model}",
                    model=profile.model,
                    input={"messages": messages},
                    metadata=trace.get("metadata") or {},
                )
                _gen = _gen_cm.__enter__()
            except Exception:  # noqa: S110 -- 观测失败不阻断业务
                _gen = None
                _gen_cm = None

    budget = derive_output_budget(profile.capability, requested=max_tokens)
    provider_kwargs = apply_provider_options(profile)
    suppress_temperature = bool(provider_kwargs.pop("suppress_temperature", False))
    request_kwargs: dict[str, Any] = {
        "model": profile.model,
        "messages": messages,
        "max_tokens": budget,
        "api_base": profile.base_url,
        "tool_choice": tool_choice,
        # 关键：必须流式（raw_acompletion 非流式返回 ModelResponse 而非 async 迭代器）
        "stream": True,
        **{k: v for k, v in (profile.default_params or {}).items() if k != "max_tokens"},
        **provider_kwargs,
        **apply_api_form_kwargs(profile),
    }
    if profile.api_key:
        # keyless 本地端点（如 Ollama）api_key 为 None：不发送空 key
        request_kwargs["api_key"] = profile.api_key
    if tools:
        request_kwargs["tools"] = tools
    if not suppress_temperature and temperature is not None:
        request_kwargs["temperature"] = temperature

    try:
        for attempt in range(max_retries):
            accumulator = ToolCallAccumulator()
            answer = ""
            reasoning_acc = ""
            last_usage = None
            try:
                resp = await raw_acompletion(**request_kwargs)
                _iter = resp.__aiter__()
                finish: str | None = None
                while True:
                    try:
                        chunk = await asyncio.wait_for(_iter.__anext__(), timeout=chunk_timeout)
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        raise _ChunkTimeoutError(
                            f"流式 chunk 超过 {chunk_timeout}s 未到达"
                        ) from None
                    if getattr(chunk, "usage", None):
                        last_usage = chunk.usage
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.delta
                    finish = getattr(choice, "finish_reason", None) or finish
                    if delta and getattr(delta, "reasoning_content", None):
                        rc = str(delta.reasoning_content)
                        reasoning_acc += rc
                        yield CanonicalEvent(kind="reasoning", reasoning=rc)
                    if delta and getattr(delta, "content", None):
                        ct = str(delta.content)
                        answer += ct
                        yield CanonicalEvent(kind="text", text=ct)
                    for t in getattr(delta, "tool_calls", None) or []:
                        accumulator.add(t)

                    if finish == "length":
                        # 断点续写（llm-output-resume Task 5）：finish_reason=length
                        # 且正文非空 → 构造续写请求二次异步流式续发（resume 上限 1），
                        # text/reasoning 事件直接续发，最终 finished 携带续写段 reason；
                        # 续写仍 length → OutputTruncatedError（观测 truncated=true）。
                        # 正文为空 → 直接抛（修复「length 被静默当 finished(None)」）。
                        if _maybe_resume_text(finish, answer):
                            from finance_agent.llm.adapters.litellm_adapter import (
                                build_resume_kwargs,
                            )
                            from finance_agent.llm.contracts import partial_json_progress

                            ann = None
                            if top_fields:
                                prog = partial_json_progress(answer, top_fields)
                                if prog is not None:
                                    ann = _build_progress_annotation(answer, top_fields)
                            resume_kwargs = build_resume_kwargs(
                                request_kwargs, prior_text=answer, progress_annotation=ann
                            )
                            resp2 = await raw_acompletion(**resume_kwargs)
                            _iter2 = resp2.__aiter__()
                            _finish2 = None
                            while True:
                                try:
                                    chunk2 = await asyncio.wait_for(
                                        _iter2.__anext__(), timeout=chunk_timeout
                                    )
                                except StopAsyncIteration:
                                    break
                                except TimeoutError:
                                    raise _ChunkTimeoutError("续写段 chunk 超时") from None
                                if getattr(chunk2, "usage", None):
                                    last_usage = chunk2.usage
                                choices2 = getattr(chunk2, "choices", None) or []
                                if not choices2:
                                    continue
                                c2 = choices2[0]
                                d2 = c2.delta
                                _finish2 = getattr(c2, "finish_reason", None) or _finish2
                                if d2 and getattr(d2, "reasoning_content", None):
                                    r2 = str(d2.reasoning_content)
                                    reasoning_acc += r2
                                    yield CanonicalEvent(kind="reasoning", reasoning=r2)
                                if d2 and getattr(d2, "content", None):
                                    ct2 = str(d2.content)
                                    answer += ct2
                                    yield CanonicalEvent(kind="text", text=ct2)
                            if _gen is not None:
                                from contextlib import suppress

                                with suppress(Exception):  # noqa: S110 -- 观测失败不阻断
                                    _gen.update(
                                        metadata={
                                            **((trace or {}).get("metadata") or {}),
                                            "resume_count": 1,
                                        }
                                    )
                            if _finish2 == "length":
                                _finalize_observation(
                                    _gen,
                                    answer,
                                    reasoning_acc,
                                    last_usage,
                                    metadata=(trace or {}).get("metadata"),
                                )
                                if _gen is not None:
                                    from contextlib import suppress

                                    with suppress(Exception):  # noqa: S110 -- 观测失败不阻断
                                        _gen.update(
                                            metadata={
                                                **((trace or {}).get("metadata") or {}),
                                                "resume_count": 1,
                                                "truncated": True,
                                            },
                                            level="ERROR",
                                        )
                                raise OutputTruncatedError(
                                    "续写仍 finish_reason=length：输出被截断（resume 上限 1）"
                                )
                            finish = _finish2
                            # 落入下方 stop/tool_calls 正常收口分支
                        else:
                            raise OutputTruncatedError(
                                "finish_reason=length 且正文为空：输出被截断（无内容可续写）"
                            )

                    if finish == "tool_calls" and accumulator.calls:
                        calls = finalize_tool_calls(accumulator)
                        _finalize_observation(
                            _gen,
                            answer,
                            reasoning_acc,
                            last_usage,
                            tool_calls=calls,
                            metadata=(trace or {}).get("metadata"),
                        )
                        yield CanonicalEvent(
                            kind="tool_call",
                            tool_call={"calls": calls},
                            usage=_canonical_usage(last_usage),
                        )
                        yield CanonicalEvent(
                            kind="finished",
                            finish_reason="tool_calls",
                            usage=_canonical_usage(last_usage),
                        )
                        return
                    if finish == "stop":
                        if accumulator.calls:
                            calls = finalize_tool_calls(accumulator)
                            _finalize_observation(
                                _gen,
                                answer,
                                reasoning_acc,
                                last_usage,
                                tool_calls=calls,
                                metadata=(trace or {}).get("metadata"),
                            )
                            yield CanonicalEvent(
                                kind="tool_call",
                                tool_call={"calls": calls},
                                usage=_canonical_usage(last_usage),
                            )
                            yield CanonicalEvent(
                                kind="finished",
                                finish_reason="tool_calls",
                                usage=_canonical_usage(last_usage),
                            )
                        else:
                            _finalize_observation(
                                _gen,
                                answer,
                                reasoning_acc,
                                last_usage,
                            )
                            yield CanonicalEvent(
                                kind="finished",
                                finish_reason="stop",
                                usage=_canonical_usage(last_usage),
                            )
                        return

                # 流结束但无明确 finish_reason
                if accumulator.calls:
                    calls = finalize_tool_calls(accumulator)
                    _finalize_observation(
                        _gen,
                        answer,
                        reasoning_acc,
                        last_usage,
                        tool_calls=calls,
                        metadata=(trace or {}).get("metadata"),
                    )
                    yield CanonicalEvent(
                        kind="tool_call",
                        tool_call={"calls": calls},
                        usage=_canonical_usage(last_usage),
                    )
                    yield CanonicalEvent(
                        kind="finished",
                        finish_reason="tool_calls",
                        usage=_canonical_usage(last_usage),
                    )
                else:
                    _finalize_observation(
                        _gen,
                        answer,
                        reasoning_acc,
                        last_usage,
                        metadata=(trace or {}).get("metadata"),
                    )
                    yield CanonicalEvent(
                        kind="finished",
                        finish_reason=None,
                        usage=_canonical_usage(last_usage),
                    )
                return
            except Exception as exc:  # noqa: BLE001
                err = normalize_exception(exc)
                if err.retryable and attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * 2**attempt)
                    continue
                # 观测落痕：重试耗尽/不可重试上抛前写 error output（对齐旧 harness
                # _finish_langfuse 失败路径，避免错误 trace 静默）
                if _gen is not None:
                    from contextlib import suppress

                    with suppress(Exception):  # noqa: S110 -- 观测失败不阻断
                        _gen.update(
                            output={"error": str(err)},
                            level="ERROR",
                            metadata={"error_type": type(err).__name__},
                        )
                raise err from exc
    finally:
        if _gen_cm is not None and _gen is not None:
            import sys as _sys
            from contextlib import suppress

            # GeneratorExit：调用方提前终止流（ReAct tool_call 后 return 等），
            # async generator 在 yield 处被 aclose/GC。此时 OTel detach 的 token
            # 创建于 enter 时的 Context，跨 context reset 必抛 ValueError 且被
            # opentelemetry 内部 logger 刷 "Failed to detach context" 告警（噪音）。
            # 改为 _gen.end() 优雅收口：span 数据照常落 langfuse，不触发 detach。
            if _sys.exc_info()[0] is GeneratorExit:
                with suppress(Exception):  # noqa: S110 -- 观测失败不阻断业务
                    _gen.end()
            else:
                with suppress(Exception):  # noqa: S110 -- 观测失败不阻断业务
                    _gen_cm.__exit__(None, None, None)


class _ChunkTimeoutError(LLMTimeoutError):
    """内部信号：per-chunk 超时（归一化后 retryable=True 参与重试）。"""
