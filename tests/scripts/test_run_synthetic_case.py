"""合成案例运行器单测（mock 图，不真跑 LLM）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.run_synthetic_case import _parse_mapping, run_case


def test_parse_mapping_covers_seven_variants():
    m = _parse_mapping(
        [
            Path("d/trader_bprime.md"),
            Path("d/risk_judge_bprime.md"),
            Path("d/fund_manager_bprime.md"),
            Path("d/fundamental_analyst_bprime.md"),
            Path("d/technical_analyst_bprime.md"),
            Path("d/macro_analyst_bprime.md"),
            Path("d/sentiment_analyst_bprime.md"),
        ]
    )
    assert m == {
        "trader": "trader_bprime.md",
        "risk_judge": "risk_judge_bprime.md",
        "fund_manager": "fund_manager_bprime.md",
        "fundamental_analyst": "fundamental_analyst_bprime.md",
        "technical_analyst": "technical_analyst_bprime.md",
        "macro_analyst": "macro_analyst_bprime.md",
        "sentiment_analyst": "sentiment_analyst_bprime.md",
    }


def test_parse_mapping_rejects_unknown():
    with pytest.raises(ValueError):
        _parse_mapping([Path("d/unknown_bprime.md")])


def test_run_case_returns_decision_and_layers(monkeypatch):
    import scripts.run_synthetic_case as mod

    class FakeGraph:
        def invoke(self, state):
            return {
                "final_trade_decision": SimpleNamespace(
                    model_dump=lambda mode="python": {"action": "buy", "confidence": 0.72}
                ),
                "analyst_reports": {"fundamental": "ok"},
                "kline": "SHOULD_BE_OMITTED",
            }

    monkeypatch.setattr(mod, "build_variant_graph", lambda variant: FakeGraph())
    out = run_case("perfect")
    assert out["case"] == "perfect"
    assert out["decision"]["action"] == "buy"
    assert out["layers"]["analyst_reports"] == {"fundamental": "ok"}
    assert "SHOULD_BE_OMITTED" not in str(out["layers"])


def test_dump_layers_keeps_all_analyst_reports_lossless(monkeypatch):
    """四个分析师报告不得被 8000 字截断，非可序列化对象降级为字符串而非丢层。"""
    import scripts.run_synthetic_case as mod

    long_text = {name: f"{name}:" + ("x" * 3000) for name in ("fundamental", "technical", "macro")}
    long_text["sentiment"] = "sentiment:" + ("y" * 3500)

    class FakeGraph:
        def invoke(self, state):
            return {
                "final_trade_decision": SimpleNamespace(
                    model_dump=lambda mode="python": {"action": "watch"}
                ),
                "analyst_reports": {**long_text, "widget": SimpleNamespace(a=1)},
                "kline": "SHOULD_BE_OMITTED",
            }

    monkeypatch.setattr(mod, "build_variant_graph", lambda variant: FakeGraph())
    out = run_case("perfect")
    reports = out["layers"]["analyst_reports"]
    assert set(long_text) <= set(reports)  # 四个分析师 key 全部保留
    assert len("".join(long_text.values())) > 10_000
    for name, text in long_text.items():
        assert reports[name] == text  # 长文本逐字保留，无截断
    assert isinstance(reports["widget"], str)
    assert "namespace(a=1)" in reports["widget"]
    assert out["layers"]["kline"] == "<str omitted>"  # _OMIT 占位仍在
    json.dumps(out["layers"], ensure_ascii=False)  # 层 dump 整体可直接落盘
