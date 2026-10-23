"""系统操作工具能力门禁（add-quick-mode-operator）。"""

import pytest

from finance_agent.operator.gating import operator_tools_enabled


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        (None, True),
        ({}, True),
        ({"tool_call": True}, True),
        ({"tool_call": False}, False),
    ],
)
def test_capability_gate(capability, expected):
    assert operator_tools_enabled(capability) is expected


@pytest.mark.parametrize("env_value", ["0", "false", "off", "no"])
def test_env_switch_disables(env_value):
    assert operator_tools_enabled({"tool_call": True}, env_value=env_value) is False


def test_env_switch_defaults_on(monkeypatch):
    monkeypatch.delenv("QUICK_OPERATOR_TOOLS", raising=False)
    assert operator_tools_enabled(None) is True
