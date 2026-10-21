"""快速模式系统操作工具注册（add-quick-mode-operator）。"""

import json

import pytest

from finance_agent.agent_factory import build_agent
from finance_agent.operator.confirmation import _reset_for_tests

EXPECTED_OPERATOR_TOOLS = {
    "search_stock",
    "start_stock_analysis",
    "get_screening_pool",
    "get_track_record_overview",
    "get_broker_overview",
    "get_daily_ops_status",
    "get_memory_clauses",
    "get_factor_library",
    "get_factor_research_status",
    "set_factor_research_running",
    "run_ops_job",
    "review_memory_clause",
    "start_pool_analysis",
}


@pytest.fixture(autouse=True)
def _clean():
    _reset_for_tests()
    yield
    _reset_for_tests()


def test_quick_mode_registers_operator_tools_by_default():
    agent = build_agent(mode="quick", api_key="test-key")
    names = set(agent.tools.get_tool_names())
    assert names >= EXPECTED_OPERATOR_TOOLS
    assert "web_search" in names
    assert agent.max_iterations == 6


def test_quick_mode_can_disable_operator_tools():
    agent = build_agent(mode="quick", api_key="test-key", operator_enabled=False)
    names = set(agent.tools.get_tool_names())
    assert names == {"web_search"}


def test_quick_mode_env_switch_disables_operator_tools(monkeypatch):
    monkeypatch.setenv("QUICK_OPERATOR_TOOLS", "0")
    agent = build_agent(mode="quick", api_key="test-key")
    assert set(agent.tools.get_tool_names()) == {"web_search"}


@pytest.mark.asyncio
async def test_current_user_message_gates_write_confirmation():
    """未带确认词的当前消息 → 写工具拒绝执行（证明 message 注入生效）。"""
    agent = build_agent(
        mode="quick",
        api_key="test-key",
        current_user_message="停止因子研发",
    )
    tool = agent.tools.tools["set_factor_research_running"]

    preview = json.loads(await tool(False))
    assert preview["status"] == "confirmation_required"
    out = json.loads(await tool(False, confirm=True, token=preview["token"]))
    assert out["ok"] is False
    assert "确认" in out["error"]
