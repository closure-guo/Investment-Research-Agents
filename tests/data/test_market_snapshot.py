"""TDD tests for data/snapshot.py — 市场快照 schema/meta 与行构造纯函数。

覆盖 add-market-snapshot 计划 Task 1（schema/连接/meta 工具）与 Task 2
（build_snapshot_rows / compute_rebase_factors）的验收行为。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from finance_agent.data.snapshot import (
    _connect,
    _norm_code,
    _num,
    _text,
    build_snapshot_rows,
    compute_rebase_factors,
    get_all_meta,
    get_meta,
    init_snapshot_db,
    set_meta,
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
        assert master_cols == {"code", "name", "list_date", "updated_at"}

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
