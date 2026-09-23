"""outcome 健康检查（spec evaluation「Outcome 读数收口纪律」第①步）：只读、三主检查 + 两告警。"""

import json
import sqlite3
from pathlib import Path

from evals.outcome.health import collect_outcome_health, main

from finance_agent.outcome.track_record.model import (
    compute_snapshot_hash,
    init_track_record_tables,
)


def _insert(
    db: Path,
    *,
    status: str,
    symbol: str = "600519",
    source_type: str = "live",
    entry_price: float | None = 100.0,
    trace: str | None = "t1",
    snapshot: dict | None = None,
    horizon: int = 20,
    created_at: str = "2026-09-20T10:00:00",
    direction: str = "long",
    avoidance_status: str | None = None,
) -> None:
    import json as _json

    snap = snapshot if snapshot is not None else {"action": "watch", "n": symbol + status}
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO predictions (prediction_id, source_type, symbol, direction, entry_price,"
            " horizon_days, confidence, rationale_snapshot, langfuse_trace_id, status, created_at,"
            " updated_at, snapshot_hash, avoidance_status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"p-{symbol}-{status}-{_json.dumps(snap)}",
                source_type,
                symbol,
                direction,
                entry_price,
                horizon,
                0.6,
                _json.dumps(snap),
                trace,
                status,
                created_at,
                created_at,
                compute_snapshot_hash(snap),
                avoidance_status,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "sessions.db"
    init_track_record_tables(db)
    return db


class TestHealth:
    def test_all_resolved_passes(self, tmp_path: Path):
        db = _db(tmp_path)
        for i in range(4):
            _insert(db, status="resolved_win", symbol=f"60{i}")
        for i in range(3):
            _insert(db, status="resolved_loss", symbol=f"61{i}")
        _insert(db, status="resolved_neutral", symbol="620")
        _insert(db, status="open", symbol="630")
        _insert(db, status="open", symbol="631")
        rep = collect_outcome_health(db, source_type="live")
        assert rep["settlement_success_rate"] == 1.0
        assert rep["unresolvable_rate"] == 0.0
        assert rep["bookkeeping_completeness"] == 1.0
        assert rep["checks"]["settlement_success"] is True
        assert rep["integrity_scope"] == "whole-db"
        assert rep["passed"] is True and rep["checks"]["integrity"] is True

    def test_unresolvable_majority_fails_unresolvable(self, tmp_path: Path):
        db = _db(tmp_path)
        for i in range(5):
            _insert(db, status="resolved_win", symbol=f"70{i}")
        for i in range(5):
            _insert(db, status="unresolvable", symbol=f"71{i}")
        rep = collect_outcome_health(db, source_type="live")
        assert rep["unresolvable_rate"] == 0.5
        assert rep["checks"]["unresolvable"] is False
        assert rep["checks"]["settlement_success"] is False
        assert rep["passed"] is False

    def test_avoidance_terminal_counted_as_settled(self, tmp_path: Path):
        """Δ2 终审 A：avoidance 终态计入 settled 分母（否则 §1.8⑤ 门禁会假 FAIL）。

        旧公式只认 resolved_* 三态 → 95 条 avoidance 两侧都不计，
        success = 9/(9+6) = 0.60（假 FAIL）；新公式 settled = 95 avoidance + 9 resolved。
        """
        db = _db(tmp_path)
        for i in range(95):
            _insert(
                db,
                status="avoidance",
                avoidance_status="avoidance_win",
                direction="neutral",
                symbol=f"n{i:02d}",
            )
        for i in range(5):
            _insert(db, status="unresolvable", direction="neutral", symbol=f"nu{i}")
        for i in range(9):
            _insert(db, status="resolved_win", symbol=f"l{i}")
        _insert(db, status="unresolvable", symbol="lu0")

        rep = collect_outcome_health(db, source_type="live")
        assert rep["settled"] == 104  # 95 avoidance + 9 resolved
        assert rep["settled_avoidance"] == 95
        assert rep["unresolvable"] == 6
        assert rep["settlement_success_rate"] == round(104 / 110, 4)  # ≈0.9455（旧公式 0.60）
        assert rep["unresolvable_rate"] == round(6 / 110, 4)
        assert rep["checks"]["settlement_success"] is True
        assert rep["checks"]["unresolvable"] is True

    def test_avoidance_without_status_not_settled(self, tmp_path: Path):
        """status='avoidance' 但 avoidance_status 为空（未判定）→ 不计 settled（不虚增分母）。"""
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519")
        _insert(db, status="avoidance", avoidance_status=None, direction="neutral", symbol="600520")
        rep = collect_outcome_health(db, source_type="live")
        assert rep["settled"] == 1
        assert rep["settled_avoidance"] == 0
        assert rep["settlement_success_rate"] == 1.0

    def test_integrity_mismatch_fails(self, tmp_path: Path):
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519")
        conn = sqlite3.connect(db)
        conn.execute("UPDATE predictions SET snapshot_hash='deadbeef'")  # 模拟篡改
        conn.commit()
        conn.close()
        rep = collect_outcome_health(db)
        assert rep["integrity_mismatches"] == 1
        assert rep["checks"]["integrity"] is False and rep["passed"] is False

    def test_warnings_for_trace_missing_and_duplicate_hash(self, tmp_path: Path):
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519", trace=None)
        dup = {"action": "buy", "same": "snapshot"}
        _insert(db, status="resolved_win", symbol="600520", snapshot=dup)
        _insert(db, status="resolved_loss", symbol="600521", snapshot=dup)
        rep = collect_outcome_health(db)
        assert rep["trace_missing"] == 1
        assert rep["duplicate_snapshot_groups"] == 1
        assert any("snapshot_hash" in w for w in rep["warnings"])

    def test_empty_db_no_rates_and_not_passed(self, tmp_path: Path):
        rep = collect_outcome_health(_db(tmp_path))
        assert rep["total"] == 0
        assert rep["settlement_success_rate"] is None
        assert rep["passed"] is False

    def test_source_type_filters(self, tmp_path: Path):
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519", source_type="live")
        _insert(db, status="resolved_loss", symbol="600519", source_type="backtest")
        rep = collect_outcome_health(db, source_type="live")
        assert rep["total"] == 1
        rep_all = collect_outcome_health(db, source_type=None)
        assert rep_all["total"] == 2

    def test_since_filter_narrows_population(self, tmp_path: Path):
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519", created_at="2026-09-10T10:00:00")
        _insert(db, status="resolved_win", symbol="600520", created_at="2026-09-20T10:00:00")
        assert collect_outcome_health(db, source_type="live")["total"] == 2
        rep = collect_outcome_health(db, source_type="live", since="2026-09-15")
        assert rep["total"] == 1

    def test_default_db_path_from_env(self, tmp_path: Path, monkeypatch):
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519")
        monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
        assert collect_outcome_health(None, source_type="live")["total"] == 1


class TestCli:
    def test_cli_json_and_exit_code(self, tmp_path: Path, capsys):
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519")
        code = main(["--db", str(db), "--source-type", "live"])
        out = json.loads(capsys.readouterr().out)
        assert out["passed"] is True and code == 0

    def test_cli_exit_one_when_failed(self, tmp_path: Path):
        db = _db(tmp_path)
        _insert(db, status="unresolvable", symbol="600519")
        assert main(["--db", str(db), "--source-type", "live"]) == 1

    def test_cli_missing_db_exit_two(self, tmp_path: Path, capsys):
        missing = tmp_path / "nope.db"
        assert main(["--db", str(missing)]) == 2
        assert "DB 不存在" in capsys.readouterr().err
