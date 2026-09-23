"""add-track-record Task 2:horizon + 中性带 + superseded 判定纯函数。

delta update-decision-settlement-contract Task 3:入场基准改为 K 线派生 settle_entry_price
（存档参考价 entry_price 判定时忽略）;neutral 方向判定输出 avoidance_* 状态。
"""

import datetime

import pandas as pd

from finance_agent.outcome.track_record.judgment import (
    Resolution,
    _effective_horizon,
    derive_entry,
    resolve_prediction,
    should_supersede,
)


def _kline(rows: list[tuple[str, float]]) -> "pd.DataFrame":
    import pandas as pd

    return pd.DataFrame({"日期": [r[0] for r in rows], "收盘": [r[1] for r in rows]})


def _kline_prices(prices, start="2026-09-02"):
    """连续自然日 OHLCV 序列(夹具捷径:交易日=自然日,判定只读日期/收盘)。"""
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


def _pred(horizon=10, direction="long"):
    """观点存档。created_at 早于 kline 首行 → 派生入场 = kline 首行收盘。"""
    return {
        "entry_price": 88.0,  # 存档参考价:判定忽略(故意不等于派生入场,防误用)
        "horizon_days": horizon,
        "direction": direction,
        "created_at": "2026-09-01T10:00:00",
    }


def test_horizon_win():
    # 派生入场 9/2 收盘 100;10 个交易日后(9/12)收盘 115(无基准) → 超额 +15% → win
    r = resolve_prediction(
        _pred(), _kline_prices([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115])
    )
    assert isinstance(r, Resolution)
    assert r.status == "resolved_win" and r.exit_price == 115.0
    assert abs(r.raw_return - 0.15) < 1e-9


def test_neutral_band():
    # 派生入场 9/2 收盘 100;2 个交易日后 101.5 → 区间超额 +1.5% (< 2%) → neutral
    r = resolve_prediction(_pred(horizon=2), _kline_prices([100, 101, 101.5]))
    assert r is not None and r.status == "resolved_neutral"


def test_loss_and_short_symmetry():
    r = resolve_prediction(_pred(horizon=2), _kline_prices([100, 95, 90]))
    assert r is not None and r.status == "resolved_loss"
    # short 对称: 跌 → win
    rs = resolve_prediction(_pred(horizon=2, direction="short"), _kline_prices([100, 95, 90]))
    assert rs is not None and rs.status == "resolved_win"


def test_excess_uses_benchmark():
    bench = _kline_prices([100, 100, 100])  # 基准不涨
    r = resolve_prediction(_pred(horizon=2), _kline_prices([100, 101, 103]), bench)
    assert r is not None and r.status == "resolved_win"
    assert abs(r.excess_return - (0.03 - 0.0)) < 1e-9


def test_not_enough_rows_returns_none():
    # 派生入场 9/2 后仅 1 个交易日 < horizon 10 → 未到点 None(保持 open)
    assert resolve_prediction(_pred(horizon=10), _kline_prices([101, 102])) is None


def test_derive_entry_before_close_takes_same_day_close():
    kline = _kline([("2026-09-01", 10.0), ("2026-09-02", 11.0), ("2026-09-03", 12.0)])
    p = {"created_at": "2026-09-02T10:00:00"}
    assert derive_entry(p, kline) == ("2026-09-02", 11.0)


def test_derive_entry_after_close_takes_next_trading_day():
    kline = _kline([("2026-09-01", 10.0), ("2026-09-02", 11.0), ("2026-09-03", 12.0)])
    p = {"created_at": "2026-09-02T18:00:00"}
    assert derive_entry(p, kline) == ("2026-09-03", 12.0)


def test_derive_entry_non_trading_day_takes_next():
    kline = _kline([("2026-09-04", 10.0), ("2026-09-07", 11.0)])  # 9/5-9/6 周末
    p = {"created_at": "2026-09-05T10:00:00"}
    assert derive_entry(p, kline) == ("2026-09-07", 11.0)


def test_derive_entry_no_rows_returns_none():
    assert (
        derive_entry({"created_at": "2026-09-10T10:00:00"}, _kline([("2026-09-01", 10.0)])) is None
    )


def test_resolve_neutral_avoidance_semantics():
    # 入场 9/2 收盘 11；20 个交易日后收盘 9.9（跌 10%）→ 回避正确
    rows = [("2026-09-02", 11.0)] + [(f"2026-10-{d:02d}", 9.9) for d in range(1, 21)]
    p = {
        "created_at": "2026-09-02T10:00:00",
        "direction": "neutral",
        "horizon_days": 20,
    }
    r = resolve_prediction(p, _kline(rows))
    assert r is not None
    assert r.status == "avoidance_win"
    assert r.entry_date == "2026-09-02" and r.entry_price == 11.0
    assert r.exit_date == "2026-10-20" and r.hold_days == 20


def test_resolve_neutral_missed_upside():
    rows = [("2026-09-02", 11.0)] + [(f"2026-10-{d:02d}", 13.2) for d in range(1, 21)]
    p = {"created_at": "2026-09-02T10:00:00", "direction": "neutral", "horizon_days": 20}
    r = resolve_prediction(p, _kline(rows))
    assert r is not None and r.status == "avoidance_loss"


def test_resolve_long_uses_derived_entry_not_reference():
    rows = [("2026-09-02", 11.0)] + [(f"2026-10-{d:02d}", 12.1) for d in range(1, 21)]
    p = {
        "created_at": "2026-09-02T10:00:00",
        "direction": "long",
        "horizon_days": 20,
        "entry_price": 999.0,  # 参考价离谱也必须被忽略
    }
    r = resolve_prediction(p, _kline(rows))
    assert r is not None and r.entry_price == 11.0
    assert abs(r.raw_return - (12.1 / 11.0 - 1.0)) < 1e-6


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


def test_avoidance_statuses_and_terminal_constant():
    """回避结果域(avoidance_win/loss/neutral)与生命周期终态(avoidance)是两个域。"""
    from finance_agent.outcome.track_record import judgment

    assert judgment.AVOIDANCE_STATUSES == (
        "avoidance_win",
        "avoidance_loss",
        "avoidance_neutral",
    )
    assert judgment.TERMINAL_AVOIDANCE_STATUS == "avoidance"
    assert judgment.TERMINAL_AVOIDANCE_STATUS not in judgment.AVOIDANCE_STATUSES


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
