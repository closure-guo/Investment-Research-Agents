"""data/conn.py 连接 seam 行为测试（C4：docs/architecture-debt/c4-sqlite-connector.md）。

零行为迁移的钉子：
- open_hot：mkdir + Row factory + busy_timeout + 可选瞬态重试预算
- WAL 只进 init（init_wal），连接路径不设 journal_mode（#515 连接纪律）
- connect_retry_budget：clamp [30,300] 预算公式（session_store 原语义搬入）
"""

import sqlite3

import pytest

from finance_agent.data import conn


def test_open_hot_creates_parent_dirs_and_row_factory(tmp_path):
    nested = tmp_path / "a" / "b" / "x.db"
    c = conn.open_hot(nested)
    try:
        assert nested.exists()
        assert c.row_factory is sqlite3.Row
    finally:
        c.close()


def test_open_hot_busy_timeout_pragma(tmp_path):
    c = conn.open_hot(tmp_path / "x.db", busy_timeout_ms=15000)
    try:
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
    finally:
        c.close()


def test_open_hot_accepts_none_with_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = conn.open_hot(None, default="data/x.db")
    try:
        assert (tmp_path / "data" / "x.db").exists()
    finally:
        c.close()


def test_open_hot_does_not_set_wal(tmp_path):
    """连接纪律（#515）：WAL 只进 init，open_hot 不设置 journal_mode。"""
    c = conn.open_hot(tmp_path / "x.db")
    try:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode != "wal"
    finally:
        c.close()


def test_init_wal_sets_journal_mode(tmp_path):
    c = conn.open_hot(tmp_path / "x.db")
    try:
        conn.init_wal(c)
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        c.close()


class _FlakyConnect:
    """按次数抛瞬态错误的 sqlite3.connect 替身（记录真实调用次数）。

    注意：monkeypatch 打在全局 sqlite3.connect 上，兜底须调用**打补丁前**
    捕获的原函数，否则替身递归自身。
    """

    def __init__(self, fail_times: int, exc: Exception, real_connect):
        self.calls = 0
        self._fail_times = fail_times
        self._exc = exc
        self._real = real_connect

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc
        return self._real(*args, **kwargs)


def test_open_hot_retries_transient_within_budget(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    flaky = _FlakyConnect(2, sqlite3.OperationalError("unable to open database file"), real_connect)
    monkeypatch.setattr(conn.sqlite3, "connect", flaky)
    c = conn.open_hot(tmp_path / "x.db", retry_budget_s=5.0)
    try:
        assert flaky.calls == 3
    finally:
        c.close()


def test_open_hot_non_transient_raises_without_retry(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    flaky = _FlakyConnect(99, sqlite3.OperationalError("file is not a database"), real_connect)
    monkeypatch.setattr(conn.sqlite3, "connect", flaky)
    with pytest.raises(sqlite3.OperationalError):
        conn.open_hot(tmp_path / "x.db", retry_budget_s=5.0)
    assert flaky.calls == 1


def test_connect_retry_budget_clamped(monkeypatch):
    monkeypatch.setenv("SESSION_DB_CONNECT_RETRY_BUDGET_S", "5")
    assert conn.connect_retry_budget() == 30.0
    monkeypatch.setenv("SESSION_DB_CONNECT_RETRY_BUDGET_S", "9999")
    assert conn.connect_retry_budget() == 300.0
    monkeypatch.setenv("SESSION_DB_CONNECT_RETRY_BUDGET_S", "120")
    assert conn.connect_retry_budget() == 120.0


def test_connect_retry_budget_default_without_env(monkeypatch):
    monkeypatch.delenv("SESSION_DB_CONNECT_RETRY_BUDGET_S", raising=False)
    assert conn.connect_retry_budget() == 120.0
