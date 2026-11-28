"""参考数据面（delta extend-factor-library-coverage §6）：侧表 / 主表行业列 / 读路径附加列。

口径（spec factor-reference-data）：
- ``market_ref_daily`` 侧表与 ``security_master.industry_l1`` 列只在写路径初始化；
- 读路径 ``read_universe`` / ``read_universe_range`` 附加 ``float_cap`` / ``turnover`` /
  ``industry_l1`` / 派生 ``listed_days``（LEFT JOIN，仍是单次 SQL）；
- 旧库（无侧表 / 无列）→ 退化为原查询，既有键集合不变、不抛错，
  MUST NOT 被误判为 ``SnapshotEmptyError``。
"""

from __future__ import annotations

import sqlite3

import pytest

from finance_agent.data.snapshot import (
    SnapshotEmptyError,
    get_security_master,
    init_snapshot_db,
    read_universe,
    read_universe_range,
    update_security_master_industry,
    upsert_market_ref_rows,
    upsert_security_master,
    write_snapshot_rows,
)

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


def _row(code: str, trade_date: str, close: float = 10.0, *, suspended: int = 0) -> dict:
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
        "is_suspended": suspended,
        "limit_up_sealed": 0,
        "limit_down_sealed": 0,
        "listed_days": None,
        "list_date": "2020-01-02",
    }


class TestRefSideTable:
    def test_ddl_and_upsert_idempotent(self, tmp_path):
        db = tmp_path / "s.db"
        init_snapshot_db(db)
        rows = [
            {
                "trade_date": "2026-11-27",
                "code": "600519",
                "float_cap": 2.0e9,
                "total_cap": 2.1e9,
                "turnover": 0.0125,
                "source": "tencent",
            }
        ]
        assert upsert_market_ref_rows(rows, db_path=db) == 1
        assert upsert_market_ref_rows(rows, db_path=db) == 1  # 幂等：同键覆盖不重复
        conn = sqlite3.connect(db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM market_ref_daily").fetchone()[0]
            got = conn.execute(
                "SELECT float_cap, turnover, source FROM market_ref_daily"
            ).fetchone()
        finally:
            conn.close()
        assert count == 1
        assert got[0] == pytest.approx(2.0e9)
        assert got[1] == pytest.approx(0.0125)
        assert got[2] == "tencent"


class TestIndustryColumn:
    def test_migration_and_update_preserve(self, tmp_path):
        db = tmp_path / "s.db"
        init_snapshot_db(db)
        upsert_security_master(
            [{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}], db_path=db
        )
        assert update_security_master_industry({"600519": "食品饮料"}, db_path=db) == 1
        assert get_security_master(db_path=db)["600519"]["industry_l1"] == "食品饮料"
        # 上市清单刷新不带 industry → COALESCE 保值语义：不得清空
        upsert_security_master([{"code": "600519", "name": "贵州茅台"}], db_path=db)
        assert get_security_master(db_path=db)["600519"]["industry_l1"] == "食品饮料"
        # 只写非空：空值不覆盖
        assert update_security_master_industry({"600519": ""}, db_path=db) == 0
        assert get_security_master(db_path=db)["600519"]["industry_l1"] == "食品饮料"


class TestReadUniverseAttach:
    def _seed(self, tmp_path):
        db = tmp_path / "s.db"
        init_snapshot_db(db)
        write_snapshot_rows(
            "2026-11-27",
            [_row("600519", "2026-11-27"), _row("000001", "2026-11-27", 12.0)],
            db_path=db,
        )
        upsert_security_master(
            [
                {"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"},
                {"code": "000001", "name": "平安银行", "list_date": "1991-04-03"},
            ],
            db_path=db,
        )
        update_security_master_industry({"600519": "食品饮料"}, db_path=db)
        upsert_market_ref_rows(
            [
                {
                    "trade_date": "2026-11-27",
                    "code": "600519",
                    "float_cap": 2.0e9,
                    "turnover": 0.0125,
                    "source": "tencent",
                }
            ],
            db_path=db,
        )
        return db

    def test_read_universe_attaches_columns(self, tmp_path):
        db = self._seed(tmp_path)
        rows = {row["code"]: row for row in read_universe("2026-11-27", db_path=db)}
        assert rows["600519"]["float_cap"] == pytest.approx(2.0e9)
        assert rows["600519"]["turnover"] == pytest.approx(0.0125)
        assert rows["600519"]["industry_l1"] == "食品饮料"
        assert rows["600519"]["listed_days"] is not None  # 由 list_date 派生
        # 无侧表行的票 → NULL（NaN 语义由面板层承担，读层为 None）
        assert rows["000001"]["float_cap"] is None
        assert rows["000001"]["industry_l1"] is None

    def test_read_universe_range_attaches_columns(self, tmp_path):
        db = self._seed(tmp_path)
        rows = read_universe_range("2026-11-27", "2026-11-27", db_path=db)
        assert {row["code"] for row in rows} == {"600519", "000001"}
        assert all("float_cap" in row and "industry_l1" in row for row in rows)


class TestOldDbDegradation:
    def _old_db(self, tmp_path):
        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        try:
            conn.executescript(_OLD_DDL)
            conn.execute(
                "INSERT INTO market_snapshot (trade_date, code, name, open, high, low,"
                " close_raw, close_qfq, volume, amount, is_suspended)"
                " VALUES ('2026-11-27','600519','贵州茅台',10,10,10,10,10,100,1000,0)"
            )
            conn.execute(
                "INSERT INTO security_master (code, name, list_date) "
                "VALUES ('600519','贵州茅台','2001-08-27')"
            )
            conn.commit()
        finally:
            conn.close()
        return db

    def test_read_universe_plain_keys(self, tmp_path):
        db = self._old_db(tmp_path)
        rows = read_universe("2026-11-27", db_path=db)
        assert len(rows) == 1
        row = rows[0]
        assert "float_cap" not in row and "industry_l1" not in row
        assert row["code"] == "600519"

    def test_read_universe_range_plain_keys(self, tmp_path):
        db = self._old_db(tmp_path)
        rows = read_universe_range("2026-11-27", "2026-11-27", db_path=db)
        assert len(rows) == 1
        assert "float_cap" not in rows[0]

    def test_empty_still_fails_loud(self, tmp_path):
        db = self._old_db(tmp_path)
        with pytest.raises(SnapshotEmptyError):
            read_universe("2026-11-26", db_path=db)
