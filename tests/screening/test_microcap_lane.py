"""小票通道档位 / 前置门禁 / 通道批次（delta add-microcap-lane-guardrails T5；先红后绿）。

口径（docs/evals/metrics.md §1.16 与 spec「小票通道约束档位」「通道批次产出与前置门禁」）：

- 档位：``MICROCAP_THRESHOLDS``（流动性阈值 1000 万；其余同主档）；2000 万成交额标的
  在 main 档剔除（``ILLIQUID``）、microcap 档通过；主档 ``DEFAULT_THRESHOLDS`` 逐字不变；
- 门禁两判据（纯代码检查）：``delist_ready`` = 主表含 ≥1 条 ``delist_date`` 非空记录；
  ``regime_ready`` = ``regime_daily`` 最新行距判定日 ≤ 7 自然日；缺任一 →
  ``frontier_gate="observation_only:missing=<要点列表>"``；
- 批次：``run_microcap_lane_daily`` 落 ``pool="microcap"`` 批次行（与主档行同库并存，
  主档读侧不受影响）；建议执行约束为记录字段；**v1 MUST NOT 产生订单**。

零网络：面板/事件/triage client 全部注入（同 test_pipeline 纪律）；regime 库与快照库
均为 tmp 库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from finance_agent.data.snapshot import get_security_master, set_meta_many, write_snapshot_rows
from finance_agent.factors.eval_engine import MarketPanel, build_panel_from_rows
from finance_agent.factors.library import add_factor, record_ic
from finance_agent.outcome.regime.model import init_regime_db, upsert_regime_daily
from finance_agent.screening import constraints, pipeline
from finance_agent.screening.constraints import FilterThresholds
from finance_agent.screening.filters import CandidateRecord, RejectReason, filter_universe
from finance_agent.screening.store import (
    latest_screening_batch,
    list_batches,
    record_screening_batch,
)
from tests.screening.test_pipeline import _business_days, _ScriptedClient

_AS_OF = "2026-10-13"
_LIQUID = "600519"  # 1 亿：两档皆过
_MICRO = "300750"  # 2000 万：main 剔（<5000 万）/ microcap 过（≥1000 万）
_TINY = "000001"  # 500 万：两档皆剔
_ST_CODE = "600999"  # ST：两档皆剔（ST_FLAG）
_CALENDAR = _business_days(_AS_OF, 80)
_PANEL_DATES = _CALENDAR[-25:]
_AMOUNTS = {
    _LIQUID: 100_000_000.0,
    _MICRO: 20_000_000.0,
    _TINY: 5_000_000.0,
    _ST_CODE: 100_000_000.0,
}
_GATE_TODAY = _AS_OF  # 门禁新鲜度判定基准（测试钉死，不受真实时钟影响）


@dataclass
class _LaneWorkspace:
    snapshot_db: Path
    library_db: Path
    screening_db: Path
    regime_db: Path
    panel: MarketPanel
    ic_values: list[float] = field(default_factory=list)


def _snapshot_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(_PANEL_DATES):
        for code, amount in _AMOUNTS.items():
            close = 10.0 + index * 0.1
            rows.append(
                {
                    "trade_date": day,
                    "code": code,
                    "name": f"合成{code}",
                    "open": close,
                    "high": close,
                    "low": close,
                    "close_raw": close,
                    "close_qfq": close,
                    "volume": 1_000_000.0,
                    "amount": amount,
                    "pct_chg": 0.0,
                    "prev_close": close,
                    "is_st": 1 if code == _ST_CODE else 0,
                    "is_suspended": 0,
                    "limit_up_sealed": 0,
                    "limit_down_sealed": 0,
                    "listed_days": len(_CALENDAR),
                    "list_date": _CALENDAR[0],
                }
            )
    return rows


def _build_workspace(root: Path) -> _LaneWorkspace:
    snapshot_db = root / "market_snapshot.db"
    library_db = root / "factor_library.db"
    screening_db = root / "screening.db"
    regime_db = root / "regime.db"
    rows = _snapshot_rows()
    frame = pd.DataFrame(rows)
    for day in _PANEL_DATES:
        write_snapshot_rows(
            day, frame.loc[frame["trade_date"] == day].to_dict("records"), db_path=snapshot_db
        )
    set_meta_many(
        {"last_success_date": _AS_OF, "trade_calendar": __import__("json").dumps(_CALENDAR)},
        db_path=snapshot_db,
    )
    panel = build_panel_from_rows(frame)
    factor_id = add_factor(
        expression="close",
        hypothesis="测试假设文本（≥30 字长度达标的合成因子说明）",
        category="builtin",
        status="active",
        db_path=library_db,
    )
    ic_values: list[float] = []
    for index, day in enumerate(_CALENDAR[-60:]):
        value = 0.01 + 0.001 * index
        record_ic(factor_id, day, value, db_path=library_db)
        ic_values.append(value)
    init_regime_db(regime_db)
    return _LaneWorkspace(
        snapshot_db=snapshot_db,
        library_db=library_db,
        screening_db=screening_db,
        regime_db=regime_db,
        panel=panel,
        ic_values=ic_values,
    )


@pytest.fixture
def ws(tmp_path) -> _LaneWorkspace:
    return _build_workspace(tmp_path)


@pytest.fixture(autouse=True)
def _no_default_io(monkeypatch):
    """默认 triage client 与默认事件装配在测试内不可用（须显式注入，零网络）。"""

    def _forbidden_client(*args, **kwargs):
        raise AssertionError("测试不得构造默认 triage client（须注入 stub，零网络）")

    monkeypatch.setattr(pipeline, "GatewayTriageClient", _forbidden_client)
    monkeypatch.setattr(pipeline, "_assemble_events_by_symbol", lambda candidates: {})
    yield


def _seed_regime(db: Path, trade_date: str) -> None:
    """落一行 regime（indicator 键齐备；分位缺省 None = 样本不足，如实落库）。"""
    upsert_regime_daily(
        trade_date,
        {
            "small_cap_share": 0.07,
            "up_count": 2500,
            "down_count": 2300,
            "flat_count": 200,
            "limit_up_count": 60,
            "limit_down_count": 5,
            "total_amount": 8.0e11,
        },
        {},
        computed_at=f"{trade_date}T15:35:00",
        db_path=db,
    )


def _seed_delist_master(db: Path) -> None:
    """主表落 1 条带退市日的记录（退市池就绪判据）。"""
    from finance_agent.data.snapshot import upsert_delisted_masters

    upsert_delisted_masters(
        [
            {
                "code": "600001",
                "name": "退市样本",
                "list_date": "1998-01-01",
                "delist_date": "2020-06-30",
                "source": "sh_suspend",
            }
        ],
        db_path=db,
    )


def _run_lane(ws: _LaneWorkspace, **overrides) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "as_of": _AS_OF,
        "panel": ws.panel,
        "events_by_symbol": {},
        "triage_client": _ScriptedClient(),
        "db_path": ws.screening_db,
        "snapshot_db_path": ws.snapshot_db,
        "library_db_path": ws.library_db,
        "regime_db_path": ws.regime_db,
        "gate_today": _GATE_TODAY,
    }
    kwargs.update(overrides)
    return pipeline.run_microcap_lane_daily(**kwargs)


def _run_main(ws: _LaneWorkspace, **overrides) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "as_of": _AS_OF,
        "panel": ws.panel,
        "events_by_symbol": {},
        "triage_client": _ScriptedClient(),
        "db_path": ws.screening_db,
        "snapshot_db_path": ws.snapshot_db,
        "library_db_path": ws.library_db,
    }
    kwargs.update(overrides)
    return pipeline.run_screening_batch(**kwargs)


class TestMicrocapThresholds:
    def test_constants_verbatim_and_main_unchanged(self):
        """档位常量逐字（1000 万 / 建议单票上限 5% / 滑点提一档）；主档逐字不变。"""
        assert constraints.MICROCAP_MIN_AVG_AMOUNT_20D == 1e7
        assert FilterThresholds(min_avg_amount_20d=1e7) == constraints.MICROCAP_THRESHOLDS
        assert constraints.MICROCAP_THRESHOLDS.min_trading_days == constraints.MIN_TRADING_DAYS
        assert (
            constraints.MICROCAP_THRESHOLDS.min_liquidity_history_days
            == constraints.MIN_LIQUIDITY_HISTORY_DAYS
        )
        assert constraints.MICROCAP_SUGGESTED_SINGLE_CAP == 0.05
        assert constraints.MICROCAP_SLIPPAGE_TIER_BUMP == 1
        # 主档默认值不得漂移（现有调用路径逐字节不变）
        assert FilterThresholds() == constraints.DEFAULT_THRESHOLDS

    def test_2000w_stock_rejected_by_main_passed_by_microcap(self):
        """越档场景：2000 万标的 main 档 ``ILLIQUID``、microcap 档通过。"""
        record = CandidateRecord(
            code=_MICRO, name="合成标的", trading_days_listed=100, avg_amount_20d=2e7
        )

        main_decision = filter_universe([record])[0]
        micro_decision = filter_universe([record], thresholds=constraints.MICROCAP_THRESHOLDS)[0]

        assert main_decision.passed is False
        assert main_decision.reasons == (RejectReason.ILLIQUID,)
        assert micro_decision.passed is True
        assert micro_decision.reasons == ()


class TestFrontierGate:
    def test_both_missing_lists_both_items(self, tmp_path):
        snapshot_db = tmp_path / "snap.db"
        regime_db = tmp_path / "regime.db"

        gate = pipeline.evaluate_frontier_gate(
            snapshot_db_path=snapshot_db, regime_db_path=regime_db, today=_GATE_TODAY
        )

        assert gate["frontier_gate"] == "observation_only:missing=delist_pool,regime_data"
        assert gate["missing"] == ["delist_pool", "regime_data"]
        assert gate["delist_ready"] is False and gate["regime_ready"] is False

    def test_only_regime_missing(self, tmp_path):
        snapshot_db = tmp_path / "snap.db"
        _seed_delist_master(snapshot_db)

        gate = pipeline.evaluate_frontier_gate(
            snapshot_db_path=snapshot_db,
            regime_db_path=tmp_path / "regime.db",
            today=_GATE_TODAY,
        )

        assert gate["frontier_gate"] == "observation_only:missing=regime_data"
        assert gate["delist_ready"] is True and gate["regime_ready"] is False

    def test_only_delist_missing(self, tmp_path):
        regime_db = tmp_path / "regime.db"
        _seed_regime(regime_db, _GATE_TODAY)

        gate = pipeline.evaluate_frontier_gate(
            snapshot_db_path=tmp_path / "snap.db", regime_db_path=regime_db, today=_GATE_TODAY
        )

        assert gate["frontier_gate"] == "observation_only:missing=delist_pool"
        assert gate["delist_ready"] is False and gate["regime_ready"] is True

    def test_ready_when_both_present(self, tmp_path):
        snapshot_db = tmp_path / "snap.db"
        regime_db = tmp_path / "regime.db"
        _seed_delist_master(snapshot_db)
        _seed_regime(regime_db, _GATE_TODAY)

        gate = pipeline.evaluate_frontier_gate(
            snapshot_db_path=snapshot_db, regime_db_path=regime_db, today=_GATE_TODAY
        )

        assert gate["frontier_gate"] == "ready"
        assert gate["missing"] == []

    def test_regime_freshness_boundary_is_seven_days(self, tmp_path):
        """≤7 自然日算新鲜；第 8 日算陈旧（口径同 §1.14）。"""
        snapshot_db = tmp_path / "snap.db"
        _seed_delist_master(snapshot_db)
        today = date.fromisoformat(_GATE_TODAY)

        for age, ready in ((7, True), (8, False)):
            regime_db = tmp_path / f"regime-{age}.db"
            _seed_regime(regime_db, (today - timedelta(days=age)).isoformat())
            gate = pipeline.evaluate_frontier_gate(
                snapshot_db_path=snapshot_db, regime_db_path=regime_db, today=_GATE_TODAY
            )
            assert gate["regime_ready"] is ready, f"age={age}"
            assert gate["delist_ready"] is True


class TestRunMicrocapLaneDaily:
    def test_lane_marks_pool_and_leaves_main_batch_untouched(self, ws):
        """通道批次落 ``pool=microcap``；同日主档批次行/读出不受影响。"""
        main_result = _run_main(ws)
        lane_result = _run_lane(ws)

        # 档位生效：主档只放过 1 亿标的；通道额外放过 2000 万标的（500 万仍剔除）
        assert main_result["l1_passed"] == 1
        assert lane_result["l1_passed"] == 2

        main_row = latest_screening_batch(db_path=ws.screening_db)
        assert main_row is not None
        assert main_row["pool"] == "main"
        assert main_row["l1_passed"] == 1
        assert main_row["frontier_gate"] is None

        lane_row = latest_screening_batch(db_path=ws.screening_db, pool="microcap")
        assert lane_row is not None
        assert lane_row["pool"] == "microcap"
        assert lane_row["l1_passed"] == 2
        assert lane_row["frontier_gate"] == "observation_only:missing=delist_pool,regime_data"

        assert len(list_batches(db_path=ws.screening_db)) == 1  # 主档历史只见主档行
        assert len(list_batches(db_path=ws.screening_db, pool="microcap")) == 1

    def test_lane_is_observation_only_and_places_no_orders(self, ws, monkeypatch):
        """v1 不下单：执行链入口若被触碰即失败；记录字段含建议约束。"""
        import finance_agent.execution.brokers.paper as paper_module
        import finance_agent.execution.driver as driver_module

        def _forbidden(*args, **kwargs):
            raise AssertionError("小票通道 v1 不得触碰执行链")

        monkeypatch.setattr(driver_module, "run_execution_daily", _forbidden)
        monkeypatch.setattr(paper_module, "run_paper_settle_daily", _forbidden)

        result = _run_lane(ws)

        assert result["pool"] == "microcap"
        assert result["frontier_gate"].startswith("observation_only:missing=")
        assert result["frontier_missing"] == ["delist_pool", "regime_data"]
        assert result["orders_placed"] == 0
        assert result["suggested_single_cap"] == constraints.MICROCAP_SUGGESTED_SINGLE_CAP
        assert result["slippage_tier_bump"] == constraints.MICROCAP_SLIPPAGE_TIER_BUMP
        # F5：建议执行约束随批次落库（记录字段，spec「含建议执行约束」）
        row = latest_screening_batch(db_path=ws.screening_db, pool="microcap")
        assert row is not None and row["exec_constraints"] == {
            "suggested_single_cap": constraints.MICROCAP_SUGGESTED_SINGLE_CAP,
            "slippage_tier_bump": constraints.MICROCAP_SLIPPAGE_TIER_BUMP,
        }
        # 主档读侧不受影响（本用例只跑了通道：主档无行）
        assert latest_screening_batch(db_path=ws.screening_db) is None

    def test_lane_gate_ready_when_prerequisites_present(self, ws):
        """前置齐备 → ``frontier_gate="ready"``（可执行候选，仍不下单）。"""
        _seed_delist_master(ws.snapshot_db)
        _seed_regime(ws.regime_db, _GATE_TODAY)

        result = _run_lane(ws)

        assert result["frontier_gate"] == "ready"
        assert result["frontier_missing"] == []
        assert result["orders_placed"] == 0
        row = latest_screening_batch(db_path=ws.screening_db, pool="microcap")
        assert row is not None and row["frontier_gate"] == "ready"

    def test_lane_failure_recorded_as_failed_batch(self, ws):
        """通道批次失败（快照不新鲜）→ failed 批次行 + 错误文案（fail-closed 同主档）。"""
        result = _run_lane(ws, as_of="2026-10-14")  # 快照最新为 10-13

        assert result["status"] == "failed"
        row = latest_screening_batch(db_path=ws.screening_db, pool="microcap")
        assert row is not None and row["status"] == "failed"
        assert "SnapshotNotFreshError" in str(row["error"])
        # 失败路径不得污染主档：主档读侧无行
        assert latest_screening_batch(db_path=ws.screening_db) is None


class TestMainLaneRegression:
    def test_default_paths_unchanged_with_new_kwargs(self, ws):
        """新增关键字参数默认值 = 既有行为（主档批次 pool='main'、frontier_gate NULL）。"""
        result = _run_main(ws)

        assert result["status"] == "ok"
        row = latest_screening_batch(db_path=ws.screening_db)
        assert row is not None
        assert row["pool"] == "main"
        assert row["frontier_gate"] is None
        assert row["selection_mode"] == "factor"

    def test_explicit_main_thresholds_equal_defaults(self, ws):
        """显式传主档阈值 = 不传（档位参数默认不改变主档判定）。"""
        default_result = _run_main(ws)
        explicit_result = _run_main(ws, thresholds=constraints.DEFAULT_THRESHOLDS)

        assert default_result["l1_passed"] == explicit_result["l1_passed"] == 1

    def test_invalid_pool_rejected_before_writing(self, ws):
        with pytest.raises(ValueError, match="pool"):
            _run_main(ws, pool="sidecar")


def test_master_shape_used_by_gate(tmp_path):
    """门禁读主表形状（``delist_date``）——与 Task 2 落库口径一致。"""
    from finance_agent.data.snapshot import count_delisted_masters

    db = tmp_path / "snap.db"
    assert count_delisted_masters(db) == 0
    _seed_delist_master(db)
    assert count_delisted_masters(db) == 1
    assert get_security_master(db)["600001"]["delist_date"] == "2020-06-30"


def test_ops_wrapper_raises_on_failed_lane(monkeypatch):
    """ops 入口：批次 failed → 上抛（run_job 记 failed），与主档 run_screening_daily 同纪律。"""
    monkeypatch.setattr(
        pipeline,
        "run_microcap_lane_daily",
        lambda *args, **kwargs: {
            "trade_date": "2026-10-13",
            "status": "failed",
            "error": "快照过期",
        },
    )
    with pytest.raises(RuntimeError, match="快照过期"):
        pipeline.run_microcap_lane()


def test_pipeline_has_no_module_level_regime_dependency():
    """F6：screening → outcome.regime 的反向依赖为函数内延迟 import（不得模块级导入）。"""
    assert not hasattr(pipeline, "latest_regime")


def test_gate_today_defaults_to_today(tmp_path):
    """``today`` 缺省 = 当天（生产路径）：陈旧行必然不新鲜。"""
    snapshot_db = tmp_path / "snap.db"
    regime_db = tmp_path / "regime.db"
    _seed_delist_master(snapshot_db)
    _seed_regime(regime_db, (datetime.now().date() - timedelta(days=30)).isoformat())

    gate = pipeline.evaluate_frontier_gate(snapshot_db_path=snapshot_db, regime_db_path=regime_db)

    assert gate["frontier_gate"] == "observation_only:missing=regime_data"


def test_record_screening_batch_pool_field_is_readable(tmp_path):
    """批次表 pool 列在 store 层可独立读写（pipeline 依赖的底座）。"""
    db = tmp_path / "screening.db"
    record_screening_batch(
        "2026-10-13",
        status="ok",
        started_at="t0",
        finished_at="t1",
        pool="microcap",
        frontier_gate="ready",
        db_path=db,
    )
    micro = latest_screening_batch(db_path=db, pool="microcap")
    assert micro is not None and (micro["pool"], micro["frontier_gate"]) == ("microcap", "ready")
