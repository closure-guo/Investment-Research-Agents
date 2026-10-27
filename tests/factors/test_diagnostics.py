"""诊断读数日批单测（add-factor-diagnostics-batch Task 2；Task 4 注册表断言并入本文件）。

契约（spec factor-library「诊断读数日批」）：存量补算 / 已新鲜零面板构建 / 失败
隔离 / 幂等 / 面板按需构建（无待算因子零面板）。面板注入打桩
``diagnostics._panel_from_snapshot``（生产路径零触碰，构造沿
test_api_factors.test_detail_monotonicity_observed_with_panel 同款结构化矩阵）。
"""

from __future__ import annotations

from datetime import date as _date
from datetime import timedelta as _timedelta

import numpy as np
import pandas as pd
import pytest

from finance_agent.factors.diagnostics import run_factor_diagnostics
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.library import (
    add_factor,
    get_factor_diagnostics,
    init_library,
    record_ic,
    set_status,
)

_DAYS = 41
_DATES = [(_date(2026, 1, 5) + _timedelta(days=o)).isoformat() for o in range(_DAYS)]
_CODES = [f"c{i:03d}" for i in range(40)]


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTOR_LIBRARY_DB_PATH", str(tmp_path / "factor_library.db"))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "snapshot.db"))
    init_library()
    return tmp_path


@pytest.fixture()
def _panel(monkeypatch):
    """注入合成面板并记录构建次数（面板按需/单次构建的可观测断言）。"""
    calls = {"n": 0}
    t_axis = np.arange(_DAYS)[:, None]
    i_axis = np.arange(len(_CODES))[None, :]
    daily = np.linspace(0.0005, 0.0025, len(_CODES))[None, :] * np.ones((_DAYS, 1))
    daily = daily + 0.0008 * np.sin(t_axis / 5.0 + i_axis / 7.0)
    close = 100.0 * np.cumprod(1.0 + daily, axis=0)

    def _frame(matrix: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(matrix, index=_DATES, columns=_CODES)

    panel = MarketPanel(
        fields={
            "open": _frame(close),
            "high": _frame(close),
            "low": _frame(close),
            "close": _frame(close),
            "volume": _frame(close),
            "amount": _frame(close),
        }
    )

    def _fake(snapshot_db_path=None):
        calls["n"] += 1
        return panel

    monkeypatch.setattr("finance_agent.factors.diagnostics._panel_from_snapshot", _fake)
    # 新鲜度源同步打桩：panel=None 路径的 as_of 取自快照库最新日（生产语义），
    # tmp 快照库为空 → 桩为面板末日（与注入面板一致）
    monkeypatch.setattr(
        "finance_agent.factors.diagnostics.latest_snapshot_date",
        lambda _snapshot_db_path=None: _DATES[-1],
    )
    return panel, calls


def _seed_factor(expression: str, *, ic_days: int) -> int:
    fid = add_factor(
        expression=expression,
        hypothesis="诊断日批用例",
        category="价量",
        status="candidate",
        source="llm",
    )
    set_status(fid, "active", detail={"reason": "ADMITTED"})
    for offset in range(ic_days):
        record_ic(fid, _DATES[offset], 0.1)
    return fid


def test_backfills_eligible_and_skips_low_samples(_panel) -> None:
    panel, calls = _panel
    hi_a = _seed_factor("close", ic_days=35)
    hi_b = _seed_factor("delta(close,20)", ic_days=35)
    low = _seed_factor("rank(volume)", ic_days=4)

    summary = run_factor_diagnostics()

    assert summary["computed"] == sorted([hi_a, hi_b])
    assert summary["skipped_samples"] == 1
    assert calls["n"] == 1  # 面板单次构建、逐因子复用
    stored = get_factor_diagnostics(hi_a)
    assert stored is not None
    assert stored["as_of"] == str(panel.fields["close"].index[-1])
    assert stored["style_exposure"] is not None
    assert stored["monotonicity"] is not None
    assert {"passed", "spearman", "q10_q1", "valid_days", "threshold"} <= set(stored["monotonicity"])
    assert get_factor_diagnostics(low) is None


def test_fresh_readings_skip_without_panel_build(_panel) -> None:
    _, calls = _panel
    _seed_factor("close", ic_days=35)

    run_factor_diagnostics()
    first = calls["n"]
    assert first == 1

    # 二跑：读数已新鲜（as_of == 面板末日）→ 零面板构建、零重算
    summary = run_factor_diagnostics()
    assert summary["computed"] == []
    assert summary["skipped_fresh"] == 1
    assert calls["n"] == first


def test_no_candidates_no_panel_build(_panel) -> None:
    _, calls = _panel
    _seed_factor("close", ic_days=4)  # 仅 4 点 → 非候选

    summary = run_factor_diagnostics()

    assert summary["candidates"] == 0
    assert summary["computed"] == []
    assert calls["n"] == 0  # 零待算 → 零面板构建


def test_failure_isolation_keeps_others_and_does_not_raise(_panel, monkeypatch) -> None:
    panel, _ = _panel
    bad = _seed_factor("rank(volume)", ic_days=35)
    good = _seed_factor("close", ic_days=35)

    import finance_agent.factors.diagnostics as diag

    real_evaluate = diag.evaluate

    def _boom(expression, panel_):
        if "rank" in str(expression):
            raise ValueError("合成求值失败")
        return real_evaluate(expression, panel_)

    monkeypatch.setattr(diag, "evaluate", _boom)

    summary = run_factor_diagnostics()

    assert summary["computed"] == [good]
    assert bad in summary["failed"]
    assert "合成求值失败" in summary["failed"][bad]
    stored = get_factor_diagnostics(good)
    assert stored is not None
    assert stored["as_of"] == str(panel.fields["close"].index[-1])
    assert get_factor_diagnostics(bad) is None  # 失败因子无读数（不虚构）


def test_diagnostics_job_registered() -> None:
    """作业注册（add-factor-diagnostics-batch Task 4）：17:15、含周末、触发序正确。

    既有 ops 侧顺序断言（tests/test_ops_api.py）动态对照 list(JOB_IDS)，插位不破坏。
    """
    from finance_agent.outcome.ops.jobs import JOB_FUNCS, JOB_IDS, JOB_LABELS, SCHEDULES

    assert "factor_diagnostics_daily" in JOB_IDS
    assert JOB_LABELS["factor_diagnostics_daily"] == "因子诊断读数"
    assert callable(JOB_FUNCS["factor_diagnostics_daily"])
    sched = SCHEDULES["factor_diagnostics_daily"]
    assert sched == {"day_of_week": "*", "hour": 17, "minute": 15, "timezone": "Asia/Shanghai"}
    # 触发序：17:15 在外资快讯（17:05）之后、cohort 之前
    assert JOB_IDS.index("factor_diagnostics_daily") > JOB_IDS.index("foreign_broker_news_daily")
    assert JOB_IDS.index("factor_diagnostics_daily") < JOB_IDS.index("cohort_batch")


def test_diagnostics_scheduler_wiring() -> None:
    """scheduler 接线：wrapper 存在、cron 可解析、start_scheduler 注册块就位。"""
    import inspect

    from finance_agent.outcome import scheduler as sched

    assert callable(sched._factor_diagnostics_job)
    assert sched._cron_for("factor_diagnostics_daily")
    src = inspect.getsource(sched.start_scheduler)
    assert 'id="factor_diagnostics_daily"' in src
