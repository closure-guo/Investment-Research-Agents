"""_get_db 瞬态错误重试（delta add-event-delivery-resilience spec session-persistence）。

monkeypatch 说明：`sqlite3.connect` / `time.sleep` 用字符串目标 patch 全局模块
（pytest 自动还原，勿污染全局）；session_store 内 `import sqlite3` / `import time`
引用同一模块对象，调用时属性查找即可命中 patch。
"""

from __future__ import annotations

import sqlite3

import pytest

from finance_agent import session_store as ss
from finance_agent.data import conn as _conn


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "sessions.db")
    return tmp_path / "sessions.db"


class TestGetDbTransientRetry:
    def test_unable_to_open_retries_then_succeeds(self, isolated_db, monkeypatch):
        """瞬断自愈窗口内：前 2 次 connect 抛 unable to open，第 3 次成功。"""
        calls = {"n": 0}
        real_connect = sqlite3.connect

        def flaky_connect(*a, **kw):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise sqlite3.OperationalError("unable to open database file")
            return real_connect(*a, **kw)

        monkeypatch.setattr("sqlite3.connect", flaky_connect)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        conn = ss._get_db()
        assert calls["n"] == 3
        conn.close()

    def test_pragma_failure_also_retried(self, isolated_db, monkeypatch):
        """connect 成功但 PRAGMA 阶段瞬断，同样走重试（整块包裹）。

        harden-sqlite-connection-discipline（#515）：``_get_db`` 不再执行
        ``PRAGMA journal_mode``（journal 收敛到 init_db 一次）；连接期 pragma
        只剩 ``busy_timeout``，重试包裹保证改钉在它上。
        """

        class FlakyConn:
            def __init__(self, inner):
                self._inner = inner
                self._pragma_calls = 0

            def execute(self, sql, *a):
                self._pragma_calls += 1
                if sql.startswith("PRAGMA busy_timeout") and self._pragma_calls == 1:
                    raise sqlite3.OperationalError("disk I/O error")
                return self._inner.execute(sql, *a)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        real_connect = sqlite3.connect
        state = {"n": 0}

        def connect_once_flaky(*a, **kw):
            state["n"] += 1
            inner = real_connect(*a, **kw)
            if state["n"] == 1:
                return FlakyConn(inner)
            return inner

        monkeypatch.setattr("sqlite3.connect", connect_once_flaky)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        conn = ss._get_db()
        assert state["n"] == 2
        conn.close()

    def test_retry_exhausted_raises(self, isolated_db, monkeypatch):
        """瞬态错误持续超过重试预算：显式 raise 原异常，不静默。

        harden-sqlite-connection-discipline ④ 改判（#515）：固定 6 次/≈3.1s →
        预算制（默认 120s，单次退避封顶 2s，硬顶次数防 sleep 打桩后的空转）。
        sleep 打桩为空收集器时真实时钟不推进、预算到不了——按硬顶次数
        ``conn._RETRY_MAX_ATTEMPTS`` 耗尽即抛（同为 spec 契约的一部分；
        C4 后重试循环收编在 data/conn，常量随实现走）。
        """
        calls = {"n": 0}
        sleeps: list[float] = []

        def down_connect(*a, **kw):
            calls["n"] += 1
            raise sqlite3.OperationalError("unable to open database file")

        monkeypatch.setattr("sqlite3.connect", down_connect)
        monkeypatch.setattr("time.sleep", sleeps.append)
        with pytest.raises(sqlite3.OperationalError):
            ss._get_db()
        assert calls["n"] == _conn._RETRY_MAX_ATTEMPTS
        assert len(sleeps) == calls["n"] - 1, "末次尝试失败后不再 sleep"
        assert all(s <= _conn._RETRY_SLEEP_CAP_S for s in sleeps)

    def test_non_transient_error_not_retried(self, isolated_db, monkeypatch):
        """非瞬态错误（如 no such table）不重试，立即 raise。"""
        calls = {"n": 0}

        def bad_connect(*a, **kw):
            calls["n"] += 1
            raise sqlite3.OperationalError("no such table: something")

        monkeypatch.setattr("sqlite3.connect", bad_connect)
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            ss._get_db()
        assert calls["n"] == 1
