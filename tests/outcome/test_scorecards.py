"""策略族成绩单测试（delta add-strategy-family-scorecards）：四槽纯函数 + 注册表。

口径权威：docs/evals/metrics.md §1.13（2026-10-18 登记）+ 变更 design.md。
测试分层：本文件先覆盖纯计算（Task 2），族数据读取与组装（Task 3）随后追加。
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta

import pytest

from finance_agent.data.snapshot import write_snapshot_rows
from finance_agent.execution.store import (
    execution_conn,
    upsert_paper_equity,
    write_position,
)
from finance_agent.outcome.scorecards import (
    _LOADERS,
    FAMILY_REGISTRY,
    FamilyContract,
    FamilySpec,
    Sample,
    aggregate_daily_returns,
    build_family_payload,
    estimate_round_trip_cost_rate,
    get_scorecards,
    load_execution_samples,
    load_prediction_samples,
    max_drawdown_from_returns,
)
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
    update_prediction_status,
)


def _contract(win_rate: float | None = None, note: str = "口径注释") -> FamilyContract:
    return FamilyContract(win_rate=win_rate, caliber_note=note)


def _s(ticker: str, day: str, ret: float) -> Sample:
    return Sample(ticker, day, ret)


# ── 纯计算（Task 2）──


def test_registry_declares_full_family_contract() -> None:
    """spec：每族 SHALL 声明 family_id / 名称 / 数据源 / 收益口径（含费与否）/ 费后说明 / 样本门槛。"""
    assert [spec.family_id for spec in FAMILY_REGISTRY] == ["predictions", "execution", "event"]
    for spec in FAMILY_REGISTRY:
        assert spec.name
        assert spec.data_source in _LOADERS  # 数据源已接线装载器
        assert isinstance(spec.cost_bearing, bool)
        assert spec.min_samples == 10
        assert spec.extreme_threshold < 0
        assert spec.caliber_note


def test_cost_bearing_declaration_drives_net_deduction() -> None:
    """费后处理随族声明：cost_bearing=True → 净=毛−成本率；False → 毛=净（不二次扣减）。"""
    samples = [_s("600519", f"2026-09-{day:02d}", 0.01) for day in range(1, 12)]
    bearing, contained = FAMILY_REGISTRY[0], FAMILY_REGISTRY[1]
    assert bearing.cost_bearing is True
    assert contained.cost_bearing is False

    net_payload = build_family_payload(bearing, samples, contract=_contract())
    cost_rate = estimate_round_trip_cost_rate()
    assert net_payload["expectancy"]["net"] == pytest.approx(
        net_payload["expectancy"]["gross"] - cost_rate, abs=1e-4
    )
    assert net_payload["expectancy"]["net"] < net_payload["expectancy"]["gross"]

    gross_payload = build_family_payload(contained, samples, contract=_contract())
    assert gross_payload["expectancy"]["net"] == gross_payload["expectancy"]["gross"]


def test_cost_rate_matches_matching_config() -> None:
    rate = estimate_round_trip_cost_rate()
    # 0.00025*2 佣金 + 0.0005 印花税 + 0.001*2 中档滑点 = 0.003
    assert math.isclose(rate, 0.003, abs_tol=1e-9)


def test_max_drawdown_known_sequence() -> None:
    assert math.isclose(max_drawdown_from_returns([0.1, -0.2, 0.05, -0.3]), -0.412, abs_tol=1e-3)
    assert max_drawdown_from_returns([]) == 0.0
    assert max_drawdown_from_returns([0.01, 0.02]) == 0.0


def test_aggregate_daily_returns_equal_weight() -> None:
    samples = [
        Sample("600519", "2026-09-01", 0.10),
        Sample("000001", "2026-09-01", -0.02),
        Sample("600000", "2026-09-02", 0.04),
    ]
    assert aggregate_daily_returns(samples) == pytest.approx([0.04, 0.04])


def test_build_payload_null_semantics() -> None:
    """缺数据槽位一律 null + 原因码，MUST NOT 折算 0 或静默省略。"""
    spec = FAMILY_REGISTRY[0]
    payload = build_family_payload(spec, [], contract=_contract())
    assert payload["expectancy"] is None
    assert payload["expectancy_reason"] == "no_samples"
    assert payload["max_drawdown"] is None
    assert payload["max_drawdown_reason"] == "insufficient_history"
    assert payload["capacity"] is None
    assert payload["capacity_reason"] == "no_liquidity_data"
    assert payload["extreme_samples"] == []
    assert payload["win_rate"] is None


def test_build_payload_field_order() -> None:
    """四槽字段位于胜率类字段之前（spec 展示位次序）。"""
    spec = FAMILY_REGISTRY[0]
    samples = [_s("600519", "2026-09-01", 0.05)] * 3
    payload = build_family_payload(spec, samples, contract=_contract(win_rate=1.0))
    keys = list(payload.keys())
    assert (
        keys.index("expectancy")
        < keys.index("max_drawdown")
        < keys.index("capacity")
        < keys.index("extreme_samples")
    )
    assert keys.index("extreme_samples") < keys.index("win_rate")


def test_insufficient_sample_and_extreme() -> None:
    spec = FAMILY_REGISTRY[0]
    samples = [_s("600519", f"2026-09-{d:02d}", -0.20 if d == 1 else 0.01) for d in range(1, 7)]
    payload = build_family_payload(spec, samples, contract=_contract())
    assert payload["expectancy"] is None  # n=6 < 10
    assert payload["expectancy_reason"] == "insufficient_sample"
    assert [row["ticker"] for row in payload["extreme_samples"]] == ["600519"]


def test_execution_threshold_and_gross_equals_net() -> None:
    """执行线：组合级阈值 −5%、毛=净（净值已含费），样本足够时给双值。"""
    spec = FAMILY_REGISTRY[1]
    samples = [
        _s("<portfolio>", f"2026-09-{d:02d}", 0.01 if d > 1 else -0.06) for d in range(1, 12)
    ]
    payload = build_family_payload(spec, samples, contract=_contract())
    assert payload["expectancy"]["n"] == 11
    assert payload["expectancy"]["gross"] == payload["expectancy"]["net"]
    assert [row["ret"] for row in payload["extreme_samples"]] == [-0.06]


def test_capacity_slot_excludes_missing_symbols() -> None:
    """缺数据标的剔除并计数；最小值 × 参与率 = 建议资金上限。"""
    spec = FAMILY_REGISTRY[0]
    samples = [_s("600519", f"2026-09-{d:02d}", 0.01) for d in range(1, 12)]
    payload = build_family_payload(
        spec,
        samples,
        contract=_contract(),
        capacity_inputs={"600519": 100_000_000.0, "000001": None},
    )
    assert payload["capacity"]["min_adv20"] == pytest.approx(100_000_000.0)
    assert payload["capacity"]["excluded"] == 1
    assert payload["capacity"]["suggested_capital"] == pytest.approx(1_000_000.0)


def test_capacity_slot_all_missing_is_null() -> None:
    spec = FAMILY_REGISTRY[0]
    samples = [_s("600519", f"2026-09-{d:02d}", 0.01) for d in range(1, 12)]
    payload = build_family_payload(
        spec,
        samples,
        contract=_contract(),
        capacity_inputs={"600519": None, "000001": None},
    )
    assert payload["capacity"] is None
    assert payload["capacity_reason"] == "no_liquidity_data"


# ── 族数据读取与组装（Task 3）──

_PREDICTION_DAYS = [f"2026-10-{d:02d}" for d in range(1, 23)]  # 22 个结算日（≥ 回撤门槛 20）


def _add_prediction(
    db, *, symbol: str, resolved_at: str, raw_return: float | None, status: str
) -> None:
    prediction_id = insert_prediction(
        {
            "source_type": "live",
            "symbol": symbol,
            "direction": "long",
            "horizon_days": 20,
            "rationale_snapshot": {"action": "buy"},
            "created_at": "2026-09-01T09:30:00",
        },
        db_path=db,
    )
    update_prediction_status(
        prediction_id,
        {"status": status, "resolved_at": resolved_at, "raw_return": raw_return},
        db_path=db,
    )


def _seed_predictions(db) -> None:
    """22 条已判定样本：20 胜（+5%）+ 2 负（−2%、−20%），逐日排布（600519 含极端样本）。"""
    for index, day in enumerate(_PREDICTION_DAYS, start=1):
        symbol = "600519" if index % 2 else "000001"
        if index == 2:
            ret, status = -0.02, "resolved_loss"
        elif index == 20:
            symbol, ret, status = "600519", -0.20, "resolved_loss"
        else:
            ret, status = 0.05, "resolved_win"
        _add_prediction(
            db, symbol=symbol, resolved_at=f"{day}T09:30:00", raw_return=ret, status=status
        )


def _seed_execution(db) -> None:
    """22 个净值点（1 交易日 1 点）：21 个日收益，末日 −6%（组合级极端样本）。"""
    equity = 1_000_000.0
    upsert_paper_equity(_PREDICTION_DAYS[0], equity, 0.0, equity, db_path=db)
    for index, day in enumerate(_PREDICTION_DAYS[1:], start=1):
        equity = equity * 0.94 if index == 21 else equity * 1.002
        upsert_paper_equity(day, equity, 0.0, equity, db_path=db)


def _seed_snapshot(db, codes: dict[str, float] | None = None) -> None:
    """21 个交易日的成交额行；缺省两标的（600519 = 1 亿、000001 = 5000 万）。"""
    amounts = codes if codes is not None else {"600519": 100_000_000.0, "000001": 50_000_000.0}
    for offset in range(21):
        day = (date(2026, 9, 25) + timedelta(days=offset)).isoformat()
        write_snapshot_rows(
            day,
            [{"code": code, "amount": amount} for code, amount in amounts.items()],
            db_path=db,
        )


def _three_db_env(monkeypatch, tmp_path):
    sessions = tmp_path / "s.db"
    execution = tmp_path / "e.db"
    snapshot = tmp_path / "snap.db"
    init_track_record_tables(sessions)
    monkeypatch.setenv("SESSIONS_DB_PATH", str(sessions))
    monkeypatch.setenv("EXECUTION_DB_PATH", str(execution))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(snapshot))
    return sessions, execution, snapshot


def _family(result: dict, family_id: str) -> dict:
    return {family["family_id"]: family for family in result["families"]}[family_id]


def test_load_prediction_samples_filters_open_and_null_return(tmp_path) -> None:
    """已判定且 raw_return 非空才入样本；open/回避/NULL 收益排除，日期取前 10 字符。"""
    db = tmp_path / "s.db"
    init_track_record_tables(db)
    _add_prediction(
        db,
        symbol="600519",
        resolved_at="2026-09-30T15:00:00",
        raw_return=0.12,
        status="resolved_win",
    )
    _add_prediction(
        db,
        symbol="000001",
        resolved_at="2026-09-29",
        raw_return=None,
        status="resolved_neutral",
    )
    _add_prediction(
        db, symbol="600000", resolved_at="2026-09-28", raw_return=0.03, status="avoidance"
    )
    insert_prediction(
        {
            "source_type": "live",
            "symbol": "600036",
            "direction": "long",
            "rationale_snapshot": {"action": "buy"},
            "created_at": "2026-09-01T09:30:00",
        },
        db_path=db,
    )
    assert load_prediction_samples(db_path=db) == [Sample("600519", "2026-09-30", 0.12)]


def test_load_execution_samples_skips_non_positive_equity(tmp_path) -> None:
    """净值 ≤ 0 的行跳过并重设基准（不跨缺口连乘），跳过日不作为后续收益分母。"""
    db = tmp_path / "e.db"
    upsert_paper_equity("2026-10-01", 100.0, 0.0, 1_000_000.0, db_path=db)
    upsert_paper_equity("2026-10-02", 0.0, 0.0, 0.0, db_path=db)
    upsert_paper_equity("2026-10-03", 0.0, 0.0, 1_100_000.0, db_path=db)
    upsert_paper_equity("2026-10-04", 0.0, 0.0, 1_111_000.0, db_path=db)
    samples = load_execution_samples(db_path=db)
    assert [sample.ticker for sample in samples] == ["<portfolio>"]
    assert samples[0].date == "2026-10-04"
    assert samples[0].ret == pytest.approx(1_111_000.0 / 1_100_000.0 - 1)


def test_get_scorecards_end_to_end(tmp_path, monkeypatch) -> None:
    sessions, execution, snapshot = _three_db_env(monkeypatch, tmp_path)
    _seed_predictions(sessions)
    _seed_execution(execution)
    _seed_snapshot(snapshot)
    with execution_conn(execution) as conn:
        write_position(conn, "600519.SH", 100, 1500.0, "2026-10-22T15:00:00")

    result = get_scorecards()
    families = {family["family_id"]: family for family in result["families"]}
    # 注册表序；未注册族不出现（event 族已注册：见 TestEventFamilyScorecards）
    assert list(families) == ["predictions", "execution", "event"]
    assert families["event"]["expectancy_reason"] == "no_samples"  # 空事件库：如实置空

    pred = families["predictions"]
    assert pred["name"] == "观点线"
    assert pred["as_of"] == "2026-10-22"
    assert pred["expectancy"]["n"] == 22
    assert pred["expectancy"]["gross"] == pytest.approx(0.0355, abs=1e-4)
    assert pred["expectancy"]["net"] < pred["expectancy"]["gross"]  # 净 = 毛 − 0.003
    assert pred["max_drawdown"]["value"] < 0
    assert pred["max_drawdown"]["n_days"] == 22
    assert pred["capacity"]["min_adv20"] == pytest.approx(50_000_000.0)
    assert pred["capacity"]["suggested_capital"] == pytest.approx(500_000.0)
    assert pred["capacity"]["excluded"] == 0
    assert [row["ticker"] for row in pred["extreme_samples"]] == ["600519"]
    assert pred["win_rate"] == pytest.approx(20 / 22, abs=1e-4)
    assert "往返成本" in pred["caliber_note"]
    keys = list(pred.keys())
    assert keys.index("expectancy") < keys.index("max_drawdown") < keys.index("capacity")
    assert keys.index("extreme_samples") < keys.index("win_rate")

    exe = families["execution"]
    assert exe["name"] == "执行线"
    assert exe["expectancy"]["n"] == 21
    assert exe["expectancy"]["gross"] == exe["expectancy"]["net"]  # 已含费，无二次扣减
    assert exe["max_drawdown"]["value"] < 0
    assert len(exe["extreme_samples"]) == 1
    assert exe["extreme_samples"][0]["ret"] <= -0.05  # 组合级阈值
    assert exe["capacity"]["min_adv20"] == pytest.approx(100_000_000.0)  # 持仓去后缀命中快照
    assert exe["win_rate"] is None  # 执行线无胜率口径（仅观点线展示）


def test_capacity_slot_excludes_symbols_missing_from_snapshot(tmp_path, monkeypatch) -> None:
    """快照只覆盖部分标的：缺数据标的剔除计数，容量按可得标的给出（不折算 0）。"""
    sessions, _execution, snapshot = _three_db_env(monkeypatch, tmp_path)
    _seed_predictions(sessions)
    _seed_snapshot(snapshot, {"600519": 100_000_000.0})

    pred = _family(get_scorecards(), "predictions")
    assert pred["capacity"]["excluded"] == 1
    assert pred["capacity"]["min_adv20"] == pytest.approx(100_000_000.0)


def test_capacity_slot_null_when_snapshot_empty(tmp_path, monkeypatch) -> None:
    """快照库为空：容量槽 null + no_liquidity_data，异常不上抛给调用方。"""
    sessions, _execution, _snapshot = _three_db_env(monkeypatch, tmp_path)
    _seed_predictions(sessions)

    pred = _family(get_scorecards(), "predictions")
    assert pred["capacity"] is None
    assert pred["capacity_reason"] == "no_liquidity_data"
    assert pred["expectancy"]["n"] == 22  # 其余槽位不受快照缺失影响


def test_capacity_slot_null_when_snapshot_db_corrupt(tmp_path, monkeypatch) -> None:
    """快照库为损坏文件（非 SQLite 内容）：容量槽 null + no_liquidity_data，不抛错。"""
    sessions, _execution, snapshot = _three_db_env(monkeypatch, tmp_path)
    _seed_predictions(sessions)
    snapshot.write_bytes(b"not a sqlite database")  # sqlite3.DatabaseError（非 SnapshotError）

    pred = _family(get_scorecards(), "predictions")
    assert pred["capacity"] is None
    assert pred["capacity_reason"] == "no_liquidity_data"
    assert pred["expectancy"]["n"] == 22  # 其余槽位不受快照损坏影响


def test_get_scorecards_empty_databases(tmp_path, monkeypatch) -> None:
    """三库皆空（文件不存在）：两族均以 null + 原因码如实返回，MUST NOT 报错。"""
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "missing-s.db"))
    monkeypatch.setenv("EXECUTION_DB_PATH", str(tmp_path / "missing-e.db"))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "missing-snap.db"))

    result = get_scorecards()
    for family in result["families"]:
        assert family["expectancy"] is None
        assert family["expectancy_reason"] == "no_samples"
        assert family["max_drawdown_reason"] == "insufficient_history"
        assert family["capacity_reason"] == "no_liquidity_data"
        assert family["as_of"] is None


def test_registered_family_with_unknown_data_source_reports_no_loader(
    tmp_path, monkeypatch
) -> None:
    """未知数据源（未接线装载器）：四槽 null + ``no_loader``，且既有两族读数不受影响。"""
    sessions, _execution, snapshot = _three_db_env(monkeypatch, tmp_path)
    _seed_predictions(sessions)
    _seed_snapshot(snapshot)
    extended = (
        *FAMILY_REGISTRY,
        FamilySpec(
            "hypothetical",
            "假想族",
            data_source="unknown",
            cost_bearing=False,
            min_samples=10,
            extreme_threshold=-0.10,
            caliber_note="假想族口径（极端样本阈值 -10%，测试声明）",
        ),
    )
    monkeypatch.setattr("finance_agent.outcome.scorecards.FAMILY_REGISTRY", extended)

    result = get_scorecards()
    assert [family["family_id"] for family in result["families"]] == [
        "predictions",
        "execution",
        "event",
        "hypothetical",
    ]
    events = result["families"][3]
    assert events["name"] == "假想族"
    assert events["as_of"] is None
    assert events["expectancy"] is None
    assert events["expectancy_reason"] == "no_loader"
    assert events["max_drawdown"] is None
    assert events["max_drawdown_reason"] == "no_loader"
    assert events["capacity"] is None
    assert events["capacity_reason"] == "no_loader"
    assert events["extreme_samples"] == []
    assert events["win_rate"] is None
    assert "阈值 -10%" in events["caliber_note"]  # 口径注释取自注册声明

    pred = _family(result, "predictions")  # 既有两族读数不受未知数据源影响
    assert pred["expectancy"]["n"] == 22
    assert pred["capacity"]["min_adv20"] == pytest.approx(50_000_000.0)
    exe = _family(result, "execution")
    assert exe["expectancy_reason"] == "no_samples"
    assert exe["capacity_reason"] == "no_liquidity_data"


def test_extreme_threshold_is_configurable() -> None:
    """阈值调整后极端样本清单随之变化（同一批样本，仅 spec 阈值不同）。"""
    samples = [_s("600519", "2026-09-01", -0.20), _s("000001", "2026-09-02", 0.01)]
    default_spec = FAMILY_REGISTRY[0]
    default = build_family_payload(default_spec, samples, contract=_contract())
    stricter = build_family_payload(
        default_spec._replace(extreme_threshold=-0.30), samples, contract=_contract()
    )
    assert [row["ret"] for row in default["extreme_samples"]] == [-0.20]
    assert stricter["extreme_samples"] == []


def test_missing_predictions_table_reports_empty(tmp_path, monkeypatch) -> None:
    """老库无 predictions 表：样本空、胜率 null（如实返回，不抛错）。"""
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    assert load_prediction_samples(db_path=legacy) == []

    monkeypatch.setenv("SESSIONS_DB_PATH", str(legacy))
    monkeypatch.setenv("EXECUTION_DB_PATH", str(tmp_path / "e.db"))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "snap.db"))
    pred = _family(get_scorecards(), "predictions")
    assert pred["expectancy_reason"] == "no_samples"
    assert pred["win_rate"] is None


class TestEventFamilyScorecards:
    """delta add-event-driven-lane T6：事件族注册与装载（样本 = 已结算 event_journal 行）。"""

    def _seed_events(self, db, *, settled: int = 12) -> None:
        from finance_agent.outcome.events.model import (
            init_events_db,
            insert_journal,
            settle_journal,
        )

        init_events_db(db)
        for index in range(1, settled + 1):
            ret = -0.20 if index == 1 else 0.02
            row_id = insert_journal(
                forecast_key=f"60051{index % 10}|2026-03-31|ind|2026-04-{index:02d}",
                symbol="600519.SH" if index % 2 else "000001.SZ",
                entry_date=f"2026-04-{index:02d}",
                entry_price=100.0,
                horizon_days=5,
                db_path=db,
            )
            assert settle_journal(
                row_id,
                settle_date=f"2026-05-{index:02d}",
                settle_price=100.0 * (1 + ret),
                raw_return=ret,
                db_path=db,
            )

    def test_event_family_declaration_verbatim(self):
        spec = next(spec for spec in FAMILY_REGISTRY if spec.family_id == "event")
        assert spec.name == "事件线"
        assert spec.data_source == "events" and spec.data_source in _LOADERS
        assert spec.cost_bearing is True  # 虚拟结算族：净 = 毛 − 往返成本估算率
        assert spec.min_samples == 10
        assert spec.extreme_threshold == -0.15
        assert "阈值 -15%" in spec.caliber_note
        assert "T+20" in spec.caliber_note  # 家族隔离声明（独立于观点线窗口）

    def test_event_family_reads_settled_journal_only(self, tmp_path, monkeypatch):
        events_db = tmp_path / "events.db"
        self._seed_events(events_db, settled=12)
        # 未结算行不入样本
        from finance_agent.outcome.events.model import insert_journal

        insert_journal(
            forecast_key="999999|2026-03-31|ind|2026-04-30",
            symbol="600000.SH",
            entry_date="2026-04-30",
            entry_price=100.0,
            horizon_days=5,
            db_path=events_db,
        )
        monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "missing-s.db"))
        monkeypatch.setenv("EXECUTION_DB_PATH", str(tmp_path / "missing-e.db"))
        monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "missing-snap.db"))
        monkeypatch.setenv("EVENTS_DB_PATH", str(events_db))

        event = _family(get_scorecards(), "event")
        assert event["name"] == "事件线"
        assert event["expectancy"]["n"] == 12  # 只数已结算行
        assert event["expectancy"]["net"] < event["expectancy"]["gross"]  # cost_bearing
        assert event["as_of"] == "2026-05-12"
        assert [row["ticker"] for row in event["extreme_samples"]] == ["600519.SH"]
        assert event["win_rate"] is None  # 事件线无胜率口径
        assert "往返成本" in event["caliber_note"]

    def test_family_isolation_event_rows_do_not_enter_predictions(self, tmp_path, monkeypatch):
        """家族隔离（spec 场景）：事件读数不进观点线统计，反之亦然。"""
        events_db = tmp_path / "events.db"
        sessions = tmp_path / "s.db"
        init_track_record_tables(sessions)
        self._seed_events(events_db, settled=12)
        monkeypatch.setenv("SESSIONS_DB_PATH", str(sessions))
        monkeypatch.setenv("EXECUTION_DB_PATH", str(tmp_path / "e.db"))
        monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "snap.db"))
        monkeypatch.setenv("EVENTS_DB_PATH", str(events_db))

        result = get_scorecards()
        pred = _family(result, "predictions")
        event = _family(result, "event")
        assert pred["expectancy_reason"] == "no_samples"  # 观点库空：不受事件行影响
        assert event["expectancy"]["n"] == 12

        # 观点线样本装载器只读 predictions 表（事件行不可见）
        assert load_prediction_samples(db_path=sessions) == []
