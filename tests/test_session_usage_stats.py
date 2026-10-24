# tests/test_session_usage_stats.py
"""add-composer-stat-pills：会话用量记账与 stats 聚合。

契约：
- 新会话四个计量列为 0（旧库迁移后同样为 0）；
- ``update_session_usage`` 累加 token、按最大值更新上下文峰值；
- ``session_stats`` 返回轮次（chat_history user 条数）、步骤（node_start 去重节点数）、
  耗时与用量；会话不存在返回 None；
- 事件里 ``node_id`` / ``node`` 两种键都能识别（历史事件形态差异）。
"""

from __future__ import annotations

from finance_agent import session_store


def _new_session(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
    session_store.init_db()
    return session_store.create_session(
        stock_code="600519", stock_name="贵州茅台", status="completed"
    )


def test_new_session_usage_defaults_to_zero(tmp_path, monkeypatch):
    sid = _new_session(tmp_path, monkeypatch)
    row = session_store.get_session(sid)
    assert row is not None
    assert row["tokens_prompt"] == 0
    assert row["tokens_completion"] == 0
    assert row["tokens_total"] == 0
    assert row["context_peak_tokens"] == 0


def test_update_session_usage_sums_tokens_and_keeps_peak(tmp_path, monkeypatch):
    sid = _new_session(tmp_path, monkeypatch)

    session_store.update_session_usage(sid, prompt=100, completion=20, total=120, context_peak=100)
    session_store.update_session_usage(sid, prompt=50, completion=10, total=60, context_peak=40)

    row = session_store.get_session(sid)
    assert row is not None
    assert (row["tokens_prompt"], row["tokens_completion"], row["tokens_total"]) == (
        150,
        30,
        180,
    )
    # 峰值取最大，不是累加
    assert row["context_peak_tokens"] == 100


def test_session_stats_counts_turns_and_distinct_steps(tmp_path, monkeypatch):
    sid = _new_session(tmp_path, monkeypatch)
    session_store.append_chat(sid, role="user", content="分析茅台")
    session_store.append_chat(sid, role="assistant", content="好的")
    session_store.append_chat(sid, role="user", content="再看风险")

    session_store.append_session_event(sid, {"type": "node_start", "node_id": "check_cache"})
    session_store.append_session_event(sid, {"type": "node_start", "node_id": "check_cache"})
    session_store.append_session_event(sid, {"type": "node_start", "node": "trader"})
    session_store.append_session_event(sid, {"type": "chat_token", "token": "x"})

    stats = session_store.session_stats(sid)
    assert stats is not None
    assert stats["turns"] == 2
    assert stats["steps"] == 2  # check_cache 去重 + trader


def test_session_stats_returns_none_for_unknown_session(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
    session_store.init_db()
    assert session_store.session_stats("does-not-exist") is None


def test_chat_session_steps_fall_back_to_assistant_messages(tmp_path, monkeypatch):
    """quick 会话没有管线 node_start：steps 回退为 assistant 消息数（模型步）。"""
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
    session_store.init_db()
    sid = session_store.create_chat_session("快速会话")
    session_store.append_chat(sid, role="user", content="茅台怎么样")
    session_store.append_chat(sid, role="assistant", content="茅台近期…")
    session_store.append_chat(sid, role="user", content="那风险呢")
    session_store.append_chat(sid, role="assistant", content="风险主要在…")

    stats = session_store.session_stats(sid)
    assert stats is not None
    assert stats["turns"] == 2
    assert stats["steps"] == 2
