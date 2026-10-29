"""拒绝池复苏重评通道（add-rejected-factor-revival）单测。

IC 序列造数：点值 0.05/0.15 交替 → mean=0.10, std≈0.05 → 滚动 ICIR≈2.0（过静默线）；
点值恒 0.01（常量序列）→ ICIR=0.0（低于静默线）。准入面板复用
``tests.factors.test_builtin`` 合成强信号面板（``delta(close,20)`` 过闸）。

时钟纪律（clock-bomb 教训）：事件时间戳是**真实运行时钟**（library._now()），
``now`` 一律从事件戳**动态推导**（+40 天=冷却外；+10 天=冷却内），禁止写死日期——
写死日期的用例在任何非编写日运行必炸。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from finance_agent.factors import library
from finance_agent.factors.constraints import REVIVAL_COOLDOWN_DAYS, REVIVAL_DAILY_CAP
from finance_agent.factors.revival import (
    _in_cooldown,
    _last_revival_event_at,
    run_factor_revival,
)
from tests.factors.test_builtin import _STRONG_SEED, _strong_panel

_HYPOTHESIS = "拒绝池复苏重提：测试用经济学假设文本，长度达标并描述与下一期收益的关系。"
_ADMIT_EXPRESSION = "delta(close,20)"
_REJECT_EXPRESSION = "div(ts_mean(close,5),close)"


def _seed_rejected(db: str | Path, expression: str, *, ic_points: int, ic_value: float) -> int:
    """造 rejected 行 + IC 序列（点数 ic_points、点值 ic_value 或 "alternating" 强信号）。

    IC 日期取 2026-05-01 起逐日递增的**互异**日期（record_ic 按 (factor, date)
    幂等 upsert，日期循环会折叠成 30 点——正好踩 60 点门槛的假阴性）。
    """
    result = library.record_rejected_factor(
        expression=expression,
        hypothesis=_HYPOTHESIS,
        category="外部动量",
        ast_json=None,
        source="external",
        gate=2,
        reason="WEAK_ICIR",
        detail=None,
        db_path=db,
    )
    factor_id = int(result["factor_id"])
    base = datetime(2026, 5, 1)
    for i in range(ic_points):
        value = ic_value
        if ic_value == "alternating":
            value = 0.05 if i % 2 == 0 else 0.15
        library.record_ic(
            factor_id, (base + timedelta(days=i)).date().isoformat(), value, db_path=db
        )
    return factor_id


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "factor_library.db")


def _now_after_cooldown(db: str | Path, *, days: int = REVIVAL_COOLDOWN_DAYS + 10) -> datetime:
    """从库内最新 created/re_rejected 事件戳推导 now（冷却外/内由 days 控制）。"""
    last = _last_revival_event_at(library.list_events(db_path=db))
    assert last is not None
    return datetime.fromisoformat(last) + timedelta(days=days)


def test_in_cooldown_unit() -> None:
    now = datetime(2026, 12, 1, 12, 0, 0)
    assert _in_cooldown(None, now=now, cooldown_days=30) is False
    recent = (now - timedelta(days=10)).isoformat()
    assert _in_cooldown(recent, now=now, cooldown_days=30) is True
    stale = (now - timedelta(days=40)).isoformat()
    assert _in_cooldown(stale, now=now, cooldown_days=30) is False
    assert _in_cooldown("not-a-timestamp", now=now, cooldown_days=30) is False  # 损坏放行


def test_last_revival_event_at_takes_max() -> None:
    events = [
        {"event": "created", "created_at": "2026-10-28T09:00:00"},
        {"event": "status_change", "created_at": "2026-10-29T09:00:00"},
        {"event": "re_rejected", "created_at": "2026-11-20T09:00:00"},
    ]
    assert _last_revival_event_at(events) == "2026-11-20T09:00:00"
    assert _last_revival_event_at([]) is None


def test_eligible_row_submitted_and_admitted(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _ADMIT_EXPRESSION, ic_points=60, ic_value="alternating")
    summary = run_factor_revival(
        db_path=db, panel=_strong_panel(), seed=_STRONG_SEED, now=_now_after_cooldown(db)
    )
    assert summary["checked"] == 1
    assert summary["eligible"] == 1
    assert summary["submitted"] == 1
    assert summary["admitted"] == 1
    rows = library.list_factors(db_path=db, include_rejected=True)
    active = [r for r in rows if r["status"] == "active"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    assert len(active) == 1 and len(rejected) == 1  # 新行入库，原行终态不变
    assert active[0]["expression"] == _ADMIT_EXPRESSION


def test_below_line_row_not_eligible(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _ADMIT_EXPRESSION, ic_points=60, ic_value=0.01)  # 常量 → ICIR 0.0
    summary = run_factor_revival(db_path=db, now=_now_after_cooldown(db))
    assert summary["eligible"] == 0 and summary["submitted"] == 0


def test_few_points_row_not_eligible(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _ADMIT_EXPRESSION, ic_points=30, ic_value="alternating")
    summary = run_factor_revival(db_path=db, now=_now_after_cooldown(db))
    assert summary["eligible"] == 0


def test_cooldown_skip(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _ADMIT_EXPRESSION, ic_points=60, ic_value="alternating")
    summary = run_factor_revival(db_path=db, now=_now_after_cooldown(db, days=10))
    assert summary["cooldown_skipped"] == 1 and summary["submitted"] == 0


def test_re_rejection_persists_event_and_resets_clock(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _REJECT_EXPRESSION, ic_points=60, ic_value="alternating")
    outside = _now_after_cooldown(db)  # 冷却外
    # 第一次：面板不可得 → external 路径 fail loud（可复苏候选 + panel=None + 空快照库）
    with pytest.raises(ValueError):  # external 路径对空快照库 fail loud
        run_factor_revival(
            db_path=db, snapshot_db_path=str(tmp_path / "empty-snap"), now=outside
        )
    # 第二次：真面板 + 会再拒的表达式 → re_rejected 落事件（冷却时钟锚点更新）
    summary = run_factor_revival(
        db_path=db, panel=_strong_panel(), seed=_STRONG_SEED, now=outside
    )
    assert summary["re_rejected"] == 1 and summary["admitted"] == 0
    events = library.list_events(db_path=db)
    assert any(e["event"] == "re_rejected" for e in events)
    # 第三次：自最新 re_rejected 事件 +5 天 → 冷却内不再提交
    again = run_factor_revival(
        db_path=db, panel=_strong_panel(), now=_now_after_cooldown(db, days=5)
    )
    assert again["cooldown_skipped"] == 1 and again["submitted"] == 0


def test_zero_candidates_no_panel_build(tmp_path, monkeypatch) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _ADMIT_EXPRESSION, ic_points=10, ic_value="alternating")  # 点数不足

    def _boom(**kwargs):
        raise AssertionError("面板不得构建")

    monkeypatch.setattr("finance_agent.factors.revival.run_factor_external_import", _boom)
    summary = run_factor_revival(db_path=db, now=_now_after_cooldown(db))
    assert summary["submitted"] == 0


def test_daily_cap_orders_by_abs_icir(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _ADMIT_EXPRESSION, ic_points=60, ic_value="alternating")  # |ICIR|≈2.0
    # 8 个**结构不同构**的 rejected 行（同构匹配数字只按「有」——同形变体会被
    # record_rejected_factor 折叠成一行，凑不出 9 个 eligible）
    distinct = [
        "sub(div(close,ref(close,1)),1)",
        "ts_corr(volume,close,10)",
        "div(ts_max(volume,10),ts_sum(volume,10))",
        "log(add(volume,1))",
        "sub(ts_mean(high,5),ts_mean(low,5))",
        "div(ts_std(close,20),ts_mean(close,20))",
        "mul(sign(sub(close,ref(close,1))),volume)",
        "sqrt(div(amount,volume))",
    ]
    for expression in distinct:
        _seed_rejected(db, expression, ic_points=60, ic_value="alternating")
    summary = run_factor_revival(
        db_path=db,
        panel=_strong_panel(),
        seed=_STRONG_SEED,
        daily_cap=5,
        now=_now_after_cooldown(db),
    )
    assert summary["eligible"] == 9 and summary["submitted"] == 5


def test_dry_run_previews_without_submit(tmp_path, monkeypatch) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _ADMIT_EXPRESSION, ic_points=60, ic_value="alternating")

    def _boom(**kwargs):
        raise AssertionError("dry_run 不得提交")

    monkeypatch.setattr("finance_agent.factors.revival.run_factor_external_import", _boom)
    summary = run_factor_revival(
        db_path=db, panel=_strong_panel(), dry_run=True, now=_now_after_cooldown(db)
    )
    assert summary["dry_run"] is True and summary["submitted"] == 0
    assert summary["eligible"] == 1 and summary["revived"]


def test_constants_mirror_constraints() -> None:
    assert REVIVAL_COOLDOWN_DAYS == 30 and REVIVAL_DAILY_CAP == 5
