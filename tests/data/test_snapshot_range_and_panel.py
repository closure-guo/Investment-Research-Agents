"""read_universe_range 区间读取 + _load_panel 收敛（delta harden-sqlite-connection-discipline ③）。

语义锚点：与逐日 ``read_universe`` 逐值等价（含缺日 fail-loud、防前视、空语义）；
面板加载全窗口单连接；``(dates, mtime_ns, size)`` 键控 LRU 缓存命中/失效。
"""

from __future__ import annotations

import pandas as pd
import pytest

from finance_agent.data import snapshot as snap
from finance_agent.data.snapshot import (
    SnapshotEmptyError,
    SnapshotError,
    SnapshotNotFreshError,
    init_snapshot_db,
    read_universe,
    read_universe_range,
    write_snapshot_rows,
)
from finance_agent.factors.monitor import _load_panel


def _row(code: str, trade_date: str, close: float, *, suspended: int = 0) -> dict:
    return {
        "trade_date": trade_date,
        "code": code,
        "name": code,
        "open": close,
        "high": close,
        "low": close,
        "close_raw": close,
        "close_qfq": close,
        "pct_chg": 0.0,
        "volume": 100,
        "amount": 1000,
        "turnover_rate": None,
        "pe_ttm": None,
        "total_mv": None,
        "is_suspended": suspended,
        "is_st": 0,
        "limit_up_sealed": 0,
        "limit_down_sealed": 0,
        "listed_days": None,
    }


@pytest.fixture
def snap_db(tmp_path):
    db_path = tmp_path / "market_snapshot.db"
    init_snapshot_db(db_path)
    write_snapshot_rows("2026-01-05", [_row("000001.SZ", "2026-01-05", 10.0)], db_path=db_path)
    write_snapshot_rows("2026-01-06", [_row("000001.SZ", "2026-01-06", 11.0)], db_path=db_path)
    write_snapshot_rows(
        "2026-01-07",
        [_row("000001.SZ", "2026-01-07", 12.0), _row("000002.SZ", "2026-01-07", 5.0)],
        db_path=db_path,
    )
    return db_path


class TestReadUniverseRange:
    def test_returns_rows_across_dates_ordered(self, snap_db):
        rows = read_universe_range("2026-01-05", "2026-01-07", db_path=snap_db)
        keys = [(r["trade_date"], r["code"]) for r in rows]
        assert keys == [
            ("2026-01-05", "000001.SZ"),
            ("2026-01-06", "000001.SZ"),
            ("2026-01-07", "000001.SZ"),
            ("2026-01-07", "000002.SZ"),
        ]
        assert all("trade_date" in r for r in rows)

    def test_single_connection(self, snap_db, monkeypatch):
        calls = {"n": 0}
        real = snap._connect_ro

        def counting(*a, **kw):
            calls["n"] += 1
            return real(*a, **kw)

        monkeypatch.setattr(snap, "_connect_ro", counting)
        read_universe_range("2026-01-05", "2026-01-07", db_path=snap_db)
        assert calls["n"] == 1

    def test_missing_table_empty_semantics(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        db_path.write_bytes(b"")  # 存在但无表：不建表，空语义
        with pytest.raises(SnapshotEmptyError):
            read_universe_range("2026-01-05", "2026-01-07", db_path=db_path)

    def test_missing_db_empty_semantics(self, tmp_path):
        with pytest.raises(SnapshotEmptyError):
            read_universe_range("2026-01-05", "2026-01-07", db_path=tmp_path / "nope.db")

    def test_not_fresh_rejected(self, snap_db):
        with pytest.raises(SnapshotNotFreshError):
            read_universe_range("2026-01-05", "2026-02-01", db_path=snap_db)

    def test_end_before_start_rejected(self, snap_db):
        with pytest.raises(SnapshotError):
            read_universe_range("2026-01-07", "2026-01-05", db_path=snap_db)

    def test_include_suspended_false_filters(self, snap_db):
        write_snapshot_rows(
            "2026-01-08", [_row("000003.SZ", "2026-01-08", 3.0, suspended=1)], db_path=snap_db
        )
        rows = read_universe_range(
            "2026-01-05", "2026-01-08", include_suspended=False, db_path=snap_db
        )
        assert all(r["is_suspended"] != 1 for r in rows)
        assert any(r["code"] == "000001.SZ" for r in rows)

    def test_partial_window_only_matching_rows(self, snap_db):
        rows = read_universe_range("2026-01-06", "2026-01-06", db_path=snap_db)
        assert len(rows) == 1 and rows[0]["trade_date"] == "2026-01-06"


class TestLoadPanelRange:
    def test_equivalent_to_per_day_read(self, snap_db):
        from finance_agent.factors.eval_engine import build_panel_from_rows

        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        panel = _load_panel(snap_db, dates=dates)
        chunks = [
            build_panel_from_rows(pd.DataFrame(read_universe(d, db_path=snap_db))) for d in dates
        ]
        expected = type(panel)(
            fields={
                name: pd.concat([c.fields[name] for c in chunks]).sort_index()
                for name in panel.fields
            },
            rows_present=(
                pd.concat([c.rows_present for c in chunks]).sort_index().fillna(False).astype(bool)
            ),
        )
        for name in ("open", "high", "low", "close", "volume", "amount"):
            pd.testing.assert_frame_equal(panel.fields[name], expected.fields[name])
        pd.testing.assert_frame_equal(panel.rows_present, expected.rows_present)

    def test_missing_date_fail_loud(self, tmp_path):
        # 窗口内缺行日（01-06 无行）→ 保留逐日 read_universe 的 fail-loud 语义
        db_path = tmp_path / "gap.db"
        init_snapshot_db(db_path)
        write_snapshot_rows("2026-01-05", [_row("000001.SZ", "2026-01-05", 10.0)], db_path=db_path)
        write_snapshot_rows("2026-01-07", [_row("000001.SZ", "2026-01-07", 12.0)], db_path=db_path)
        with pytest.raises(SnapshotEmptyError):
            _load_panel(db_path, dates=["2026-01-05", "2026-01-06", "2026-01-07"])

    def test_cache_hit_same_instance_zero_connect(self, snap_db, monkeypatch):
        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        first = _load_panel(snap_db, dates=dates)
        calls = {"n": 0}
        real = snap._connect_ro

        def counting(*a, **kw):
            calls["n"] += 1
            return real(*a, **kw)

        monkeypatch.setattr(snap, "_connect_ro", counting)
        second = _load_panel(snap_db, dates=dates)
        assert calls["n"] == 0  # latest_snapshot_date 也不走连接（缓存键用 mtime）
        assert second is first

    def test_cache_invalidated_on_write(self, snap_db):
        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        first = _load_panel(snap_db, dates=dates)
        assert "000009.SZ" not in first.fields["close"].columns
        write_snapshot_rows("2026-01-07", [_row("000009.SZ", "2026-01-07", 9.0)], db_path=snap_db)
        second = _load_panel(snap_db, dates=dates)
        assert second is not first  # 同键日期、数据变更 → mtime 失效重读
        assert second.fields["close"].loc["2026-01-07", "000009.SZ"] == 9.0

    def test_cache_lru_bound(self, snap_db):
        from finance_agent.factors import monitor as mon

        base = ["2026-01-05", "2026-01-06", "2026-01-07"]
        for extra in ("000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"):
            write_snapshot_rows("2026-01-07", [_row(extra, "2026-01-07", 1.0)], db_path=snap_db)
            _load_panel(snap_db, dates=base)
        # 4 次不同键加载（mtime 每次变化即新键），缓存不得无界增长
        assert len(mon._PANEL_CACHE) <= mon._PANEL_CACHE_MAX


def test_range_missing_table_no_ddl(tmp_path):
    """读路径绝不建库建表（读纪律回归钉）：range 读后库文件仍不存在。"""
    db_path = tmp_path / "ghost.db"
    with pytest.raises(SnapshotEmptyError):
        read_universe_range("2026-01-05", "2026-01-07", db_path=db_path)
    assert not db_path.exists()
