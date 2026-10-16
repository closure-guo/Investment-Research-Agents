"""add-market-snapshot T11 回填脚本单测（零网络：stub client 注入）。

覆盖：(a) 断点续跑跳过且不重取；(b) 单票取数失败计数后继续；(c) 行字段逐一对断
（含显式 0 状态位与 None 列）；(d) ``--dry-run`` 不写库；(e) 主表为空退 2；
(f) symbols-file 解析（空行/注释跳过）。另钉：限速只在两次真实取数之间 sleep；
非 dry-run 起跑打印一次「不得用于 compute_rebase_factors 链」的警示。
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd
import scripts.backfill_market_snapshot as backfill_module
from scripts.backfill_market_snapshot import (
    _dedupe,
    _parse_symbols_file,
    _split_csv,
    main,
    run_backfill,
)

from finance_agent.data.snapshot import upsert_security_master, write_snapshot_rows

_START = "2026-10-01"
_END = "2026-10-12"


def _frame(rows: list[dict]) -> pd.DataFrame:
    """按 T5 包装出口列构造历史帧（八列：日期为 date 对象，同 akshare 出口形态）。

    用例行未显式给 最高/最低 时按 ``收盘_raw ± 0.1`` 派生——回填契约关心「列在不在、
    映射对不对」，取值细节由字段映射用例单独钉死（extend-backfill-high-low）。
    """
    for row in rows:
        row.setdefault("最高", float(row["收盘_raw"]) + 0.1)
        row.setdefault("最低", float(row["收盘_raw"]) - 0.1)
    return pd.DataFrame(
        rows,
        columns=["日期", "开盘", "最高", "最低", "收盘_raw", "收盘_qfq", "成交量", "成交额"],
    )


class FakeHistoryClient:
    """只实现 fetch_symbol_history_raw_qfq 的 stub 客户端（零网络）。"""

    def __init__(self, *, data=None, fail=()):
        self.data = data or {}
        self.fail = set(fail)
        self.calls: list[str] = []
        self.requested: tuple[str, str] | None = None

    def fetch_symbol_history_raw_qfq(self, code: str, start: str, end: str):
        self.calls.append(code)
        self.requested = (start, end)
        if code in self.fail:
            return None
        return self.data.get(code)


def _rows_of(db, code: str) -> list[dict]:
    """直读快照行（断言用；避免经 read_universe 的 as_of 守卫绕路）。"""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM market_snapshot WHERE code = ? ORDER BY trade_date", (code,)
            )
        ]
    finally:
        conn.close()


def test_resume_skips_covered_symbol_without_refetch(tmp_path):
    """(a) 区间内已有行 → 跳过、不取数、不 sleep；区间外有行不算覆盖。"""
    db = tmp_path / "snap.db"
    write_snapshot_rows(
        "2026-10-09",
        [
            {
                "trade_date": "2026-10-09",
                "code": "000001",
                "close_raw": 5.0,
                "close_qfq": 5.0,
                "amount": 1_000_000.0,  # 覆盖判据含 amount/high/low：字段齐备才跳过
                "high": 5.2,
                "low": 4.8,
            }
        ],
        db_path=db,
    )
    write_snapshot_rows(
        "2026-09-01",  # 区间外：不构成断点续跑覆盖
        [{"trade_date": "2026-09-01", "code": "600519", "close_raw": 9.0, "close_qfq": 9.0}],
        db_path=db,
    )
    sleeps: list[float] = []
    client = FakeHistoryClient(
        data={
            "600519": _frame(
                [
                    {
                        "日期": date(2026, 10, 9),
                        "开盘": 10.0,
                        "收盘_raw": 10.5,
                        "收盘_qfq": 10.2,
                        "成交量": 1000.0,
                    }
                ]
            )
        }
    )

    summary = run_backfill(
        start=_START,
        end=_END,
        symbols=["000001", "600519"],
        client=client,
        db_path=db,
        sleep_seconds=0.5,
        sleep_func=sleeps.append,
    )

    assert summary["skipped_resumed"] == 1
    assert summary["symbols_ok"] == 1
    assert summary["symbols_failed"] == 0
    assert summary["rows_written"] == 1
    assert client.calls == ["600519"]  # 已覆盖票不重取
    assert sleeps == []  # 跳过票不触发限速等待
    assert len(_rows_of(db, "000001")) == 1  # 既有行保持（未被重复写）
    assert [r["trade_date"] for r in _rows_of(db, "600519")] == ["2026-09-01", "2026-10-09"]
    assert client.requested == ("20261001", "20261012")  # akshare 紧凑日期入参


def test_resume_refetches_rows_missing_amount(tmp_path):
    """(a2) 区间内已有行但 amount 缺失（high/low 齐备）→ 不跳过、重取 upsert 补齐。"""
    db = tmp_path / "snap.db"
    write_snapshot_rows(
        "2026-10-09",
        [
            {
                "trade_date": "2026-10-09",
                "code": "000001",
                "close_raw": 5.0,
                "close_qfq": 5.0,
                "high": 5.2,
                "low": 4.8,
            }
        ],
        db_path=db,
    )
    client = FakeHistoryClient(
        data={
            "000001": _frame(
                [
                    {
                        "日期": date(2026, 10, 9),
                        "开盘": 10.0,
                        "收盘_raw": 10.5,
                        "收盘_qfq": 10.2,
                        "成交量": 1000.0,
                        "成交额": 2_500_000.0,
                    }
                ]
            )
        }
    )

    summary = run_backfill(
        start=_START,
        end=_END,
        symbols=["000001"],
        client=client,
        db_path=db,
        sleep_seconds=0.5,
        sleep_func=lambda _: None,
    )

    assert summary["skipped_resumed"] == 0  # 缺 amount 不构成覆盖
    assert client.calls == ["000001"]  # 重取
    rows = _rows_of(db, "000001")
    assert len(rows) == 1  # upsert 不产生重复行
    assert rows[0]["amount"] == 2_500_000.0  # amount 已补齐


def test_resume_refetches_rows_missing_high_low(tmp_path):
    """(a3) 既有行 amount 齐备但 high/low 空（旧六列契约产物）→ 不跳过、重取补齐
    （extend-backfill-high-low；spec 场景「缺 high/low 的既有行重取补齐」）。"""
    db = tmp_path / "snap.db"
    write_snapshot_rows(
        "2026-10-09",
        [
            {
                "trade_date": "2026-10-09",
                "code": "000001",
                "close_raw": 5.0,
                "close_qfq": 5.0,
                "amount": 1_000_000.0,
            }
        ],
        db_path=db,
    )
    client = FakeHistoryClient(
        data={
            "000001": _frame(
                [
                    {
                        "日期": date(2026, 10, 9),
                        "开盘": 10.0,
                        "收盘_raw": 10.5,
                        "收盘_qfq": 10.5,
                        "成交量": 1000.0,
                        "成交额": 2_500_000.0,
                    }
                ]
            )
        }
    )

    summary = run_backfill(
        start=_START,
        end=_END,
        symbols=["000001"],
        client=client,
        db_path=db,
        sleep_seconds=0,
    )

    assert summary["skipped_resumed"] == 0  # 缺 high/low 不构成覆盖
    assert client.calls == ["000001"]  # 重取
    rows = _rows_of(db, "000001")
    assert len(rows) == 1  # upsert 不产生重复行
    assert rows[0]["high"] == 10.6 and rows[0]["low"] == 10.4  # 已补齐


def test_fetch_none_counts_failed_and_continues(tmp_path, capsys):
    """(b) 单票取数 None → failed 计数后继续；两次取数之间限速一次。"""
    db = tmp_path / "snap.db"
    sleeps: list[float] = []
    client = FakeHistoryClient(
        data={
            "000001": _frame(
                [
                    {
                        "日期": date(2026, 10, 9),
                        "开盘": 10.0,
                        "收盘_raw": 10.5,
                        "收盘_qfq": 10.5,
                        "成交量": 1000.0,
                    }
                ]
            )
        },
        fail={"600519"},
    )

    summary = run_backfill(
        start=_START,
        end=_END,
        symbols=["600519", "000001"],
        client=client,
        db_path=db,
        sleep_seconds=0.5,
        sleep_func=sleeps.append,
    )

    assert summary["symbols_failed"] == 1
    assert summary["symbols_ok"] == 1
    assert summary["rows_written"] == 1
    assert client.calls == ["600519", "000001"]  # 失败不短路后续票
    assert sleeps == [0.5]  # 仅两次真实取数之间等一次
    assert _rows_of(db, "600519") == []
    assert _rows_of(db, "000001")[0]["name"] == ""  # 主表无该票 → 空串
    out = capsys.readouterr().out
    assert out.count("compute_rebase_factors") == 1  # 警示仅打印一次（非 dry-run）


def test_rows_mapping_field_by_field(tmp_path):
    """(c) 行映射逐字段：双复权价/开/高/低/量/额 + 显式 0 状态位 + None 列 + 主表名。"""
    db = tmp_path / "snap.db"
    upsert_security_master(
        [{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}], db_path=db
    )
    client = FakeHistoryClient(
        data={
            "600519": _frame(
                [
                    {
                        "日期": date(2026, 10, 9),
                        "开盘": 10.0,
                        "收盘_raw": 10.5,
                        "收盘_qfq": 9.8,
                        "成交量": 1000.0,
                        "成交额": 2_500_000.0,
                    },
                    {
                        "日期": date(2026, 10, 12),
                        "开盘": 10.6,
                        "收盘_raw": 10.8,
                        "收盘_qfq": 10.1,
                        "成交量": 1200.0,
                        "成交额": 3_100_000.0,
                    },
                ]
            )
        }
    )

    summary = run_backfill(
        start=_START,
        end=_END,
        symbols=["600519"],
        client=client,
        db_path=db,
        sleep_seconds=0,
    )

    assert summary["rows_written"] == 2
    rows = _rows_of(db, "600519")
    assert rows[0] == {
        "trade_date": "2026-10-09",
        "code": "600519",
        "name": "贵州茅台",
        "open": 10.0,
        "high": 10.6,  # 收盘_raw 10.5 + 0.1（_frame 缺省派生）
        "low": 10.4,
        "close_raw": 10.5,
        "close_qfq": 9.8,
        "volume": 1000.0,
        "amount": 2_500_000.0,
        "pct_chg": None,
        "prev_close": None,
        "is_st": 0,
        "is_suspended": 0,
        "limit_up_sealed": 0,
        "limit_down_sealed": 0,
        "listed_days": None,
        "list_date": None,
    }
    assert rows[1]["trade_date"] == "2026-10-12"
    assert rows[1]["close_qfq"] == 10.1


def test_dry_run_writes_nothing(tmp_path, capsys):
    """(d) --dry-run：只取数计数，不建库不落行，不打非 dry-run 警示。"""
    db = tmp_path / "snap.db"
    client = FakeHistoryClient(
        data={
            "600519": _frame(
                [
                    {
                        "日期": date(2026, 10, 9),
                        "开盘": 10.0,
                        "收盘_raw": 10.5,
                        "收盘_qfq": 10.2,
                        "成交量": 1000.0,
                    }
                ]
            )
        }
    )

    summary = run_backfill(
        start=_START,
        end=_END,
        symbols=["600519"],
        client=client,
        db_path=db,
        dry_run=True,
        sleep_seconds=0,
    )

    assert summary["dry_run"] is True
    assert summary["rows_written"] == 1  # 本应写入的行数
    assert client.calls == ["600519"]  # dry-run 仍取数（计数依赖真实数据）
    assert not db.exists()  # 不建库 → 零落行
    assert "compute_rebase_factors" not in capsys.readouterr().out


def test_main_exits_2_when_master_empty(tmp_path, capsys):
    """(e) 主表为空且未显式指定标的 → 清晰报错并退 2（禁止静默空跑）。"""
    db = tmp_path / "snap.db"

    code = main(["--start", _START, "--end", _END, "--db", str(db), "--sleep", "0"])

    assert code == 2
    err = capsys.readouterr().err
    assert "错误" in err
    assert "证券主表" in err


def test_main_rejects_non_canonical_dates(tmp_path, capsys):
    """日期入参严格 YYYY-MM-DD（与读取接口同款 fail loud）→ 退 2。"""
    db = tmp_path / "snap.db"

    assert main(["--start", "2026-10-5", "--end", _END, "--db", str(db)]) == 2
    assert main(["--start", _END, "--end", _START, "--db", str(db)]) == 2  # start > end
    assert "错误" in capsys.readouterr().err


def test_parse_symbols_file_skips_blanks_and_comments(tmp_path):
    """(f) symbols-file：空行/整行注释跳过；行内逗号分隔容忍；去重归一在 _dedupe。"""
    path = tmp_path / "symbols.txt"
    path.write_text("# 观察池\n600519\n\n  000001  \n# 尾注\n600519,300750\n", encoding="utf-8")

    assert _parse_symbols_file(path) == ["600519", "000001", "600519", "300750"]
    assert _split_csv(" 600519 , ,000001,") == ["600519", "000001"]
    assert _dedupe(["600519", "1", "600519", ""]) == ["600519", "000001"]


def test_main_symbols_file_limit_and_exit_0(tmp_path, monkeypatch, capsys):
    """CLI 全链路（stub 客户端注入）：--limit 限额 → 再跑续跑跳过 → 退 0。"""
    db = tmp_path / "snap.db"
    path = tmp_path / "symbols.txt"
    path.write_text("600519\n000001\n", encoding="utf-8")
    fake = FakeHistoryClient(
        data={
            "600519": _frame(
                [
                    {
                        "日期": date(2026, 10, 9),
                        "开盘": 10.0,
                        "收盘_raw": 10.5,
                        "收盘_qfq": 10.2,
                        "成交量": 1000.0,
                        "成交额": 2_500_000.0,
                    }
                ]
            ),
            "000001": _frame(
                [
                    {
                        "日期": date(2026, 10, 9),
                        "开盘": 5.0,
                        "收盘_raw": 5.2,
                        "收盘_qfq": 5.1,
                        "成交量": 2000.0,
                        "成交额": 1_100_000.0,
                    }
                ]
            ),
        }
    )
    monkeypatch.setattr(backfill_module, "AKShareClient", lambda: fake)

    args = ["--start", _START, "--end", _END, "--symbols-file", str(path), "--db", str(db)]
    assert main([*args, "--limit", "1", "--sleep", "0"]) == 0  # 限额 1：只跑 600519
    assert fake.calls == ["600519"]
    assert main([*args, "--sleep", "0"]) == 0  # 全量再跑：600519 跳过、000001 补上
    assert fake.calls == ["600519", "000001"]
    out = capsys.readouterr().out
    assert "skipped_resumed=1" in out
    assert "symbols_ok=1" in out
    assert len(_rows_of(db, "600519")) == 1
    assert len(_rows_of(db, "000001")) == 1
