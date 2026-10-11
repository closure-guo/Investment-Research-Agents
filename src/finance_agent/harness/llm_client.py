"""
Mini Harness - LLM Client
LLM API 客户端：支持流式输出、Tool Calling、错误恢复

设计原则：
- 异步生成器实现流式输出（Claude Code 的核心交互模式）
- 统一的 Tool Schema 注册（OpenAI function calling 格式）
- 自动重试 + 指数退避
- 可插拔：支持不同 LLM 提供商
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from finance_agent.harness.types import ToolCallRequest, ToolSchema

logger = logging.getLogger("finance_agent.harness.llm")


# ───────────────────────────────────────────────
# 工具 Schema 生成（从函数签名自动推导）
# ───────────────────────────────────────────────

PYTHON_TYPE_TO_JSON: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "NoneType": "null",
}


def _annotation_to_json_schema(annotation: Any) -> dict[str, Any]:
    """把 Python 类型注解映射为 JSON Schema 片段。

    泛型/Optional 解包（issue #277）：裸名内省对 list[str] | None 一律
    fallback "string"，LLM 看到的类型与 docstring「列表」矛盾而高频传字符串。
    Optional/Union 剥壳取非 None 成员；list/tuple/set → array（list 带 items）；
    dict → object；未识别注解回退 string（兜底语义不变）。
    """
    import types as _types
    import typing

    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is getattr(_types, "UnionType", None):
        members = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return _annotation_to_json_schema(members[0])
        return {"type": "string"}
    if origin is list:
        args = typing.get_args(annotation)
        items = _annotation_to_json_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": items}
    if origin in (tuple, set, frozenset):
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}
    if origin is not None:
        return {"type": "string"}

    name = getattr(annotation, "__name__", "") or ""
    return {"type": PYTHON_TYPE_TO_JSON.get(str(name), "string")}


def _resolve_annotations(func: Callable, sig: Any) -> dict[str, Any]:
    """经 get_type_hints 解析字符串注解（future annotations 模块）；失败回退裸名。"""
    import typing

    try:
        return typing.get_type_hints(func)
    except Exception:
        return {name: p.annotation for name, p in sig.parameters.items()}


def build_schema_from_function(
    func: Callable,
    name: str | None = None,
    description: str | None = None,
) -> ToolSchema:
    """
    从 Python 函数的签名和 docstring 自动生成 JSON Schema。

    示例：
        def read_file(path: str, limit: int = 100) -> str:
            '''读取文件内容'''

    生成：
        ToolSchema(
            name="read_file",
            description="读取文件内容",
            parameters={"type": "object", "properties": {...}, "required": ["path"]}
        )
    """
    import inspect

    sig = inspect.signature(func)
    hints = _resolve_annotations(func, sig)
    doc = description or (func.__doc__ or "").strip().split("\n")[0].strip()

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        annotation = hints.get(param_name, param.annotation)
        if annotation == inspect.Parameter.empty:
            prop = {"type": "string"}
        else:
            prop = _annotation_to_json_schema(annotation)

        # 从 docstring 提取参数描述
        param_desc = ""
        if func.__doc__:
            for line in func.__doc__.split("\n"):
                if f"{param_name}:" in line or f"{param_name} --" in line:
                    param_desc = line.split(":", 1)[-1].split("--", 1)[-1].strip()
                    break

        properties[param_name] = {**prop, "description": param_desc or param_name}

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return ToolSchema(
        name=name or func.__name__,
        description=doc,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required,
        },
    )


# ───────────────────────────────────────────────
# 流式响应解析
# ───────────────────────────────────────────────


@dataclass
class LLMResponse:
    """LLM 响应 -- 可能是增量文本、增量思考或工具调用

    思考模式（DeepSeek）下：
    - reasoning_delta：原生思维链增量（reasoning_content），先于 text_delta 输出
    - text_delta：最终回答增量（content），与 reasoning_delta 分离
    """

    text_delta: str = ""
    reasoning_delta: str = ""  # 原生思考增量（DeepSeek reasoning_content）
    tool_calls: list[ToolCallRequest] | None = None
    is_finished: bool = False
    usage: dict[str, int] | None = None


# ───────────────────────────────────────────────
# LLM 客户端
# ───────────────────────────────────────────────


class LLMClient:
    """
    LLM API 客户端

    职责：
    1. 发送聊天请求（流式）
    2. 注册工具 schema
    3. 解析 tool_call 块
    4. 自动重试

    当前支持 OpenAI 兼容 API。
    可扩展为支持 Anthropic、本地模型等。
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._http_client = None  # 延迟初始化

    async def _get_client(self):
        """延迟初始化 httpx 客户端"""
        if self._http_client is None:
            import httpx

            self._http_client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=120.0,
            )
        return self._http_client

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[LLMResponse]:
        """
        流式聊天请求 -- 核心方法

        返回异步生成器，每次 yield 一个 LLMResponse。
        消费者可实时获取文本增量或工具调用。

        示例：
            async for chunk in client.chat_stream(messages, tools):
                if chunk.text_delta:
                    print(chunk.text_delta, end="")
                if chunk.tool_calls:
                    print(f"工具调用: {chunk.tool_calls}")
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        for attempt in range(self.max_retries):
            try:
                async for response in self._do_stream_request(payload):
                    yield response
                return
            except Exception as e:
                logger.warning(f"LLM 请求失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (2**attempt))
                else:
                    yield LLMResponse(
                        text_delta=f"\n[错误: LLM 请求失败 - {e}]\n", is_finished=True
                    )

    async def _do_stream_request(self, payload: dict[str, Any]) -> AsyncIterator[LLMResponse]:
        """执行流式 HTTP 请求并解析 SSE 响应"""
        client = await self._get_client()

        async with client.stream("POST", "/chat/completions", json=payload) as response:
            if response.status_code != 200:
                text = await response.aread()
                raise RuntimeError(f"API 错误 {response.status_code}: {text.decode()[:500]}")

            buffer = ""
            current_tool_calls: dict[int, dict[str, Any]] = {}

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue

                data = line[6:]  # 去掉 "data: " 前缀
                if data == "[DONE]":
                    # 发送最终的 tool_calls
                    if current_tool_calls:
                        parsed = self._parse_tool_calls(current_tool_calls)
                        yield LLMResponse(tool_calls=parsed, is_finished=True)
                    else:
                        yield LLMResponse(is_finished=True)
                    return

                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})

                # 文本增量
                text = delta.get("content") or ""
                if text:
                    buffer += text
                    yield LLMResponse(text_delta=text)

                # Tool call 增量
                tool_delta = delta.get("tool_calls", [])
                for t in tool_delta:
                    idx = t.get("index", 0)
                    if idx not in current_tool_calls:
                        current_tool_calls[idx] = {
                            "id": "",
                            "function": {"name": "", "arguments": ""},
                        }

                    call = current_tool_calls[idx]
                    if t.get("id"):
                        call["id"] = t["id"]
                    if t.get("function", {}).get("name"):
                        call["function"]["name"] = t["function"]["name"]
                    if t.get("function", {}).get("arguments"):
                        call["function"]["arguments"] += t["function"]["arguments"]

                # 检查 finish_reason
                finish = chunk.get("choices", [{}])[0].get("finish_reason")
                if finish == "tool_calls" and current_tool_calls:
                    parsed = self._parse_tool_calls(current_tool_calls)
                    yield LLMResponse(tool_calls=parsed, is_finished=True)
                    return

    def _parse_tool_calls(self, raw_calls: dict[int, dict[str, Any]]) -> list[ToolCallRequest]:
        """解析累积的 tool_call 数据"""
        results = []
        for idx in sorted(raw_calls.keys()):
            raw = raw_calls[idx]
            try:
                args = (
                    json.loads(raw["function"]["arguments"]) if raw["function"]["arguments"] else {}
                )
            except json.JSONDecodeError:
                args = {}
            results.append(
                ToolCallRequest(
                    id=raw.get("id", f"call_{idx}"),
                    name=raw["function"]["name"],
                    arguments=args,
                )
            )
        return results

    def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._http_client:
            import asyncio
            import contextlib

            with contextlib.suppress(Exception):
                asyncio.get_event_loop().run_until_complete(self._http_client.aclose())

    def __repr__(self) -> str:
        return f"LLMClient(model={self.model}, base_url={self.base_url})"
