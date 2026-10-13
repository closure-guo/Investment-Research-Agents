"""add-analyst-factor-tools Task 2/3：技术分析师因子库工具接线测试。

- 带工具的节点把工具输出 JSON 注入自己的上下文；裸变体（消融对照臂）不调用工具、
  不注入任何因子库段。
- 工具故障 / 无标的 → 如实标注或跳过，**不阻断**分析（节点照常产出报告）。
- 其他三个分析师上下文零改动（不含工具输出）。
"""

from __future__ import annotations

import json

from finance_agent.nodes import analysts
from finance_agent.nodes.analysts import (
    _build_fundamental_context,
    _build_macro_context,
    _build_sentiment_context,
)

_PAYLOAD = json.dumps(
    {
        "symbol": "600519",
        "as_of": "2026-01-25",
        "factors": [
            {
                "factor_id": 1,
                "expression": "delta(close,20)",
                "category": "动量",
                "value": 20.0,
                "zscore": -0.22,
                "ic_mean_60d": 0.2,
                "icir_60d": 2.0,
            }
        ],
    },
    ensure_ascii=False,
)

_STATE = {"stock_name": "贵州茅台", "stock_code": "600519", "technical_indicators": {}}


def _report_json() -> str:
    return json.dumps(
        {
            "agent_name": "technical",
            "summary": "技术面分析显示短期趋势向上",
            "plain_conclusion": "技术面偏多",
            "key_findings": ["MA5 上穿 MA20"],
            "claims": [],
            "markdown": "## 技术面分析\n短期趋势向上。",
        },
        ensure_ascii=False,
    )


def _capture_prompts(monkeypatch) -> list[str]:
    """替换 LLM 调用：捕获节点实际发送的 context。"""
    prompts: list[str] = []

    def fake_llm(prompt: str, **kwargs) -> str:
        prompts.append(prompt)
        return _report_json()

    monkeypatch.setattr(analysts, "call_llm_streaming_with_fallback", fake_llm)
    return prompts


class TestFactorToolInjection:
    def test_tool_payload_injected_verbatim(self, monkeypatch):
        monkeypatch.setattr(analysts, "_factor_library_tool", lambda: lambda symbol: _PAYLOAD)
        prompts = _capture_prompts(monkeypatch)
        analysts.make_technical_analyst(with_factor_tool=True)(dict(_STATE))
        context = prompts[0]
        assert "query_factor_library" in context  # 工具名对 LLM 可见（出处可溯）
        assert _PAYLOAD in context  # 工具输出逐字注入（不做二次加工）

    def test_tool_called_with_stock_code(self, monkeypatch):
        calls: list[dict] = []

        def stub(symbol: str) -> str:
            calls.append({"symbol": symbol})
            return _PAYLOAD

        monkeypatch.setattr(analysts, "_factor_library_tool", lambda: stub)
        _capture_prompts(monkeypatch)
        analysts.make_technical_analyst(with_factor_tool=True)(dict(_STATE))
        assert calls == [{"symbol": "600519"}]

    def test_bare_variant_skips_tool_entirely(self, monkeypatch):
        """消融裸对照臂：不调用工具，也不出现因子库段（单变量差异）。"""
        calls: list[str] = []
        monkeypatch.setattr(
            analysts,
            "_factor_library_tool",
            lambda: lambda symbol: calls.append(symbol) or _PAYLOAD,
        )
        prompts = _capture_prompts(monkeypatch)
        analysts.make_technical_analyst(with_factor_tool=False)(dict(_STATE))
        assert calls == []
        assert "query_factor_library" not in prompts[0]

    def test_production_node_binds_factor_tool(self):
        """生产入口（主图/消融 tech_factor_tool 臂）确为带工具构造。"""
        assert analysts.technical_analyst.factor_tool_enabled is True

    def test_missing_stock_code_skips_tool(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            analysts,
            "_factor_library_tool",
            lambda: lambda symbol: calls.append(symbol) or _PAYLOAD,
        )
        prompts = _capture_prompts(monkeypatch)
        analysts.make_technical_analyst(with_factor_tool=True)(
            {"stock_name": "X", "technical_indicators": {}}
        )
        assert calls == []
        assert "query_factor_library" not in prompts[0]

    def test_tool_failure_degrades_without_breaking(self, monkeypatch):
        """工具故障 → 上下文如实标注不可用，分析照常产出（旁路铁律）。"""

        def boom(symbol: str) -> str:
            raise RuntimeError("库不可用")

        monkeypatch.setattr(analysts, "_factor_library_tool", lambda: boom)
        prompts = _capture_prompts(monkeypatch)
        result = analysts.make_technical_analyst(with_factor_tool=True)(dict(_STATE))
        assert "query_factor_library 不可用" in prompts[0]
        assert "RuntimeError" in prompts[0]
        assert result["analyst_reports"]["technical"].agent_name == "technical"

    def test_tool_absent_from_toolset_skips(self, monkeypatch):
        """工具集声明里没有该工具（返回 None）→ 不调用、不注入（声明为唯一开关）。"""
        monkeypatch.setattr(analysts, "_factor_library_tool", lambda: None)
        prompts = _capture_prompts(monkeypatch)
        analysts.make_technical_analyst(with_factor_tool=True)(dict(_STATE))
        assert "query_factor_library" not in prompts[0]


class TestOtherAnalystsUnchanged:
    def test_other_contexts_have_no_factor_section(self):
        """非目标：其他三个分析师上下文零改动（无因子库段）。"""
        state = {"stock_name": "X", "stock_code": "600519"}
        for builder in (_build_macro_context, _build_fundamental_context, _build_sentiment_context):
            context = builder(dict(state))
            assert "query_factor_library" not in context
