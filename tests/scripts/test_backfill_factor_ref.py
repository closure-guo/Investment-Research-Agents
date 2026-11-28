"""参考数据历史回填脚本单测（extend-factor-library-coverage §9，零网络：stub client）。

覆盖：(a) 回填行口径（``float_cap`` = 流通股本 × 不复权收盘、``turnover`` 源值小数、
``source`` 留痕）；(b) 源缺列/取数失败计入 ``symbols_failed`` 且不落半口径行；
(c) 幂等（同键覆盖、行数不变）；(d) ``--dry-run`` 不写库；(e) 主表为空且未显式
指定标的 → 退 2；(f) 同区间 ``market_snapshot`` 行逐字不变。
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest
from scripts.backfill_factor_ref import main, run_backfill

from finance_agent.data.snapshot import (
    init_snapshot_db,
    upsert_security_master,
    write_snapshot_rows,
)

_START = "2026-11-24"
_END = "2026-11-25"


def _share_frame(*days: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": day,
                "close": 10.0 + index,
                "outstanding_share": 1.0e9,
                "turnover": 0.01 + index * 0.001,
            }
            for index, day in enumerate(days)
        ],
        columns=["date", "close", "outstanding_share", "turnover"],
    )


def _snapshot_row(date: str, code: str) -> dict:
    return {
        "trade_date": date,
        "code": code,
        "name": code,
        "open": 10.0,
        "high": 10.1,
        "low": 9.9,
        "close_raw": 10.0,
        "close_qfq": 10.0,
        "volume": 100.0,
        "amount": 1000.0,
        "pct_chg": 0.0,
        "prev_close": 10.0,
        "is_st": 0,
        "is_suspended": 0,
        "limit_up_sealed": 0,
        "limit_down_sealed": 0,
        "listed_days": None,
        "list_date": "2020-01-02",
    }


class FakeShareClient:
    """只实现 ``fetch_symbol_share_history`` 的 stub 客户端（零网络）。"""

    def __init__(self, *, data=None, fail=()):
        self.data = data or {}
        self.fail = set(fail)
        self.calls: list[tuple[str, str, str]] = []

    def fetch_symbol_share_history(self, code: str, start: str, end: str):
        self.calls.append((code, start, end))
        if code in self.fail:
            return None
        return self.data.get(code)


def _seed_db(tmp_path):
    db = tmp_path / "snap.db"
    init_snapshot_db(db)
    upsert_security_master(
        [
            {"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"},
            {"code": "000001", "name": "平安银行", "list_date": "1991-04-03"},
        ],
        db_path=db,
    )
    write_snapshot_rows(_START, [_snapshot_row(_START, "600519")], db_path=db)
    return db


def _ref_rows(db) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT trade_date, code, float_cap, total_cap, turnover, source"
            " FROM market_ref_daily ORDER BY code, trade_date"
        ).fetchall()
    finally:
        conn.close()


def _snapshot_rows(db) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT * FROM market_snapshot ORDER BY trade_date, code").fetchall()
    finally:
        conn.close()


def test_backfill_rows_caliber_and_summary(tmp_path):
    db = _seed_db(tmp_path)
    before = _snapshot_rows(db)
    client = FakeShareClient(
        data={
            "600519": _share_frame(_START, _END),
            "000001": _share_frame(_START, _END),
        }
    )
    summary = run_backfill(
        start=_START,
        end=_END,
        symbols=["600519", "000001"],
        db_path=db,
        client=client,
    )
    assert summary["symbols_ok"] == 2
    assert summary["symbols_failed"] == 0
    assert summary["rows_written"] == 4
    assert summary["start"] == _START
    assert summary["end"] == _END
    rows = _ref_rows(db)
    assert [row[0] for row in rows] == [_START, _END, _START, _END]
    assert [row[1] for row in rows] == ["000001", "000001", "600519", "600519"]
    first = rows[2]  # 600519 on _START
    assert first[2] == pytest.approx(10.0 * 1.0e9)
    assert first[3] is None  # 历史源不提供总市值：不下推断值
    assert first[4] == pytest.approx(0.01)
    assert first[5] == "sina_history"
    # 快照行不可变
    assert _snapshot_rows(db) == before


def test_failed_symbol_counted_no_half_rows(tmp_path):
    db = _seed_db(tmp_path)
    half = _share_frame(_START, _END)
    half.loc[1, "turnover"] = None  # 单行缺换手率 → 该行跳过（不落半口径行）
    client = FakeShareClient(
        data={"600519": half},
        fail=("000001",),
    )
    summary = run_backfill(
        start=_START,
        end=_END,
        symbols=["600519", "000001"],
        db_path=db,
        client=client,
    )
    assert summary["symbols_failed"] == 1
    assert summary["symbols_ok"] == 1
    assert summary["rows_written"] == 1
    rows = _ref_rows(db)
    assert [(row[0], row[1]) for row in rows] == [(_START, "600519")]
    assert all(row[4] is not None for row in rows)  # 无 NULL turnover 行


def test_backfill_idempotent(tmp_path):
    db = _seed_db(tmp_path)
    client = FakeShareClient(data={"600519": _share_frame(_START, _END)})
    first = run_backfill(start=_START, end=_END, symbols=["600519"], db_path=db, client=client)
    second = run_backfill(start=_START, end=_END, symbols=["600519"], db_path=db, client=client)
    assert first["symbols_ok"] == second["symbols_ok"] == 1
    assert first["rows_written"] == second["rows_written"] == 2
    assert len(_ref_rows(db)) == 2  # 同键覆盖、行数不变


def test_dry_run_writes_nothing(tmp_path):
    db = _seed_db(tmp_path)
    client = FakeShareClient(data={"600519": _share_frame(_START, _END)})
    summary = run_backfill(
        start=_START,
        end=_END,
        symbols=["600519"],
        db_path=db,
        dry_run=True,
        client=client,
    )
    assert summary["rows_written"] == 0
    assert summary["symbols_ok"] == 1  # 取数成功但未落库
    assert _ref_rows(db) == []


def test_empty_master_without_symbols_exits_2(tmp_path):
    db = tmp_path / "empty.db"
    init_snapshot_db(db)
    assert main(["--start", _START, "--end", _END, "--db", str(db)]) == 2
