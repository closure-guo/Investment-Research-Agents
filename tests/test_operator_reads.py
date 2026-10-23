"""只读系统查询工具（add-quick-mode-operator P2）。"""

from __future__ import annotations

import json

import pytest

import finance_agent.api as api_mod
from finance_agent.operator.reads import (
    _compact_broker,
    _compact_factor_research,
    _compact_factors,
    _compact_memory,
    _compact_ops,
    _compact_screening,
    _compact_track,
    make_read_tools,
)


def test_compact_screening_limits_and_discloses():
    payload = {
        "batch_date": "2026-10-20",
        "batch_status": "ok",
        "stale": True,
        "selection_mode": "factor",
        "notice": "降级提示",
        "pool": [
            {
                "symbol": f"{i:06d}",
                "composite_score": 1.0,
                "rank": i,
                "consecutive_days": 2,
                "l3_verdict": "pass",
                "pool": "main",
            }
            for i in range(25)
        ],
        "queue": [{"symbol": "x", "session_id": "s", "state": "queued"}] * 12,
    }
    out = _compact_screening(payload)
    assert out["pool_size"] == 25
    assert len(out["pool"]) == 20
    assert len(out["queue"]) == 10
    assert out["stale"] is True
    assert out["notice"] == "降级提示"


def test_compact_track_drops_series_and_keeps_insufficient_sample():
    payload = {
        "as_of": "2026-10-21",
        "total": 3,
        "open": 1,
        "settled": 2,
        "win_rate": None,
        "avg_excess": None,
        "insufficient_sample": True,
        "status_counts": {"open": 1},
        "portfolio": {"available": False, "annual_return": None},
        "paper": {"series": [{"date": "x"}] * 100, "loss_pct": 1.2, "note": "n"},
        "avoidance": {"settled": 0, "avoidance_rate": None},
        "caliber_horizon": 20,
        "legacy_settled": 0,
        "disclaimer": "d",
    }
    out = _compact_track(payload)
    assert out["settled"] == 2
    assert out["insufficient_sample"] is True
    assert out["paper_loss_pct"] == 1.2
    assert "paper_series" not in out
    assert out["avoidance"]["settled"] == 0


def test_compact_broker_limits_positions_and_keeps_null():
    overview = {
        "cash": 1000000.0,
        "positions_value": None,
        "equity": None,
        "unrealized_pnl": None,
        "position_cost": 5000.0,
        "positions_count": 25,
        "snapshot_date": None,
        "missing_symbols": ["600519"],
        "market_value_note": "快照缺失",
    }
    positions = {
        "snapshot_date": None,
        "positions": [
            {
                "symbol": f"{i:06d}",
                "qty": 100,
                "avg_cost": 10.0,
                "close": None,
                "market_value": None,
                "unrealized_pnl": None,
            }
            for i in range(25)
        ],
    }
    out = _compact_broker(overview, positions)
    assert out["positions_count"] == 25
    assert len(out["positions"]) == 20
    assert out["positions_value"] is None
    assert out["market_value_note"] == "快照缺失"


def test_compact_ops_drops_history():
    payload = {
        "scheduler_running": True,
        "jobs": [
            {
                "job_id": "factor_harvest_daily",
                "label": "因子收割",
                "next_fire_time": "2026-10-21T15:55:00+08:00",
                "last_run": {
                    "run_id": 1,
                    "status": "ok",
                    "started_at": "t0",
                    "finished_at": "t1",
                    "error": None,
                    "summary": {"x": 1},
                },
                "history": [{"run_id": i} for i in range(10)],
            }
        ],
        "cohort": {"enabled": True},
    }
    out = _compact_ops(payload)
    assert out["jobs"][0]["job_id"] == "factor_harvest_daily"
    assert "history" not in out["jobs"][0]
    assert out["jobs"][0]["last_run"]["status"] == "ok"


def test_compact_memory_limits():
    payload = {
        "status": "pending",
        "clauses": [
            {"id": str(i), "text": "t", "status": "pending", "source": "s", "created_at": "c"}
            for i in range(25)
        ],
    }
    out = _compact_memory(payload)
    assert out["count"] == 25
    assert len(out["clauses"]) == 20


def test_compact_factors_limits():
    payload = {
        "factors": [
            {
                "id": i,
                "name": f"f{i}",
                "expression": "close",
                "status": "active",
                "source": "builtin",
                "ic_mean_60d": 0.01,
                "icir_60d": 0.2,
                "ic_points": 60,
                "decayed": False,
                "last_event": None,
            }
            for i in range(25)
        ]
    }
    out = _compact_factors(payload)
    assert out["count"] == 25
    assert len(out["factors"]) == 20
    assert out["factors"][0]["name"] == "f0"


def test_compact_factor_research_keeps_control_and_drops_log_tail():
    payload = {
        "available": True,
        "stale": False,
        "age_seconds": 15,
        "supervisor": {"running": True, "pid": 1, "started_at": "t"},
        "current_session": {
            "alive": True,
            "loop_index": 1,
            "step_index": 2,
            "step_name": "running",
            "steps_total": 5,
            "step_eta_s": 10,
            "factors": [{"name": "a"}] * 10,
            "log_tail": ["x"] * 100,
        },
        "control": {
            "desired_running": True,
            "pending": False,
            "honor": "live",
            "honor_eta_seconds": 0,
        },
        "agent": {"last_action": "noop", "last_error": None},
        "launcher": {"available": True, "fresh": True, "agent_ok": True},
        "totals": {"sessions": 1, "candidates": 3},
    }
    out = _compact_factor_research(payload)
    assert out["supervisor"]["running"] is True
    assert out["control"]["honor"] == "live"
    assert len(out["current_session"]["factors"]) == 5
    assert "log_tail" not in out["current_session"]
    assert out["launcher"]["agent_ok"] is True


@pytest.mark.asyncio
async def test_read_tool_wiring_and_error(monkeypatch):
    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(api_mod, "screening_pool", boom)
    tools = make_read_tools()
    out = json.loads(await tools["get_screening_pool"]())
    assert out["ok"] is False
    assert "db down" in out["error"]
