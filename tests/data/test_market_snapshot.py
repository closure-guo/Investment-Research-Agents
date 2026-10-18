"""TDD tests for data/snapshot.py — 市场快照 schema/meta、行构造与读写/状态接口。

覆盖 add-market-snapshot 计划 Task 1（schema/连接/meta 工具）、Task 2
（build_snapshot_rows / compute_rebase_factors）、Task 3（幂等写 + as_of 防前视
读取）与 Task 4（stale 状态纯函数 + 缓存 TTL 豁免契约）的验收行为。
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from finance_agent.data.cache import DataCache
from finance_agent.data.snapshot import (
    SnapshotEmptyError,
    SnapshotError,
    SnapshotNotFreshError,
    _connect,
    _norm_code,
    _num,
    _text,
    apply_rebase,
    build_snapshot_rows,
    compute_rebase_factors,
    compute_snapshot_status,
    delete_meta,
    get_all_meta,
    get_meta,
    get_security_master,
    init_snapshot_db,
    latest_snapshot_date,
    norm_code,
    read_bars,
    read_universe,
    reset_snapshot_db,
    set_meta,
    set_meta_many,
    upsert_security_master,
    write_snapshot_rows,
    write_snapshot_rows_bulk,
)

# Global Constraints 钉死的 market_snapshot 列集合（逐字，不得改名）。
_SNAPSHOT_COLUMNS = {
    "trade_date",
    "code",
    "name",
    "open",
    "high",
    "low",
    "close_raw",
    "close_qfq",
    "volume",
    "amount",
    "pct_chg",
    "prev_close",
    "is_st",
    "is_suspended",
    "limit_up_sealed",
    "limit_down_sealed",
    "listed_days",
    "list_date",
}


def test_init_creates_tables_and_is_idempotent(tmp_path):
    """init 幂等建 3 表 + 2 索引；列名逐字一致；连接为 WAL + busy_timeout。"""
    db = tmp_path / "market_snapshot.db"
    assert init_snapshot_db(db) == db
    assert init_snapshot_db(db) == db  # 二次调用不报错（CREATE IF NOT EXISTS）

    conn = _connect(db)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {"market_snapshot", "snapshot_meta", "security_master"} <= tables

        cols = {row[1] for row in conn.execute("PRAGMA table_info(market_snapshot)").fetchall()}
        assert cols == _SNAPSHOT_COLUMNS

        meta_cols = {row[1] for row in conn.execute("PRAGMA table_info(snapshot_meta)").fetchall()}
        assert meta_cols == {"key", "value"}
        master_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(security_master)").fetchall()
        }
        # add-microcap-lane-guardrails：security_master 扩 delist_date/delist_source
        # （退市日 + 语义来源审计，见 metrics.md §1.16）
        assert master_cols == {
            "code",
            "name",
            "list_date",
            "delist_date",
            "delist_source",
            "updated_at",
        }

        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert {"idx_market_snapshot_code_date", "idx_market_snapshot_date"} <= indexes

        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
    finally:
        conn.close()


def test_meta_roundtrip(tmp_path):
    """set/get/get_all_meta：缺失 → None，覆盖写生效，全量读出。"""
    db = tmp_path / "market_snapshot.db"
    init_snapshot_db(db)

    assert get_meta("last_success_date", db_path=db) is None
    assert get_all_meta(db_path=db) == {}

    set_meta("last_success_date", "2026-10-13", db_path=db)
    set_meta("last_error", "boom", db_path=db)
    assert get_meta("last_success_date", db_path=db) == "2026-10-13"
    assert get_all_meta(db_path=db) == {
        "last_success_date": "2026-10-13",
        "last_error": "boom",
    }

    set_meta("last_success_date", "2026-10-14", db_path=db)  # 幂等覆盖
    assert get_meta("last_success_date", db_path=db) == "2026-10-14"
    assert len(get_all_meta(db_path=db)) == 2


def test_meta_value_not_null_and_autocreate_on_fresh_db(tmp_path):
    """FIX-4：value 列 NOT NULL；get_meta/get_all_meta 入口幂等建表（空库不抛错）。"""
    fresh = tmp_path / "never_initialized.db"
    assert get_all_meta(db_path=fresh) == {}  # 未 init 的路径：入口自建表后返回空
    assert get_meta("last_success_date", db_path=fresh) is None

    conn = _connect(fresh)
    try:
        notnull = {
            row[1]: row[3] for row in conn.execute("PRAGMA table_info(snapshot_meta)").fetchall()
        }
        assert notnull["value"] == 1  # NOT NULL
    finally:
        conn.close()


# ── Task 2: 行构造纯函数 ──────────────────────────────────────────────────────


def test_build_rows_normal_day():
    """2 票 spot → 2 行，字段逐一对断（D1: close_qfq=close_raw；prev_close 取 spot 昨收）。"""
    spot = pd.DataFrame(
        {
            "代码": ["600519", "000001"],
            "名称": ["贵州茅台", "平安银行"],
            "最新价": [1700.0, 10.5],
            "涨跌幅": [1.2, -2.5],
            "成交量": [10000.0, 200000.0],
            "成交额": [1.7e7, 2.1e6],
            "今开": [1680.0, 10.8],
            "最高": [1710.0, 10.9],
            "最低": [1675.0, 10.4],
            "昨收": [1679.84, 10.77],
        }
    )
    master = {
        "600519": {"name": "贵州茅台", "list_date": "2001-08-27"},
        "000001": {"name": "平安银行", "list_date": "1991-04-03"},
    }

    rows = build_snapshot_rows(trade_date="2026-10-13", spot=spot, prev_rows={}, master=master)

    assert len(rows) == 2
    by_code = {row["code"]: row for row in rows}
    assert by_code["600519"] == {
        "trade_date": "2026-10-13",
        "code": "600519",
        "name": "贵州茅台",
        "open": 1680.0,
        "high": 1710.0,
        "low": 1675.0,
        "close_raw": 1700.0,
        "close_qfq": 1700.0,
        "volume": 10000.0,
        "amount": 1.7e7,
        "pct_chg": 1.2,
        "prev_close": 1679.84,
        "is_st": 0,
        "is_suspended": 0,
        "limit_up_sealed": 0,
        "limit_down_sealed": 0,
        "listed_days": (date(2026, 10, 13) - date(2001, 8, 27)).days,
        "list_date": "2001-08-27",
    }
    assert set(by_code["000001"]) == _SNAPSHOT_COLUMNS
    assert by_code["000001"]["close_raw"] == 10.5
    assert by_code["000001"]["close_qfq"] == 10.5
    assert by_code["000001"]["pct_chg"] == -2.5
    assert by_code["000001"]["prev_close"] == 10.77
    assert by_code["000001"]["is_suspended"] == 0


def test_build_rows_suspended_synthesized():
    """昨日 2 票（仍在 master）、今日 spot 仅 1 票 → 缺票合成停牌行；退市票不补行。"""
    spot = pd.DataFrame(
        {
            "代码": ["600519"],
            "名称": ["贵州茅台"],
            "最新价": [1700.0],
            "涨跌幅": [0.5],
            "成交量": [9000.0],
            "成交额": [1.5e7],
            "今开": [1690.0],
            "最高": [1710.0],
            "最低": [1680.0],
            "昨收": [1691.0],
        }
    )
    prev_rows = {
        "600519": {
            "trade_date": "2026-10-12",
            "code": "600519",
            "name": "贵州茅台",
            "close_raw": 1700.0,
            "close_qfq": 1650.0,
            "list_date": "2001-08-27",
        },
        "000001": {
            "trade_date": "2026-10-12",
            "code": "000001",
            "name": "平安银行",
            "open": 10.6,
            "high": 10.8,
            "low": 10.4,
            "close_raw": 10.5,
            "close_qfq": 10.2,
            "volume": 200000.0,
            "amount": 2.1e6,
            "pct_chg": -1.0,
            "prev_close": 10.6,
            "is_st": 0,
            "is_suspended": 0,
            "limit_up_sealed": 0,
            "limit_down_sealed": 0,
            "listed_days": 100,
            "list_date": "1991-04-03",
        },
        # 退市票：昨日有行、master 名单中已消失 → 不正向补行（D2）
        "000002": {
            "trade_date": "2026-10-12",
            "code": "000002",
            "name": "万科A",
            "close_raw": 8.0,
            "close_qfq": 8.0,
            "list_date": "1991-01-29",
        },
    }
    master = {
        "600519": {"name": "贵州茅台", "list_date": "2001-08-27"},
        "000001": {"name": "平安银行", "list_date": "1991-04-03"},
    }

    rows = build_snapshot_rows(
        trade_date="2026-10-13", spot=spot, prev_rows=prev_rows, master=master
    )

    assert len(rows) == 2  # 000002 退市：不补行
    by_code = {row["code"]: row for row in rows}
    assert "000002" not in by_code
    assert by_code["600519"]["is_suspended"] == 0

    synth = by_code["000001"]
    assert synth["is_suspended"] == 1
    assert synth["open"] == synth["high"] == synth["low"] == 10.5
    assert synth["close_raw"] == 10.5  # 承接昨行 close_raw
    assert synth["close_qfq"] == 10.2  # 承接昨行 close_qfq
    assert synth["volume"] == 0
    assert synth["amount"] == 0
    assert synth["pct_chg"] == 0
    assert synth["prev_close"] == 10.5
    assert synth["name"] == "平安银行"
    assert synth["list_date"] == "1991-04-03"
    assert synth["listed_days"] == (date(2026, 10, 13) - date(1991, 4, 3)).days
    assert synth["limit_up_sealed"] == 0
    assert synth["limit_down_sealed"] == 0


def test_build_rows_nan_price_treated_suspended():
    """spot 最新价 NaN：有昨行 → 合成停牌行；无昨行 → 跳过（无价可承，不编造）。"""
    spot = pd.DataFrame(
        {
            "代码": ["600519", "301999"],
            "名称": ["贵州茅台", "新股"],
            "最新价": [float("nan"), float("nan")],
            "涨跌幅": [float("nan"), float("nan")],
            "成交量": [float("nan"), float("nan")],
            "成交额": [float("nan"), float("nan")],
            "今开": [float("nan"), float("nan")],
            "最高": [float("nan"), float("nan")],
            "最低": [float("nan"), float("nan")],
            "昨收": [1679.84, float("nan")],
        }
    )
    prev_rows = {
        "600519": {
            "trade_date": "2026-10-12",
            "code": "600519",
            "name": "贵州茅台",
            "close_raw": 1700.0,
            "close_qfq": 1650.0,
            "list_date": "2001-08-27",
        }
    }
    master = {"600519": {"name": "贵州茅台", "list_date": "2001-08-27"}}

    rows = build_snapshot_rows(
        trade_date="2026-10-13", spot=spot, prev_rows=prev_rows, master=master
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["code"] == "600519"
    assert row["is_suspended"] == 1
    assert row["open"] == row["high"] == row["low"] == row["close_raw"] == 1700.0
    assert row["close_qfq"] == 1650.0
    assert row["volume"] == 0
    assert row["amount"] == 0
    assert row["pct_chg"] == 0
    assert row["prev_close"] == 1700.0


def test_status_flags_st_and_limit_sealed():
    """D3：ST 名标记；一字板 + pct>=4.8/<=-4.8 才算封板（0/3.2/非一字 → 0）。"""
    spot = pd.DataFrame(
        {
            "代码": ["600001", "600002", "600003", "600004", "600005", "600006"],
            "名称": ["*ST金泰", "涨停股份", "跌停股份", "平盘股份", "小涨股份", "大涨未封"],
            "最新价": [5.1, 11.0, 9.0, 10.0, 10.0, 11.0],
            "涨跌幅": [1.0, 9.98, -9.97, 0.0, 3.2, 9.9],
            "成交量": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            "成交额": [1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
            "今开": [5.0, 11.0, 9.0, 10.0, 10.0, 10.0],
            "最高": [5.2, 11.0, 9.0, 10.0, 10.0, 11.0],
            "最低": [4.9, 11.0, 9.0, 10.0, 10.0, 9.9],
            "昨收": [5.0, 10.0, 10.0, 10.0, 9.7, 10.0],
        }
    )

    rows = build_snapshot_rows(trade_date="2026-10-13", spot=spot, prev_rows={}, master={})
    by_code = {row["code"]: row for row in rows}

    assert by_code["600001"]["is_st"] == 1  # *ST 含 "ST"
    assert by_code["600002"]["is_st"] == 0

    assert by_code["600002"]["limit_up_sealed"] == 1  # 一字 + 9.98
    assert by_code["600002"]["limit_down_sealed"] == 0
    assert by_code["600003"]["limit_down_sealed"] == 1  # 一字 + -9.97
    assert by_code["600003"]["limit_up_sealed"] == 0
    assert by_code["600004"]["limit_up_sealed"] == 0  # 一字但 pct=0
    assert by_code["600004"]["limit_down_sealed"] == 0
    assert by_code["600005"]["limit_up_sealed"] == 0  # pct=3.2 < 4.8
    assert by_code["600006"]["limit_up_sealed"] == 0  # 涨幅大但开!=高（非一字）

    # master 未知 → list_date/listed_days 不编造
    assert by_code["600002"]["list_date"] is None
    assert by_code["600002"]["listed_days"] is None


def test_listed_days_and_unknown_list_date():
    """master 有 list_date → 自然日差；list_date 缺失 / master 无该票 → NULL（不编造）。"""
    spot = pd.DataFrame(
        {
            "代码": ["600519", "000001", "300750"],
            "名称": ["贵州茅台", "平安银行", "宁德时代"],
            "最新价": [1700.0, 10.5, 200.0],
            "涨跌幅": [1.0, 1.0, 1.0],
            "成交量": [1.0, 1.0, 1.0],
            "成交额": [1.0, 1.0, 1.0],
            "今开": [1700.0, 10.5, 200.0],
            "最高": [1700.0, 10.5, 200.0],
            "最低": [1700.0, 10.5, 200.0],
            "昨收": [1690.0, 10.4, 199.0],
        }
    )
    master = {
        "600519": {"name": "贵州茅台", "list_date": "2026-09-28"},
        "000001": {"name": "平安银行", "list_date": None},
    }

    rows = build_snapshot_rows(trade_date="2026-10-13", spot=spot, prev_rows={}, master=master)
    by_code = {row["code"]: row for row in rows}

    assert by_code["600519"]["list_date"] == "2026-09-28"
    assert by_code["600519"]["listed_days"] == 15  # 09-28 → 10-13 共 15 个自然日
    assert by_code["000001"]["list_date"] is None  # master 有条目但未知
    assert by_code["000001"]["listed_days"] is None
    assert by_code["300750"]["list_date"] is None  # master 完全无该票
    assert by_code["300750"]["listed_days"] is None


def test_rebase_factor_detects_dividend():
    """D1（修订）：k = spot 昨收 / 昨行 close_qfq；仅 abs(k-1) > 1e-4 且有历史行才返回。"""
    spot = pd.DataFrame(
        {
            "代码": ["600519", "000001", "000002", "300750", "688981"],
            "昨收": [9.0, 20.0, 10.0005, 50.0, float("nan")],
        }
    )
    prev_rows = {
        "600519": {"close_raw": 10.0, "close_qfq": 10.0},  # 10 → 9.0：k=0.9，除权除息
        "000001": {"close_raw": 20.0, "close_qfq": 20.0},  # 昨收不变：无事件
        "000002": {"close_raw": 10.0, "close_qfq": 10.0},  # k-1=5e-5 < 1e-4：低于检测阈值
        "688981": {"close_raw": 30.0, "close_qfq": 30.0},  # spot 昨收 NaN：无法判定
        # 300750 无历史行（新股次日）→ 不做 rebase
    }

    factors = compute_rebase_factors(spot, prev_rows)

    assert set(factors) == {"600519"}
    assert factors["600519"] == pytest.approx(0.9)


def test_rebase_ignores_already_adjusted_basis():
    """FIX-1：昨日 close_qfq 已是当前基准时不再重复复权（k == 1 不返回）。

    场景：停牌股在除息后复牌，历史行已被前次 rebase 修订过（raw=10、qfq=9），
    spot 昨收 9.0 与 close_qfq 一致 → 若误用 close_raw 会二次复权（k=0.9）。
    """
    spot = pd.DataFrame({"代码": ["600519"], "昨收": [9.0]})
    prev_rows = {"600519": {"close_raw": 10.0, "close_qfq": 9.0}}

    assert compute_rebase_factors(spot, prev_rows) == {}


def test_rebase_skips_history_without_qfq_basis():
    """FIX-1：历史行无 close_qfq（旧数据）→ 无当前基准可比，不返回（不编造 k）。"""
    spot = pd.DataFrame({"代码": ["600519"], "昨收": [9.0]})
    prev_rows = {"600519": {"close_raw": 10.0}}

    assert compute_rebase_factors(spot, prev_rows) == {}


def test_norm_code_normalizes_numeric_and_string_forms():
    """FIX-2：float 整数值不带 ".0" 尾巴；数字串补足 6 位；缺值 → 空串。"""
    assert _norm_code(600519.0) == "600519"
    assert _norm_code("1") == "000001"
    assert _norm_code(1) == "000001"
    assert _norm_code("600519") == "600519"
    assert _norm_code(float("nan")) == ""
    assert _norm_code(None) == ""


def test_norm_code_public_alias_for_cross_module_consumers():
    """公开别名：跨模块消费方（outcome/regime）走 ``norm_code``，不负下划线私有名。"""
    assert norm_code is _norm_code
    assert norm_code("1") == "000001"


def test_text_and_num_reject_missing_and_non_finite():
    """FIX-3：_text 对 None/NaN/pd.NA/空串返回 None；_num 对 inf/-inf 返回 None。"""
    assert _text(None) is None
    assert _text(float("nan")) is None
    assert _text(pd.NA) is None
    assert _text(pd.NaT) is None
    assert _text("") is None
    assert _text("   ") is None
    assert _text(" 贵州茅台 ") == "贵州茅台"

    assert _num(None) is None
    assert _num(float("nan")) is None
    assert _num(float("inf")) is None
    assert _num(float("-inf")) is None
    assert _num("10.5") == 10.5
    assert _num("abc") is None


# ── Task 3: 幂等写入 + as_of 防前视读取 ──────────────────────────────────────


def _snapshot_row(trade_date: str, code: str, **overrides) -> dict:
    """完整快照行（键与表列名一致），供写入接口测试复用。"""
    row = {
        "trade_date": trade_date,
        "code": code,
        "name": f"票{code}",
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close_raw": 10.5,
        "close_qfq": 10.5,
        "volume": 1000.0,
        "amount": 10500.0,
        "pct_chg": 1.0,
        "prev_close": 10.4,
        "is_st": 0,
        "is_suspended": 0,
        "limit_up_sealed": 0,
        "limit_down_sealed": 0,
        "listed_days": 100,
        "list_date": "2020-01-01",
    }
    row.update(overrides)
    return row


def _count(db_path, table: str) -> int:
    assert table in {"market_snapshot", "security_master", "snapshot_meta"}
    conn = _connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    finally:
        conn.close()


def test_write_idempotent_same_day(tmp_path):
    """write_snapshot_rows：同日重复写行数不变；同票同日覆盖不产生新行。"""
    db = tmp_path / "market_snapshot.db"
    rows = [_snapshot_row("2026-10-10", "600519"), _snapshot_row("2026-10-10", "000001")]

    assert write_snapshot_rows("2026-10-10", rows, db_path=db) == 2
    assert write_snapshot_rows("2026-10-10", rows, db_path=db) == 2  # 幂等
    assert _count(db, "market_snapshot") == 2
    assert latest_snapshot_date(db_path=db) == "2026-10-10"

    changed = [_snapshot_row("2026-10-10", "600519", close_raw=11.0, close_qfq=11.0)]
    write_snapshot_rows("2026-10-10", changed, db_path=db)
    assert _count(db, "market_snapshot") == 2  # INSERT OR REPLACE 覆盖
    universe = read_universe("2026-10-10", db_path=db)
    assert [r["close_raw"] for r in universe if r["code"] == "600519"] == [11.0]


def test_write_snapshot_rows_bulk_multi_day_single_txn(tmp_path):
    """write_snapshot_rows_bulk：多交易日一次写入（行内 trade_date 逐行取用）+ upsert 语义。"""
    db = tmp_path / "market_snapshot.db"
    rows = [
        _snapshot_row("2026-10-09", "600519"),
        _snapshot_row("2026-10-10", "600519"),
        _snapshot_row("2026-10-10", "000001"),
    ]

    assert write_snapshot_rows_bulk(rows, db_path=db) == 3
    assert _count(db, "market_snapshot") == 3
    assert latest_snapshot_date(db_path=db) == "2026-10-10"

    # 重跑幂等 + 覆盖（同票同日 upsert 不产生新行）
    changed = [_snapshot_row("2026-10-10", "600519", close_raw=11.0, close_qfq=11.0)]
    assert write_snapshot_rows_bulk(changed, db_path=db) == 1
    assert _count(db, "market_snapshot") == 3
    universe = read_universe("2026-10-10", db_path=db)
    assert [r["close_raw"] for r in universe if r["code"] == "600519"] == [11.0]

    # 空批 → 0（不建行）
    assert write_snapshot_rows_bulk([], db_path=db) == 0


def test_read_universe_rejects_future_as_of(tmp_path):
    """R3：as_of 晚于最新数据日期 → SnapshotNotFreshError（消息含最新日期），不返回记录。"""
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows("2026-10-10", [_snapshot_row("2026-10-10", "600519")], db_path=db)

    with pytest.raises(SnapshotNotFreshError) as exc:
        read_universe("2026-10-20", db_path=db)

    assert "2026-10-10" in str(exc.value)  # 消息含最新数据日期
    assert "2026-10-20" in str(exc.value)


def test_read_universe_empty_day_raises(tmp_path):
    """as_of 在数据范围内但该日无行 → SnapshotEmptyError（不静默返回他日数据）。"""
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows("2026-10-10", [_snapshot_row("2026-10-10", "600519")], db_path=db)

    with pytest.raises(SnapshotEmptyError) as exc:
        read_universe("2026-10-08", db_path=db)

    assert "2026-10-08" in str(exc.value)


def test_read_universe_delisted_symbol_history(tmp_path):
    """R2：退市标的历史可查；其退市后的日期结果不含该票（当日有他行不报错）。"""
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows(
        "2025-03-01",
        [
            _snapshot_row("2025-03-01", "000002", name="万科A"),
            _snapshot_row("2025-03-01", "600519", name="贵州茅台"),
        ],
        db_path=db,
    )
    write_snapshot_rows(
        "2025-09-01",
        [
            _snapshot_row("2025-09-01", "600519", name="贵州茅台"),
            _snapshot_row("2025-09-01", "600520", name="停牌股", is_suspended=1),
        ],
        db_path=db,
    )

    early = read_universe("2025-03-01", db_path=db)
    assert "000002" in {row["code"] for row in early}  # 退市前历史含 X

    late = read_universe("2025-09-01", db_path=db)
    assert {row["code"] for row in late} == {"600519", "600520"}  # 退市后不含 X

    active_only = read_universe("2025-09-01", include_suspended=False, db_path=db)
    assert {row["code"] for row in active_only} == {"600519"}


def test_read_bars_window_and_guard(tmp_path):
    """read_bars：区间按 (trade_date, code) 升序；end>as_of 与 as_of>latest 双保险拒绝。"""
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows("2026-10-08", [_snapshot_row("2026-10-08", "600519")], db_path=db)
    write_snapshot_rows(
        "2026-10-09",
        [_snapshot_row("2026-10-09", "600519"), _snapshot_row("2026-10-09", "000001")],
        db_path=db,
    )
    write_snapshot_rows("2026-10-10", [_snapshot_row("2026-10-10", "600519")], db_path=db)

    df = read_bars(["600519"], "2026-10-08", "2026-10-09", as_of="2026-10-10", db_path=db)
    assert list(df["trade_date"]) == ["2026-10-08", "2026-10-09"]
    assert set(df["code"]) == {"600519"}
    assert {"trade_date", "code", "close_raw", "close_qfq", "volume"} <= set(df.columns)
    assert float(df.iloc[-1]["close_raw"]) == 10.5

    with pytest.raises(SnapshotNotFreshError) as exc:
        read_bars(["600519"], "2026-10-08", "2026-10-12", as_of="2026-10-10", db_path=db)
    assert "2026-10-12" in str(exc.value)  # end 晚于 as_of：拒绝读未来

    with pytest.raises(SnapshotNotFreshError) as exc2:
        read_bars(["600519"], "2026-10-08", "2026-10-10", as_of="2026-10-20", db_path=db)
    assert "2026-10-10" in str(exc2.value)  # as_of 超最新：消息含最新日期

    with pytest.raises(SnapshotEmptyError):
        read_bars(["999999"], "2026-10-08", "2026-10-10", as_of="2026-10-10", db_path=db)


def test_apply_rebase_multiplies_history_only(tmp_path):
    """apply_rebase：仅 trade_date < before_date 的行 close_qfq × k，close_raw 与他票不动。"""
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows(
        "2026-10-09",
        [
            _snapshot_row("2026-10-09", "600519", close_raw=10.0, close_qfq=10.0),
            _snapshot_row("2026-10-09", "000001", close_raw=20.0, close_qfq=20.0),
        ],
        db_path=db,
    )
    write_snapshot_rows(
        "2026-10-10",
        [_snapshot_row("2026-10-10", "600519", close_raw=10.2, close_qfq=10.2)],
        db_path=db,
    )
    write_snapshot_rows(
        "2026-10-12",
        [_snapshot_row("2026-10-12", "600519", close_raw=9.0, close_qfq=9.0)],
        db_path=db,
    )

    assert apply_rebase("600519", 0.9, "2026-10-12", db_path=db) == 2  # 10-09 / 10-10 两行

    df = read_bars(["600519"], "2026-10-01", "2026-10-12", as_of="2026-10-12", db_path=db)
    df = df.set_index("trade_date")
    assert df.loc["2026-10-09", "close_qfq"] == pytest.approx(9.0)  # 10.0 × 0.9
    assert df.loc["2026-10-09", "close_raw"] == pytest.approx(10.0)  # raw 不动
    assert df.loc["2026-10-10", "close_qfq"] == pytest.approx(9.18)  # 10.2 × 0.9
    assert df.loc["2026-10-12", "close_qfq"] == pytest.approx(9.0)  # 不早于 before_date：不动
    assert df.loc["2026-10-12", "close_raw"] == pytest.approx(9.0)

    other = read_bars(["000001"], "2026-10-01", "2026-10-09", as_of="2026-10-12", db_path=db)
    assert float(other.iloc[0]["close_qfq"]) == pytest.approx(20.0)  # 他票不动


def test_security_master_upsert_roundtrip(tmp_path):
    """upsert/get_security_master：同 code 覆盖不重复；空字段不清空已有值。"""
    db = tmp_path / "market_snapshot.db"
    assert get_security_master(db_path=db) == {}  # 空库入口自建表

    n = upsert_security_master(
        [
            {"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"},
            {"code": "1", "name": "平安银行", "list_date": "1991-04-03"},  # 归一 000001
        ],
        db_path=db,
    )
    assert n == 2

    upsert_security_master(
        [{"code": "600519", "name": "贵州茅台A", "list_date": "2001-08-27"}], db_path=db
    )
    master = get_security_master(db_path=db)
    assert set(master) == {"600519", "000001"}
    assert master["600519"]["name"] == "贵州茅台A"
    assert master["000001"] == {
        "name": "平安银行",
        "list_date": "1991-04-03",
        # add-microcap-lane-guardrails：主表形状扩 delist_date/delist_source（存续标的为 None）
        "delist_date": None,
        "delist_source": None,
    }
    assert _count(db, "security_master") == 2

    upsert_security_master(  # 新值缺失 → COALESCE 保留旧值
        [{"code": "600519", "name": None, "list_date": None}], db_path=db
    )
    assert get_security_master(db_path=db)["600519"]["name"] == "贵州茅台A"


# ── Task 4: stale 状态纯函数 + 缓存 TTL 豁免契约 ─────────────────────────────

# 固定日历（含 2026-10-08 四 / 10-09 五 / 10-12 一 / 10-13 二），避免测试依赖真实日历
_CAL = '["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]'


def test_status_fresh_db_not_stale():
    """D5：fresh DB（无 last_attempt_at）不告警——即使时刻已过 18:00。"""
    status = compute_snapshot_status({}, now=datetime(2026, 10, 13, 18, 30))

    assert status == {
        "latest_date": None,
        "stale": False,
        "message": None,
        "last_error": None,
        "last_error_date": None,
    }


def test_status_failed_today_stale():
    """D5：last_error_date == 今天 → 立即 stale（早于 18:00 也告警）；文案逐字。"""
    meta = {
        "last_attempt_at": "2026-10-13T15:31:00",
        "last_success_date": "2026-10-08",
        "last_error": "boom",
        "last_error_date": "2026-10-13",
    }
    status = compute_snapshot_status(meta, now=datetime(2026, 10, 13, 15, 40))

    assert status["stale"] is True
    assert status["message"] == "行情快照未更新（数据日期: 2026-10-08）"
    assert status["latest_date"] == "2026-10-08"
    assert status["last_error"] == "boom"
    assert status["last_error_date"] == "2026-10-13"

    never = {  # 从未成功过：latest 为 None → 无日期文案
        "last_attempt_at": "2026-10-13T15:31:00",
        "last_error": "boom",
        "last_error_date": "2026-10-13",
    }
    status2 = compute_snapshot_status(never, now=datetime(2026, 10, 13, 15, 40))
    assert status2["stale"] is True
    assert status2["message"] == "行情快照未更新"
    assert status2["latest_date"] is None


def test_status_after_18_without_success_stale():
    """D5：18:00 后 last_success_date 落后于日历 expected（含从未成功）→ stale。"""
    base = {"last_attempt_at": "2026-10-13T15:31:00", "trade_calendar": _CAL}

    never_success = compute_snapshot_status(base, now=datetime(2026, 10, 13, 18, 0))
    assert never_success["stale"] is True
    assert never_success["message"] == "行情快照未更新"

    lagging = compute_snapshot_status(
        {**base, "last_success_date": "2026-10-12"}, now=datetime(2026, 10, 13, 18, 0)
    )
    assert lagging["stale"] is True
    assert lagging["message"] == "行情快照未更新（数据日期: 2026-10-12）"


def test_status_before_18_pending_not_stale():
    """D5：18:00 前当日批未完成（有 attempt、无 success）不算 stale。"""
    meta = {"last_attempt_at": "2026-10-13T15:31:00", "trade_calendar": _CAL}
    status = compute_snapshot_status(meta, now=datetime(2026, 10, 13, 17, 59))

    assert status["stale"] is False
    assert status["message"] is None


def test_status_success_clears():
    """D5：last_success_date == expected → 非 stale；历史错误字段仍如实回传。"""
    meta = {
        "last_attempt_at": "2026-10-13T15:31:00",
        "last_success_date": "2026-10-13",
        "last_error": "yesterday boom",
        "last_error_date": "2026-10-12",
        "trade_calendar": _CAL,
    }
    status = compute_snapshot_status(meta, now=datetime(2026, 10, 13, 18, 30))

    assert status == {
        "latest_date": "2026-10-13",
        "stale": False,
        "message": None,
        "last_error": "yesterday boom",
        "last_error_date": "2026-10-12",
    }


def test_status_no_calendar_weekday_fallback():
    """D5 退化：无日历 → 工作日取今天；周末取最近工作日（周六 → 周五）。"""
    weekend = {"last_attempt_at": "2026-10-17T15:31:00", "last_success_date": "2026-10-16"}
    assert compute_snapshot_status(weekend, now=datetime(2026, 10, 17, 19, 0))["stale"] is False

    weekend_old = {"last_attempt_at": "2026-10-17T15:31:00", "last_success_date": "2026-10-15"}
    status = compute_snapshot_status(weekend_old, now=datetime(2026, 10, 17, 19, 0))
    assert status["stale"] is True
    assert status["message"] == "行情快照未更新（数据日期: 2026-10-15）"

    weekday = {"last_attempt_at": "2026-10-13T15:31:00", "last_success_date": "2026-10-12"}
    assert compute_snapshot_status(weekday, now=datetime(2026, 10, 13, 19, 0))["stale"] is True


def test_cache_cleanup_does_not_touch_snapshot(tmp_path):
    """cache-management delta：DataCache 过期清理/清空不触碰快照三表（TTL 豁免契约）。"""
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows(
        "2025-06-30",
        [_snapshot_row("2025-06-30", "600519"), _snapshot_row("2025-06-30", "000001")],
        db_path=db,
    )
    upsert_security_master(
        [{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}], db_path=db
    )
    set_meta("last_success_date", "2025-12-31", db_path=db)
    before = {
        table: _count(db, table)
        for table in ("market_snapshot", "security_master", "snapshot_meta")
    }

    cache = DataCache(str(tmp_path / "cache.db"))
    try:
        cache.set("600519:quote", {"close": 1.0}, ttl_seconds=-10)  # 已过期
        assert cache.get("600519:quote") is None  # 读取即触发该条 TTL 清理
        cache.set("000001:quote", {"close": 2.0}, ttl_seconds=-10)
        assert cache.clear_all() >= 1  # 全量清理
    finally:
        cache.close()

    assert {table: _count(db, table) for table in before} == before  # 快照表行数不变
    assert get_all_meta(db_path=db) == {"last_success_date": "2025-12-31"}  # meta 键不变


# ── 评审修正（2026-10-13）：北京时区 / 严格日期解析 / rebase 守卫 / meta 批量写 ──


def test_status_aware_now_converted_to_beijing_time():
    """评审修正：aware now 先转北京时区（10:30Z = 18:30 北京）→ 触发 18:00 后 stale 分支。

    naive 输入视为已是北京时间（同墙钟同判定），不做二次换算。
    """
    meta = {
        "last_attempt_at": "2026-10-12T15:31:00",
        "last_success_date": "2026-10-09",
        "trade_calendar": _CAL,
    }
    utc_now = datetime(2026, 10, 12, 10, 30, tzinfo=ZoneInfo("UTC"))

    status = compute_snapshot_status(meta, now=utc_now)
    assert status["stale"] is True
    assert status["message"] == "行情快照未更新（数据日期: 2026-10-09）"

    # naive 输入 = 已是北京本地时间：同墙钟 18:30 → 同判定
    assert compute_snapshot_status(meta, now=datetime(2026, 10, 12, 18, 30))["stale"] is True
    # 北京同日 15:31 尚未到 18:00 → 不 stale（时区换算不改变边界语义）
    assert compute_snapshot_status(meta, now=datetime(2026, 10, 12, 15, 31))["stale"] is False


def test_date_params_reject_non_canonical_values(tmp_path):
    """评审修正：日期入参 date.fromisoformat 解析 + 严格 YYYY-MM-DD；非规范左 fail loud。"""
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows("2026-10-10", [_snapshot_row("2026-10-10", "600519")], db_path=db)
    write_snapshot_rows("2026-10-12", [_snapshot_row("2026-10-12", "600519")], db_path=db)

    with pytest.raises(SnapshotError) as exc:
        read_bars(["600519"], "2026-10-01", "2026-10-12", as_of="2026-10-5", db_path=db)
    assert "2026-10-5" in str(exc.value)  # 消息含坏值
    assert "YYYY-MM-DD" in str(exc.value)

    with pytest.raises(SnapshotError) as exc2:
        read_universe("2026-10-5", db_path=db)
    assert "2026-10-5" in str(exc2.value)

    with pytest.raises(SnapshotError) as exc3:
        read_bars(["600519"], "2026-10-5", "2026-10-12", as_of="2026-10-12", db_path=db)
    assert "2026-10-5" in str(exc3.value)

    with pytest.raises(SnapshotError) as exc4:
        read_bars(["600519"], "2026-10-01", "2026-10-12", as_of="20261012", db_path=db)
    assert "20261012" in str(exc4.value)

    with pytest.raises(SnapshotError) as exc5:
        apply_rebase("600519", 0.9, "2026-10-5", db_path=db)
    assert "2026-10-5" in str(exc5.value)


@pytest.mark.parametrize(
    "factor", [0.0, -0.5, float("nan"), float("inf"), float("-inf"), None, "abc"]
)
def test_apply_rebase_rejects_invalid_factor(tmp_path, factor):
    """评审修正：factor 必须有限且 > 0，否则 SnapshotError；坏 factor 不得落库。"""
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows("2026-10-09", [_snapshot_row("2026-10-09", "600519")], db_path=db)

    with pytest.raises(SnapshotError) as exc:
        apply_rebase("600519", factor, "2026-10-10", db_path=db)
    assert "factor" in str(exc.value)

    assert read_universe("2026-10-09", db_path=db)[0]["close_qfq"] == 10.5  # 未被修改


def test_read_bars_coerces_numeric_columns_with_nulls(tmp_path):
    """评审修正：read_bars 出口数值列 pd.to_numeric(errors="coerce")——NULL 混入仍是 float64。"""
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows(
        "2026-10-09",
        [
            _snapshot_row("2026-10-09", "600519", listed_days=None, list_date=None),
            _snapshot_row("2026-10-09", "000001", listed_days=100),
        ],
        db_path=db,
    )

    df = read_bars(["600519", "000001"], "2026-10-01", "2026-10-09", as_of="2026-10-09", db_path=db)

    for col in (
        "open",
        "high",
        "low",
        "close_raw",
        "close_qfq",
        "volume",
        "amount",
        "pct_chg",
        "prev_close",
        "listed_days",
    ):
        assert df[col].dtype == "float64", col
    assert len(df) == 2
    assert df["listed_days"].isna().sum() == 1  # NULL 保留为 NaN，不丢行不编造


def test_set_meta_many_batch_write(tmp_path):
    """评审修正：set_meta_many 单事务批量写——写入后全部键一次可见，覆盖/空映射安全。"""
    db = tmp_path / "market_snapshot.db"

    set_meta_many({"last_success_date": "2026-10-13", "last_error": ""}, db_path=db)
    assert get_all_meta(db_path=db) == {"last_success_date": "2026-10-13", "last_error": ""}

    set_meta_many({"last_error": "boom", "last_error_date": "2026-10-13"}, db_path=db)
    assert get_all_meta(db_path=db) == {
        "last_success_date": "2026-10-13",
        "last_error": "boom",
        "last_error_date": "2026-10-13",
    }

    set_meta_many({}, db_path=db)  # 空映射：无操作不报错
    assert len(get_all_meta(db_path=db)) == 3


def test_set_meta_many_delete_keys_removes_and_writes_in_one_call(tmp_path):
    """评审修正 2：set_meta_many(delete_keys=...) 单事务「先删后写」。

    成功路径一次调用同时清失败标记并写成功键——分两步会在两次提交之间产生
    「成功已写、错误未清」的中间态，状态端点可能闪告警横幅。断言单次调用后的
    终态即全部就位；空 delete_keys 不触碰既有键。
    """
    db = tmp_path / "market_snapshot.db"
    set_meta_many(
        {"last_error": "boom", "last_error_date": "2026-10-12", "last_success_date": "2026-10-09"},
        db_path=db,
    )

    set_meta_many(
        {"last_success_date": "2026-10-13", "last_attempt_at": "2026-10-13T15:31:00"},
        db_path=db,
        delete_keys=("last_error", "last_error_date"),
    )
    assert get_all_meta(db_path=db) == {
        "last_success_date": "2026-10-13",
        "last_attempt_at": "2026-10-13T15:31:00",
    }

    set_meta_many({"trade_calendar": "[]"}, db_path=db, delete_keys=())  # 空删除集：无操作
    assert get_all_meta(db_path=db) == {
        "last_success_date": "2026-10-13",
        "last_attempt_at": "2026-10-13T15:31:00",
        "trade_calendar": "[]",
    }

    set_meta_many({}, db_path=db, delete_keys=("trade_calendar", "missing"))  # 只删不写
    assert get_all_meta(db_path=db) == {
        "last_success_date": "2026-10-13",
        "last_attempt_at": "2026-10-13T15:31:00",
    }


def test_set_meta_many_delete_keys_rolls_back_together(tmp_path):
    """delete 与 insert 必须同一事务：插入失败（value NOT NULL）→ 删除一并回滚。"""
    db = tmp_path / "market_snapshot.db"
    set_meta_many({"last_error": "boom", "keep": "1"}, db_path=db)
    before = get_all_meta(db_path=db)

    with pytest.raises(sqlite3.IntegrityError):
        set_meta_many({"last_success_date": None}, db_path=db, delete_keys=("keep",))

    assert get_all_meta(db_path=db) == before  # 删除未随半途失败落库


def test_status_no_calendar_sunday_monday_fallback():
    """D5 退化补缺：周日 → 上周五；周一 → 周一本身（不得回退到上周五）。"""
    sunday_fresh = {"last_attempt_at": "2026-10-18T15:31:00", "last_success_date": "2026-10-16"}
    assert (
        compute_snapshot_status(sunday_fresh, now=datetime(2026, 10, 18, 19, 0))["stale"] is False
    )

    sunday_stale = {"last_attempt_at": "2026-10-18T15:31:00", "last_success_date": "2026-10-15"}
    status = compute_snapshot_status(sunday_stale, now=datetime(2026, 10, 18, 19, 0))
    assert status["stale"] is True
    assert status["message"] == "行情快照未更新（数据日期: 2026-10-15）"

    monday_lagging = {"last_attempt_at": "2026-10-19T15:31:00", "last_success_date": "2026-10-16"}
    monday_status = compute_snapshot_status(monday_lagging, now=datetime(2026, 10, 19, 19, 0))
    assert monday_status["stale"] is True  # expected = 10-19 本身；上周五不算新鲜

    monday_fresh = {"last_attempt_at": "2026-10-19T15:31:00", "last_success_date": "2026-10-19"}
    assert (
        compute_snapshot_status(monday_fresh, now=datetime(2026, 10, 19, 19, 0))["stale"] is False
    )


def test_rebase_factor_threshold_is_strict():
    """D1 补缺：abs(k-1) > 1e-4 不取等号——恰在阈值不 rebase，超一个 ulp 才 rebase。"""
    at_threshold = 1.0001  # 1e-4 阈值侧的最近 double（比阈值小约 1.1e-17）
    above_threshold = math.nextafter(at_threshold, 2.0)
    assert abs(at_threshold - 1.0) <= 1e-4 < abs(above_threshold - 1.0)  # 钉住边界两侧

    spot = pd.DataFrame({"代码": ["600519", "000001"], "昨收": [at_threshold, above_threshold]})
    prev_rows = {
        "600519": {"close_raw": 1.0, "close_qfq": 1.0},
        "000001": {"close_raw": 1.0, "close_qfq": 1.0},
    }

    factors = compute_rebase_factors(spot, prev_rows)

    assert set(factors) == {"000001"}
    assert factors["000001"] == pytest.approx(above_threshold)


def test_rebase_skips_zero_basis_without_error():
    """D1 补缺：历史 close_qfq == 0（坏数据）→ 跳过不除零、不返回。"""
    spot = pd.DataFrame({"代码": ["600519"], "昨收": [5.0]})
    prev_rows = {"600519": {"close_raw": 0.0, "close_qfq": 0.0}}

    assert compute_rebase_factors(spot, prev_rows) == {}


def test_read_bars_orders_by_date_and_code_and_never_past_as_of(tmp_path):
    """read_bars 补缺：多票按 (trade_date, code) 升序；as_of 之后的行绝不返回。"""
    db = tmp_path / "market_snapshot.db"
    for trade_date in ("2026-10-08", "2026-10-09", "2026-10-10"):
        write_snapshot_rows(
            trade_date,
            [_snapshot_row(trade_date, "600519"), _snapshot_row(trade_date, "000001")],
            db_path=db,
        )

    df = read_bars(["600519", "000001"], "2026-10-01", "2026-10-09", as_of="2026-10-10", db_path=db)
    assert list(zip(df["trade_date"], df["code"], strict=True)) == [
        ("2026-10-08", "000001"),
        ("2026-10-08", "600519"),
        ("2026-10-09", "000001"),
        ("2026-10-09", "600519"),
    ]
    assert max(df["trade_date"]) <= "2026-10-09"  # 表内 10-10 的行不出现在 end 窗口外

    # as_of 早于表内最新数据（历史读取）：as_of 之后的行绝不返回
    historical = read_bars(["600519"], "2026-10-01", "2026-10-09", as_of="2026-10-09", db_path=db)
    assert list(historical["trade_date"]) == ["2026-10-08", "2026-10-09"]

    # as_of 晚于表内最新数据 → 整体拒绝（消息含最新数据日期，不返回任何行）
    with pytest.raises(SnapshotNotFreshError) as exc:
        read_bars(["600519"], "2026-10-01", "2026-10-11", as_of="2026-10-11", db_path=db)
    assert "2026-10-10" in str(exc.value)

    # end == as_of == latest：包含边界行
    inclusive = read_bars(["600519"], "2026-10-01", "2026-10-10", as_of="2026-10-10", db_path=db)
    assert list(inclusive["trade_date"]) == ["2026-10-08", "2026-10-09", "2026-10-10"]


def test_delete_meta_removes_keys_in_one_transaction(tmp_path):
    """T6 评审修正：delete_meta 单事务删多键（成功路径清 last_error 用）。"""
    db = tmp_path / "market_snapshot.db"
    set_meta_many({"a": "1", "b": "2", "c": "3"}, db_path=db)

    assert delete_meta(["a", "b"], db_path=db) == 2
    assert get_all_meta(db_path=db) == {"c": "3"}

    assert delete_meta(["missing"], db_path=db) == 0  # 缺失键不计、不抛
    assert delete_meta([], db_path=db) == 0  # 空键集：无操作
    assert get_all_meta(db_path=db) == {"c": "3"}


def test_latest_snapshot_date_before_excludes_bound_date(tmp_path):
    """同日重跑基线：``before`` 取严格早于该日的最大交易日（不含当日已写行）。

    非规范 ``before``（"20261013"、"2026-10-5"）→ ``SnapshotError``（评审修正：
    日期入参一律严格 ``YYYY-MM-DD``，与读取接口同款 fail loud）。
    """
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows("2026-10-12", [_snapshot_row("2026-10-12", "600519")], db_path=db)
    write_snapshot_rows("2026-10-13", [_snapshot_row("2026-10-13", "600519")], db_path=db)

    assert latest_snapshot_date(db_path=db) == "2026-10-13"
    assert latest_snapshot_date(db_path=db, before="2026-10-13") == "2026-10-12"
    assert latest_snapshot_date(db_path=db, before="2026-10-12") is None
    assert latest_snapshot_date(db_path=db, before="2026-10-14") == "2026-10-13"

    with pytest.raises(SnapshotError) as exc:
        latest_snapshot_date(db_path=db, before="20261013")
    assert "20261013" in str(exc.value)
    assert "YYYY-MM-DD" in str(exc.value)

    with pytest.raises(SnapshotError) as exc2:
        latest_snapshot_date(db_path=db, before="2026-10-5")
    assert "2026-10-5" in str(exc2.value)


def test_reset_snapshot_db_clears_all_three_tables(tmp_path):
    """T8 造数钩子：reset 清空快照三表，返回删除行数（其余库不受影响）。"""
    db = tmp_path / "market_snapshot.db"
    write_snapshot_rows("2026-10-13", [_snapshot_row("2026-10-13", "600519")], db_path=db)
    upsert_security_master([{"code": "600519", "name": "贵州茅台"}], db_path=db)
    set_meta_many({"last_success_date": "2026-10-13"}, db_path=db)

    assert reset_snapshot_db(db_path=db) == 3  # 1 行快照 + 1 行主表 + 1 个 meta 键

    assert latest_snapshot_date(db_path=db) is None
    assert get_security_master(db_path=db) == {}
    assert get_all_meta(db_path=db) == {}
    assert reset_snapshot_db(db_path=db) == 0  # 幂等：空库再清为 0
