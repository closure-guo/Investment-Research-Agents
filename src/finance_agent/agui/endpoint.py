"""POST /api/agui/quick：AG-UI 协议 quick 模式对话端点（add-assistant-ui-thread PoC）。

双轨隔离（调研 §2.3）：生成任务由请求内联驱动（StreamingResponse 生成器直接消费
agent.run() → translate_to_agui → EventEncoder SSE），不经过 StreamRegistry /
journal——移除本路由后深度模式与现有 /api/chat 通道零影响。

对接点（调研 §3.1）：
- build_agent(mode="quick")：TESTING=1 时与现有通道同一 stub 注入路径；
- Langfuse react_loop span 等价保留（ADR-0015）；
- 落库等价 _ChatCollector：user 消息任务内 append_chat，assistant 以 AG-UI 事件
  累积（TEXT_MESSAGE_CONTENT → response 等），运行中每 10s 增量 upsert，
  终态（RUN_FINISHED/RUN_ERROR）先落库再下发 + update_session_status
  （completed/failed/interrupted）；
- 客户端断开（abortRun/关页）→ CancelledError → 中断落库。

取消路径 PoC 限制：/api/sessions/{id}/cancel 经 registry 取消 registry 任务，
本通道任务不在 registry 中（隔离要求），取消由前端 HttpAgent.abortRun() 断开
连接触发服务端中断路径；挂入 cancel 端点需改 api.py（本任务硬约束禁止）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import nullcontext
from typing import Any

from ag_ui.core.events import (
    BaseEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    RunErrorEvent,
    RunFinishedEvent,
    TextMessageContentEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from finance_agent import session_store
from finance_agent.agui.translator import translate_to_agui
from finance_agent.llm.config import LLMConfig
from finance_agent.timeline_builder import close_last_thinking, summarize_tool_args

_logger = logging.getLogger("finance_agent.agui.endpoint")

router = APIRouter()

# SSE 响应统一头（与 api._SSE_HEADERS 同款：禁缓存、禁代理缓冲）
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

_HEARTBEAT_INTERVAL = 10.0  # 秒，与现有通道一致
_PERSIST_INTERVAL = 10.0  # 增量 upsert 间隔（对齐现有通道 api.py 节奏）
_TOOL_RESULT_MAX = 150  # tool result_text 截断长度（等价 _summarize_tool_result）


class _AguiMessage(BaseModel):
    """AG-UI Message 子集（camelCase 兼容 HttpAgent 线上格式）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    id: str | None = None
    role: str
    content: str = ""


class _RunAgentInputBody(BaseModel):
    """AG-UI RunAgentInput 兼容请求体。

    官方 RunAgentInput 的 thread_id 必填，本端点 PoC 允许为空——为空则服务端
    create_chat_session 新建会话，thread_id 经 RUN_STARTED 回传（实施计划 Task 2）。
    extra="allow" 容忍 state/tools/context 等官方字段直通。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    thread_id: str | None = None
    run_id: str | None = None
    messages: list[_AguiMessage] = Field(default_factory=list)
    forwarded_props: Any = None


class _AguiCollector:
    """从 AG-UI 事件流累积 assistant 回复/思考/工具调用，用于落库。

    等价 api._ChatCollector（分块拼接 == 落库全文的挂钩点，调研 §2.3）：
    仅收翻译后的官方事件，response 只累积 TEXT_MESSAGE_CONTENT delta。

    同时按事件顺序构建结构化 agentTimeline（TimelineItem 同构 dict，经
    deserializeTimeline 被前端恢复路径直接消费——修复刷新恢复丢 web_search
    步骤的保真度缺口）。与旧通道 apply_chat_event 的差异：搜索类工具
    （web_search 等）不跳过——本通道实时渲染就是工具横幅（QuickThread
    ToolCallBlock），无结构化搜索结果可建 search item。
    """

    def __init__(self) -> None:
        self.response: str = ""
        self.thinking: str = ""
        self.tool_calls: list[dict] = []
        self.saw_error: bool = False
        self.error_message: str = ""
        # AG-UI tool_call_id -> self.tool_calls 索引（ARGS/RESULT 回查）
        self._tool_index: dict[str, int] = {}
        # 结构化时序（thinking / tool_call 交错，事件顺序）
        self.agent_timeline: list[dict] = []
        # 当前接收 content 的 thinking item 索引；None = 无开段
        self._thinking_idx: int | None = None
        # 首个 delta 重置 content（THINK_REPLACE 语义：新段紧邻上一个思考段结束）
        self._replace_pending: bool = False
        # 最近一次 REASONING_MESSAGE_END 后尚无其他 item（换段紧邻判定）
        self._just_closed: bool = False
        # AG-UI tool_call_id -> agent_timeline 索引（RESULT 回查）
        self._timeline_tool_index: dict[str, int] = {}

    def feed(self, event: BaseEvent) -> None:
        if isinstance(event, TextMessageContentEvent):
            self.response += event.delta
            # 文本不是 timeline item；文本段开启意味着思考段已成历史（不再 replace）
            self._just_closed = False
        elif isinstance(event, ReasoningMessageStartEvent):
            self.agent_timeline = close_last_thinking(self.agent_timeline)
            last = self.agent_timeline[-1] if self.agent_timeline else None
            if self._just_closed and last is not None and last.get("type") == "thinking":
                # 新思考段紧邻上一思考段结束（中间无工具/文本）：THINK_REPLACE 语义，
                # 首个 delta 重置该 item 内容（对齐前端 thinking_replace）
                self._thinking_idx = len(self.agent_timeline) - 1
                self._replace_pending = True
            else:
                self._thinking_idx = None
            self._just_closed = False
        elif isinstance(event, ReasoningMessageContentEvent):
            self.thinking += event.delta
            cur = self._thinking_idx
            last = (
                self.agent_timeline[cur]
                if cur is not None and cur < len(self.agent_timeline)
                else None
            )
            if last is not None and cur is not None:
                if self._replace_pending:
                    self.agent_timeline[cur] = {
                        **last,
                        "content": event.delta,
                        "done": False,
                    }
                    self._replace_pending = False
                else:
                    self.agent_timeline[cur] = {
                        **last,
                        "content": last["content"] + event.delta,
                    }
            else:
                self.agent_timeline.append(
                    {"type": "thinking", "content": event.delta, "done": False}
                )
                self._thinking_idx = len(self.agent_timeline) - 1
        elif isinstance(event, ReasoningMessageEndEvent):
            self.agent_timeline = close_last_thinking(self.agent_timeline)
            self._thinking_idx = None
            self._just_closed = True
        elif isinstance(event, ToolCallStartEvent):
            self._tool_index[event.tool_call_id] = len(self.tool_calls)
            self.tool_calls.append(
                {
                    "name": event.tool_call_name,
                    "args": {},
                    "result_text": "",
                    "done": False,
                }
            )
            # 思考后接工具调用：末尾 thinking 显式收口（镜像前端 tool_call 语义）
            self.agent_timeline = close_last_thinking(self.agent_timeline)
            self.agent_timeline.append(
                {
                    "type": "tool_call",
                    "name": event.tool_call_name,
                    "args": "",
                    "result": "",
                    "done": False,
                }
            )
            self._timeline_tool_index[event.tool_call_id] = len(self.agent_timeline) - 1
            self._thinking_idx = None
            self._just_closed = False
        elif isinstance(event, ToolCallArgsEvent):
            idx = self._tool_index.get(event.tool_call_id)
            if idx is not None:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    self.tool_calls[idx]["args"] = json.loads(event.delta)
            tl_idx = self._timeline_tool_index.get(event.tool_call_id)
            if idx is not None and tl_idx is not None:
                self.agent_timeline[tl_idx]["args"] = summarize_tool_args(
                    self.tool_calls[idx]["args"]
                )
        elif isinstance(event, ToolCallResultEvent):
            idx = self._tool_index.get(event.tool_call_id)
            if idx is not None:
                self.tool_calls[idx]["result_text"] = event.content[:_TOOL_RESULT_MAX]
                self.tool_calls[idx]["done"] = True
            tl_idx = self._timeline_tool_index.get(event.tool_call_id)
            if tl_idx is not None:
                self.agent_timeline[tl_idx]["result"] = event.content[:_TOOL_RESULT_MAX]
                self.agent_timeline[tl_idx]["done"] = True
        elif isinstance(event, ToolCallEndEvent):
            pass  # 协议闭合事件，collector 无状态变化
        elif isinstance(event, RunErrorEvent):
            self.saw_error = True
            self.error_message = event.message


def _upsert_assistant_chat(session_id: str, collector: _AguiCollector) -> None:
    """collector 内容 upsert 到 chat_history（等价 api._upsert_assistant_chat）。"""
    response = collector.response.strip()
    thinking = collector.thinking.strip() or None
    tool_calls = collector.tool_calls or None
    agent_timeline = collector.agent_timeline or None
    if not (response or thinking or tool_calls or agent_timeline):
        return
    session_store.upsert_chat(
        session_id,
        "assistant",
        response,
        thinking=thinking,
        tool_calls=tool_calls,
        agent_timeline=agent_timeline,
    )


def _persist_interrupted(session_id: str, collector: _AguiCollector) -> None:
    """中断兜底持久化（等价 api._persist_interrupted）：不出现悬空 user 消息。"""
    if collector.response.strip():
        collector.response += "\n\n[输出中断]"
    else:
        collector.response = "[输出中断]"
    _upsert_assistant_chat(session_id, collector)


async def _next_event(gen: AsyncIterator[BaseEvent]) -> BaseEvent:
    """包装 __anext__ 为协程函数（asyncio.create_task 需要协程而非 Awaitable）。"""
    return await gen.__anext__()


async def _aclose(gen: Any) -> None:
    """尽力关闭异步生成器（等价 stream_agent_to_sse finally 清理）。

    gen 参数弱类型：Agent.run 与 translate_to_agui 的声明返回类型为
    AsyncIterator，aclose 仅存在于运行时的异步生成器对象上。
    """
    with contextlib.suppress(Exception):
        await gen.aclose()


def _schedule_interrupted_persist(thread_id: str, collector: _AguiCollector) -> None:
    """在独立任务中执行中断落库 + status=interrupted。

    调用点可能处于已取消任务（await 会立即重抛 CancelledError）或生成器
    aclose（GeneratorExit）上下文，两种情况下直接 await 落库都不安全/不可达，
    必须 detach 到新任务保证执行——否则会话永久泄漏 running（E2E 实证：
    quick 会话切换 abort 后状态再未收敛，会话被「生成中」守卫锁死）。
    """

    async def _run() -> None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(_persist_interrupted, thread_id, collector)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(session_store.update_session_status, thread_id, "interrupted")

    # 事件循环已关闭（进程退出路径）时无法调度：尽力而为
    with contextlib.suppress(RuntimeError):
        asyncio.get_running_loop().create_task(_run())


async def _agui_run_stream(
    thread_id: str,
    run_id: str,
    user_message: str,
    api_key: str | None,
    encoder: EventEncoder,
    llm_config: LLMConfig | None = None,
    operator_enabled: bool = True,
) -> AsyncGenerator[str, None]:
    """内联驱动一次 quick run：落库 user → agent.run() → 翻译 → SSE，三路落库。"""
    from finance_agent.agent_factory import build_agent

    collector = _AguiCollector()
    # 终态落库标记前置到 try 外：外层 finally 兜底在任何时机退出都要可读
    terminal_persisted = False
    try:
        # user 消息任务内落库（调研 §3.1：409/single-flight 语义不适用于本通道）
        await asyncio.to_thread(session_store.append_chat, thread_id, "user", user_message)
        await asyncio.to_thread(session_store.update_session_status, thread_id, "running")

        agent = build_agent(
            mode="quick",
            api_key=api_key,
            session_id=thread_id,
            llm_config=llm_config,
            operator_enabled=operator_enabled,
            current_user_message=user_message,
        )

        # ADR-0015：Langfuse react_loop span 等价保留（调研 §3.1）
        from finance_agent.langfuse_tracing import get_langfuse

        lf = get_langfuse()
        react_cm: contextlib.AbstractContextManager[Any] = nullcontext()
        propagate_cm: contextlib.AbstractContextManager[Any] = nullcontext()
        react_obs: Any = None
        if lf is not None:
            react_cm = lf.start_as_current_observation(
                as_type="span", name="react_loop", input={"query": user_message}
            )
            try:
                from langfuse import propagate_attributes

                propagate_cm = propagate_attributes(session_id=thread_id)
            except Exception:  # noqa: S110
                pass
        react_obs = react_cm.__enter__()
        propagate_cm.__enter__()

        agent_gen = agent.run(user_message)
        agui_events = translate_to_agui(agent_gen, thread_id=thread_id, run_id=run_id)
        next_task: asyncio.Task[BaseEvent] = asyncio.create_task(_next_event(agui_events))
        last_persist = time.monotonic()
        try:
            while True:
                done, _ = await asyncio.wait({next_task}, timeout=_HEARTBEAT_INTERVAL)
                if not done:
                    # 增量持久化：空闲期到点也 upsert，进行中回复不丢
                    now = time.monotonic()
                    if now - last_persist >= _PERSIST_INTERVAL:
                        await asyncio.to_thread(_upsert_assistant_chat, thread_id, collector)
                        last_persist = now
                    # 空闲心跳：SSE 注释行（映射表 #15，非协议事件）
                    yield ": heartbeat\n\n"
                    continue
                try:
                    event = next_task.result()
                except StopAsyncIteration:
                    break
                collector.feed(event)
                if isinstance(event, RunFinishedEvent):
                    # 终态先落库再下发：客户端收到 RUN_FINISHED 的瞬间读
                    # session_store 必须已见到 assistant 全文（审查修复）
                    await asyncio.to_thread(_upsert_assistant_chat, thread_id, collector)
                    await asyncio.to_thread(
                        session_store.update_session_status, thread_id, "completed"
                    )
                    # add-user-feedback:把本次运行的 Langfuse trace 关联到 session
                    # (反馈端点按 session 解析最近一次运行的 trace 落 score)
                    _trace_id = getattr(react_obs, "trace_id", None)
                    if _trace_id:
                        await asyncio.to_thread(
                            session_store.set_session_trace_id, thread_id, _trace_id
                        )
                    terminal_persisted = True
                elif isinstance(event, RunErrorEvent):
                    # 终态先落库再下发：部分内容落库 + failed（不落库成功回复）
                    await asyncio.to_thread(_upsert_assistant_chat, thread_id, collector)
                    await asyncio.to_thread(
                        session_store.update_session_status,
                        thread_id,
                        "failed",
                        failure_reason=collector.error_message or "agent error",
                    )
                    terminal_persisted = True
                else:
                    # 增量持久化：每 _PERSIST_INTERVAL upsert 一次进行中回复
                    # （进程崩溃可兜底，对齐现有通道 api.py 节奏）
                    now = time.monotonic()
                    if now - last_persist >= _PERSIST_INTERVAL:
                        await asyncio.to_thread(_upsert_assistant_chat, thread_id, collector)
                        last_persist = now
                yield encoder.encode(event)
                next_task = asyncio.create_task(_next_event(agui_events))
        finally:
            if not next_task.done():
                next_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await next_task
            with contextlib.suppress(Exception):
                await _aclose(agent_gen)
            with contextlib.suppress(Exception):
                await _aclose(agui_events)
            if react_obs is not None and collector.response:
                with contextlib.suppress(Exception):
                    react_obs.update(output={"answer": collector.response})
            with contextlib.suppress(Exception):
                propagate_cm.__exit__(None, None, None)
                react_cm.__exit__(None, None, None)

        if not terminal_persisted:
            # 兜底：流耗尽但未出现终态事件（正常情况下 RUN_FINISHED/RUN_ERROR
            # 已在循环内先落库再下发，不会走到这里，避免重复落库）
            if collector.saw_error:
                await asyncio.to_thread(_upsert_assistant_chat, thread_id, collector)
                await asyncio.to_thread(
                    session_store.update_session_status,
                    thread_id,
                    "failed",
                    failure_reason=collector.error_message or "agent error",
                )
            else:
                await asyncio.to_thread(_upsert_assistant_chat, thread_id, collector)
                await asyncio.to_thread(session_store.update_session_status, thread_id, "completed")
            terminal_persisted = True
    except asyncio.CancelledError:
        # 客户端断开/取消：中断落库 + interrupted（RunAgent abortRun 语义）。
        # 已取消任务里 await 可能立即重抛（二次取消），落库 detach 到独立任务保证执行；
        # 置终态标记避免外层 finally 重复调度（否则 [输出中断] 占位会落两遍）
        _schedule_interrupted_persist(thread_id, collector)
        terminal_persisted = True
        raise
    except Exception as exc:
        # agent.run() 自身异常（真 LLM 失败等）：RUN_ERROR 终止 + failed
        _logger.exception("AG-UI run 异常 session=%s", thread_id)
        collector.saw_error = True
        collector.error_message = f"{type(exc).__name__}: {exc}"
        await asyncio.to_thread(_upsert_assistant_chat, thread_id, collector)
        await asyncio.to_thread(
            session_store.update_session_status,
            thread_id,
            "failed",
            failure_reason=collector.error_message,
        )
        terminal_persisted = True
        yield encoder.encode(RunErrorEvent(message=str(exc)))
    finally:
        # GeneratorExit（客户端断开时 StreamingResponse/uvicorn 走 aclose 清理
        # 响应生成器）是 BaseException，不经上面任何 except——若无此兜底，
        # 该路径下终态落库被整体跳过，会话永久泄漏 running（E2E debug-cursor
        # 实证，2026-09-01）。aclose 上下文里 await 落库不安全，detach 执行。
        if not terminal_persisted:
            _schedule_interrupted_persist(thread_id, collector)


@router.post("/api/agui/quick")
async def agui_quick(req: _RunAgentInputBody):
    """AG-UI 协议 quick 模式对话端点。

    接受 RunAgentInput（thread_id 可空——为空则新建会话，thread_id 从
    RUN_STARTED 回传），以 SSE 返回 EventEncoder 编码的标准 AG-UI 事件流。
    """
    user_message = next(
        (m.content for m in reversed(req.messages) if m.role == "user" and m.content),
        "",
    )
    if not user_message:
        raise HTTPException(status_code=422, detail="messages must contain a user message")

    thread_id = (req.thread_id or "").strip()
    if thread_id:
        session = await asyncio.to_thread(session_store.get_session, thread_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        display_name = user_message.strip()[:30] or "快速问答"
        thread_id = await asyncio.to_thread(session_store.create_chat_session, display_name)

    api_key: str | None = None
    llm_config: LLMConfig | None = None
    capability: dict[str, Any] | None = None
    if isinstance(req.forwarded_props, dict):
        key = req.forwarded_props.get("apiKey")
        # 非空字符串即可透传（审查修复：不限定 sk- 前缀，key 形态由上游决定）
        if isinstance(key, str) and key:
            api_key = key
        # llm_config 透传（主规范 Quick Chat Entry：api_key 与 llm_config 经
        # forwardedProps 透传）。仅接受已知字段，畸形载荷静默忽略回退 env。
        raw_cfg = req.forwarded_props.get("llmConfig")
        if isinstance(raw_cfg, dict):
            # capability 是 probe 事实，不是 LLMConfig 字段（不进 LLMConfig 构造）；
            # 仅用于判断是否注册系统操作工具。
            raw_cap = raw_cfg.get("capability")
            if isinstance(raw_cap, dict):
                capability = raw_cap
            allowed = {"model", "baseUrl", "apiKey", "thinking", "apiForm", "contextLength"}
            kwargs = {
                k: v for k, v in raw_cfg.items() if k in allowed and isinstance(v, (str, int))
            }
            if kwargs:
                llm_config = LLMConfig(**kwargs)

    from finance_agent.operator.gating import operator_tools_enabled

    operator_enabled = operator_tools_enabled(capability)
    run_id = req.run_id or uuid.uuid4().hex
    encoder = EventEncoder()
    return StreamingResponse(
        _agui_run_stream(
            thread_id,
            run_id,
            user_message,
            api_key,
            encoder,
            llm_config=llm_config,
            operator_enabled=operator_enabled,
        ),
        media_type=encoder.get_content_type(),
        headers=_SSE_HEADERS,
    )
