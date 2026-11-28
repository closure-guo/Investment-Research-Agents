"""选股缺省面板拼装：可选参考字段与行业分组不丢失（extend-factor-library-coverage §7）。

`_build_default_panel` 逐日读快照再拼装——不拼可选帧会在选股面板上静默丢掉
``float_cap``/``turnover``/``listed_days`` 与 ``groups``（闸①预检与 L2 缺口披露
都会失真）。
"""

from __future__ import annotations

import sqlite3

from finance_agent.data.snapshot import (
    init_snapshot_db,
    update_security_master_industry,
    upsert_market_ref_rows,
    upsert_security_master,
    write_snapshot_rows,
)
from finance_agent.screening.pipeline import _build_default_panel

_OLD_DDL = """
CREATE TABLE market_snapshot (
  trade_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT,
  open REAL, high REAL, low REAL, close_raw REAL, close_qfq REAL,
  volume REAL, amount REAL, pct_chg REAL, prev_close REAL,
  is_st INTEGER, is_suspended INTEGER, limit_up_sealed INTEGER,
  limit_down_sealed INTEGER, listed_days INTEGER, list_date TEXT,
  PRIMARY KEY (trade_date, code)
);
CREATE TABLE security_master (
  code TEXT PRIMARY KEY, name TEXT, list_date TEXT, delist_date TEXT,
  delist_source TEXT, updated_at TEXT
);
"""


def _row(code: str, trade_date: str, close: float = 10.0) -> dict:
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
        "prev_close": close,
        "volume": 100,
        "amount": 1000,
        "is_st": 0,
        "is_suspended": 0,
        "limit_up_sealed": 0,
        "limit_down_sealed": 0,
        "listed_days": None,
        "list_date": "2020-01-02",
    }


def _seed(tmp_path):
    db = tmp_path / "s.db"
    init_snapshot_db(db)
    write_snapshot_rows("2026-11-26", [_row("600519", "2026-11-26")], db_path=db)
    write_snapshot_rows("2026-11-27", [_row("600519", "2026-11-27", 11.0)], db_path=db)
    upsert_security_master(
        [{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}], db_path=db
    )
    update_security_master_industry({"600519": "食品饮料"}, db_path=db)
    upsert_market_ref_rows(
        [
            {
                "trade_date": "2026-11-26",
                "code": "600519",
                "float_cap": 2.0e9,
                "turnover": 0.011,
                "source": "tencent",
            },
            {
                "trade_date": "2026-11-27",
                "code": "600519",
                "float_cap": 2.1e9,
                "turnover": 0.012,
                "source": "tencent",
            },
        ],
        db_path=db,
    )
    return db


def test_panel_keeps_optional_fields_and_groups(tmp_path):
    db = _seed(tmp_path)
    calendar = ["2026-11-26", "2026-11-27"]
    panel = _build_default_panel("2026-11-27", calendar, db)
    assert {"float_cap", "turnover", "listed_days"} <= set(panel.fields)
    assert panel.fields["float_cap"].loc["2026-11-27", "600519"] == 2.1e9
    assert panel.fields["turnover"].loc["2026-11-26", "600519"] == 0.011
    assert panel.groups is not None
    assert panel.groups["industry"].loc["2026-11-26", "600519"] == "食品饮料"


def test_old_db_panel_no_side_table_fields(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(_OLD_DDL)
        conn.execute(
            "INSERT INTO market_snapshot (trade_date, code, name, open, high, low,"
            " close_raw, close_qfq, volume, amount, is_suspended)"
            " VALUES ('2026-11-27','600519','贵州茅台',10,10,10,10,10,100,1000,0)"
        )
        conn.commit()
    finally:
        conn.close()
    panel = _build_default_panel("2026-11-27", ["2026-11-27"], db)
    # 旧库：侧表字段（float_cap/turnover）与行业分组不构造；
    # listed_days 是 market_snapshot 自带列 → 帧存在（该行 NULL → 全 NaN）
    assert set(panel.fields) == {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "listed_days",
    }
    assert panel.fields["listed_days"]["600519"].isna().all()
    assert panel.groups is None
