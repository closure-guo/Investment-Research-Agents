"""工具注册表权威源测试（harden-eval-implementation-decoupling）。

评估允许集与 agent 实际注册必须共享 tool_registry 单一来源，防止漂移。
"""

import re
from pathlib import Path

from evals.toolcall.measure import DEFAULT_ALLOWED_TOOLS

from finance_agent.tool_registry import AGENT_TOOL_NAMES


class TestToolRegistry:
    def test_measure_default_matches_registry(self):
        """toolcall 评估默认允许集 = 权威注册表（评估不维护独立副本）。"""
        assert DEFAULT_ALLOWED_TOOLS == AGENT_TOOL_NAMES

    def test_agent_factory_registers_only_registry_tools(self):
        """agent_factory 注册的工具名均 ∈ 注册表（无评估外注册的工具）。"""
        src = Path("src/finance_agent/agent_factory.py").read_text(encoding="utf-8")
        # 常量引用形式：_trace_tool(TOOL_X) / name=TOOL_X
        import finance_agent.tool_registry as tr

        refs = {
            g for pair in re.findall(r"_trace_tool\((\w+)\)|name=(\w+)", src) for g in pair if g
        }
        names = {getattr(tr, r) for r in refs if r and hasattr(tr, r)}
        assert names, "未解析到注册引用"
        assert names <= AGENT_TOOL_NAMES, names - AGENT_TOOL_NAMES

    def test_registry_fully_registered(self):
        """注册表每个工具名都在 agent_factory 中出现（无"评估声明了但 agent 未注册"）。

        P2/P3 起快速模式系统工具经 make_read_tools()/make_write_tools() 动态注册，
        源码扫描拿不到这些名字，故把动态工厂的键并入实际注册集合。
        """
        src = Path("src/finance_agent/agent_factory.py").read_text(encoding="utf-8")
        import finance_agent.tool_registry as tr
        from finance_agent.operator.reads import make_read_tools
        from finance_agent.operator.writes import make_write_tools

        refs = {
            g for pair in re.findall(r"_trace_tool\((\w+)\)|name=(\w+)", src) for g in pair if g
        }
        names = {getattr(tr, r) for r in refs if r and hasattr(tr, r)}
        names |= set(make_read_tools())
        names |= set(make_write_tools(session_id="", user_message=""))
        assert names >= AGENT_TOOL_NAMES, AGENT_TOOL_NAMES - names
