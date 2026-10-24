"""
Mini Harness - ReAct Loop
ReAct 主循环：Agent 的心脏

设计来源：Claude Code 的 query.ts -- "5 行骨架 + 1695 行基础设施"

骨架代码（Claude Code 原始伪代码）：
    while (true) {
      // 1. Reason + Act: 调用模型
      for await (const msg of callModel({...})) {
        if (msg has tool_use blocks) needsFollowUp = true
      }
      // 2. Observe: 无工具调用 -> 完成
      if (!needsFollowUp) return { reason: 'completed' }
      // 3. 执行工具，追加结果，继续循环
      for await (const update of runTools(...)) { ... }
      state = { messages: [...old, ...assistant, ...toolResults] }
    }

我们的实现保留了相同的骨架结构，但增加了：
- 流式事件输出（async generator）
- Token 预算管理
- 权限检查
- 生命周期钩子
- 最大迭代限制（防止无限循环）
- 错误恢复
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from finance_agent.harness.context import ContextBudget, ContextManager
from finance_agent.harness.hooks import HookManager
from finance_agent.harness.llm_client import LLMClient
from finance_agent.harness.permissions import PermissionChecker
from finance_agent.harness.tool_manager import ToolManager
from finance_agent.harness.types import (
    ActionType,
    HookPoint,
    PermissionMode,
    PermissionRequest,
    Role,
    StreamEvent,
    ToolCallRequest,
    ToolResult,
)
from finance_agent.langfuse_tracing import open_span, update_current_span

logger = logging.getLogger("finance_agent.harness.loop")


# ───────────────────────────────────────────────
# DSML 防御性解析
# ───────────────────────────────────────────────
# DeepSeek（V3.2+/V4）偶发以 DSML 文本标记输出工具调用，而非结构化 tool_calls
# 字段。标记形如：<｜｜DSML｜｜tool_calls> <｜｜DSML｜｜invoke name="search_stock"> ...
# litellm 不解析该文本格式，会作为 content 泄漏给用户（深度模式出现乱码）。
# 此处防御性解析：将 DSML 文本还原为 ToolCallRequest，并清除标记文本。
#
# 竖线字符变体：｜(U+FF5C 全角)、‖(U+2016 数学双竖线)、|(U+007C 普通)、│(U+2502)
_DSML_BAR = r"[｜‖|│]"
# DSML 标签起始：< 后跟任意竖线组合 + DSML（大小写不敏感）
_DSML_TAG = r"<\s*" + _DSML_BAR + r"*\s*[Dd][Ss][Mm][Ll]" + _DSML_BAR + r"*"
_DSML_DETECT = re.compile(_DSML_TAG)
# invoke name="...">body</...invoke>
_DSML_INVOKE = re.compile(
    _DSML_TAG + r'\s*invoke\s+name="([^"]+)"[^>]*>(.*?)'
    r"<\s*/\s*" + _DSML_BAR + r"*\s*[Dd][Ss][Mm][Ll]" + _DSML_BAR + r"*\s*invoke\s*>",
    re.DOTALL,
)
# parameter name="...">value</...parameter>
_DSML_PARAM = re.compile(
    _DSML_TAG + r'\s*parameter\s+name="([^"]+)"[^>]*>(.*?)'
    r"<\s*/\s*" + _DSML_BAR + r"*\s*[Dd][Ss][Mm][Ll]" + _DSML_BAR + r"*\s*parameter\s*>",
    re.DOTALL,
)
# 任意 DSML 标签（用于清理残留，如 <｜｜DSML｜｜tool_calls>）
_DSML_ANY_TAG = re.compile(r"<\s*/?\s*" + _DSML_BAR + r"*\s*[Dd][Ss][Mm][Ll][^>]*>")

# DSML 标记中出现的竖线字符集合（全角 ｜ / 数学双竖线 ‖ / 普通 | / 制表 │）
_DSML_BARS_SET = frozenset("｜‖|│")


def _safe_think_len(text: str, start: int) -> tuple[int, bool]:
    """计算 ``text[start:]`` 中可安全作为 THINK 逐 token 流式输出的结束位置。

    防止 DSML 标记在流末清理前泄漏给用户：逐 token 流式时，若累积文本中出现
    DSML 标记起始（``<`` + 空白/竖线 + ``DSML``），则标记部分不流式，留待流末
    :func:`_parse_dsml_from_text` 清理后用 THINK_REPLACE 覆盖。

    Args:
        text: 累积的完整文本（含已流式与未流式部分）。
        start: 已流式到的偏移量，从此处开始扫描。

    Returns:
        ``(safe_end, dsml_found)``：
        - ``safe_end``：可安全流式到 ``text[start:safe_end]``。
        - ``dsml_found``：True 表示 ``text[safe_end:]`` 起始处检测到完整 DSML 标记，
          调用方应停止后续流式（标记留待流末清理）。
    """
    i = start
    while i < len(text):
        lt = text.find("<", i)
        if lt == -1:
            return len(text), False
        # lt 处是否已是完整 DSML 标记起始
        if _DSML_DETECT.match(text, lt):
            return lt, True
        # lt 处是否可能是「尚未补全的 DSML 标记前缀」：
        # 标记形如 < [空白] [竖线] [空白] DSML...；若 '<' 后仅有空白/竖线，
        # 或紧接 'DSML' 的前缀（尚未凑齐 4 字母），则按可能为标记处理，回退到 lt 之前等待更多 token。
        j = lt + 1
        while j < len(text) and (text[j].isspace() or text[j] in _DSML_BARS_SET):
            j += 1
        if "dsml".startswith(text[j:].lower()):
            return lt, False
        # lt 处确定不是 DSML 标记 -> 越过继续扫描下一个 '<'
        i = lt + 1
    return len(text), False


def _parse_dsml_params(body: str) -> dict:
    """从 invoke body 中解析参数（parameter 标签优先，回退 JSON）。"""
    args: dict = {}
    for m in _DSML_PARAM.finditer(body):
        args[m.group(1).strip()] = m.group(2).strip()

    if not args:
        body_stripped = body.strip()
        if body_stripped.startswith("{"):
            try:
                parsed = json.loads(body_stripped)
                if isinstance(parsed, dict):
                    args = parsed
            except json.JSONDecodeError:
                pass
    return args


def _parse_dsml_from_text(text: str) -> tuple[list[ToolCallRequest], str]:
    """从文本中防御性解析 DSML 工具调用标记。

    Returns:
        (tool_calls, cleaned_text)。若无 DSML 标记，返回 ([], 原文本)。
    """
    if not _DSML_DETECT.search(text):
        return [], text

    tool_calls: list[ToolCallRequest] = []
    for m in _DSML_INVOKE.finditer(text):
        name = m.group(1).strip()
        args = _parse_dsml_params(m.group(2))
        tool_calls.append(
            ToolCallRequest(
                id=f"dsml_{len(tool_calls)}",
                name=name,
                arguments=args,
            )
        )

    # 清理：移除 invoke 块（含 body），再移除残留标签（tool_calls 等）
    cleaned = _DSML_INVOKE.sub("", text)
    cleaned = _DSML_ANY_TAG.sub("", cleaned)
    return tool_calls, cleaned.strip()


# ───────────────────────────────────────────────
# 默认系统提示
# ───────────────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = """你是一个智能助手，可以通过工具解决复杂问题。

工作方式：
1. 分析用户需求，决定是否需要使用工具
2. 如需工具，调用合适的工具获取信息
3. 根据工具结果继续推理或给出最终回答
4. 你可以多次调用工具，直到获得足够信息

规则：
- 优先使用工具获取实时信息，不要依赖训练数据的过时知识
- 每次只调用一个工具，等待结果后再决定下一步
- 如果任务复杂，先制定计划再执行
- 最终回答使用用户使用的语言
- 工具调用之间用 <thinking> 标签展示推理过程
"""


# ───────────────────────────────────────────────
# Agent 主类
# ───────────────────────────────────────────────


class Agent:
    """
    Mini Harness Agent -- 简化版 ReAct 循环实现

    架构组成：
    - ContextManager: 对话历史 + Token 预算
    - ToolManager: 工具注册 + 执行
    - PermissionChecker: 权限决策
    - LLMClient: 模型调用
    - HookManager: 生命周期事件

    使用方法：
        agent = Agent(model="gpt-4o-mini")
        agent.tools.register_all_builtins()

        async for event in agent.run("帮我读取 main.py 的内容"):
            print(event.content)
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        system_prompt: str | None = None,
        permission_mode: PermissionMode = PermissionMode.YOLO,
        max_iterations: int = 15,
        context_budget: ContextBudget | None = None,
        interactive_permission: Callable[[PermissionRequest], Any] | None = None,
        llm: LLMClient | None = None,
    ):
        # 初始化各子系统
        self.context = ContextManager(budget=context_budget or ContextBudget())
        self.llm = llm or LLMClient(model=model, api_key=api_key, base_url=base_url)
        self.tools = ToolManager()
        self.hooks = HookManager()

        # 权限系统
        self.permission_callback = interactive_permission
        self.permissions = PermissionChecker(
            mode=permission_mode,
            interactive_callback=self._default_permission_handler,
        )
        self.tools.checker = self.permissions

        # 配置
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.context.set_system(self.system_prompt)

        # 运行时状态
        self.session_id: str = str(uuid.uuid4())[:8]
        self._running = False
        # provider usage 累计（add-composer-stat-pills）：quick/chat 通道不经过管线
        # 节点，用量在 Agent 层累计，由调用方在收尾时落库（session_store）。
        self.usage_calls: int = 0
        self.usage_prompt_tokens: int = 0
        self.usage_completion_tokens: int = 0
        self.usage_total_tokens: int = 0
        self.usage_max_prompt_tokens: int = 0

    # ── 权限处理 ──

    async def _default_permission_handler(self, request: PermissionRequest) -> bool:
        """
        默认权限处理器。

        优先级：
        1. 外部传入的交互式回调
        2. 自动拒绝（安全默认）

        建议：在生产环境中传入交互式回调（如 CLI 确认、WebSocket 请求等）
        """
        if self.permission_callback:
            return await self.permission_callback(request)

        # 无交互能力时：读取操作自动允许，写入操作拒绝
        if request.risk_level in (
            PermissionChecker.RiskLevel.READ if hasattr(PermissionChecker, "RiskLevel") else []
        ):
            return True

        logger.warning(f"权限请求（无交互回调，默认拒绝）: {request.describe()}")
        return False

    # ── 核心运行入口 ──

    def _record_usage(self, usage: dict[str, int] | None) -> None:
        """累计一次 LLM 调用的 provider usage（缺失/畸形按 0，不估算填充）。

        quick 模式的用量落库真值来源：调用方（AG-UI / SSE 收尾）读取
        ``usage_*`` 属性后写入 session_store。
        """
        if not isinstance(usage, dict) or not usage:
            return

        def _to_int(key: str) -> int:
            try:
                return int(usage.get(key) or 0)
            except (TypeError, ValueError, OverflowError):
                return 0

        prompt = _to_int("prompt_tokens")
        completion = _to_int("completion_tokens")
        total = _to_int("total_tokens") or (prompt + completion)
        self.usage_calls += 1
        self.usage_prompt_tokens += prompt
        self.usage_completion_tokens += completion
        self.usage_total_tokens += total
        self.usage_max_prompt_tokens = max(self.usage_max_prompt_tokens, prompt)

    async def run(self, user_input: str, force_tool: bool = False) -> AsyncIterator[StreamEvent]:
        """
        运行 Agent -- 核心入口

        参数：
            user_input: 用户输入文本

        返回：
            异步生成器，yield StreamEvent

        使用示例：
            async for event in agent.run("帮我分析一下这个文件"):
                if event.event_type == ActionType.THINK:
                    print(f"思考: {event.content}")
                elif event.event_type == ActionType.TOOL_CALL:
                    print(f"调用工具: {event.tool_call.name}")
                elif event.event_type == ActionType.ANSWER:
                    print(f"回答: {event.content}")
        """
        if self._running:
            yield StreamEvent.error("Agent 正在运行中，请等待当前任务完成")
            return

        self._running = True
        start_time = time.time()
        iterations = 0
        analysis_completed = False  # run_deep_analysis 完成后标记
        empty_retries = 0  # LLM 空输出重试计数
        max_empty_retries = 3  # 最大空输出重试次数
        text_only_retries = 0  # LLM 纯文本无工具调用重试计数
        max_text_only_retries = 2  # 最大纯文本重试次数
        dsml_count = 0  # DSML 防御性解析命中计数（trace 上报用）

        # 1. 触发会话开始钩子
        await self.hooks.emit(
            HookPoint.ON_SESSION_START,
            {
                "session_id": self.session_id,
                "user_input": user_input,
            },
        )

        # 2. 追加用户消息到上下文
        self.context.append_user(user_input)

        try:
            # ═══════════════════════════════════
            # ReAct 主循环 -- Claude Code 的核心
            # ═══════════════════════════════════
            while iterations < self.max_iterations:
                iterations += 1
                logger.debug(f"=== ReAct 循环第 {iterations} 轮 ===")

                # ── 检查 token 预算，如需则压缩 ──
                compacted = await self.context.maybe_compact()
                if compacted:
                    await self.hooks.emit(
                        HookPoint.PRE_COMPACT,
                        {
                            "session_id": self.session_id,
                            "context": self.context,
                        },
                    )

                # ── 获取工具 schemas ──
                tool_schemas = self.tools.get_schemas_for_llm()

                # 分析完成后，不给 LLM 工具，强制生成文本摘要
                if analysis_completed:
                    tool_schemas = []

                # ── 构建 API 消息 ──
                api_messages = self.context.build_messages_for_api()

                # ── 调用 LLM（流式）──
                # DeepSeek 思考模式：reasoning_delta（思维链）与 text_delta（content）
                # 天然分离，无需 thinking_to_answer 剥离。
                assistant_text = ""
                assistant_reasoning = ""  # 累积 reasoning_content 用于上下文回传
                pending_tool_calls: list[ToolCallRequest] = []
                _streamed_answer_len = 0  # assistant_text 中已作为 ANSWER 流式输出的偏移量
                _dsml_active = False  # 检测到 DSML 标记后停止流式，留待流末清理

                # 第一次迭代且 force_tool=True 时，强制调用工具
                _tool_choice = "required" if (force_tool and iterations == 1) else "auto"

                # 本次调用的 usage 真值（provider 返回时非 None）→ 预算校准用
                _call_usage: dict[str, int] | None = None
                async for chunk in self.llm.chat_stream(
                    messages=api_messages,
                    tools=tool_schemas if tool_schemas else None,
                    tool_choice=_tool_choice,
                ):
                    # 原生思考增量 -> THINK 事件（思考横幅消费）
                    if chunk.reasoning_delta:
                        assistant_reasoning += chunk.reasoning_delta
                        yield StreamEvent.think(chunk.reasoning_delta)

                    # 回答增量 -> ANSWER 事件（回答区消费，与思考分离）
                    # 安全流式：DSML 标记部分不下发，避免在流末清理前泄漏给用户
                    if chunk.text_delta:
                        assistant_text += chunk.text_delta
                        if not _dsml_active:
                            _safe_end, _dsml_found = _safe_think_len(
                                assistant_text, _streamed_answer_len
                            )
                            # action 文本协议块（delta 3.5）同样不流式：
                            # 留待 parsing 阶段经 _parse_action_from_text 还原后清理下发
                            _act_start = assistant_text.find("<action", _streamed_answer_len)
                            if _act_start != -1:
                                _safe_end = min(_safe_end, _act_start)
                                _dsml_found = _dsml_found or True
                            if _safe_end > _streamed_answer_len:
                                yield StreamEvent.answer(
                                    assistant_text[_streamed_answer_len:_safe_end]
                                )
                            _streamed_answer_len = _safe_end
                            if _dsml_found:
                                _dsml_active = True

                    if chunk.tool_calls:
                        pending_tool_calls = chunk.tool_calls

                    if chunk.usage:
                        _call_usage = chunk.usage

                    if chunk.is_finished:
                        break

                # usage 真值用于校准上下文预算（spec llm-budget-governance：usage 真实值
                # 存在时用于校准 token 计数；不存在时保持估算态）。入参取 prompt_tokens：
                # 预算约束的是请求上下文，不含输出。此前 calibrate 零生产调用方——预算永远
                # 停在 capability 派生值、usage_estimated 恒为 True，估算偏差无从纠正。
                self.context.budget.calibrate((_call_usage or {}).get("prompt_tokens"))
                self._record_usage(_call_usage)

                # ── DSML 防御性解析 ──
                # DeepSeek 偶发以 DSML 文本标记输出工具调用，litellm 未解析为
                # 结构化 tool_calls。此处从文本中还原工具调用，避免标记泄漏给用户。
                # 注意：DSML 标记在 content 中，不影响 reasoning_content。
                if not pending_tool_calls and assistant_text:
                    dsml_calls, dsml_cleaned = _parse_dsml_from_text(assistant_text)
                    if dsml_calls:
                        dsml_count += 1  # 命中 DSML 防御性解析，循环结束上报 span metadata
                        pending_tool_calls = dsml_calls
                        assistant_text = dsml_cleaned
                        # DSML 清理后，把未流式的安全部分下发给消费者
                        if _streamed_answer_len < len(assistant_text):
                            yield StreamEvent.answer(assistant_text[_streamed_answer_len:])
                        _streamed_answer_len = len(assistant_text)
                    else:
                        # action 文本协议兜底（delta 3.5）：弱工具 provider 用
                        # <action name=...> 输出工具调用，native/DSML 均未命中时解析
                        action_calls, action_cleaned = _parse_action_from_text(assistant_text)
                        if action_calls:
                            dsml_count += 1  # 复用「文本还原工具调用」计数/上报
                            pending_tool_calls = action_calls
                            assistant_text = action_cleaned
                            if _streamed_answer_len < len(assistant_text):
                                yield StreamEvent.answer(assistant_text[_streamed_answer_len:])
                            _streamed_answer_len = len(assistant_text)

                # ── 无工具调用但有文本 -> 检查是否应该继续调用工具 ──
                if assistant_text and not pending_tool_calls:
                    has_prior_tools = any(m.role == Role.TOOL for m in self.context.messages)
                    # 仅在强制工具模式（force_tool）下重试；深度模式允许 Agent 自主
                    # 返回纯文本澄清反问（ADR-0017 D2：反问触发条件由 Agent 自主判断），
                    # 避免强制要求调用 run_deep_analysis 而绕过澄清流程。
                    if (
                        force_tool
                        and has_prior_tools
                        and not analysis_completed
                        and text_only_retries < max_text_only_retries
                    ):
                        text_only_retries += 1
                        logger.warning(
                            f"LLM 返回纯文本但未调用工具（第 {text_only_retries}/{max_text_only_retries} 次重试）"
                        )
                        # 先把 LLM 的文本回复追加到上下文，让 LLM 知道自己说了什么
                        self.context.append_assistant(
                            assistant_text,
                            None,
                            reasoning_content=assistant_reasoning or None,
                        )
                        # 追加 system 提示，引导 LLM 继续调用工具
                        self.context.append_system(
                            "你刚才返回了文字但没有调用工具。请根据对话上下文，"
                            "若用户意图已明确则调用下一步工具（如 run_deep_analysis），"
                            "若信息不足可以文字反问澄清。不要重复已说过的话。"
                        )
                        iterations -= 1
                        continue
                    # 确实是最终回复（澄清反问或分析摘要）
                    # reasoning 与 content 已分离下发，无需 thinking_to_answer 转换

                # ── 发送工具调用事件 ──
                if pending_tool_calls:
                    for tc in pending_tool_calls:
                        yield StreamEvent.for_tool_call(tc)

                # ── 追加助手消息到上下文 ──
                # 工具调用轮次必须回传 reasoning_content（DeepSeek 思考模式要求）
                tool_calls_raw = []
                for tc in pending_tool_calls:
                    tool_calls_raw.append(
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": str(tc.arguments)},
                        }
                    )
                self.context.append_assistant(
                    assistant_text,
                    tool_calls_raw if tool_calls_raw else None,
                    reasoning_content=assistant_reasoning or None,
                )

                # ── 检查是否有工具调用 ──
                if not pending_tool_calls:
                    if not assistant_text:
                        # LLM 返回空输出（API 异常/限流），重试而非结束
                        empty_retries = empty_retries + 1
                        if empty_retries <= max_empty_retries:
                            logger.warning(
                                f"LLM 返回空输出（第 {empty_retries}/{max_empty_retries} 次重试）"
                            )
                            await asyncio.sleep(1 * empty_retries)
                            # 移除刚才追加的空 assistant 消息，避免上下文污染
                            if (
                                self.context.messages
                                and self.context.messages[-1].role == Role.ASSISTANT
                            ):
                                self.context.messages.pop()
                            # 空输出重试不消耗迭代配额
                            iterations -= 1
                            continue
                        else:
                            logger.error("LLM 连续返回空输出，已达最大重试次数")
                            yield StreamEvent(
                                event_type=ActionType.ERROR,
                                content="AI 服务暂时不可用，请稍后重试。",
                            )
                            break
                    # 确实是最终回复 -> 任务完成
                    logger.debug("无工具调用，循环结束")
                    break

                # ── 执行工具调用 ──
                for tc in pending_tool_calls:
                    # 发送权限请求事件（消费者可展示确认 UI）
                    risk = self.permissions._assess_risk(tc.name, tc.arguments)
                    perm_req = PermissionRequest(
                        tool_name=tc.name,
                        arguments=tc.arguments,
                        risk_level=risk,
                        reason=f"工具 '{tc.name}' 需要权限",
                    )
                    yield StreamEvent.permission_required(perm_req)

                    # 权限检查
                    permitted = await self.permissions.check(tc.name, tc.arguments)
                    if not permitted:
                        result = ToolResult(
                            tool_call_id=tc.id,
                            name=tc.name,
                            output=f"权限被拒绝：工具 '{tc.name}' 的执行未获授权。",
                            is_error=True,
                            permission_granted=False,
                        )
                        self.context.append_tool_result(tc.id, result.output, is_error=True)
                        yield StreamEvent.for_tool_result(result)
                        continue

                    # 触发 pre_tool_use 钩子
                    await self.hooks.emit(
                        HookPoint.PRE_TOOL_USE,
                        {
                            "session_id": self.session_id,
                            "tool_name": tc.name,
                            "arguments": tc.arguments,
                            "tool_call_id": tc.id,
                        },
                    )

                    # 执行工具：流式工具走 execute_stream，普通工具走 execute
                    # 用 open_span 包裹工具执行，创建 tool:{name} span（挂到 react_loop 下）
                    with open_span(
                        name=f"tool:{tc.name}", input={"args": tc.arguments}
                    ) as _toolObs:
                        if self.tools.is_streaming(tc.name):
                            # 流式工具：透传 PROGRESS / THINK 事件，提取最终 ToolResult
                            result = None
                            async for event in self.tools.execute_stream(
                                tc.id, tc.name, tc.arguments
                            ):
                                if isinstance(event, StreamEvent):
                                    if event.event_type == ActionType.PROGRESS:
                                        yield event
                                    elif event.event_type == ActionType.THINK:
                                        # 透传管线节点思考（含 node metadata），供 SSE 按 agent 分组
                                        # （此前只透传 PROGRESS/TOOL_RESULT，丢 THINK——真实 bug 修复）
                                        yield event
                                    elif (
                                        event.event_type == ActionType.TOOL_RESULT
                                        and event.tool_result
                                    ):
                                        result = event.tool_result
                                        # 同时 yield 这个事件（含 metadata）
                                        yield event
                                elif isinstance(event, ToolResult):
                                    result = event

                            if result is None:
                                result = ToolResult(
                                    tool_call_id=tc.id,
                                    name=tc.name,
                                    output="[错误] 流式工具未返回结果",
                                    is_error=True,
                                )
                            elif not (result.output or "").strip():
                                # delta add-event-delivery-resilience spec「管线异常终止的工具结果显式错误语义」：
                                # 空串结果与缺失等价——MUST NOT 让摘要 LLM 拿到空输入自由发挥
                                # （incident 037：「本次未返回有效报告内容」的空结果歧义）
                                result = ToolResult(
                                    tool_call_id=tc.id,
                                    name=tc.name,
                                    output="[错误] 流式工具返回空结果",
                                    is_error=True,
                                )
                        else:
                            # 普通工具
                            result = await self.tools.execute(tc.id, tc.name, tc.arguments)
                        # 记录工具执行结果到 span output
                        if _toolObs:
                            _toolObs.update(output={"result": result.output})

                    # 追加工具结果到上下文
                    self.context.append_tool_result(tc.id, result.output, result.is_error)

                    # run_deep_analysis 完成后标记，允许 LLM 再生成一次摘要。
                    # spec「管线异常终止的工具结果显式错误语义」成对语义：终态
                    # 失败 TOOL_RESULT（is_error=True + pipeline_* 机器标志）表示
                    # 管线已收口（超时/异常/阻断），同样置位 analysis_completed——
                    # 否则 LLM 保有工具 schemas 可能对已终结的管线自动重跑（40
                    # 分钟级）；而缺失/空结果等非终态失败仍不置位（保留重试引导）。
                    _terminal_meta = (
                        "pipeline_timeout",
                        "pipeline_error",
                        "pipeline_blocked",
                    )
                    if tc.name == "run_deep_analysis" and (
                        not result.is_error
                        or (
                            result.metadata is not None
                            and any(result.metadata.get(k) for k in _terminal_meta)
                        )
                    ):
                        analysis_completed = True

                    # 触发 post_tool_use 钩子
                    await self.hooks.emit(
                        HookPoint.POST_TOOL_USE,
                        {
                            "session_id": self.session_id,
                            "tool_name": tc.name,
                            "arguments": tc.arguments,
                            "result": result,
                        },
                    )

                    yield StreamEvent.for_tool_result(result)

            # 循环结束
            # 重试与降级路径上 trace（react_loop span 经 OTel contextvar 自动定位，
            # 无需显式传 span 引用；计数全为 0 时不打扰 trace）
            _meta: dict[str, Any] = {}
            if empty_retries > 0 or text_only_retries > 0:
                _meta["retries"] = {"empty": empty_retries, "text_only": text_only_retries}
            if dsml_count > 0:
                _meta["degradation"] = "dsml_fallback"
                _meta["count"] = dsml_count
            if _meta:
                update_current_span(metadata=_meta, level="WARNING")

            if iterations >= self.max_iterations:
                # 不直接报错，让 LLM 基于已有上下文生成回复
                answered = False
                try:
                    api_messages = self.context.build_messages_for_api()
                    async for chunk in self.llm.chat_stream(
                        messages=api_messages,
                        tools=None,  # 不提供工具，强制生成文本
                    ):
                        if chunk.text_delta:
                            answered = True
                            yield StreamEvent(
                                event_type=ActionType.ANSWER,
                                content=chunk.text_delta,
                            )
                        if chunk.is_finished:
                            break
                except Exception as e:
                    logger.warning("max_iterations 后生成回复失败: %s", e)
                    yield StreamEvent.error(
                        f"达到最大迭代次数 ({self.max_iterations})，请提供更明确的信息"
                    )
                if not answered:
                    yield StreamEvent.error(
                        f"达到最大迭代次数 ({self.max_iterations})，请提供更明确的信息"
                    )

        except Exception as e:
            logger.exception("Agent 运行时错误")
            await self.hooks.emit(
                HookPoint.ON_ERROR,
                {
                    "session_id": self.session_id,
                    "error": str(e),
                },
            )
            yield StreamEvent.error(f"运行时错误: {e}")

        finally:
            # 会话结束钩子
            duration_ms = int((time.time() - start_time) * 1000)
            await self.hooks.emit(
                HookPoint.ON_SESSION_END,
                {
                    "session_id": self.session_id,
                    "iterations": iterations,
                    "duration_ms": duration_ms,
                },
            )
            self._running = False

    # ── 便捷方法 ──

    async def run_sync(self, user_input: str) -> str:
        """
        同步风格运行 -- 收集所有事件，返回最终回答

        用于不需要流式处理的场景。
        """
        final_answer_parts = []
        async for event in self.run(user_input):
            if event.event_type == ActionType.ANSWER:
                final_answer_parts.append(event.content)
        return "".join(final_answer_parts)

    def add_tool(self, func: Callable, name: str | None = None) -> None:
        """注册自定义工具"""
        self.tools.register(func, name=name)

    def on(self, hook_point: HookPoint, callback: Callable) -> None:
        """订阅生命周期事件"""
        self.hooks.on(hook_point, callback)

    # ── 内部工具 ──

    @staticmethod
    def _extract_thinking(text: str) -> str | None:
        """从助手输出中提取 <thinking> 标签内容"""
        if "<thinking>" in text and "</thinking>" in text:
            start = text.index("<thinking>") + len("<thinking>")
            end = text.index("</thinking>")
            return text[start:end].strip()
        return None

    def __repr__(self) -> str:
        return (
            f"Agent(model={self.llm.model}, "
            f"tools={len(self.tools.tools)}, "
            f"mode={self.permissions.mode.value}, "
            f"session={self.session_id})"
        )


# ───────────────────────────────────────────────
# 快速构建函数
# ───────────────────────────────────────────────


def create_agent(
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
    system_prompt: str | None = None,
    permission_mode: str = "normal",
    with_builtins: bool = True,
    **kwargs: Any,
) -> Agent:
    """
    快速创建 Agent 实例

    参数：
        model: LLM 模型名称
        api_key: API 密钥（默认从环境变量读取）
        system_prompt: 自定义系统提示
        permission_mode: 权限模式 (ask/normal/auto_edit/auto/yolo)
        with_builtins: 是否注册内置工具
        **kwargs: 传递给 Agent 的其他参数

    示例：
        agent = create_agent(model="gpt-4o", permission_mode="auto_edit")
        result = await agent.run_sync("帮我读取 README.md")
    """
    mode = PermissionMode(permission_mode)

    agent = Agent(
        model=model,
        api_key=api_key,
        system_prompt=system_prompt,
        permission_mode=mode,
        **kwargs,
    )

    if with_builtins:
        agent.tools.register_all_builtins()

    return agent


def _parse_action_from_text(text: str) -> tuple[list[ToolCallRequest], str]:
    """从文本防御性解析 action 文本协议工具调用（delta 3.5，设计档案 §12）。

    弱工具 provider（capability.tools=none）降级协议：
    <action name="search_stock">{"query":"茅台"}</action> → ToolCallRequest。
    与 DSML 解析并列：native tool_calls → DSML → action 三级兜底。
    若无 action 标记，返回 ([], 原文本)。
    """
    from finance_agent.llm.action_protocol import extract_action, is_action_block

    if not is_action_block(text):
        return [], text
    tc = extract_action(text)
    if tc is None:
        return [], text
    # 清理 action 块，避免协议标记泄漏给用户
    import re as _re

    cleaned = _re.sub(
        r"<action\s+name=[\"'][^\"']+[\"']\s*>.*?</action>", "", text, flags=_re.DOTALL
    )
    return [tc], cleaned.strip()
