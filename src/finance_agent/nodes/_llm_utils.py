"""Agent 节点共享的 LLM 工具函数。"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

# ── 管线确定性 stub（agent-turn-box-display delta task 5.5）──
#
# TESTING=1 时 call_llm_streaming 不连真实 LLM，按 node_name 产出：
#   1. 固定思考 token（经 stream writer 写入 custom 流，node 用图节点名），
#      使前端 PipelineCard 能按 agent 阶段分组渲染（E2E 断言对象）；
#   2. 该节点 schema 合法的最小 answer（AnalystReport/DebateMessage/TradeDecision 等），
#      保证 parse_json_response + Pydantic 校验通过、管线确定推进。
# 生产链路（无 TESTING）不受影响，仍走 call_llm_stream -> litellm。

# 函数级 node_name -> 图节点名（前端 NODE_DISPLAY_NAMES 的 key）。
# 节点 writer 用的是函数级名（如 bull_debater 被 bull_r1/bull_r2 两个图节点复用，
# 图上下文不可知），统一映射为首个图节点名，保证前端分组标题正确。
_NODE_NAME_MAP: dict[str, str] = {
    "technical_analyst": "technical_analyst",
    "macro_analyst": "technical_analyst",
    "fundamental_analyst": "technical_analyst",
    "sentiment_analyst": "technical_analyst",
    "bull_debater": "bull_r1",
    "bear_debater": "bear_r1",
    "research_manager": "research_manager",
    "trader": "trader",
    "aggressive_debater": "aggressive_r1",
    "conservative_debater": "conservative_r1",
    "neutral_debater": "neutral_r1",
    "risk_judge": "risk_judge",
    "fund_manager": "fund_manager",
}

# 各分析师 stub answer 的 agent_name（AnalystReport.agent_name 字段值）
_ANALYST_AGENT_NAMES: dict[str, str] = {
    "technical_analyst": "technical",
    "macro_analyst": "macro",
    "fundamental_analyst": "fundamental",
    "sentiment_analyst": "sentiment",
}

# 各辩论角色 stub answer 的 role（DebateMessage.role 字段值）
_DEBATER_ROLES: dict[str, str] = {
    "bull_debater": "bull",
    "bear_debater": "bear",
    "aggressive_debater": "aggressive",
    "conservative_debater": "conservative",
    "neutral_debater": "neutral",
}

# 交易决策 stub answer（Trader / Risk Judge 共用 TradeDecision schema）
_STUB_TRADE_DECISION: dict = {
    "action": "hold",
    "confidence": 0.6,
    "reasoning": "STUB 交易决策：多因素均衡，建议持有观察（测试数据）",
    "evidence_refs": [],
}


def _stub_pipeline_answer(node_name: str) -> str:
    """按 node_name 返回该节点可解析的最小合法 answer（JSON 字符串或纯文本）。"""
    if node_name in _ANALYST_AGENT_NAMES:
        agent_name = _ANALYST_AGENT_NAMES[node_name]
        # claims 为空：verify_citations 零 claim 时 all_passed=True，管线确定推进
        return json.dumps(
            {
                "agent_name": agent_name,
                "summary": f"STUB {agent_name} 分析摘要（测试数据）",
                "plain_conclusion": f"STUB {agent_name} 分析结论（测试数据）",
                "key_findings": [f"STUB 发现：{agent_name} 指标正常"],
                "claims": [],
                "markdown": f"## {agent_name} 分析\n\nSTUB 分析正文（测试数据）。",
            },
            ensure_ascii=False,
        )
    if node_name in _DEBATER_ROLES:
        role = _DEBATER_ROLES[node_name]
        return json.dumps(
            {
                "role": role,
                "round": 1,
                "content": f"STUB {role} 方论点（测试数据）",
                "key_arguments": [
                    {"text": f"STUB 论据：{role} 方观点成立", "kind": "inference", "anchors": []}
                ],
            },
            ensure_ascii=False,
        )
    if node_name in ("trader", "risk_judge"):
        return json.dumps(_STUB_TRADE_DECISION, ensure_ascii=False)
    if node_name == "fund_manager":
        # 固定 approve：保证管线确定走 generate_report（return 会回退 trader 引入不确定性）
        # action/confidence：harden-decision-report-semantics 起 approve 必填，
        # 缺失会被 FundManagerDecision 校验拒绝并中断管线（契约同步由
        # tests/nodes/test_stub_contract_sync.py 护栏）
        return json.dumps(
            {
                "decision": "approve",
                "reasoning": "STUB 审批通过（测试数据）",
                "action": "hold",
                "confidence": 0.6,
            },
            ensure_ascii=False,
        )
    # research_manager 输出纯文本结论；未知节点兜底纯文本
    return f"STUB {node_name or 'unknown'} 结论（测试数据）"


def _is_testing() -> bool:
    """运行时读取 TESTING 开关（与 finance_agent.api 同源，避免模块级缓存导致测试隔离失效）。"""
    import os

    return os.getenv("TESTING") == "1"


def parse_json_response(text: str) -> dict:
    """从 LLM 响应中提取 JSON（委托 llm.contracts 统一实现，delta Task 3.2）。

    容错（incident 016 遗留行失败）：方舟 GLM-5.2 概率性输出尾逗号
    （"Illegal trailing comma before end of array"），下游节点解析无
    try/except，此处必须自行消化常见格式瑕疵，避免单次坏输出炸整行。
    实现唯一化：解析逻辑只存在于 finance_agent.llm.contracts.extract_json，
    本函数保持签名/异常语义兼容（测试与调用点无需改动）。
    """
    from finance_agent.llm.contracts import extract_json

    return extract_json(text)


_JSON_RETRY_SUFFIX = (
    "\n\n[系统提示] 上一次响应为空或不是合法 JSON。请直接输出一个合法的 JSON 对象，"
    "不要输出 JSON 以外的任何文字。"
)


def _validation_retry_suffix(exc: ValidationError) -> str:
    """把 pydantic 校验错误压成一行摘要，作为重试时的修正指令。"""
    problems = "；".join(
        f"{'.'.join(str(p) for p in e.get('loc', ())) or '整体'}: {e.get('msg', '')}"
        for e in exc.errors()
    )
    return (
        f"\n\n[系统提示] 上一次输出的 JSON 未通过字段校验：{problems}。"
        "请按输出格式补齐所有必填字段后重新输出完整 JSON，不要输出 JSON 以外的任何文字。"
    )


def call_llm_for_json(
    prompt: str,
    system: str = "",
    api_key: str | None = None,
    node_name: str = "",
    llm_config=None,
    prompt_name: str | None = None,
    prompt_version: str | int | None = None,
    stock_code: str | None = None,
    validate: Callable[[dict], Any] | None = None,
) -> dict:
    """call_llm_streaming + parse_json_response 收口：坏输出带强化指令重试一次。

    方舟 GLM-5.2 在部分 prompt 下稳定触发「thinking 后即止」（incident 017
    实测：aggressive_debater 连续多次 reasoning 正常而 content 为空，
    与 max_tokens 配额无关）。下游节点（debate/risk/trader/fund_manager）
    解析无降级，单次空输出即炸整行 —— 统一经本函数调用并重试。

    重试一次后仍失败向上抛 JSONDecodeError：保留 fund_manager
    「非法输出中断管线、不静默降级」的既有设计（重试 ≠ 降级）。

    validate：可选字段校验钩子（典型为 pydantic Model.model_validate）。JSON 合法
    但校验抛 ValidationError 时，带错误摘要重试一次；仍不过向上抛 ValidationError。
    其余参数与 call_llm_streaming 一致，原样透传。
    """
    kwargs: dict = {
        "system": system,
        "api_key": api_key,
        "node_name": node_name,
        "llm_config": llm_config,
        "prompt_name": prompt_name,
        "prompt_version": prompt_version,
        "stock_code": stock_code,
    }
    try:
        response = call_llm_streaming(prompt, **kwargs)
    except Exception:
        # 服务瞬时故障（方舟偶发 500 / 流式中断，r4 实测）重试一次
        response = call_llm_streaming(prompt, **kwargs)
    try:
        data = parse_json_response(response)
    except json.JSONDecodeError:
        response = call_llm_streaming(prompt + _JSON_RETRY_SUFFIX, **kwargs)
        data = parse_json_response(response)
    if validate is None:
        return data
    try:
        validate(data)
    except ValidationError as exc:
        response = call_llm_streaming(prompt + _validation_retry_suffix(exc), **kwargs)
        data = parse_json_response(response)
        validate(data)
    return data


def focus_hint(state: dict) -> str:
    """从 state 提取用户关注点（深度研究意图澄清环节收集），返回 LLM context 注入行。

    focus 为空时返回空字符串，调用方据此决定是否 append。
    """
    focus = (state.get("focus") or "").strip()
    if not focus:
        return ""
    return f"用户关注点: {focus}"


# ── migrate-off-legacy-llm-shim Task 2：直连 gateway.complete_stream ──
#
# call_llm_streaming 不再经 legacy.call_llm_stream 薄壳，直接消费
# gateway.complete_stream 的 CanonicalEvent 流：
#   reasoning → ("thinking", ev.reasoning) → stream writer 实时转发
#   text      → ("answer", ev.text)        → answer 拼接返回
#   finished  → 忽略（不 yield）
#   error     → 按 ev.finish_reason（typed 类名字符串，errors 模块内类名即
#               finish_reason）经 getattr 查表还原为 typed error 后 raise
#               （查不到 → UnknownLLMError）——对齐 legacy._ERROR_CLASS_BY_NAME。
# 请求构造逐条复刻 gateway 合约（purpose=deep /
# temperature=0.3 / max_tokens=65536（GLM 官方默认）/ 请求级 llm_config dict /
# trace.name + metadata），截断翻倍至 131072（官方上限）重试 fallback 行为不变。

_DEFAULT_MODEL = "deepseek/deepseek-v4-pro"


def _llm_model_for_name(llm_config: Any, quick: bool = False) -> str:
    """trace 命名用 model 解析（镜像 legacy._llm_model_for_name）。"""
    cfg_model = None
    if isinstance(llm_config, dict):
        cfg_model = llm_config.get("model")
    elif llm_config is not None:
        cfg_model = getattr(llm_config, "model", None)
    return (
        cfg_model or os.environ.get("LLM_QUICK_MODEL" if quick else "LLM_MODEL") or _DEFAULT_MODEL
    )


def _generation_metadata(
    prompt_name: str | None,
    prompt_version: str | int | None,
    agent: str = "",
    session_id: str | None = None,
    stock_code: str | None = None,
) -> dict:
    """Langfuse generation metadata（复刻 legacy._generation_metadata）。

    prompt 元数据 + agent/session/stock 过滤字段仅在显式提供时写入
    （向后兼容约定，不污染 metadata 命名空间）。
    """
    md: dict = {}
    if prompt_name:
        md["prompt_name"] = prompt_name
    if prompt_version is not None:
        md["prompt_version"] = prompt_version
    if agent:
        md["agent"] = agent
    if session_id:
        md["session_id"] = session_id
    if stock_code:
        md["stock_code"] = stock_code
    return md


def _request_config_dict(llm_config: Any, api_key: str | None) -> dict | None:
    """LLMConfig / dict → gateway 请求级 llm_config dict（复刻 legacy._request_config_dict）。

    - 无 model → None（complete_stream 经 env/preset 解析）
    - baseUrl 缺 → env LLM_BASE_URL；apiKey 缺 → llm_config.apiKey → api_key
      参数 → LLM_API_KEY → DEEPSEEK_API_KEY（镜像 legacy 回退链）
    - thinking / apiForm 仅在显式设置时携带
    """
    if isinstance(llm_config, dict):
        model = llm_config.get("model")
        base_url = llm_config.get("baseUrl")
        key = llm_config.get("apiKey")
        thinking = llm_config.get("thinking")
        api_form = llm_config.get("apiForm")
        context_length = llm_config.get("contextLength")
    elif llm_config is not None:
        model = getattr(llm_config, "model", None)
        base_url = getattr(llm_config, "baseUrl", None)
        key = getattr(llm_config, "apiKey", None)
        thinking = getattr(llm_config, "thinking", None)
        api_form = getattr(llm_config, "apiForm", None)
        context_length = getattr(llm_config, "contextLength", None)
    else:
        return None
    if not model:
        return None
    cfg: dict = {"model": model}
    effective_base = base_url or os.environ.get("LLM_BASE_URL", "")
    if effective_base:
        cfg["baseUrl"] = effective_base
    effective_key = (
        key or api_key or os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    )
    if effective_key:
        cfg["apiKey"] = effective_key
    if thinking:
        cfg["thinking"] = thinking
    if api_form:
        cfg["apiForm"] = api_form
    if context_length is not None:
        cfg["contextLength"] = context_length
    return cfg


def call_llm_streaming(
    prompt: str,
    system: str = "",
    api_key: str | None = None,
    node_name: str = "",
    llm_config=None,
    prompt_name: str | None = None,
    prompt_version: str | int | None = None,
    stock_code: str | None = None,
) -> str:
    """Like call_llm but streams thinking tokens via LangGraph custom stream writer.

    直连 gateway.complete_stream 消费 CanonicalEvent（migrate-off-legacy-llm-shim
    Task 2）：reasoning 事件经 stream writer 转发实时展示，text 事件拼接为
    answer 返回（同一接口）。error 事件按 finish_reason（typed 类名字符串）
    还原为 typed error 并 raise；finished 事件忽略。

    llm_config（LLMConfig | dict | None）经请求级 dict 注入（复刻
    legacy._request_config_dict，实现请求级配置注入）。

    prompt_name / prompt_version（ADR-0015 Task 4）与 node_name / stock_code
    经 trace.metadata 挂到 Langfuse generation（Prompt 元数据可追溯 +
    Langfuse 过滤字段）。
    """
    from finance_agent.llm.gateway import complete_stream

    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:
        writer = None

    # TESTING=1：管线确定性 stub（不调真实 LLM）。
    # 思考 token 的 node 用图节点名（前端 NODE_DISPLAY_NAMES 的 key），
    # 使 PipelineCard 按 agent 阶段分组；answer 为该节点 schema 合法的最小 JSON。
    if _is_testing():
        if writer:
            writer(
                {
                    "type": "thinking",
                    "node": _NODE_NAME_MAP.get(node_name, node_name),
                    "token": f"[STUB] {node_name or 'unknown'} 正在分析（确定性测试数据）\n",
                }
            )
        # stub 管线默认瞬时推进，CI 等快环境下 node 分组标题（流式中间态）
        # 一闪即逝导致 E2E 抓不到。加可配置的节点延迟，让分组标题在屏幕上
        # 停留足够窗口（report_ready 后管线卡隐藏、分组消失）。
        # 默认 0.25s；可用 STUB_NODE_DELAY 覆盖（0 表示不延迟）。
        import os
        import time

        delay = float(os.getenv("STUB_NODE_DELAY", "0.25"))
        if delay > 0:
            time.sleep(delay)
        return _stub_pipeline_answer(node_name)

    # 请求构造（复刻 legacy.call_llm_stream 薄壳：system+prompt → messages，
    # llm_config/api_key → 请求级 dict，node_name → trace.name，metadata 过滤字段）。
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    cfg_dict = _request_config_dict(llm_config, api_key)
    trace = {
        "name": node_name or f"litellm:{_llm_model_for_name(llm_config)}",
        "metadata": _generation_metadata(prompt_name, prompt_version, node_name, None, stock_code),
    }

    # retryable LLMError（OutputTruncated/EmptyLLMOutput/超时/限流）重试一次：
    # gateway 按 Task 2.2 合同把截断/空输出归一为 typed error，但管线节点直调
    # 本函数无兜底（evals 跑批暴露：一次 finish_reason=length 炸整跑批）。
    # 与 call_llm_for_json 的「服务瞬时故障重试一次」同语义；非 retryable 直接上抛。
    # 截断特例（delta truncation-escalate-resume）：升级重试以续写进行——
    # 复用 gateway 续写机制（已生成正文尾部注入 + 续写指令），配额取翻倍
    # 预算（131072）扣除已生成部分。汉森制药复盘：从头重跑 34 分钟再失败，
    # 首轮 17 分钟的部分正文被整段丢弃。
    from finance_agent.llm import errors as _llm_errors
    from finance_agent.llm.adapters.litellm_adapter import build_resume_kwargs
    from finance_agent.llm.errors import LLMError, OutputTruncatedError

    # 截断升级预算（官方上限）
    _ESCALATE_BUDGET = 131072

    # complete_stream 基线参数：对齐 gateway 合约（purpose=deep / temperature=0.3 /
    # max_tokens=65536——GLM 官方默认，deep 长 JSON 节点 reasoning 与正文共享配额，
    # 16k 在长节点会 reasoning 吃空触发 length 截断；65536 给足余量）。
    _call_base: dict = {
        "purpose": "deep",
        "temperature": 0.3,
        "max_tokens": 65536,
        "llm_config": cfg_dict,
        "trace": trace,
    }
    escalate: dict = {}
    # 续写场景下的已完成正文累积（升级重试不从头重跑，返回值须拼接两轮）
    carried_answer = ""
    attempt_messages: list[dict] = messages
    for attempt in range(2):
        answer_parts: list[str] = []
        try:
            # _call_base + escalate 合并下发（escalate 覆盖 max_tokens：
            # 截断续写的翻倍剩余配额）。不能在调用处裸拼 **_call_base, **escalate——
            # 两处同键（max_tokens）会抛 TypeError，须先经 dict display 合并。
            for ev in complete_stream(attempt_messages, **{**_call_base, **escalate}):
                if ev.kind == "reasoning" and writer:
                    writer({"type": "thinking", "node": node_name, "token": ev.reasoning})
                elif ev.kind == "text":
                    answer_parts.append(ev.text)
                elif ev.kind == "error":
                    # error 事件 finish_reason 是 typed 错误类名字符串（errors
                    # 模块内类名即 finish_reason），getattr 查表还原；查不到 →
                    # UnknownLLMError（对齐 legacy._ERROR_CLASS_BY_NAME 缺省）。
                    err_cls = getattr(
                        _llm_errors, ev.finish_reason or "", _llm_errors.UnknownLLMError
                    )
                    raise err_cls(ev.raw.get("error") or ev.finish_reason or "LLM error")
            return carried_answer + "".join(answer_parts)
        except OutputTruncatedError:
            if attempt == 1:
                raise
            # 升级重试改续写（delta truncation-escalate-resume）：携带首轮
            # 正文尾部 + 翻倍预算扣除已生成的剩余配额，不重发原始问题
            prior_text = carried_answer + "".join(answer_parts)
            resumed = build_resume_kwargs(
                {"messages": list(messages), "max_tokens": _ESCALATE_BUDGET},
                prior_text=prior_text,
            )
            attempt_messages = resumed["messages"]
            escalate = {"max_tokens": resumed["max_tokens"]}
            carried_answer = prior_text
        except LLMError as exc:
            if not exc.retryable or attempt == 1:
                raise
    return ""  # 不可达（循环内 return/raise）
