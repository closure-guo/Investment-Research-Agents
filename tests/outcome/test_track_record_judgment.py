"""add-track-record Task 2:horizon + 中性带 + superseded 判定纯函数。"""

import datetime

import pandas as pd

from finance_agent.outcome.track_record.judgment import (
    Resolution,
    _effective_horizon,
    resolve_prediction,
    should_supersede,
)


def _kline(prices, start="2026-09-02"):
    return pd.DataFrame(
        {
            "日期": [
                str(datetime.date.fromisoformat(start) + datetime.timedelta(days=i))
                for i in range(len(prices))
            ],
            "开盘": prices,
            "最高": prices,
            "最低": prices,
            "收盘": prices,
            "成交量": [1] * len(prices),
        }
    )


def _pred(entry=100.0, horizon=10, direction="long"):
    return {
        "entry_price": entry,
        "horizon_days": horizon,
        "direction": direction,
        "created_at": "2026-09-01T10:00:00",
    }


def test_horizon_win():
    # 10 天后收盘 115(无基准),超额正 → win
    r = resolve_prediction(_pred(), _kline([101, 102, 103, 104, 105, 106, 107, 108, 109, 115]))
    assert isinstance(r, Resolution)
    assert r.status == "resolved_win" and r.exit_price == 115.0
    assert abs(r.raw_return - 0.15) < 1e-9


def test_neutral_band():
    # 区间超额 +1.5% (< 2%) → neutral
    r = resolve_prediction(_pred(horizon=2), _kline([101, 101.5]))
    assert r is not None and r.status == "resolved_neutral"


def test_loss_and_short_symmetry():
    r = resolve_prediction(_pred(horizon=2), _kline([95, 90]))
    assert r is not None and r.status == "resolved_loss"
    # short 对称: 跌 → win
    rs = resolve_prediction(_pred(horizon=2, direction="short"), _kline([95, 90]))
    assert rs is not None and rs.status == "resolved_win"


def test_excess_uses_benchmark():
    bench = _kline([100, 100, 100])  # 基准不涨
    r = resolve_prediction(_pred(horizon=2, entry=100.0), _kline([101, 103]), bench)
    assert r is not None and r.status == "resolved_win"
    assert abs(r.excess_return - (0.03 - 0.0)) < 1e-9


def test_not_enough_rows_returns_none():
    assert resolve_prediction(_pred(horizon=10), _kline([101])) is None


def test_horizon_capped_at_252():
    assert _effective_horizon({"horizon_days": 999}) == 252
    assert _effective_horizon({"horizon_days": 60}) == 60


def test_superseded():
    old = {"symbol": "600519.SH", "direction": "long", "target_price": 120.0}
    new = {"symbol": "600519.SH", "direction": "short", "target_price": 100.0}
    assert should_supersede(old, new) is True
    # 同方向同目标价 → 不触发提前结算
    same = {"symbol": "600519.SH", "direction": "long", "target_price": 120.0}
    assert should_supersede(old, same) is False
    # 目标价不同 → 触发
    diff_target = {"symbol": "600519.SH", "direction": "long", "target_price": 121.0}
    assert should_supersede(old, diff_target) is True


def test_default_horizon_days_and_cutoff_pinned():
    from finance_agent.outcome.track_record import judgment

    assert judgment.DEFAULT_HORIZON_DAYS == 20
    assert judgment.CLOSE_TIME_CUTOFF == "15:00"


def test_default_horizon_days_env_override():
    """env 覆盖生效（DEFAULT_HORIZON_DAYS 为导入期读取 → 须子进程验证）。"""
    import os
    import subprocess
    import sys

    probe = "import finance_agent.outcome.track_record.judgment as j; print(j.DEFAULT_HORIZON_DAYS)"
    out = subprocess.run(  # noqa: S603 - sys.executable 固定路径，代码为内联常量
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=dict(os.environ, OUTCOME_DEFAULT_HORIZON_DAYS="30"),
        check=True,
    )
    assert out.stdout.strip() == "30"
