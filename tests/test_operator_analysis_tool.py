"""start_stock_analysis 工具：派单、回执、幂等、失败披露。"""

from __future__ import annotations

import json

import pytest

from finance_agent.operator.analysis import make_start_stock_analysis


@pytest.mark.asyncio
async def test_rejects_non_six_digit_code():
    calls: list[tuple] = []

    async def dispatcher(symbol, name, api_key, llm_config):
        calls.append((symbol, name))
        return {}

    tool = make_start_stock_analysis(dispatcher=dispatcher)
    out = json.loads(await tool("茅台", "贵州茅台"))

    assert out["ok"] is False
    assert "6 位" in out["error"]
    assert calls == []


@pytest.mark.asyncio
async def test_dispatches_and_returns_receipt():
    calls: list[tuple] = []

    async def dispatcher(symbol, name, api_key, llm_config):
        calls.append((symbol, name, api_key, llm_config))
        return {
            "session_id": "sid-1",
            "display_name": "贵州茅台 深度分析",
            "status": "running",
            "symbol": symbol,
        }

    tool = make_start_stock_analysis(api_key="sk-test", dispatcher=dispatcher)
    out = json.loads(await tool("600519", "贵州茅台"))

    assert out == {
        "ok": True,
        "session_id": "sid-1",
        "display_name": "贵州茅台 深度分析",
        "status": "running",
        "symbol": "600519",
    }
    assert calls[0][:2] == ("600519", "贵州茅台")


@pytest.mark.asyncio
async def test_duplicate_dispatch_returns_first_receipt():
    calls: list[str] = []

    async def dispatcher(symbol, name, api_key, llm_config):
        calls.append(symbol)
        return {
            "session_id": f"sid-{len(calls)}",
            "display_name": "x",
            "status": "running",
            "symbol": symbol,
        }

    tool = make_start_stock_analysis(dispatcher=dispatcher)
    first = await tool("600519")
    second = await tool("600519")

    assert first == second
    assert calls == ["600519"]


@pytest.mark.asyncio
async def test_dispatch_failure_has_no_session_id():
    async def dispatcher(symbol, name, api_key, llm_config):
        raise RuntimeError("db down")

    tool = make_start_stock_analysis(dispatcher=dispatcher)
    out = json.loads(await tool("600519"))

    assert out["ok"] is False
    assert "db down" in out["error"]
    assert "session_id" not in out
