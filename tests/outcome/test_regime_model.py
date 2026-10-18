"""``regime_daily`` 存储层单测（delta add-crowding-regime-positioning T1；先红后绿）。

契约（计划 Task 3 Interfaces / design「存储与运维」）：

- 新库 ``data/regime.db``（env ``REGIME_DB_PATH`` **调用期**读，测试注入不写开发库）；
- ``trade_date`` 唯一：同日重跑幂等覆盖（仍单行、值取最新一次）；
- ``latest_regime`` 取最大交易日一行；空库 → ``None``；
- ``list_regime_history`` 时间轴升序（旧→新，供曲线与分位窗口）；``limit`` 取最近 N 行。
"""

from __future__ import annotations

import sqlite3

import pytest

from finance_agent.outcome.regime.model import (
    init_regime_db,
    latest_regime,
    list_regime_history,
    upsert_regime_daily,
)

_PCTILES = {"small_cap_share_pctile": 0.5, "total_amount_pctile": 0.6}


def _indicators(**overrides: object) -> dict:
    base: dict = {
        "small_cap_share": 0.0714,
        "up_count": 2500,
        "down_count": 2300,
        "flat_count": 200,
        "limit_up_count": 60,
        "limit_down_count": 5,
        "total_amount": 8.0e11,
    }
    base.update(overrides)
    return base


def _upsert(db, trade_date: str, indicators: dict | None = None, pctiles: dict | None = None):
    upsert_regime_daily(
        trade_date,
        indicators if indicators is not None else _indicators(),
        pctiles if pctiles is not None else _PCTILES,
        computed_at=f"{trade_date}T15:35:00",
        db_path=db,
    )


class TestUpsert:
    def test_same_day_rerun_keeps_single_row_and_overwrites(self, tmp_path):
        """spec 场景「同日重跑幂等」：同日双写仍单行，值被覆盖为最新一次计算。"""
        db = tmp_path / "regime.db"
        _upsert(db, "2026-10-17", _indicators(small_cap_share=0.07))

        _upsert(
            db,
            "2026-10-17",
            _indicators(small_cap_share=0.09, total_amount=9.0e11),
            {"small_cap_share_pctile": 0.7, "total_amount_pctile": 0.8},
        )

        conn = sqlite3.connect(db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM regime_daily").fetchone()[0]
        finally:
            conn.close()
        assert count == 1
        row = latest_regime(db_path=db)
        assert row is not None
        assert row["trade_date"] == "2026-10-17"
        assert row["small_cap_share"] == pytest.approx(0.09)
        assert row["total_amount"] == pytest.approx(9.0e11)
        assert row["small_cap_share_pctile"] == pytest.approx(0.7)
        assert row["total_amount_pctile"] == pytest.approx(0.8)
        assert row["computed_at"] == "2026-10-17T15:35:00"

    def test_all_indicator_columns_roundtrip(self, tmp_path):
        db = tmp_path / "regime.db"
        _upsert(db, "2026-10-17")
        row = latest_regime(db_path=db)
        assert row is not None
        for key, value in _indicators().items():
            assert row[key] == pytest.approx(value), key

    def test_null_pctiles_persisted_as_null(self, tmp_path):
        """样本不足 → 分位 null 如实落库（不折算 0），指标当日值照常落库。"""
        db = tmp_path / "regime.db"
        _upsert(
            db,
            "2026-10-17",
            pctiles={"small_cap_share_pctile": None, "total_amount_pctile": None},
        )
        row = latest_regime(db_path=db)
        assert row is not None
        assert row["small_cap_share_pctile"] is None
        assert row["total_amount_pctile"] is None
        assert row["up_count"] == 2500

    def test_missing_indicator_key_raises(self, tmp_path):
        """指标键缺一 → ValueError（不静默写 NULL 冒充）。"""
        indicators = _indicators()
        del indicators["total_amount"]
        with pytest.raises(ValueError, match="total_amount"):
            upsert_regime_daily(
                "2026-10-17",
                indicators,
                _PCTILES,
                computed_at="2026-10-17T15:35:00",
                db_path=tmp_path / "regime.db",
            )


class TestReads:
    def test_latest_orders_by_trade_date_desc(self, tmp_path):
        db = tmp_path / "regime.db"
        _upsert(db, "2026-10-15")
        _upsert(db, "2026-10-17", _indicators(small_cap_share=0.2))
        _upsert(db, "2026-10-16")
        row = latest_regime(db_path=db)
        assert row is not None
        assert row["trade_date"] == "2026-10-17"
        assert row["small_cap_share"] == pytest.approx(0.2)

    def test_empty_db_latest_is_none_and_history_empty(self, tmp_path):
        """空库（含库文件尚不存在）→ latest None、history []（入口幂等建表，不抛）。"""
        db = tmp_path / "absent.db"
        assert latest_regime(db_path=db) is None
        assert list_regime_history(db_path=db) == []

    def test_history_is_ascending_and_limit_keeps_most_recent(self, tmp_path):
        db = tmp_path / "regime.db"
        for day in ("2026-10-13", "2026-10-14", "2026-10-15"):
            _upsert(db, day)

        full = list_regime_history(db_path=db)
        assert [row["trade_date"] for row in full] == [
            "2026-10-13",
            "2026-10-14",
            "2026-10-15",
        ]

        recent = list_regime_history(limit=2, db_path=db)
        assert [row["trade_date"] for row in recent] == ["2026-10-14", "2026-10-15"]


class TestDefaultDbPath:
    def test_env_read_at_call_time(self, tmp_path, monkeypatch):
        """``REGIME_DB_PATH`` 调用期读取（导入期冻结会让 E2E 注入失效）。"""
        target = tmp_path / "injected" / "regime.db"
        monkeypatch.setenv("REGIME_DB_PATH", str(target))
        assert init_regime_db() == target
        _upsert(None, "2026-10-17")
        assert latest_regime() is not None
        assert target.is_file()
