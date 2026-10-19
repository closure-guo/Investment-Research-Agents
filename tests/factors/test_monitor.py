"""factors/monitor.py 单测（add-factor-library-and-monitor Task 6）。

覆盖计划关键设计决策 E9（① 记录当日可算 RankIC、幂等覆盖；② 最近 ``DECAY_WINDOW``
日滚动 IC 序列的**最后 ``DECAY_NEGATIVE_DAYS`` 个滚动点全部为负且
``|滚动 ICIR| < DECAY_ICIR_FLOOR``** → 置 ``degraded`` + 事件落库含滚动快照；
历史不足 → skipped 不判降级）与 ops 日批接线（``factor_decay_monitor`` 15:45
mon-fri、``_with_retry`` 包装、注册表四处对齐）。

合成面板：固定种子；面板第 80 天（``_RECORDED_OFFSET``）由**并列秩构造**使当日
RankIC 恰为 ``_RECORDED_IC = −0.02``（volume 二值 × 次日收益二值 → Spearman =
phi 系数 = (49·49 − 51·51)/200² = −0.02），其余日期因子/收益恒定 → IC 为 NaN，
于是「最新可算 IC 日」唯一确定。数据库全部落 ``tmp_path``（零网络、零共享状态）。
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from finance_agent.factors.constraints import (
    DECAY_ICIR_FLOOR,
    DECAY_NEGATIVE_DAYS,
    DECAY_WINDOW,
)
from finance_agent.factors.dsl import parse, serialize
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.library import (
    add_factor,
    list_active_factors,
    list_events,
    list_factors,
    list_ic_series,
    record_ic,
)
from finance_agent.factors.monitor import run_factor_decay_monitor, run_factor_monitor
from finance_agent.outcome.ops.jobs import JOB_FUNCS, JOB_IDS, JOB_LABELS, SCHEDULES

_MONITOR_CODES = 200
_MONITOR_DATES = 81
_RECORDED_OFFSET = 79  # 面板第 80 天 = 最新可算 IC 日（次日收益非恒定）
_RECORDED_IC = -0.02  # 并列秩构造的 phi 系数（见模块 docstring）
_CRAFTED_DATES = 79  # 落库的历史 IC 天数（面板前 79 天）
_DB_FILENAME = "factor_library.db"

_HYPOTHESIS = "测试用经济学假设：成交额水平与未来收益存在稳定关系。"


def _day(offset: int) -> str:
    return (date(2026, 6, 1) + timedelta(days=offset)).isoformat()


def _db(tmp_path) -> str:
    return str(tmp_path / _DB_FILENAME)


def _monitor_panel() -> MarketPanel:
    """81 日 × 200 票面板：第 80 天 volume 二值、次日收益二值（RankIC = −0.02）。"""
    codes = _MONITOR_CODES
    half = codes // 2
    volume = np.ones((_MONITOR_DATES, codes))
    volume[_RECORDED_OFFSET, :half] = 1.0
    volume[_RECORDED_OFFSET, half:] = 0.0
    close = np.full((_MONITOR_DATES, codes), 100.0)
    returns = np.zeros(codes)
    returns[:49] = 1.0  # volume=1 组：49 涨 / 51 平
    returns[half : half + 51] = 1.0  # volume=0 组：51 涨 / 49 平
    close[_RECORDED_OFFSET + 1] = 100.0 * (1.0 + 0.01 * returns)
    amount = volume * 1000.0
    labels = [_day(offset) for offset in range(_MONITOR_DATES)]
    code_labels = [f"m{index:03d}" for index in range(codes)]

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=labels, columns=code_labels)

    return MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(volume),
            "amount": frame(amount),
        }
    )


def _active_factor(tmp_path, *, expression: str = "volume") -> tuple[str, int]:
    """建库 + 一条 active 因子（``volume`` 命中面板的构造信号）。"""
    db = _db(tmp_path)
    factor_id = add_factor(
        expression=expression,
        hypothesis=_HYPOTHESIS,
        category="价量",
        status="active",
        ast_json=serialize(parse(expression)),
        db_path=db,
    )
    return db, factor_id


def _craft_history(db: str, factor_id: int, values: list[float]) -> None:
    """落库前 ``len(values)`` 天的历史 IC（面板前 79 天）。"""
    assert len(values) <= _CRAFTED_DATES
    for offset, value in enumerate(values):
        record_ic(factor_id, _day(offset), value, db_path=db)


def test_run_factor_monitor_degrades_on_twenty_bad_rolling_points(tmp_path):
    """20 个滚动点全部为负且 |ICIR| < 0.1 → degraded + 事件含滚动 IC/ICIR 快照。"""
    db, factor_id = _active_factor(tmp_path)
    _craft_history(db, factor_id, [_RECORDED_IC] * _CRAFTED_DATES)
    result = run_factor_monitor(_monitor_panel(), _day(_MONITOR_DATES - 1), db_path=db)

    assert result["recorded"] == 1
    assert result["checked"] == 1
    assert result["skipped"] == 0
    assert result["degraded"] == [factor_id]

    series = list_ic_series(factor_id, db_path=db)
    assert len(series) == _CRAFTED_DATES + 1  # 历史 79 + 本次记录 1
    assert series[-1] == (_day(_RECORDED_OFFSET), pytest.approx(_RECORDED_IC))

    judged = result["judged"][0]
    assert judged["factor_id"] == factor_id
    assert judged["degraded"] is True
    assert judged["rolling_ic_mean"] < 0.0
    assert abs(judged["rolling_icir"]) < DECAY_ICIR_FLOOR

    rows = list_factors(db_path=db)
    assert rows[0]["status"] == "degraded"
    assert list_active_factors(db_path=db) == []  # 降级即从打分池剔除

    events = list_events(factor_id, db_path=db)
    assert events[-1]["from_status"] == "active"
    assert events[-1]["to_status"] == "degraded"
    detail = events[-1]["detail"]
    assert len(detail["rolling_ic_mean"]) == DECAY_NEGATIVE_DAYS
    assert len(detail["rolling_icir"]) == DECAY_NEGATIVE_DAYS
    assert detail["window"] == DECAY_WINDOW
    assert detail["as_of"] == _day(_MONITOR_DATES - 1)


def test_run_factor_monitor_skips_when_history_shorter_than_window(tmp_path):
    """IC 历史 < DECAY_WINDOW（含本次记录）→ skipped、不降级。"""
    db, factor_id = _active_factor(tmp_path)
    _craft_history(db, factor_id, [_RECORDED_IC] * (DECAY_WINDOW - 2))
    result = run_factor_monitor(_monitor_panel(), _day(_MONITOR_DATES - 1), db_path=db)
    assert result["recorded"] == 1
    assert result["checked"] == 0
    assert result["skipped"] == 1
    assert result["degraded"] == []
    assert list_factors(db_path=db)[0]["status"] == "active"


def test_run_factor_monitor_skips_without_twenty_rolling_points(tmp_path):
    """够一个滚动窗口但不足 20 个滚动点（IC 78 点 < 60+19）→ skipped、不降级。"""
    db, factor_id = _active_factor(tmp_path)
    _craft_history(db, factor_id, [_RECORDED_IC] * (_CRAFTED_DATES - 2))
    result = run_factor_monitor(_monitor_panel(), _day(_MONITOR_DATES - 1), db_path=db)
    assert result["recorded"] == 1
    assert result["checked"] == 0
    assert result["skipped"] == 1
    assert list_factors(db_path=db)[0]["status"] == "active"


def test_run_factor_monitor_does_not_degrade_when_rolling_mean_recovers(tmp_path):
    """最后 10 日 IC 转正 → 末段滚动均值为正 → 不降级（「连续 20 日」不成立）。"""
    db, factor_id = _active_factor(tmp_path)
    history = [_RECORDED_IC] * _CRAFTED_DATES
    history[-10:] = [0.5] * 10
    _craft_history(db, factor_id, history)
    result = run_factor_monitor(_monitor_panel(), _day(_MONITOR_DATES - 1), db_path=db)
    assert result["checked"] == 1
    assert result["degraded"] == []
    assert result["judged"][0]["rolling_ic_mean"] > 0.0
    assert list_factors(db_path=db)[0]["status"] == "active"


def test_run_factor_monitor_does_not_degrade_when_rolling_icir_above_floor(tmp_path):
    """滚动均值恒为负但 |ICIR| ≫ 0.1（强负 IC，非衰减）→ 不降级。"""
    db, factor_id = _active_factor(tmp_path)
    history = [(-0.4 if offset % 2 == 0 else -0.6) for offset in range(_CRAFTED_DATES)]
    _craft_history(db, factor_id, history)
    result = run_factor_monitor(_monitor_panel(), _day(_MONITOR_DATES - 1), db_path=db)
    assert result["checked"] == 1
    assert result["degraded"] == []
    judged = result["judged"][0]
    assert judged["rolling_ic_mean"] < 0.0
    assert abs(judged["rolling_icir"]) > DECAY_ICIR_FLOOR
    assert list_factors(db_path=db)[0]["status"] == "active"


def test_run_factor_monitor_rerun_is_idempotent(tmp_path):
    """同日重跑：同 (factor, date) 覆盖，不重复落行、状态不二次迁移。"""
    db, factor_id = _active_factor(tmp_path)
    _craft_history(db, factor_id, [_RECORDED_IC] * _CRAFTED_DATES)
    panel = _monitor_panel()
    as_of = _day(_MONITOR_DATES - 1)
    first = run_factor_monitor(panel, as_of, db_path=db)
    second = run_factor_monitor(panel, as_of, db_path=db)
    assert first["degraded"] == [factor_id]
    # 降级后不再出现在 active 集合 → 第二次运行不再监控该因子（不重复迁移）
    assert second["degraded"] == [] and second["recorded"] == 0
    assert len(list_ic_series(factor_id, db_path=db)) == _CRAFTED_DATES + 1
    transitions = [
        (event["from_status"], event["to_status"])
        for event in list_events(factor_id, db_path=db)
        if event["event"] == "status_change"
    ]
    assert transitions == [("active", "degraded")]  # 第二次运行不重复迁移


def test_run_factor_monitor_ignores_non_active_factors(tmp_path):
    """只监控 active：candidate / retired 因子不记录 IC、不判定。"""
    db, active_id = _active_factor(tmp_path)
    candidate_id = add_factor(
        expression="amount",
        hypothesis=_HYPOTHESIS,
        category="价量",
        status="candidate",
        ast_json=serialize(parse("amount")),
        db_path=db,
    )
    _craft_history(db, active_id, [_RECORDED_IC] * _CRAFTED_DATES)
    result = run_factor_monitor(_monitor_panel(), _day(_MONITOR_DATES - 1), db_path=db)
    assert result["recorded"] == 1
    assert list_ic_series(candidate_id, db_path=db) == []


def test_run_factor_monitor_rejects_as_of_outside_panel(tmp_path):
    """as_of 不在面板日期内 → ValueError（fail loud，不静默记录错日期）。"""
    db, _ = _active_factor(tmp_path)
    with pytest.raises(ValueError, match="as_of"):
        run_factor_monitor(_monitor_panel(), "2026-01-01", db_path=db)


def test_run_factor_monitor_empty_library_is_noop(tmp_path):
    """无 active 因子 → 空结果（不建面板副作用之外的写）。"""
    result = run_factor_monitor(_monitor_panel(), _day(_MONITOR_DATES - 1), db_path=_db(tmp_path))
    assert result == {
        "recorded": 0,
        "checked": 0,
        "skipped": 0,
        "degraded": [],
        "judged": [],
    }


# ── ops 日批接线（注册表 + scheduler + _with_retry） ────────────────────────


def test_ops_registry_contains_factor_decay_monitor():
    """注册表四处对齐；``factor_decay_monitor`` 15:45 mon-fri、按时刻序排在快照后。

    add-screening-batch-scheduling 扩容（8→9）后：快照 15:30 → 选股 15:40 → 因子衰减
    15:45；add-crowding-regime-positioning（14→15）在快照之后插入拥挤度 15:35——
    邻接断言按新时刻序更新（断言意图不变：注册表按触发时刻升序）。
    """
    assert "factor_decay_monitor" in JOB_IDS
    assert set(JOB_IDS) == set(JOB_LABELS) == set(JOB_FUNCS) == set(SCHEDULES)
    assert JOB_IDS.index("market_snapshot_daily") == 0
    # 邻接（非仅大小）：拥挤度紧邻快照之后（15:30 → 15:35），中间不得插入他任务
    assert JOB_IDS.index("regime_daily") == JOB_IDS.index("market_snapshot_daily") + 1
    assert (
        JOB_IDS.index("market_snapshot_daily")
        < JOB_IDS.index("regime_daily")
        < JOB_IDS.index("screening_daily")
        < JOB_IDS.index("factor_decay_monitor")
    )
    assert SCHEDULES["factor_decay_monitor"] == {
        "day_of_week": "mon-fri",
        "hour": 15,
        "minute": 45,
        "timezone": "Asia/Shanghai",
    }
    assert JOB_FUNCS["factor_decay_monitor"] is run_factor_decay_monitor
    assert JOB_LABELS["factor_decay_monitor"] == "因子衰减监控"


def _job_call(sched, job_id):
    for call in sched.add_job.call_args_list:
        if call.kwargs.get("id") == job_id:
            return call
    registered = [call.kwargs.get("id") for call in sched.add_job.call_args_list]
    raise AssertionError(f"{job_id} job 未注册（已注册: {registered}）")


def _job_field(sched, job_id, name) -> list[str]:
    trigger = _job_call(sched, job_id).args[1]
    for field in trigger.fields:
        if field.name == name:
            return [str(expr) for expr in field.expressions]
    raise AssertionError(f"CronTrigger 无字段 {name!r}")


@pytest.fixture(autouse=True)
def _reset_scheduler_handle(monkeypatch):
    """start_scheduler 会登记进程内句柄——逐用例从 None 起步，防用例间串味。"""
    monkeypatch.setattr("finance_agent.outcome.scheduler._scheduler", None)


@patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
@patch("finance_agent.outcome.scheduler.BackgroundScheduler")
def test_scheduler_registers_factor_decay_monitor_at_1545(mock_sched_cls):
    """scheduler 注册 ``factor_decay_monitor``（15:45 mon-fri，含 misfire 硬化）。"""
    from finance_agent.outcome.scheduler import start_scheduler, stop_scheduler

    sched = MagicMock()
    mock_sched_cls.return_value = sched
    assert start_scheduler() is sched
    assert _job_field(sched, "factor_decay_monitor", "hour") == ["15"]
    assert _job_field(sched, "factor_decay_monitor", "minute") == ["45"]
    assert "mon-fri" in _job_field(sched, "factor_decay_monitor", "day_of_week")
    call = _job_call(sched, "factor_decay_monitor")
    assert call.kwargs.get("misfire_grace_time") == 3600
    assert call.kwargs.get("coalesce") is True
    stop_scheduler(sched)


@patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
@patch("finance_agent.outcome.scheduler.BackgroundScheduler")
def test_factor_monitor_job_routes_through_run_job_with_retry(mock_sched_cls):
    """job 函数经 ``_with_retry`` → ``run_job``（job_id/func 正确）；失败退避重试。"""
    from finance_agent.outcome.scheduler import start_scheduler, stop_scheduler

    sched = MagicMock()
    mock_sched_cls.return_value = sched
    start_scheduler()
    job_fn = _job_call(sched, "factor_decay_monitor").args[0]
    ok = {"run_id": 1, "status": "ok", "summary": {"checked": 0}, "error": None}
    with patch("finance_agent.outcome.scheduler.run_job", return_value=ok) as mock_run:
        job_fn()
    mock_run.assert_called_once_with(
        "factor_decay_monitor", source="scheduled", func=run_factor_decay_monitor
    )

    failed = {"run_id": 2, "status": "failed", "summary": None, "error": "boom"}
    with (
        patch("finance_agent.outcome.scheduler.time.sleep") as mock_sleep,
        patch("finance_agent.outcome.scheduler.run_job", side_effect=[failed, ok]) as mock_run2,
    ):
        job_fn()
    assert mock_run2.call_count == 2
    assert mock_sleep.call_count == 1
    stop_scheduler(sched)


def test_run_factor_decay_monitor_loads_panel_from_snapshot(tmp_path):
    """日批入口：快照库 → 面板 → run_factor_monitor（空因子库时为 no-op）。"""
    from finance_agent.data.snapshot import write_snapshot_rows

    snapshot_db = str(tmp_path / "market_snapshot.db")
    dates = [_day(offset) for offset in range(5)]
    for offset, trade_date in enumerate(dates):
        rows = [
            {
                "code": f"s{index:03d}",
                "open": 10.0 + offset,
                "high": 10.5 + offset,
                "low": 9.5 + offset,
                "close_raw": 10.2 + offset,
                "close_qfq": 10.2 + offset,
                "volume": 1000.0 + index,
                "amount": 10000.0 + index,
            }
            for index in range(40)
        ]
        write_snapshot_rows(trade_date, rows, db_path=snapshot_db)

    result = run_factor_decay_monitor(
        as_of=dates[-1], db_path=_db(tmp_path), snapshot_db_path=snapshot_db
    )
    assert result["checked"] == 0 and result["recorded"] == 0
