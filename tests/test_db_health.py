"""热库可用性探针单测（harden-sqlite-availability task 5，issue #515）。

契约（delta specs/session-persistence「Database Availability Probe In Health Check」）：
每库给出 ok/unavailable；失败带库名与短错误类；探针受超时约束（打不开 ≠ 挂死）。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from finance_agent.data import db_health

DB_ENV_KEYS = (
    "SESSIONS_DB_PATH",
    "SCREENING_DB_PATH",
    "EXECUTION_DB_PATH",
    "EVENTS_DB_PATH",
    "REGIME_DB_PATH",
    "RESEARCH_REPORTS_DB_PATH",
    "FACTOR_LIBRARY_DB_PATH",
    "SNAPSHOT_DB_PATH",
)

DB_NAMES = (
    "sessions",
    "screening",
    "execution",
    "events",
    "regime",
    "research_reports",
    "factor_library",
    "market_snapshot",
)


_ENV_KEY_TO_NAME = {
    "SESSIONS_DB_PATH": "sessions",
    "SCREENING_DB_PATH": "screening",
    "EXECUTION_DB_PATH": "execution",
    "EVENTS_DB_PATH": "events",
    "REGIME_DB_PATH": "regime",
    "RESEARCH_REPORTS_DB_PATH": "research_reports",
    "FACTOR_LIBRARY_DB_PATH": "factor_library",
    "SNAPSHOT_DB_PATH": "market_snapshot",
}


def _point_env_at(tmp_path: Path, monkeypatch) -> None:
    """八库 env 全部指向 tmp 下的空库文件（零字节文件 = 合法空库，sqlite_master 可读）。"""
    for key, name in _ENV_KEY_TO_NAME.items():
        monkeypatch.setenv(key, str(tmp_path / f"{name}.db"))


def test_probe_all_ok_reports_every_database(tmp_path, monkeypatch):
    _point_env_at(tmp_path, monkeypatch)
    result = db_health.probe_databases()
    assert set(result) == set(DB_NAMES)
    assert all(v == {"status": "ok"} for v in result.values())


def test_probe_names_failing_database_with_error_class(tmp_path, monkeypatch):
    _point_env_at(tmp_path, monkeypatch)
    # 库路径的父目录不存在 → connect 即 OperationalError（不会静默建库）
    missing = tmp_path / "no-such-dir" / "screening.db"
    monkeypatch.setenv("SCREENING_DB_PATH", str(missing))
    result = db_health.probe_databases()
    assert result["screening"]["status"] == "unavailable"
    assert result["screening"]["error"] == "OperationalError"
    assert result["sessions"]["status"] == "ok"


def test_probe_directory_as_database_fails(tmp_path, monkeypatch):
    _point_env_at(tmp_path, monkeypatch)
    (tmp_path / "events.db").mkdir()  # 目录冒充库文件
    result = db_health.probe_databases()
    assert result["events"]["status"] == "unavailable"
    assert result["events"]["error"] == "OperationalError"


def test_probe_bounded_by_timeout(tmp_path, monkeypatch):
    _point_env_at(tmp_path, monkeypatch)

    def slow_probe(path: Path) -> None:
        time.sleep(1.5)  # 远超注入的超时；线程自然结束不悬挂解释器

    started = time.monotonic()
    result = db_health.probe_databases(timeout_s=0.2, probe_fn=slow_probe)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0  # 探针在超时处返回，不等慢库
    assert all(
        v["status"] == "unavailable" and v["error"] == "TimeoutError" for v in result.values()
    )


def test_probe_reports_not_a_database(tmp_path, monkeypatch):
    _point_env_at(tmp_path, monkeypatch)
    (tmp_path / "regime.db").write_bytes(b"definitely not sqlite" * 8)
    result = db_health.probe_databases()
    assert result["regime"]["status"] == "unavailable"
    assert "DatabaseError" in result["regime"]["error"]


def test_default_timeout_constant():
    """默认超时 2s（design §2 口径），可被调用方覆盖。"""
    assert db_health.PROBE_TIMEOUT_S == 2.0


def test_probe_uses_live_env_per_call(tmp_path, monkeypatch):
    """env 调用期读取（测试/E2E 注入路径的前提；迁移后宿主 data/ 不可直读同理）。"""
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "s.db"))
    first = db_health.probe_databases(paths={"sessions": Path(tmp_path / "s.db")})
    assert first == {"sessions": {"status": "ok"}}
    # 未建库且父目录缺失 → 探针不建库、直接不可用
    second = db_health.probe_databases(paths={"sessions": Path(tmp_path / "x" / "s.db")})
    assert second == {"sessions": {"status": "unavailable", "error": "OperationalError"}}


@pytest.mark.parametrize("name", DB_NAMES)
def test_hot_db_paths_maps_all_names(tmp_path, monkeypatch, name):
    env_key = f"{name.upper()}_DB_PATH" if name != "market_snapshot" else "SNAPSHOT_DB_PATH"
    monkeypatch.setenv(env_key, str(tmp_path / f"{name}.db"))
    paths = db_health.hot_db_paths()
    assert paths[name] == Path(tmp_path / f"{name}.db")
