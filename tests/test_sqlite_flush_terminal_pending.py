"""SQLite 连接纪律 ④：重试预算制 + flush 失败回填 + terminal_pending 旁路标记。

对应 delta harden-sqlite-connection-discipline ④（#515）。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import pytest

from finance_agent import session_store as ss
from finance_agent.data import conn as _conn
from finance_agent.stream_registry import SessionStream, StreamRegistry

# ── 连接重试预算制 ──────────────────────────────────────────────────────────


class TestGetDbRetryBudget:
    def test_retries_beyond_old_window_until_budget(self, tmp_path, monkeypatch):
        """持续瞬态失败：重试跨过旧 6 次/3.1s 窗口，按预算持续重试后抛出。"""
        monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "t.db")
        monkeypatch.setattr(ss, "_DB_CONNECT_RETRY_BUDGET_S", 0.05)
        calls = {"n": 0}

        def always_fail(*a, **kw):
            calls["n"] += 1
            raise sqlite3.OperationalError("unable to open database file")

        monkeypatch.setattr("sqlite3.connect", always_fail)
        # 真实 sleep（每次 1ms）让预算按真实时钟推进；捕获真身避免 lambda 自递归
        real_sleep = time.sleep
        monkeypatch.setattr("time.sleep", lambda s: real_sleep(0.001))
        with pytest.raises(sqlite3.OperationalError):
            ss._get_db()
        assert calls["n"] > 6, "必须突破旧固定 6 次窗口（预算制语义）"

    def test_sleep_capped(self, tmp_path, monkeypatch):
        """单次退避封顶 2s：预算大时 sleep 序列不得超过上限。"""
        monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "t.db")
        monkeypatch.setattr(ss, "_DB_CONNECT_RETRY_BUDGET_S", 30.0)
        recorded: list[float] = []

        def always_fail(*a, **kw):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr("sqlite3.connect", always_fail)
        monkeypatch.setattr("time.sleep", recorded.append)
        with pytest.raises(sqlite3.OperationalError):
            ss._get_db()
        assert recorded, "预算未耗尽前必须持续退避"
        assert max(recorded) <= _conn._RETRY_SLEEP_CAP_S + 1e-9

    def test_recovery_within_budget(self, tmp_path, monkeypatch):
        """预算内自愈：前 N 次瞬断后成功，正常返回连接。"""
        monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "t.db")
        real_connect = sqlite3.connect
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] <= 30:
                raise sqlite3.OperationalError("disk I/O error")
            return real_connect(*a, **kw)

        monkeypatch.setattr("sqlite3.connect", flaky)
        real_sleep = time.sleep
        monkeypatch.setattr("time.sleep", lambda s: real_sleep(0.001))
        monkeypatch.setattr(ss, "_DB_CONNECT_RETRY_BUDGET_S", 5.0)
        conn = ss._get_db()
        assert calls["n"] == 31
        conn.close()

    def test_non_transient_raises_immediately(self, tmp_path, monkeypatch):
        """非瞬态错误不进预算循环，立即抛出。"""
        monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "t.db")
        calls = {"n": 0}

        def bad(*a, **kw):
            calls["n"] += 1
            raise sqlite3.OperationalError("file is not a database")

        monkeypatch.setattr("sqlite3.connect", bad)
        with pytest.raises(sqlite3.OperationalError, match="file is not a database"):
            ss._get_db()
        assert calls["n"] == 1


# ── terminal_pending 旁路标记 ───────────────────────────────────────────────


@pytest.fixture
def pending_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "sessions.db")
    monkeypatch.setenv("TERMINAL_PENDING_DIR", str(tmp_path / "pending"))
    return tmp_path / "pending"


class TestTerminalPendingMarker:
    def test_write_list_pop_roundtrip(self, pending_dir):
        path = ss.write_terminal_pending_marker("s1", {"type": "done"})
        assert path is not None and path.exists()
        assert ss.list_terminal_pending_markers() == [path]
        payload = ss.pop_terminal_pending_marker("s1")
        assert payload is not None
        assert payload["session_id"] == "s1"
        assert payload["event"]["type"] == "done"
        assert "marked_at" in payload
        assert ss.list_terminal_pending_markers() == []

    def test_write_failure_returns_none(self, tmp_path, monkeypatch):
        """标记写失败（目录不可建）只返回 None，不抛——兜底中的兜底。"""
        monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "sessions.db")
        # 把目录指到「父路径是文件」的非法位置 → mkdir 必失败
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("TERMINAL_PENDING_DIR", str(blocker / "sub" / "pending"))
        assert ss.write_terminal_pending_marker("s1", {"type": "done"}) is None

    def test_pop_corrupt_marker_removed(self, pending_dir):
        corrupt = pending_dir / "s-bad.json"
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_text("{not json", encoding="utf-8")
        assert ss.pop_terminal_pending_marker("s-bad") is None
        assert not corrupt.exists()


@pytest.mark.asyncio
async def test_publish_terminal_exhaustion_writes_marker(tmp_path, monkeypatch, pending_dir):
    """终态落库 5×1s 重试耗尽：raise 前在旁路写 terminal_pending 标记。"""
    session_store = ss
    session_store.init_db()
    reg = StreamRegistry()
    sid = "s-marker-1"
    stream = reg._streams.setdefault(sid, SessionStream())  # noqa: SLF001

    def journal_down(*args, **kwargs):
        raise RuntimeError("journal down")

    monkeypatch.setattr(session_store, "append_session_event", journal_down)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0))
    with pytest.raises(RuntimeError):
        await reg.publish_terminal(sid, {"type": "done"})
    markers = session_store.list_terminal_pending_markers()
    assert len(markers) == 1
    payload = json.loads(markers[0].read_text(encoding="utf-8"))
    assert payload["session_id"] == sid
    assert payload["event"]["type"] == "done"
    assert stream.terminalPublished is False, "CAS 释放语义保持（final review C1）"


# ── 修复脚本 ────────────────────────────────────────────────────────────────


class TestRepairScript:
    def test_repair_appends_event_and_fixes_status(self, tmp_path, monkeypatch, pending_dir):
        from scripts.repair_terminal_pending import main as repair_main

        from finance_agent.session_store import create_session, get_session

        session_store = ss
        session_store.init_db()
        sid = create_session(stock_code="600519", stock_name="茅台", status="running")
        event = {"type": "done", "session_id": sid, "timestamp": "2026-11-20T03:00:00"}
        ss.write_terminal_pending_marker(sid, event)

        rc = repair_main([])
        assert rc == 0
        assert ss.list_terminal_pending_markers() == []
        terminal = ss.get_terminal_event(sid)
        assert terminal is not None and terminal["type"] == "done"
        assert get_session(sid)["status"] == "completed"

    def test_repair_status_only_when_event_already_present(
        self, tmp_path, monkeypatch, pending_dir
    ):
        """journal 已有终态（兜底发布成功过）：只修 status，不重复落事件。"""
        from scripts.repair_terminal_pending import main as repair_main

        from finance_agent.session_store import create_session, get_session, get_terminal_event

        session_store = ss
        session_store.init_db()
        sid = create_session(stock_code="600519", stock_name="茅台", status="running")
        session_store.append_session_event(sid, {"type": "error", "message": "late"})
        before = get_terminal_event(sid)
        event = {"type": "error", "session_id": sid}
        ss.write_terminal_pending_marker(sid, event)

        rc = repair_main([])
        assert rc == 0
        after = get_terminal_event(sid)
        assert after["seq"] == before["seq"], "不得重复落终态事件"
        assert get_session(sid)["status"] == "failed"

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch, pending_dir):
        from scripts.repair_terminal_pending import main as repair_main

        from finance_agent.session_store import create_session, get_session

        session_store = ss
        session_store.init_db()
        sid = create_session(stock_code="600519", stock_name="茅台", status="running")
        ss.write_terminal_pending_marker(sid, {"type": "done", "session_id": sid})
        rc = repair_main(["--dry-run"])
        assert rc == 0
        assert ss.list_terminal_pending_markers(), "dry-run 不删标记"
        assert ss.get_terminal_event(sid) is None
        assert get_session(sid)["status"] == "running"

    def test_repair_failure_retains_marker(self, tmp_path, monkeypatch, pending_dir):
        """补写失败：保留标记待下次，退出码 1。"""
        from scripts.repair_terminal_pending import main as repair_main

        from finance_agent.session_store import create_session

        session_store = ss
        session_store.init_db()
        sid = create_session(stock_code="600519", stock_name="茅台", status="running")
        ss.write_terminal_pending_marker(sid, {"type": "done", "session_id": sid})

        def broken_append(*a, **kw):
            raise RuntimeError("still down")

        monkeypatch.setattr(session_store, "append_session_event", broken_append)
        rc = repair_main([])
        assert rc == 1
        assert len(ss.list_terminal_pending_markers()) == 1, "失败标记必须保留"


# ── flush 非 Timeout 异常回填 ───────────────────────────────────────────────


def _fake_events():
    yield f"data: {json.dumps({'type': 'analysis_start', 'session_id': 's1'}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'report_ready', 'session_id': 's1', 'report_markdown': '# ok'}, ensure_ascii=False)}\n\n"


def test_flush_non_timeout_rebuffers_and_pipeline_survives(tmp_path, monkeypatch):
    """publish_many 抛非 Timeout 异常：整批回填缓冲、管线照常完成（#515 ④）。"""
    import finance_agent.pipeline_runner as pipeline_runner_module
    from finance_agent.pipeline_runner import PipelineRunner

    monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "t.db")
    ss.init_db()
    sid = ss.create_session(stock_code="600519", stock_name="茅台", status="running")

    class _FailingFuture:
        def result(self, timeout=None):
            raise RuntimeError("sqlite transient down")

    def _fake_rct(coro, loop):
        coro.close()
        return _FailingFuture()

    monkeypatch.setattr(pipeline_runner_module.asyncio, "run_coroutine_threadsafe", _fake_rct)

    PipelineRunner.start(
        sid,
        _fake_events,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
        loop=object(),
    )
    deadline = time.time() + 10
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        time.sleep(0.05)

    assert not PipelineRunner.is_running(sid)
    assert ss.get_session(sid)["status"] != "failed", "回填路径不得崩管线（旧实现会 failed）"


def test_flush_timeout_still_skips(tmp_path, monkeypatch):
    """TimeoutError 保持 #490 跳过语义（不因本次改动回填造成积压死循环）。"""
    import finance_agent.pipeline_runner as pipeline_runner_module
    from finance_agent.pipeline_runner import PipelineRunner

    monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "t.db")
    ss.init_db()
    sid = ss.create_session(stock_code="600519", stock_name="茅台", status="running")

    class _TimeoutFuture:
        def result(self, timeout=None):
            raise TimeoutError()

    def _fake_rct(coro, loop):
        coro.close()
        return _TimeoutFuture()

    monkeypatch.setattr(pipeline_runner_module.asyncio, "run_coroutine_threadsafe", _fake_rct)

    PipelineRunner.start(
        sid,
        _fake_events,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
        loop=object(),
    )
    deadline = time.time() + 10
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        time.sleep(0.05)

    assert not PipelineRunner.is_running(sid)
    assert ss.get_session(sid)["status"] != "failed"
