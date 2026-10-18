"""退市股历史补采脚本单测（delta add-microcap-lane-guardrails T4；零网络）。

覆盖：(a) 只取退市标的（存续标的不取）；(b) 窗口 = ``list_date..delist_date``；
(c) 源无历史可得 → 计数 + 代码明细（如实标注，不伪造）；(d) ``list_date`` 缺失 →
窗口不可得明细、不取数；(e) ``--dry-run`` 只计数不写库；(f) 断点续跑跳过；
(g) ``--limit`` 截断；(h) 宿主机写护栏沿用（拒绝时退 2、命令指向本脚本）；
(i) 主表无退市标的 → 退 2（禁止静默空跑）。

stub client 注入 ``fetch_symbol_history_raw_qfq``；tmp 快照库经 ``upsert_delisted_masters``
造主表。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import scripts.backfill_delisted as delisted_module
import scripts.backfill_market_snapshot as backfill_module
from scripts.backfill_delisted import main, run_backfill_delisted

from finance_agent.data.snapshot import _connect, upsert_delisted_masters

_DELISTED = "600001"  # 1998-01-01 上市，2020-06-30 退市
_NO_HISTORY = "000002"  # 退市但源无历史可得
_WINDOW_MISSING = "000003"  # 退市但 list_date 缺失（窗口不可得）
_LIVE = "600519"  # 存续标的（不取）


def _frame(dates: list[str]) -> pd.DataFrame:
    """T5 八列历史帧（日期为 date 对象；最高/最低按收盘 ±0.1 派生）。"""
    return pd.DataFrame(
        {
            "日期": [pd.Timestamp(day).date() for day in dates],
            "开盘": [10.0] * len(dates),
            "最高": [10.1] * len(dates),
            "最低": [9.9] * len(dates),
            "收盘_raw": [10.0] * len(dates),
            "收盘_qfq": [10.0] * len(dates),
            "成交量": [1000.0] * len(dates),
            "成交额": [1_000_000.0] * len(dates),
        }
    )


class FakeDelistedClient:
    """stub：退市码返回退市前 5 行；活码/无历史码返回 None。"""

    def __init__(self, frames: dict[str, pd.DataFrame | None] | None = None):
        self.frames = frames if frames is not None else {_DELISTED: _frame(_WINDOW_DATES)}
        self.calls: list[tuple[str, str, str]] = []

    def fetch_symbol_history_raw_qfq(self, code: str, start: str, end: str):
        self.calls.append((code, start, end))
        return self.frames.get(code)


_WINDOW_DATES = [
    "2020-06-22",
    "2020-06-23",
    "2020-06-24",
    "2020-06-29",
    "2020-06-30",
]


def _seed_master(db: Path, *, codes: tuple[str, ...] = (_DELISTED, _LIVE)) -> None:
    rows = {
        _DELISTED: {
            "code": _DELISTED,
            "name": "退市样本",
            "list_date": "1998-01-01",
            "delist_date": "2020-06-30",
        },
        _NO_HISTORY: {
            "code": _NO_HISTORY,
            "name": "无历史样本",
            "list_date": "1991-01-29",
            "delist_date": "2019-07-09",
        },
        _WINDOW_MISSING: {
            "code": _WINDOW_MISSING,
            "name": "无上市日样本",
            "list_date": None,
            "delist_date": "2010-05-06",
        },
        _LIVE: {"code": _LIVE, "name": "存续样本", "list_date": "2001-08-27"},
    }
    upsert_delisted_masters([rows[code] for code in codes], db_path=db)


def _market_rows(db: Path, code: str) -> list[str]:
    conn = _connect(db)
    try:
        return [
            str(row[0])
            for row in conn.execute(
                "SELECT trade_date FROM market_snapshot WHERE code = ? ORDER BY trade_date", (code,)
            ).fetchall()
        ]
    finally:
        conn.close()


class TestRunBackfillDelisted:
    def test_writes_delisted_history_and_skips_live(self, tmp_path):
        """退市标的按其 list_date..delist_date 窗口取数落库；存续标的不进池不取数。"""
        db = tmp_path / "snap.db"
        _seed_master(db, codes=(_DELISTED, _LIVE))
        client = FakeDelistedClient()

        summary = run_backfill_delisted(client=client, db_path=db, sleep_seconds=0)

        assert client.calls == [(_DELISTED, "19980101", "20200630")]
        assert summary["symbols_total"] == 1
        assert summary["symbols_ok"] == 1
        assert summary["rows_written"] == 5
        assert summary["history_missing"] == []
        assert summary["window_missing"] == []
        assert _market_rows(db, _DELISTED) == _WINDOW_DATES

    def test_source_without_history_counted_and_listed(self, tmp_path):
        """源无历史可得（None）→ 计数 + 代码明细；其余标的照常落库（如实标注）。"""
        db = tmp_path / "snap.db"
        _seed_master(db, codes=(_DELISTED, _NO_HISTORY))
        client = FakeDelistedClient(frames={_DELISTED: _frame(_WINDOW_DATES), _NO_HISTORY: None})

        summary = run_backfill_delisted(client=client, db_path=db, sleep_seconds=0)

        assert summary["symbols_total"] == 2
        assert summary["symbols_ok"] == 1
        assert summary["rows_written"] == 5
        assert summary["history_missing"] == [_NO_HISTORY]
        assert summary["history_missing_count"] == 1
        assert _market_rows(db, _NO_HISTORY) == []

    def test_missing_list_date_recorded_without_fetch(self, tmp_path):
        """list_date 缺失 → 窗口不可得（明细标注），不取数不落库。"""
        db = tmp_path / "snap.db"
        _seed_master(db, codes=(_WINDOW_MISSING,))
        client = FakeDelistedClient(frames={})

        summary = run_backfill_delisted(client=client, db_path=db, sleep_seconds=0)

        assert client.calls == []
        assert summary["symbols_total"] == 0
        assert summary["window_missing"] == [_WINDOW_MISSING]

    def test_dry_run_counts_without_writing(self, tmp_path):
        """--dry-run：只取数计数，不写库（rows_written = 本应写入行数）。"""
        db = tmp_path / "snap.db"
        _seed_master(db, codes=(_DELISTED,))
        client = FakeDelistedClient()

        summary = run_backfill_delisted(client=client, db_path=db, dry_run=True, sleep_seconds=0)

        assert summary["dry_run"] is True
        assert summary["rows_written"] == 5
        assert _market_rows(db, _DELISTED) == []
        assert summary.get("integrity") is None  # dry-run 不做收尾自检

    def test_resumed_window_is_skipped(self, tmp_path):
        """窗口内已有齐备行 → 跳过（断点续跑），不重复取数。"""
        db = tmp_path / "snap.db"
        _seed_master(db, codes=(_DELISTED,))
        client = FakeDelistedClient()
        run_backfill_delisted(client=client, db_path=db, sleep_seconds=0)

        second = run_backfill_delisted(client=client, db_path=db, sleep_seconds=0)

        assert client.calls == [(_DELISTED, "19980101", "20200630")]  # 只取过一次
        assert second["skipped_resumed"] == 1
        assert second["rows_written"] == 0

    def test_limit_truncates_pool(self, tmp_path):
        """``--limit`` 截断可处理清单（确定性顺序 = code 升序）。"""
        db = tmp_path / "snap.db"
        _seed_master(db, codes=(_DELISTED, _NO_HISTORY))
        client = FakeDelistedClient(
            frames={_DELISTED: _frame(_WINDOW_DATES), _NO_HISTORY: _frame(_WINDOW_DATES)}
        )

        summary = run_backfill_delisted(client=client, db_path=db, limit=1, sleep_seconds=0)

        assert [call[0] for call in client.calls] == [_NO_HISTORY]  # 000002 < 600001（升序）
        assert summary["symbols_total"] == 1

    def test_integrity_check_included_in_summary_when_writing(self, tmp_path):
        db = tmp_path / "snap.db"
        _seed_master(db, codes=(_DELISTED,))

        summary = run_backfill_delisted(client=FakeDelistedClient(), db_path=db, sleep_seconds=0)

        assert summary["integrity"]["ok"] is True
        assert summary["integrity"]["duplicate_keys"] == 0


class TestMain:
    def test_guard_rejection_returns_2_with_delisted_command(self, tmp_path, capsys, monkeypatch):
        """宿主机护栏沿用：拒绝 → 退 2、命令指向本脚本、零写入。"""
        db = tmp_path / "snap.db"
        _seed_master(db, codes=(_DELISTED,))

        seen: dict[str, object] = {}

        def _fake_guard(**kwargs):
            seen.update(kwargs)
            return (
                "错误：拒绝在宿主机直跑回填……\n"
                "    docker compose exec -T backend python scripts/backfill_delisted.py\n"
            )

        monkeypatch.setattr(delisted_module, "_host_write_guard", _fake_guard)

        code = main(["--db", str(db)])

        assert code == 2
        assert seen["dry_run"] is False
        assert "backfill_delisted.py" in capsys.readouterr().err
        assert _market_rows(db, _DELISTED) == []

    def test_no_delisted_symbols_returns_2(self, tmp_path, capsys, monkeypatch):
        """主表无带 delist_date 的标的 → 退 2（禁止静默空跑）。"""
        db = tmp_path / "snap.db"
        _seed_master(db, codes=(_LIVE,))
        monkeypatch.setattr(delisted_module, "_host_write_guard", lambda **kwargs: None)

        code = main(["--db", str(db)])

        assert code == 2
        assert "退市" in capsys.readouterr().err

    def test_dry_run_end_to_end_returns_0(self, tmp_path, capsys, monkeypatch):
        """--dry-run 全链：护栏豁免、摘要打印、退 0。"""
        db = tmp_path / "snap.db"
        _seed_master(db, codes=(_DELISTED,))
        monkeypatch.setattr(delisted_module, "_host_write_guard", lambda **kwargs: None)
        monkeypatch.setattr(delisted_module, "AKShareClient", FakeDelistedClient)

        code = main(["--db", str(db), "--dry-run", "--sleep", "0"])

        out = capsys.readouterr().out
        assert code == 0
        assert "rows_written=5" in out and "[dry-run]" in out
        assert _market_rows(db, _DELISTED) == []


class TestHostGuardScriptName:
    def test_guard_suggestion_names_calling_script(self, tmp_path):
        """护栏命令里的脚本名可换（本脚本复用护栏时给出可复制的正确命令）。"""
        root = tmp_path / "repo"
        (root / "data").mkdir(parents=True)
        bind_db = root / "data" / "market_snapshot.db"

        reason = backfill_module._host_write_guard(
            db_path=bind_db,
            repo_root=root,
            container_running=lambda: True,
            in_container=False,
            argv=["--db", str(bind_db)],
            script_name="scripts/backfill_delisted.py",
        )

        assert reason is not None
        assert "scripts/backfill_delisted.py" in reason
        assert "scripts/backfill_market_snapshot.py" not in reason

    def test_guard_default_script_name_unchanged(self, tmp_path):
        root = tmp_path / "repo"
        (root / "data").mkdir(parents=True)
        bind_db = root / "data" / "market_snapshot.db"

        reason = backfill_module._host_write_guard(
            db_path=bind_db,
            repo_root=root,
            container_running=lambda: True,
            in_container=False,
            argv=["--start", "2024-01-01"],
        )

        assert reason is not None
        assert "scripts/backfill_market_snapshot.py" in reason
