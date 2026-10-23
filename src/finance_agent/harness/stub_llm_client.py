"""Stub LLM 客户端--测试模式专用。

默认行为：按固定节奏吐固定文本 delta，不返回 tool_call，确保 ReAct Agent 在 1 轮完成。
用于 E2E 门禁的确定性流式断言（见 openspec/changes/add-e2e-core-specs）。

思考模式模拟：先吐 reasoning_delta（思维链），再吐 text_delta（最终回答），
与 DeepSeek 原生思考模式行为一致，确保测试模式下思考横幅有确定性 reasoning 可断言。

工具调用场景（scenario="tool_call"，由 STUB_SCENARIO=tool_call 启用）：
模拟"思考1 -> tool_call(web_search) -> 工具结果 -> 思考2 -> 回答"的完整 ReAct 序列，
用于 E2E 确定性验证思考-搜索-思考的时间序列渲染（agent-turn-box-display delta）。
默认无参构造不受影响，仍保持 1 轮完成。

管线场景（scenario="pipeline"，由 STUB_SCENARIO=pipeline 启用）：
模拟深度分析完整链路"tool_call(search_stock) -> tool_call(run_deep_analysis) -> 回答"，
用于 E2E 确定性触发 5 层管线（管线内部 LLM/数据由 _llm_utils / fetch 的 TESTING stub
接管），验证 PipelineCard 按 agent 阶段分组渲染（agent-turn-box-display delta task 5.5）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from finance_agent.harness.llm_client import LLMResponse
from finance_agent.harness.types import ToolCallRequest

# 固定的 stub 思考内容（分块吐出，模拟 reasoning_content）
_STUB_REASONING_CHUNKS = [
    "## 分析思路\n",
    "用户询问了一个测试问题，",
    "我需要给出简短回答。",
]

# 固定的 stub 回答文本（分块吐出，模拟 content）
_STUB_ANSWER_CHUNKS = [
    "这是",
    "一段",
    "测试用的",
    "固定回复。",
    "用于验证",
    "流式渲染",
    "的增量累积。",
]

# 工具调用场景：思考1（搜索前的初步思考）
_STUB_TOOLCALL_THINKING_1 = [
    "用户想知道茅台最新消息，",
    "我需要先搜索一下实时信息。",
]

# 工具调用场景：思考2（基于搜索结果的再思考）
_STUB_TOOLCALL_THINKING_2 = [
    "搜索结果显示茅台近期有提价动作，",
    "我整理一下关键信息给用户。",
]


class StubLLMClient:
    """测试模式 LLM 客户端，接口与 LiteLLMClient 一致。"""

    def __init__(
        self,
        model: str = "stub/test",
        api_key: str | None = None,
        scenario: str | None = None,
        **kwargs: Any,
    ):
        self.model = model
        self.api_key = api_key or "stub-key"
        # scenario="tool_call" 时启用工具调用场景；默认 None 保持 1 轮完成
        self.scenario = scenario
        # ReAct 轮次计数：tool_call 场景下区分第 1 轮（思考1+tool_call）与第 2 轮（思考2+回答）
        self._round = 0
        # operator 场景选中的工具（第 1 轮决定，第 2 轮据此给固定回答）
        self._operator_tool = ""

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        tool_choice: str = "auto",
    ) -> AsyncIterator[LLMResponse]:
        """按场景吐 delta。

        默认：先吐思维链（reasoning_content），再吐回答（content），1 轮完成。
        tool_call 场景：第 1 轮吐思考1 + tool_call(web_search)，第 2 轮吐思考2 + 回答。
        llm_failure 场景：第 1 轮 raise 异常，模拟 LLM 调用失败。
        """
        self._round += 1

        # LLM 失败场景：raise 异常，模拟 LLM 调用失败（harden-react-path-resilience）
        if self.scenario == "llm_failure":
            raise RuntimeError("STUB LLM 模拟失败")

        # operator 场景：快速模式系统操作，用于 E2E 验证
        # 「工具横幅 + 回执/查询结果」链路。按最后一条用户消息选择工具：
        # 含「日批」→ get_daily_ops_status；其余 → start_stock_analysis。
        if self.scenario == "operator" and self._round == 1:
            last_user = next(
                (
                    str(message.get("content") or "")
                    for message in reversed(messages)
                    if message.get("role") == "user"
                ),
                "",
            )
            if "日批" in last_user:
                self._operator_tool = "get_daily_ops_status"
                yield LLMResponse(reasoning_delta="用户询问日批状态，先读取调度器与任务状态。")
                yield LLMResponse(
                    tool_calls=[
                        ToolCallRequest(
                            id="stub_operator_ops_1",
                            name="get_daily_ops_status",
                            arguments={},
                        )
                    ],
                    is_finished=True,
                )
                return
            self._operator_tool = "start_stock_analysis"
            yield LLMResponse(reasoning_delta="用户要求分析 600519，先派单到独立分析会话。")
            yield LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="stub_operator_1",
                        name="start_stock_analysis",
                        arguments={"stock_code": "600519", "stock_name": "贵州茅台"},
                    )
                ],
                is_finished=True,
            )
            return

        if self.scenario == "operator" and self._round >= 2:
            if self._operator_tool == "get_daily_ops_status":
                yield LLMResponse(reasoning_delta="日批状态已读取，向用户摘要。")
                yield LLMResponse(text_delta="日批状态已读取（测试场景）。")
                yield LLMResponse(is_finished=True)
                return
            yield LLMResponse(reasoning_delta="派单完成，复述回执给用户。")
            yield LLMResponse(
                text_delta="已启动贵州茅台(600519)的深度分析，任务号见回执。"
                "请在左侧会话列表点进去查看进度。"
            )
            yield LLMResponse(is_finished=True)
            return

        # pipeline 场景：深度分析完整链路 search_stock -> run_deep_analysis -> 回答
        if self.scenario == "pipeline" and self._round == 1:
            yield LLMResponse(reasoning_delta="用户给了股票代码 600519，先识别股票。")
            yield LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="stub_pipeline_1",
                        name="search_stock",
                        arguments={"query": "600519"},
                    )
                ],
                is_finished=True,
            )
            return

        if self.scenario == "pipeline" and self._round == 2:
            yield LLMResponse(reasoning_delta="已识别贵州茅台(600519)，启动 5 层深度分析管线。")
            yield LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="stub_pipeline_2",
                        name="run_deep_analysis",
                        arguments={"stock_code": "600519", "stock_name": "贵州茅台"},
                    )
                ],
                is_finished=True,
            )
            return

        if self.scenario == "pipeline" and self._round >= 3:
            yield LLMResponse(reasoning_delta="管线分析完成，向用户总结结论。")
            yield LLMResponse(text_delta="STUB 深度分析完成（确定性测试数据）。")
            yield LLMResponse(is_finished=True)
            return

        if self.scenario == "tool_call" and self._round == 1:
            # 第 1 轮：思考1 -> tool_call(web_search)
            for chunk in _STUB_TOOLCALL_THINKING_1:
                await asyncio.sleep(0.02)
                yield LLMResponse(reasoning_delta=chunk)
            yield LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="stub_call_1",
                        name="web_search",
                        arguments={"query": "茅台最新消息"},
                    )
                ],
                is_finished=True,
            )
            return

        if self.scenario == "tool_call" and self._round >= 2:
            # 第 2 轮：思考2 -> 回答
            for chunk in _STUB_TOOLCALL_THINKING_2:
                await asyncio.sleep(0.02)
                yield LLMResponse(reasoning_delta=chunk)
            for chunk in _STUB_ANSWER_CHUNKS:
                await asyncio.sleep(0.02)
                yield LLMResponse(text_delta=chunk)
            yield LLMResponse(is_finished=True)
            return

        # 默认：1 轮完成（思考 + 回答，不调工具）
        for chunk in _STUB_REASONING_CHUNKS:
            await asyncio.sleep(0.05)
            yield LLMResponse(reasoning_delta=chunk)
        for chunk in _STUB_ANSWER_CHUNKS:
            await asyncio.sleep(0.05)
            yield LLMResponse(text_delta=chunk)
        yield LLMResponse(is_finished=True)

    def __repr__(self) -> str:
        return f"StubLLMClient(model={self.model}, scenario={self.scenario})"
