"""ground-comparative-delta-claims Task 1：derived_series 通道 + 技术面 context 端到端。

教训（incident 027 同构）：toolize 验证只在节点函数层用 dict state 测了「注入」，
没走编译图——而真实图中键未声明会被图合并静默丢弃。本文件必须走编译图。
"""

import os

import pandas as pd

# 测试导入链可能触发 Langfuse 初始化；清空密钥避免连接拖慢（同 test_pipeline_stub 手法）
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)


def _kline(n=80, base=100.0, trend=1.0):
    """确定性 K 线（同 tests/metrics/test_levels.py 手法）。

    TESTING=1 的 fetch stub 不含 kline → compute 不产 technical_indicators
    （派生值块与指标块同段渲染，指标空则该段整体跳过）。初始 state 注入
    kline 后，编译图真实走 calc_technical/calc_derived_series 产出派生值，
    本文件才真正验到「派生值经图合并存活并进入 context」。
    """
    rows = []
    for i in range(n):
        close = base + trend * i
        rows.append(
            {
                "日期": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "开盘": close - 0.5,
                "收盘": close,
                "最高": close + 1.0,
                "最低": close - 1.0,
                "成交量": 1000,
            }
        )
    return pd.DataFrame(rows)


class TestDerivedSeriesChannel:
    def test_graph_channels_declare_derived_series(self):
        from finance_agent.graph import build_5layer_graph

        channels = set(build_5layer_graph().builder.channels)
        assert "derived_series" in channels, "AnalysisState 缺少声明: derived_series"

    def test_derived_series_reaches_technical_context_in_real_graph(self, monkeypatch):
        """TESTING=1 确定性 stub 下跑通编译图：派生值存活且进入技术面 context。"""
        monkeypatch.setenv("TESTING", "1")
        import finance_agent.nodes.analysts as analysts_mod

        captured: dict = {}
        original = analysts_mod._build_technical_context

        def spy(state):
            ctx = original(state)
            captured.setdefault("runs", []).append((bool(state.get("derived_series")), ctx))
            return ctx

        monkeypatch.setattr(analysts_mod, "_build_technical_context", spy)
        from finance_agent.graph import build_5layer_graph

        result = build_5layer_graph().invoke(
            {"stock_code": "600519", "stock_name": "贵州茅台", "kline": _kline()},
            config={"recursion_limit": 100},
        )
        assert result.get("derived_series"), "derived_series 被图合并丢弃（未声明 channel）"
        runs = captured.get("runs") or []
        assert runs, "技术分析师 context 未构建"
        state_has_derived, ctx = runs[0]
        assert state_has_derived is True
        assert "常用派生值" in ctx
        assert "derived_series." in ctx

    def test_derived_claim_resolves_and_passes(self):
        """以 derived_series.<字段> 为 field_ref 的数值 claim 可解析并通过校验。"""
        from finance_agent.citation import Claim as CitationClaim
        from finance_agent.citation import verify_claims

        state = {"derived_series": {"chg_5d": 0.03}}
        claim = CitationClaim(
            claim_type="numerical",
            source_type="data",
            field_ref="derived_series.chg_5d",
            stated_value=0.03,
            interpretation="近 5 日涨跌幅 0.03",
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "PASS"
