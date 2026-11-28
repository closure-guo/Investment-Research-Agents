"""update-outcome-band-volatility-scaled（2026-11-28 拍板）：同窗随机基准 + 连续超额读数。

覆盖三块：
- ``baseline.window_baseline`` 纯函数（均值/中位/缺行剔除）；
- ``baseline.settled_windows`` 与 ``build_same_window_baseline``（归属日口径、窗口等权、
  缺数据如实跳过）；
- ``baseline.same_window_baseline`` I/O 包装（track-record 库 + 快照库）；
- ``prediction_stats`` 连续超额族与去重标的数。
"""

import sqlite3

from finance_agent.data import snapshot as snapshot_mod
from finance_agent.outcome.track_record.baseline import (
    build_same_window_baseline,
    same_window_baseline,
    settled_windows,
    window_baseline,
)
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
    prediction_stats,
    update_prediction_status,
    upsert_index_closes,
)

CAL = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]


def test_window_baseline_mean_median_and_skip():
    out = window_baseline({"A": 10, "B": 20, "C": 30, "D": 0}, {"A": 11, "B": 18, "C": 33}, 0.0)
    assert out["n"] == 3
    assert abs(out["mean_excess"] - 0.1 / 3) < 1e-6  # 落库口径保留 6 位
    assert abs(out["median_excess"] - 0.1) < 1e-9
    assert window_baseline({}, {}, 0.0) == {"n": 0, "mean_excess": None, "median_excess": None}


def test_settled_windows_uses_attribution_rule():
    preds = [
        {
            "status": "resolved_win",
            "direction": "long",
            "created_at": "2026-09-01T10:00:00",
            "resolved_at": "2026-09-03",
        },
        # 收盘后产出 → 归属次一交易日（09-02）
        {
            "status": "resolved_loss",
            "direction": "short",
            "created_at": "2026-09-01T20:00:00",
            "resolved_at": "2026-09-04",
        },
        # 归属日 == 结算日 → 无效窗口（不产生 0 长度区间）
        {
            "status": "resolved_neutral",
            "direction": "long",
            "created_at": "2026-09-02T20:00:00",
            "resolved_at": "2026-09-03",
        },
        {"status": "open", "direction": "long", "created_at": "2026-09-01T10:00:00"},
        # 非方向性（neutral）与未在日历内的行一律排除
        {
            "status": "resolved_win",
            "direction": "neutral",
            "created_at": "2026-09-01T10:00:00",
            "resolved_at": "2026-09-03",
        },
        {
            "status": "resolved_win",
            "direction": "long",
            "created_at": "2026-09-10T10:00:00",
            "resolved_at": "2026-09-11",
        },
    ]
    assert settled_windows(preds, CAL) == [
        ("2026-09-01", "2026-09-03"),
        ("2026-09-02", "2026-09-04"),
    ]


def test_build_same_window_baseline_aggregates_windows():
    closes = {
        "2026-09-01": {"A": 10, "B": 20},
        "2026-09-02": {"A": 10, "B": 20},
        "2026-09-03": {"A": 11, "B": 20},
        "2026-09-04": {"A": 9, "B": 20},
    }
    index = {"2026-09-01": 100.0, "2026-09-02": 100.0, "2026-09-03": 100.0, "2026-09-04": 100.0}
    preds = [
        {
            "status": "resolved_win",
            "direction": "long",
            "created_at": "2026-09-01T10:00:00",
            "resolved_at": "2026-09-03",
        },
        {
            "status": "resolved_loss",
            "direction": "long",
            "created_at": "2026-09-02T10:00:00",
            "resolved_at": "2026-09-04",
        },
    ]
    out = build_same_window_baseline(preds, CAL, index, lambda dates: closes)
    assert out["available"] is True
    assert out["windows"] == 2 and out["skipped_windows"] == 0
    # 窗口一：A +10%、B 0 → +5%；窗口二：A −10%、B 0 → −5%；窗口间等权 → 0
    assert abs(out["mean_excess"]) < 1e-9
    assert out["stocks_per_window"] == 2
    assert "同窗全市场等权" in out["caliber"]


def test_build_same_window_baseline_reports_missing_data():
    preds = [
        {
            "status": "resolved_win",
            "direction": "long",
            "created_at": "2026-09-01T10:00:00",
            "resolved_at": "2026-09-03",
        }
    ]
    out = build_same_window_baseline(preds, CAL, {}, lambda dates: {})
    assert out["available"] is False
    assert out["windows"] == 0 and out["skipped_windows"] == 1
    assert out["mean_excess"] is None and out["reason"]


def _write_snapshot(snapshot_db, rows):
    snapshot_mod.init_snapshot_db(snapshot_db)
    conn = sqlite3.connect(snapshot_db)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO market_snapshot (trade_date, code, close_raw) VALUES (?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_same_window_baseline_io(tmp_path):
    track_db = tmp_path / "track.db"
    init_track_record_tables(track_db)
    upsert_index_closes(
        [("000300.SH", "2026-09-01", 100.0), ("000300.SH", "2026-09-03", 100.0)],
        db_path=track_db,
    )
    pid = insert_prediction(
        {
            "source_type": "live",
            "symbol": "600001.SH",
            "symbol_name": "示例一",
            "direction": "long",
            "entry_price": 10.0,
            "horizon_days": 20,
            "rationale_snapshot": {},
            "created_at": "2026-09-01T10:00:00",
        },
        db_path=track_db,
    )
    update_prediction_status(
        pid,
        {"status": "resolved_win", "excess_return": 0.1, "resolved_at": "2026-09-03"},
        db_path=track_db,
    )
    snapshot_db = tmp_path / "snap.db"
    _write_snapshot(
        snapshot_db,
        [
            ("2026-09-01", "600001", 10.0),
            ("2026-09-03", "600001", 11.0),
            ("2026-09-01", "600002", 20.0),
            ("2026-09-03", "600002", 20.0),
        ],
    )
    out = same_window_baseline(db_path=track_db, snapshot_db=snapshot_db)
    assert out["available"] is True and out["windows"] == 1
    # A +10%、B 0 → 同窗随机 5%
    assert abs(out["mean_excess"] - 0.05) < 1e-9


def test_same_window_baseline_degrades_without_index(tmp_path):
    track_db = tmp_path / "track.db"
    init_track_record_tables(track_db)
    out = same_window_baseline(db_path=track_db, snapshot_db=tmp_path / "snap.db")
    assert out["available"] is False and out["reason"]


def test_prediction_stats_continuous_family(tmp_path):
    track_db = tmp_path / "track.db"
    init_track_record_tables(track_db)
    rows = [
        ("600001.SH", "resolved_win", 0.05, 0.045),
        ("600001.SH", "resolved_win", 0.03, 0.045),
        ("600002.SH", "resolved_loss", -0.02, 0.02),
        ("600003.SH", "resolved_neutral", 0.0, None),
    ]
    for symbol, status, excess, band in rows:
        pid = insert_prediction(
            {
                "source_type": "live",
                "symbol": symbol,
                "direction": "long",
                "entry_price": 10.0,
                "horizon_days": 20,
                "rationale_snapshot": {},
                "created_at": "2026-09-01T10:00:00",
            },
            db_path=track_db,
        )
        update_prediction_status(
            pid,
            {
                "status": status,
                "excess_return": excess,
                "resolved_at": "2026-09-21",
                "neutral_band_used": band,
            },
            db_path=track_db,
        )
    stats = prediction_stats(db_path=track_db)
    assert stats["excess_n"] == 4
    assert stats["excess_median"] == 0.015
    assert stats["excess_win_rate"] == 0.5
    assert stats["distinct_symbols"] == 3
    assert stats["band_settled"] == 3
    assert stats["excess_t"] is not None
