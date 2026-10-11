"""@live 用例：真实方舟 Agent Plan 链路，验证 reasoning / tool_calls 内容真实产生（防漂移）。

单测（mock）锁定的是埋点逻辑——Langfuse generation output 结构 {answer, reasoning,
tool_calls}、prompt 元数据挂载、降级不阻断；但「真实 LLM 是否真的下发
reasoning_content / tool_calls」只有真实调用能验证。本文件即 ADR-0015 要求的
@live 防漂移用例，nightly 跑，不进 PR 门禁（ci.yml -m "not live"）。

前置：LLM_API_KEY（方舟，与生产同栈）环境变量；无 key 时整文件 skip。
可选：LLM_LIVE_MODEL 覆盖模型名（默认生产 LLM_MODEL）。
历史：DeepSeek 直连时代硬编码 deepseek/deepseek-chat + DEEPSEEK_API_KEY，
2026-09-13 随生产切方舟同步（Agent Plan 端点不支持直连 DeepSeek 官方模型）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# 模块级读 env 前必须先加载 .env（conftest 不保证 import 时序）
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

_LLM_API_KEY = os.getenv("LLM_API_KEY", "")
_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (_LLM_API_KEY and _LLM_BASE_URL), reason="需 LLM_API_KEY + LLM_BASE_URL（方舟）"
    ),
]

_LIVE_MODEL = os.getenv("LLM_LIVE_MODEL") or os.getenv("LLM_MODEL") or "openai/glm-5.3"


@pytest.mark.asyncio
async def test_live_reasoning_streamed_in_thinking_mode():
    """真实 DeepSeek thinking 模式：chat_stream 应 yield 非空 reasoning_delta。

    对应 spec「LLM Generation 推理内容可观测」：若 DeepSeek 停止下发
    reasoning_content（API 行为漂移），本用例失败，nightly 报警。
    """
    from finance_agent.harness.litellm_client import LiteLLMClient

    client = LiteLLMClient(model=_LIVE_MODEL, thinking="enabled", max_retries=1)

    reasoning_parts: list[str] = []
    text_parts: list[str] = []
    finished = False
    async for resp in client.chat_stream(
        messages=[{"role": "user", "content": "用一句话说明 ROE 的含义"}]
    ):
        if resp.reasoning_delta:
            reasoning_parts.append(resp.reasoning_delta)
        if resp.text_delta:
            text_parts.append(resp.text_delta)
        if resp.is_finished:
            finished = True

    # #52 已知局限：is_finished 近恒真（截断收尾也置位），且实测正常 stop 终帧
    # usage 亦为 None（usage 未随完成帧透出）——消费者侧无法区分「正常 stop」与
    # 「截断收尾」，真正判别需客户端透出 finish_reason（挂账 issue）。
    # 判别力暂由「reasoning 与正文均非空」承担：截断通常二者缺一。
    assert finished, "流未正常结束（未收到 is_finished）"
    assert "".join(reasoning_parts), "thinking 模式未产生 reasoning_content（真实 API 行为漂移）"
    assert "".join(text_parts), "未产生最终回答 content"


def test_live_tool_calls_returned(monkeypatch):
    """真实 GLM tool calling：complete_with_tools 返回结构含 tool_calls。

    对应 spec「LLM Generation 工具调用决策可观测」：complete_with_tools 走
    非流式 litellm.completion，返回完整 response，tool_calls 在
    choices[0].message.tool_calls（migrate-off-legacy-llm-shim Task 3：
    原 call_llm_with_tools 直调 gateway.complete_with_tools）。

    #52 护栏：
    - tool_choice=required（registry ark-glm capability 实测支持，生产 ReAct
      force_tool 轮同款）——auto 下模型可直接回答不调工具，行为波动会被误报
      成协议漂移。**走 env 解析而非请求级 llm_config**：resolver 请求分支恒用
      openai-compatible 通用能力（tool_choice_required=False），env 分支才按
      模型名推断 ark-glm 能力——本用例 monkeypatch 三个 LLM_* 变量、不传
      llm_config，与生产 deep 路径（env profile）同形（请求分支能力推断缺口
      另立 issue 挂账）。
    - 同步调用超时护栏：raw_completion 未显式传 timeout 时注入
      LLM_TIMEOUT_SECONDS（默认 300，incident 016/017 卡死防护）——本用例
      收紧为 120s，夜挂 10 分钟的场景不再可能。
    """
    from finance_agent.llm.gateway import complete_with_tools

    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("LLM_MODEL", _LIVE_MODEL)
    monkeypatch.setenv("LLM_BASE_URL", _LLM_BASE_URL)
    monkeypatch.setenv("LLM_API_KEY", _LLM_API_KEY)
    monkeypatch.delenv("LLM_QUICK_MODEL", raising=False)

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_stock_price",
                "description": "查询 A 股最新价",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "股票代码，如 600519"}
                    },
                    "required": ["symbol"],
                },
            },
        }
    ]

    # env 解析（不传 llm_config）：resolver 环境分支按模型名推断 ark-glm 能力
    # （tool_choice_required=True），请求分支恒用 openai-compatible 通用能力
    # 会把 required 拒在守卫——详见本用例 docstring 与 #52 挂账 issue。
    resp = complete_with_tools(
        [{"role": "user", "content": "贵州茅台（600519）现在多少钱？请调用工具查询。"}],
        tools=tools,
        tool_choice="required",
    )

    message = resp.choices[0].message
    tool_calls = getattr(message, "tool_calls", None) or []
    assert tool_calls, "tool calling 未返回 tool_calls（真实 API 行为漂移）"
    names = [tc.function.name for tc in tool_calls]
    assert "get_stock_price" in names, f"未调用预期工具，实际: {names}"
