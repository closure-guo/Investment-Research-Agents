"""add-track-record 补丁:共享落库入口(ReAct 深模式与旧路径共用)。

真实事故回归:ReAct 的 run_deep_analysis 路径此前无落库挂点,深度分析完成后
predictions 恒为 0。本测试模拟该路径的 accumulated(含 stock_quote + 决策),
验证全量记录真正写入。
"""

from finance_agent.outcome.track_record.ingest import persist_prediction_from_accumulated
from finance_agent.outcome.track_record.model import init_predictions, list_predictions


def _db(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    init_predictions(db)
    monkeypatch.setattr("finance_agent.outcome.track_record.model._default_db_path", lambda: db)
    return db


def _reat_accumulated(**overrides):
    """ReAct 深模式 completion 时的 accumulated(工具 _merge_update 全量合并后)。"""
    base = {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "stock_quote": {"price": 100.0},
        "final_trade_decision": {
            "action": "buy",
            "confidence": 0.8,
            "entry_price": None,
            "stop_loss": 90.0,
            "target_price": 120.0,
            "position_size": "30%",
        },
        "fund_manager_decision": "approve",
        "langfuse_trace_id": "trace-deep-1",
    }
    base.update(overrides)
    return base


def test_deep_mode_approve_buy_recorded(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    persist_prediction_from_accumulated(_reat_accumulated(), "sess-deep-1", "600519", "贵州茅台")
    rows = list_predictions(db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["direction"] == "long" and row["source_type"] == "live"
    assert row["entry_price"] == 100.0  # stock_quote 回填
    assert row["status"] == "open"
    assert row["langfuse_trace_id"] == "trace-deep-1"


def test_deep_mode_reject_hold_recorded_neutral(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    persist_prediction_from_accumulated(
        _reat_accumulated(
            final_trade_decision={"action": "hold", "confidence": 0.5, "entry_price": None},
            fund_manager_decision="reject",
        ),
        "sess-deep-1",
        "600519",
        "贵州茅台",
    )
    rows = list_predictions(db_path=db)
    assert len(rows) == 1
    assert rows[0]["direction"] == "neutral"
    assert rows[0]["status"] == "open"


def test_deep_mode_no_quote_no_kline_archives_open_with_warn(monkeypatch, tmp_path, caplog):
    """深模式无参考价：状态 open（不再 unresolvable）+ WARN 留痕。

    delta update-decision-settlement-contract：判定不依赖参考价，结算入场价届时由行情派生。
    """
    import logging

    db = _db(monkeypatch, tmp_path)
    with caplog.at_level(logging.WARNING, logger="finance_agent.track_record.ingest"):
        persist_prediction_from_accumulated(
            _reat_accumulated(stock_quote=None),
            "sess-deep-1",
            "600519",
            "贵州茅台",
        )
    rows = list_predictions(db_path=db)
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert rows[0]["entry_price"] is None
    assert any("参考价不可得" in r.message for r in caplog.records)


def test_no_decision_skips(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    persist_prediction_from_accumulated(
        _reat_accumulated(final_trade_decision=None),
        "sess-deep-1",
        "600519",
        "贵州茅台",
    )
    assert list_predictions(db_path=db) == []


def test_pydantic_trade_decision_recorded(monkeypatch, tmp_path):
    """真实管线 state 的 final_trade_decision 是 pydantic TradeDecision 对象（非 dict）。

    线上事故回归：ingest 用 decision.get() 访问 pydantic 对象抛 AttributeError，
    被旁路吞掉 → 深度分析完成后 predictions 恒为 0（历史战绩空）。
    """
    from finance_agent.models import TradeDecision

    db = _db(monkeypatch, tmp_path)
    model_decision = TradeDecision(
        action="buy",
        confidence=0.8,
        reasoning="x",
        entry_price=100.0,
        stop_loss=90.0,
        target_price=120.0,
    )
    persist_prediction_from_accumulated(
        _reat_accumulated(final_trade_decision=model_decision),
        "sess-deep-1",
        "600519",
        "贵州茅台",
    )
    rows = list_predictions(db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["direction"] == "long"
    assert row["entry_price"] == 100.0  # quote 优先于模型 entry_price
    assert row["target_price"] == 120.0
    assert row["confidence"] == 0.8
    # 申报价位（含 stop_loss）冻结入快照：pydantic 路径同样富化
    import json

    assert json.loads(row["rationale_snapshot"])["declared_prices"] == {
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "target_price": 120.0,
    }
