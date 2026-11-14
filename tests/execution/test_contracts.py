"""决策契约解析器单测（enforce-decision-contract-at-execution Task 1）。"""

import sqlite3

from finance_agent.execution.contracts import (
    POSITION_TIER_WEIGHTS,
    parse_first_batch_ratio,
    parse_position_tier,
)
from finance_agent.execution.signals import Signal
from finance_agent.execution.store import (
    exit_first_batch_done,
    init_execution_db,
    list_signals,
    record_exit_first_batch_done,
    record_signal,
)
from finance_agent.outcome.track_record.ingest import _declared_contract


def test_tier_anchors_match_trader_prompt_lower_bounds():
    """锚值与 trader.md:117 档位表下沿逐字对齐（light 10-20/moderate 30-50/heavy 50+）。"""
    assert POSITION_TIER_WEIGHTS == {"light": 0.10, "moderate": 0.30, "heavy": 0.50}


def test_parse_position_tier_known_and_normalized():
    assert parse_position_tier("light") == "light"
    assert parse_position_tier(" Light ") == "light"
    assert parse_position_tier("MODERATE") == "moderate"


def test_parse_position_tier_unknown_returns_none():
    assert parse_position_tier("30%") is None  # 百分比串非档位名（落库 REAL 口径）
    assert parse_position_tier("unknown") is None
    assert parse_position_tier(None) is None
    assert parse_position_tier(123) is None


def test_parse_first_batch_ratio_single_match():
    # 601066 2026-11-12 真实方案文本形态（首批…70%，中间含其他数字）
    assert (
        parse_first_batch_ratio("首批在现价 21.72 附近即减持有敞口的 70%（较原方案 50% 上调）")
        == 0.7
    )
    assert parse_first_batch_ratio("首批减仓 30%，余下等反弹") == 0.3


def test_parse_first_batch_ratio_ambiguous_returns_none():
    assert parse_first_batch_ratio("首批 70%，另一方案首批 50%") is None


def test_parse_first_batch_ratio_invalid_inputs_return_none():
    assert parse_first_batch_ratio(None) is None
    assert parse_first_batch_ratio("") is None
    assert parse_first_batch_ratio("一次性清仓") is None  # 无首批表述
    assert parse_first_batch_ratio("首批 150%") is None  # 越界
    assert parse_first_batch_ratio("首批 0%") is None  # 越界


def test_parse_first_batch_ratio_clamps_to_ratio():
    assert parse_first_batch_ratio("首批 100% 清仓") == 1.0


def test_declared_contract_freezes_all_fields_for_buy():
    decision = {
        "sell_type": None,
        "position_size": "light",
        "exit_schedule": None,
        "trigger_high": 22.35,
        "trigger_low": None,
    }
    contract = _declared_contract(decision, action="buy")
    assert contract == {
        "sell_type": None,
        "position_size": "light",
        "exit_schedule": None,
        "trigger_high": 22.35,
        "trigger_low": None,
    }


def test_declared_contract_triggers_only_for_buy():
    decision = {
        "sell_type": "exit",
        "position_size": None,
        "exit_schedule": "首批减 70%",
        "trigger_high": 22.35,
        "trigger_low": 20.0,
    }
    assert _declared_contract(decision, action="sell")["trigger_high"] is None
    assert _declared_contract(decision, action="sell")["exit_schedule"] == "首批减 70%"
    assert _declared_contract(decision, action="watch")["trigger_high"] is None


def _base_signal(**overrides: object) -> Signal:
    base: dict = {
        "signal_id": "600519.SH:2026-11-17",
        "symbol": "600519.SH",
        "direction": "long",
        "strength": 0.8,
        "horizon_days": 20,
        "source": "predictions",
        "decision_id": "p_test1",
        "created_at": "2026-11-17",
    }
    base.update(overrides)
    return Signal(**base)


def test_signal_contract_fields_roundtrip(tmp_path):
    db = tmp_path / "execution.db"
    signal = _base_signal(
        entry_price=22.40,
        trigger_high=22.35,
        position_tier="light",
        exit_first_batch_ratio=0.7,
    )
    record_signal(signal, db_path=db)
    loaded = list_signals(db_path=db)[0]
    assert loaded.entry_price == 22.40
    assert loaded.trigger_high == 22.35
    assert loaded.position_tier == "light"
    assert loaded.exit_first_batch_ratio == 0.7


def test_legacy_signal_row_reads_back_none(tmp_path):
    """旧 8 列行（迁移前写入）读回 → 新字段 None（零迁移兼容）。"""
    db = tmp_path / "execution.db"
    init_execution_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO signals (signal_id, symbol, direction, strength, horizon_days,"
        " source, decision_id, created_at) VALUES ('x.SH:2026-11-17','x.SH','long',"
        "0.5,20,'predictions','p_old','2026-11-17')"
    )
    conn.commit()
    conn.close()
    loaded = list_signals(db_path=db)[0]
    assert loaded.entry_price is None and loaded.trigger_high is None
    assert loaded.position_tier is None and loaded.exit_first_batch_ratio is None


def test_migration_adds_columns_to_legacy_db(tmp_path):
    """迁移前旧 DDL 建表 → init_execution_db 幂等补列。"""
    db = tmp_path / "execution.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS signals (signal_id TEXT PRIMARY KEY, symbol TEXT"
        " NOT NULL, direction TEXT NOT NULL, strength REAL NOT NULL, horizon_days"
        " INTEGER NOT NULL, source TEXT NOT NULL, decision_id TEXT NOT NULL,"
        " created_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    init_execution_db(db)
    conn = sqlite3.connect(db)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(signals)")}
    conn.close()
    assert {"entry_price", "trigger_high", "position_tier", "exit_first_batch_ratio"} <= columns


def test_exit_first_batch_state_roundtrip(tmp_path):
    db = tmp_path / "execution.db"
    assert exit_first_batch_done(db_path=db) == set()
    record_exit_first_batch_done("p_a", "2026-11-17", db_path=db)
    record_exit_first_batch_done("p_a", "2026-11-17", db_path=db)  # 幂等重跑
    assert exit_first_batch_done(db_path=db) == {"p_a"}
