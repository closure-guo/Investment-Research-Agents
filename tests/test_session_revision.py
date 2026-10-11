"""终稿决策持久化与同标的回溯查询测试（add-report-revision-view Task 1/2）。

对应 delta specs:
- session-persistence「终稿决策随报告落库」：update_session_report 持久化
  final_trade_decision JSON（含在场价位/触发位字段）；无终稿时落 NULL 不报错。
- session-persistence「回溯取最近完成报告」：按 stock_code 取最近一条 completed
  且非自身的会话，MUST NOT 返回 running/failed 行。
- 「无历史报告返回 None」。
"""

from __future__ import annotations

import json

from finance_agent import session_store


def _make_db(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
    session_store.init_db()


def test_final_trade_decision_persisted_on_report(tmp_path, monkeypatch):
    """完成落库时 final_trade_decision 随会话行持久化（含触发位字段）。"""
    _make_db(tmp_path, monkeypatch)
    sid = session_store.create_session(stock_code="601066", stock_name="中信建投", status="running")

    decision = {
        "action": "watch",
        "confidence": 0.6,
        "entry_price": None,
        "stop_loss": None,
        "target_price": None,
        "trigger_low": 22.91,
        "trigger_high": 24.6,
    }
    ok = session_store.update_session_report(
        sid,
        report_markdown="# 报告",
        final_trade_decision=decision,
        status="completed",
    )
    assert ok is True

    row = session_store.get_session(sid)
    assert row is not None
    persisted = json.loads(row["final_trade_decision"])
    assert persisted["action"] == "watch"
    assert persisted["confidence"] == 0.6
    assert persisted["trigger_low"] == 22.91
    assert persisted["trigger_high"] == 24.6


def test_final_trade_decision_null_when_absent(tmp_path, monkeypatch):
    """管线阻断/未审批无终稿时字段落 NULL，落库不报错（MUST NOT 以 trader plan 冒充）。"""
    _make_db(tmp_path, monkeypatch)
    sid = session_store.create_session(stock_code="600519", stock_name="贵州茅台", status="running")

    ok = session_store.update_session_report(sid, report_markdown="# 报告", status="completed")
    assert ok is True

    row = session_store.get_session(sid)
    assert row is not None
    assert row["final_trade_decision"] is None


def test_final_trade_decision_accepts_pydantic_model(tmp_path, monkeypatch):
    """真实管线形态回归：accumulated 中 final_trade_decision 是 TradeDecision
    pydantic 对象（非 dict）——持久化必须先 model_dump 再序列化，
    否则落成 repr 串导致回溯侧 json.loads 失败恒「未申报」。"""
    from finance_agent.models import TradeDecision

    _make_db(tmp_path, monkeypatch)
    sid = session_store.create_session(stock_code="601066", stock_name="中信建投", status="running")
    decision = TradeDecision(
        action="watch",
        confidence=0.55,
        reasoning="多方空方各执",
        trigger_low=22.91,
        trigger_high=24.60,
    )
    ok = session_store.update_session_report(
        sid, report_markdown="# 报告", final_trade_decision=decision, status="completed"
    )
    assert ok is True

    row = session_store.get_session(sid)
    persisted = json.loads(row["final_trade_decision"])  # 必须是可解析 JSON
    assert persisted["action"] == "watch"
    assert persisted["trigger_low"] == 22.91
    assert persisted["trigger_high"] == 24.60


def test_previous_completed_session_skips_running_failed_and_self(tmp_path, monkeypatch):
    """回溯查询返回最近一条 completed（09-30），排除 running 行与调用会话自身。"""
    _make_db(tmp_path, monkeypatch)
    s1 = session_store.create_session(
        stock_code="601066", stock_name="中信建投", status="completed"
    )
    s2 = session_store.create_session(
        stock_code="601066", stock_name="中信建投", status="completed"
    )
    s3 = session_store.create_session(stock_code="601066", stock_name="中信建投", status="running")
    cur = session_store.create_session(
        stock_code="601066", stock_name="中信建投", status="completed"
    )
    # 固定时间序：09-28 / 09-30 / 10-08(running) / 10-09(自身)
    conn = session_store._get_db()
    for sid, ts in [
        (s1, "2026-09-28T16:00:00"),
        (s2, "2026-09-30T16:00:00"),
        (s3, "2026-10-08T16:00:00"),
        (cur, "2026-10-09T16:00:00"),
    ]:
        conn.execute("UPDATE sessions SET created_at = ? WHERE session_id = ?", (ts, sid))
    conn.execute(
        "UPDATE sessions SET final_trade_decision = ? WHERE session_id = ?",
        (json.dumps({"action": "watch", "confidence": 0.55}), s2),
    )
    conn.execute(
        "UPDATE sessions SET chart_data = ? WHERE session_id = ?",
        (json.dumps({"kpi": {"current_price": 23.03, "pe": 15.2}}), s2),
    )
    conn.commit()
    conn.close()

    prev = session_store.get_previous_completed_session("601066", cur)
    assert prev is not None
    assert prev["session_id"] == s2
    assert prev["created_at"].startswith("2026-09-30")
    assert prev["final_trade_decision"]["action"] == "watch"
    assert prev["kpi"]["current_price"] == 23.03


def test_previous_completed_session_returns_none_without_history(tmp_path, monkeypatch):
    """无其他 completed 会话时返回 None（首份报告），不报错。"""
    _make_db(tmp_path, monkeypatch)
    cur = session_store.create_session(stock_code="600519", stock_name="贵州茅台", status="running")

    assert session_store.get_previous_completed_session("600519", cur) is None


def test_previous_completed_session_failed_not_returned(tmp_path, monkeypatch):
    """failed 行不参与回溯（MUST NOT 返回 running/failed）。"""
    _make_db(tmp_path, monkeypatch)
    failed = session_store.create_session(
        stock_code="601066", stock_name="中信建投", status="failed"
    )
    cur = session_store.create_session(
        stock_code="601066", stock_name="中信建投", status="completed"
    )
    conn = session_store._get_db()
    conn.execute(
        "UPDATE sessions SET created_at = '2026-10-01T10:00:00' WHERE session_id = ?", (failed,)
    )
    conn.execute(
        "UPDATE sessions SET created_at = '2026-10-09T10:00:00' WHERE session_id = ?", (cur,)
    )
    conn.commit()
    conn.close()

    assert session_store.get_previous_completed_session("601066", cur) is None
