"""回放与决策一致性测试：mock 编排图，不调 LLM。

Δ2 Task 6：结算切 track-record 同源判定（`judgment.resolve_prediction`），
断言 `Resolution` → 下游既有契约键（settle_date/settle_price/decision_return/
benchmark_return/decision_excess/decision_hit）的映射，以及派生入场价。
"""

from __future__ import annotations

import json

import evals.backtest.replay as rp
import pandas as pd
import pytest
from evals.backtest.data_snapshot import SnapshotResult
from evals.backtest.replay import direction_agreement


def _kline(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": dates,
            "开盘": closes,
            "收盘": closes,
            "最高": [c + 0.5 for c in closes],
            "最低": [c - 0.5 for c in closes],
        }
    )


def _dated_kline(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """判定只读「日期/收盘」；OHLC 同值仅为形状完整。"""
    return pd.DataFrame(
        {
            "日期": [r[0] for r in rows],
            "开盘": [r[1] for r in rows],
            "收盘": [r[1] for r in rows],
            "最高": [r[1] for r in rows],
            "最低": [r[1] for r in rows],
        }
    )


def _stub_graph(monkeypatch, decision: dict) -> None:
    """以桩替换完整编排/compute_metrics，返回给定 TradeDecision dict。"""

    class FakeGraph:
        def invoke(self, state):
            return {"final_trade_decision": decision, "final_report": "report"}

    monkeypatch.setattr(rp, "build_variant_graph", lambda variant: FakeGraph())
    monkeypatch.setattr(rp, "compute_metrics", lambda state: {})


def _horizon_rows(entry_close: float, exit_close: float) -> list[tuple[str, float]]:
    """决策日 2026-09-02 + 其后 20 个交易日（hold_days=20，出场日 2026-10-20）。"""
    return [("2026-09-02", entry_close)] + [(f"2026-10-{d:02d}", exit_close) for d in range(1, 21)]


class TestSettlementMapping:
    """同源判定：Resolution → settlement 契约键（Task 6 核心）。"""

    def test_replay_settlement_maps_shared_judgment(self, monkeypatch):
        _stub_graph(monkeypatch, {"action": "buy", "stop_loss": 10.0, "target_price": 14.0})
        kline = _dated_kline(_horizon_rows(11.0, 12.1))
        snap = SnapshotResult(state={"kline": kline}, metadata={"as_of": "2026-09-02"})
        result = rp.replay_decision("600519", "2026-09-02", snapshot=snap, full_kline=kline)

        settlement = result["settlement"]
        # 20 个交易日后 12.1/+10% → resolved_win；无基准 → 超额不可得
        assert settlement["status"] == "resolved_win"
        assert settlement["settle_date"] == "2026-10-20"
        assert settlement["settle_price"] == 12.1
        assert settlement["hold_days"] == 20
        assert settlement["decision_return"] == pytest.approx(12.1 / 11.0 - 1.0, abs=1e-6)
        assert settlement["decision_hit"] is True
        # 入场基准由 K 线派生（决策日收盘），非存档参考价
        assert result["entry_price"] == 11.0

    def test_replay_settlement_benchmark_missing_excess_is_none(self, monkeypatch):
        """基准缺失 → benchmark_return/decision_excess 记 None（不当作 0），与旧 settle 契约一致。"""
        _stub_graph(monkeypatch, {"action": "buy"})
        kline = _dated_kline(_horizon_rows(11.0, 12.1))
        snap = SnapshotResult(state={"kline": kline}, metadata={})
        result = rp.replay_decision("600519", "2026-09-02", snapshot=snap, full_kline=kline)

        settlement = result["settlement"]
        assert settlement["benchmark_return"] is None
        assert settlement["decision_excess"] is None

    def test_replay_settlement_with_flat_benchmark_splits_excess(self, monkeypatch):
        """平坦基准：benchmark_return ≈ 0，超额 = raw（拆分保留下游两键语义）。"""
        _stub_graph(monkeypatch, {"action": "buy"})
        kline = _dated_kline(_horizon_rows(11.0, 12.1))
        bench = _dated_kline([("2026-09-02", 3000.0), ("2026-10-20", 3000.0)])
        snap = SnapshotResult(state={"kline": kline}, metadata={})
        result = rp.replay_decision(
            "600519", "2026-09-02", snapshot=snap, full_kline=kline, full_benchmark=bench
        )

        settlement = result["settlement"]
        assert settlement["benchmark_return"] == 0.0
        assert settlement["decision_excess"] == pytest.approx(12.1 / 11.0 - 1.0, abs=1e-6)
        assert settlement["decision_excess"] == pytest.approx(
            settlement["decision_return"] - settlement["benchmark_return"], abs=1e-9
        )

    def test_replay_settlement_benchmark_not_covering_window_excess_is_none(self, monkeypatch):
        """基准非空但起始晚于出场日 → 未覆盖持有窗口 → 超额仍记 None（不回落成 0）。"""
        _stub_graph(monkeypatch, {"action": "buy"})
        kline = _dated_kline(_horizon_rows(11.0, 12.1))
        bench = _dated_kline([("2026-11-02", 3000.0), ("2026-11-03", 3050.0)])
        snap = SnapshotResult(state={"kline": kline}, metadata={})
        result = rp.replay_decision(
            "600519", "2026-09-02", snapshot=snap, full_kline=kline, full_benchmark=bench
        )

        settlement = result["settlement"]
        assert settlement["benchmark_return"] is None
        assert settlement["decision_excess"] is None

    def test_replay_neutral_action_maps_avoidance(self, monkeypatch):
        """hold/watch → direction=neutral → 判定结果为 avoidance_*（回避正确）。"""
        _stub_graph(monkeypatch, {"action": "hold"})
        kline = _dated_kline(_horizon_rows(11.0, 9.9))
        snap = SnapshotResult(state={"kline": kline}, metadata={})
        result = rp.replay_decision("600519", "2026-09-02", snapshot=snap, full_kline=kline)

        assert result["action"] == "hold"
        assert result["settlement"]["status"] == "avoidance_win"
        assert result["entry_price"] == 11.0  # 派生入场仍为决策日收盘

    def test_replay_sell_maps_short_direction(self, monkeypatch):
        """sell → direction=short → 下跌（−10%）符号化为 +10% 收益 → resolved_win。"""
        _stub_graph(monkeypatch, {"action": "sell"})
        kline = _dated_kline(_horizon_rows(11.0, 9.9))
        snap = SnapshotResult(state={"kline": kline}, metadata={})
        result = rp.replay_decision("600519", "2026-09-02", snapshot=snap, full_kline=kline)

        assert result["settlement"]["status"] == "resolved_win"
        assert result["settlement"]["decision_return"] == pytest.approx(0.1, abs=1e-6)


class TestSettlementKlineCaliber:
    """口径护栏：结算必须用 hfq 全量 K 线，快照 kline（管线 qfq）不得混用。"""

    def test_snapshot_kline_fallback_raises(self, monkeypatch):
        _stub_graph(monkeypatch, {"action": "buy"})
        snapshot_kline = _dated_kline([("2026-09-02", 11.0), ("2026-09-03", 12.0)])
        snap = SnapshotResult(state={"kline": snapshot_kline}, metadata={})
        with pytest.raises(ValueError, match="hfq 全量 K 线"):
            rp.replay_decision("600519", "2026-09-02", snapshot=snap)

    def test_no_kline_channel_returns_no_settlement(self, monkeypatch):
        """既无全量也无快照 K 线 → 不抛，保持既有「无结算」返回形态。"""
        _stub_graph(monkeypatch, {"action": "buy"})
        snap = SnapshotResult(state={}, metadata={})
        result = rp.replay_decision("600519", "2026-09-02", snapshot=snap)
        assert result["settlement"] is None
        assert result["entry_price"] is None

    def test_resolution_none_keeps_no_settlement_shape(self, monkeypatch):
        """未到 horizon（resolve_prediction → None）→ settlement/entry_price 均 None。"""
        _stub_graph(monkeypatch, {"action": "buy"})
        kline = _dated_kline([("2026-09-02", 11.0), ("2026-09-03", 12.0)])
        snap = SnapshotResult(state={"kline": kline}, metadata={})
        result = rp.replay_decision("600519", "2026-09-02", snapshot=snap, full_kline=kline)
        assert result["settlement"] is None
        assert result["entry_price"] is None
        assert result["action"] == "buy"

    def test_empty_full_kline_returns_no_settlement(self, monkeypatch):
        """空全量 K 线（缺 日期 列）→ 走「无结算」优雅返回，不以 KeyError 逃逸。"""
        _stub_graph(monkeypatch, {"action": "buy"})
        snapshot_kline = _dated_kline([("2026-09-02", 11.0), ("2026-09-03", 12.0)])
        snap = SnapshotResult(state={"kline": snapshot_kline}, metadata={})
        result = rp.replay_decision(
            "600519", "2026-09-02", snapshot=snap, full_kline=pd.DataFrame()
        )
        assert result["settlement"] is None
        assert result["entry_price"] is None


class TestConsistency:
    def test_direction_uses_judgment_single_source(self):
        """replay 的 action→direction 与 ingest/model 同源（Δ2T6 审查：消除第三份拷贝）。"""
        from finance_agent.outcome.track_record.judgment import direction_for_action

        assert rp.direction_for_action is direction_for_action

    def test_full_agreement(self):
        assert direction_agreement(["buy", "buy", "buy"]) == 1.0

    def test_two_of_three(self):
        assert direction_agreement(["buy", "buy", "sell"]) == 2 / 3

    def test_flag_below_threshold(self):
        assert direction_agreement(["buy", "sell", "hold"]) < 2 / 3


class TestActionFallback:
    def test_replay_decision_missing_action_reported_as_unknown(self, monkeypatch):
        captured: dict = {}

        class EmptyDecisionGraph:
            def invoke(self, state):
                return {"final_trade_decision": {}, "final_report": "report"}

        monkeypatch.setattr(rp, "build_variant_graph", lambda variant: EmptyDecisionGraph())
        monkeypatch.setattr(rp, "compute_metrics", lambda state: {})

        def fake_resolve(prediction, kline, benchmark):
            captured["called"] = True
            return None

        monkeypatch.setattr(rp, "resolve_prediction", fake_resolve)
        snap = SnapshotResult(state={"kline": _kline(["2025-01-14"], [10.5])}, metadata={})
        result = rp.replay_decision("600519", "2025-01-14", snapshot=snap)
        # 口径统一：action 缺失 → "unknown"（不再是 ""）
        assert result["action"] == "unknown"
        # 无有效 action 不进入结算（不触碰 K 线口径护栏）
        assert result["settlement"] is None
        assert "called" not in captured

    def test_replay_decision_accepts_pydantic_trade_decision(self, monkeypatch):
        """复现 [backtest-pilot] 缺陷：编排 state 的 final_trade_decision 是
        TradeDecision pydantic 对象（非 dict），replay 必须归一化后走结算。"""
        from finance_agent.models import TradeDecision

        captured: dict = {}

        class PydanticDecisionGraph:
            def invoke(self, state):
                return {
                    "final_trade_decision": TradeDecision(
                        action="buy", confidence=0.7, reasoning="x", stop_loss=11.0
                    ),
                    "final_report": "report",
                }

        monkeypatch.setattr(rp, "build_variant_graph", lambda variant: PydanticDecisionGraph())
        monkeypatch.setattr(rp, "compute_metrics", lambda state: {})

        def fake_resolve(prediction, kline, benchmark):
            captured["prediction"] = prediction
            return None

        monkeypatch.setattr(rp, "resolve_prediction", fake_resolve)
        snap = SnapshotResult(state={"kline": _kline(["2025-01-14"], [10.5])}, metadata={})
        full_kline = _kline(["2025-01-14", "2025-01-15"], [10.5, 10.6])
        result = rp.replay_decision("600519", "2025-01-14", snapshot=snap, full_kline=full_kline)
        assert result["action"] == "buy"
        assert captured["prediction"]["direction"] == "long"
        assert captured["prediction"]["created_at"] == "2025-01-14"
        # 返回体必须 JSON 可序列化（pydantic 对象不能原样透传）
        json.dumps(result, default=str)

    def test_consistency_counts_missing_action_as_unknown(self, monkeypatch):
        monkeypatch.setattr(
            rp, "replay_decision", lambda *a, **k: {"decision": {}, "settlement": None}
        )
        out = rp.replay_with_consistency(
            "600519", "2025-01-14", n=2, snapshot=SnapshotResult(state={}, metadata={})
        )
        assert out["actions"] == ["unknown", "unknown"]
        assert out["agreement"] == 1.0


class TestFullBenchmarkChannel:
    def _fake_orchestration(self, monkeypatch, captured: dict) -> None:
        """以桩替换完整编排/compute_metrics/同源判定，捕获 resolve_prediction 参数。"""

        class FakeGraph:
            def invoke(self, state):
                return {
                    "final_trade_decision": {
                        "action": "buy",
                        "stop_loss": 11.0,
                        "target_price": 14.0,
                    },
                    "final_report": "report",
                }

        monkeypatch.setattr(rp, "build_variant_graph", lambda variant: FakeGraph())
        monkeypatch.setattr(rp, "compute_metrics", lambda state: {})

        def fake_resolve(prediction, kline, benchmark):
            captured["benchmark"] = benchmark
            return None

        monkeypatch.setattr(rp, "resolve_prediction", fake_resolve)

    def test_replay_decision_settles_against_full_benchmark(self, monkeypatch):
        captured: dict = {}
        self._fake_orchestration(monkeypatch, captured)
        full_kline = _kline(["2025-01-13", "2025-01-14"], [10.0, 10.5])
        truncated_bench = _kline(["2025-01-13"], [3000.0])
        full_bench = _kline(["2025-01-13", "2025-01-20"], [3000.0, 3100.0])
        snap = SnapshotResult(
            state={
                "kline": _kline(["2025-01-13", "2025-01-14"], [10.0, 10.5]),
                "benchmark_kline": truncated_bench,
            },
            metadata={},
        )
        rp.replay_decision(
            "600519",
            "2025-01-14",
            snapshot=snap,
            full_kline=full_kline,
            full_benchmark=full_bench,
        )
        assert captured["benchmark"] is full_bench

    def test_replay_decision_defaults_to_state_benchmark(self, monkeypatch):
        captured: dict = {}
        self._fake_orchestration(monkeypatch, captured)
        truncated_bench = _kline(["2025-01-13"], [3000.0])
        full_kline = _kline(["2025-01-13", "2025-01-14"], [10.0, 10.5])
        snap = SnapshotResult(
            state={
                "kline": _kline(["2025-01-13", "2025-01-14"], [10.0, 10.5]),
                "benchmark_kline": truncated_bench,
            },
            metadata={},
        )
        rp.replay_decision("600519", "2025-01-14", snapshot=snap, full_kline=full_kline)
        assert captured["benchmark"] is truncated_bench

    def test_replay_with_consistency_passes_full_benchmark_through(self, monkeypatch):
        received: list = []

        def fake_replay(
            code, decision_date, *, snapshot=None, client=None, full_kline=None, full_benchmark=None
        ):
            received.append(full_benchmark)
            return {"decision": {"action": "buy"}, "settlement": None}

        monkeypatch.setattr(rp, "replay_decision", fake_replay)
        full_bench = _kline(["2025-01-20"], [3100.0])
        rp.replay_with_consistency(
            "600519",
            "2025-01-14",
            n=3,
            snapshot=SnapshotResult(state={}, metadata={}),
            full_benchmark=full_bench,
        )
        assert received == [full_bench, full_bench, full_bench]


class TestFirstReplayPassthrough:
    """Task 10：replay_with_consistency 透传首轮回放的结算上下文（entry_price/action）。"""

    def test_consistency_surfaces_first_replay_entry_and_action(self, monkeypatch):
        settlement = {
            "status": "resolved_win",
            "settle_date": "2026-10-20",
            "settle_price": 14.2,
            "hold_days": 20,
            "decision_return": 0.352,
            "benchmark_return": None,
            "decision_excess": None,
            "decision_hit": True,
        }

        def fake_replay(
            code, decision_date, *, snapshot=None, client=None, full_kline=None, full_benchmark=None
        ):
            return {
                "decision": {"action": "buy"},
                "settlement": settlement,
                "entry_price": 10.5,
                "action": "buy",
                "decision_date": decision_date,
                "snapshot_metadata": {},
            }

        monkeypatch.setattr(rp, "replay_decision", fake_replay)
        out = rp.replay_with_consistency(
            "600519", "2025-01-14", n=3, snapshot=SnapshotResult(state={}, metadata={})
        )
        assert out["entry_price"] == 10.5
        assert out["action"] == "buy"
        assert out["decision_date"] == "2025-01-14"
        assert out["settlement"] == settlement

    def test_consistency_passthrough_tolerates_missing_keys(self, monkeypatch):
        """回放失败路径（无 entry_price/action 键）→ 透传 None 而非崩溃。"""

        def fake_replay(
            code, decision_date, *, snapshot=None, client=None, full_kline=None, full_benchmark=None
        ):
            return {"decision": {}, "settlement": None}

        monkeypatch.setattr(rp, "replay_decision", fake_replay)
        out = rp.replay_with_consistency(
            "600519", "2025-01-14", n=1, snapshot=SnapshotResult(state={}, metadata={})
        )
        assert out["entry_price"] is None
        assert out["action"] is None
