"""结算纯函数:止损/目标/同日/超期/方向符号化/一字板递延/停牌顺延/基准缺失。"""

import pandas as pd

from finance_agent.outcome.settle import _bench_close_on_or_before, evaluate_decision


def _kline(rows: list[dict]) -> pd.DataFrame:
    """rows: {日期, 开盘, 收盘, 最高, 最低}"""
    return pd.DataFrame(rows)


def _decision(**overrides):
    base = {
        "decision_id": "d1",
        "action": "buy",
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "target_price": 120.0,
        "timestamp": "2026-07-01T15:00:00",
    }
    base.update(overrides)
    return base


def _day(date, open_, close, high=None, low=None):
    return {
        "日期": date,
        "开盘": open_,
        "收盘": close,
        "最高": high if high is not None else max(open_, close),
        "最低": low if low is not None else min(open_, close),
    }


class TestStopAndTarget:
    def test_hit_stop(self):
        kline = _kline([_day("2026-07-02", 99, 98), _day("2026-07-03", 95, 94, low=89)])
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_stop"
        assert result.settle_price == 90.0
        assert result.hold_days == 2
        assert result.decision_return == -0.1
        assert result.decision_hit is False

    def test_hit_target(self):
        kline = _kline([_day("2026-07-02", 101, 110, high=121)])
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_target"
        assert result.settle_price == 120.0
        assert result.decision_return == 0.2
        assert result.decision_hit is True

    def test_same_day_stop_priority(self):
        # 同日触及止损和目标 → 止损优先(保守)
        kline = _kline([_day("2026-07-02", 100, 100, high=125, low=85)])
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_stop"
        assert result.settle_price == 90.0


class TestExpired:
    def test_expired_at_max_hold_days(self):
        kline = _kline([_day(f"2026-07-{d:02d}", 100, 101) for d in range(2, 22)])  # 20 行
        result = evaluate_decision(_decision(), kline, None, max_hold_days=20)
        assert result.status == "expired"
        assert result.hold_days == 20
        assert result.settle_price == 101.0

    def test_not_enough_rows_returns_none(self):
        kline = _kline([_day("2026-07-02", 100, 101)])
        assert evaluate_decision(_decision(), kline, None, max_hold_days=20) is None


class TestDirection:
    def test_sell_direction_negated(self):
        # sell 后跌为正
        kline = _kline([_day("2026-07-02", 99, 95)])
        result = evaluate_decision(
            _decision(action="sell", stop_loss=None, target_price=None),
            kline,
            None,
            max_hold_days=1,
        )
        assert result.decision_return == 0.05
        assert result.decision_hit is True

    def test_hold_watch_same_as_sell(self):
        for action in ("hold", "watch"):
            kline = _kline([_day("2026-07-02", 99, 105)])
            result = evaluate_decision(
                _decision(action=action, stop_loss=None, target_price=None),
                kline,
                None,
                max_hold_days=1,
            )
            assert result.decision_return == -0.05


class TestOneWordBoard:
    def test_limit_down_board_defers_settlement(self):
        # 一字跌停(开=高=低=收)触及止损但未成交 → 递延至打开首日开盘价
        kline = _kline(
            [
                _day("2026-07-02", 95, 95),  # 普通日,未触及
                _day("2026-07-03", 88, 88, high=88, low=88),  # 一字跌停,触及止损但未成交
                _day("2026-07-04", 88, 88, high=88, low=88),  # 继续一字
                _day("2026-07-05", 85, 86),  # 打开,首个可成交价=开盘 85
            ]
        )
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_stop"
        assert result.settle_price == 85.0  # 实际可成交价,非 stop_loss 90
        assert result.hold_days == 4  # 含等待日
        assert abs(result.decision_return - (-0.15)) < 1e-9

    def test_board_unbroken_returns_none(self):
        # 一直一字板未打开 → 本批不结算
        kline = _kline(
            [
                _day("2026-07-02", 88, 88, high=88, low=88),
                _day("2026-07-03", 88, 88, high=88, low=88),
            ]
        )
        assert evaluate_decision(_decision(), kline, None) is None


class TestSuspension:
    def test_suspension_days_not_counted(self):
        # 停牌无 K 线行:hold_days 只数有数据的行,周期自然顺延
        kline = _kline(
            [
                _day("2026-07-02", 99, 98),
                # 07-03 ~ 07-10 停牌,无行
                _day("2026-07-11", 97, 110, high=121),  # 复牌首日,触及目标
            ]
        )
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_target"
        assert result.hold_days == 2  # 停牌日不计入


class TestBenchmark:
    def _bench(self):
        return _kline(
            [
                _day("2026-07-01", 4000, 4000),
                _day("2026-07-02", 4040, 4040),
            ]
        )

    def test_excess_computed(self):
        kline = _kline([_day("2026-07-02", 101, 110, high=121)])
        result = evaluate_decision(_decision(), kline, self._bench())
        assert result.benchmark_return == 0.01
        assert abs(result.decision_excess - (0.2 - 0.01)) < 1e-9

    def test_benchmark_missing_gives_none(self):
        kline = _kline([_day("2026-07-02", 101, 110, high=121)])
        result = evaluate_decision(_decision(), kline, None)
        assert result.benchmark_return is None
        assert result.decision_excess is None
        assert result.decision_return == 0.2  # 自身收益仍记

    def test_benchmark_also_negated_for_sell(self):
        kline = _kline([_day("2026-07-02", 99, 95)])
        result = evaluate_decision(
            _decision(action="sell", stop_loss=None, target_price=None),
            kline,
            self._bench(),
            max_hold_days=1,
        )
        assert result.decision_return == 0.05
        assert result.benchmark_return == -0.01  # 基准也取负
        assert abs(result.decision_excess - 0.06) < 1e-9


class TestDateDtype:
    def test_datetime_date_kline_column(self):
        # 真实 akshare stock_zh_a_hist 的日期列是 datetime.date 对象(回归 C1)
        import datetime as dt

        kline = _kline(
            [
                _day(dt.date(2026, 7, 2), 99, 98),
                _day(dt.date(2026, 7, 3), 95, 94, low=89),
            ]
        )
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_stop"
        assert result.settle_date == "2026-07-03"
        assert result.settle_price == 90.0

    def test_pd_timestamp_benchmark_normalized(self):
        # benchmark 日期为 pd.Timestamp(str() 带时间分量)→ str[:10] 归一后仍正确
        bench = _kline(
            [
                _day(pd.Timestamp("2026-07-01 15:00:00"), 4000, 4000),
                _day(pd.Timestamp("2026-07-02 15:00:00"), 4040, 4040),
            ]
        )
        kline = _kline([_day("2026-07-02", 101, 110, high=121)])
        result = evaluate_decision(_decision(), kline, bench)
        assert result.benchmark_return == 0.01


class TestGapThrough:
    def test_gap_down_through_stop(self):
        # 开盘 85 已跳空穿越 stop 90 → 按实际可成交价 85 结算,不虚记 90
        kline = _kline([_day("2026-07-02", 85, 86, low=84)])
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_stop"
        assert result.settle_price == 85.0
        assert abs(result.decision_return - (-0.15)) < 1e-9

    def test_gap_up_through_target(self):
        # 开盘 125 已跳空穿越 target 120 → 按实际可成交价 125 结算
        kline = _kline([_day("2026-07-02", 125, 126, high=127)])
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_target"
        assert result.settle_price == 125.0
        assert result.decision_return == 0.25


class TestNumericGuards:
    def test_zero_entry_price_returns_none(self, caplog):
        import logging

        kline = _kline([_day("2026-07-02", 99, 98, low=89)])
        with caplog.at_level(logging.WARNING):
            result = evaluate_decision(_decision(entry_price=0.0), kline, None)
        assert result is None  # 数据损坏保持 open,不 ZeroDivisionError

    def test_nan_stop_target_treated_as_none(self):
        # NaN 触发位按 None 对待(否则比较恒 False 静默失效)
        kline = _kline([_day("2026-07-02", 99, 95)])
        result = evaluate_decision(
            _decision(stop_loss=float("nan"), target_price=float("nan")),
            kline,
            None,
            max_hold_days=1,
        )
        assert result.status == "expired"
        assert result.decision_return == -0.05

    def test_benchmark_all_after_decision_gives_none(self):
        # 基准全部在决策日/结算日之后 → entry_bench/settle_bench 不可得 → None
        bench = _kline([_day("2026-07-10", 4000, 4000)])
        kline = _kline([_day("2026-07-02", 101, 110, high=121)])
        result = evaluate_decision(_decision(), kline, bench)
        assert result.benchmark_return is None
        assert result.decision_excess is None
        assert result.decision_return == 0.2


class TestBoundaryAndOrdering:
    def test_low_equals_stop_triggers(self):
        # 恰等触发:low == stop_loss 含边界(<=)
        kline = _kline([_day("2026-07-02", 95, 94, low=90)])
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_stop"
        assert result.settle_price == 90.0

    def test_high_equals_target_triggers(self):
        # 恰等触发:high == target_price 含边界(>=)
        kline = _kline([_day("2026-07-02", 101, 110, high=120)])
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_target"
        assert result.settle_price == 120.0

    def test_unsorted_kline_sorted(self):
        # 乱序输入 → sort_values 守卫,仍按日期升序结算
        kline = _kline(
            [
                _day("2026-07-03", 95, 94, low=89),
                _day("2026-07-02", 99, 98),
            ]
        )
        result = evaluate_decision(_decision(), kline, None)
        assert result.status == "hit_stop"
        assert result.settle_date == "2026-07-03"
        assert result.hold_days == 2


class TestDeferralPastMaxHoldDays:
    def test_deferral_extends_past_max_hold_days(self):
        # 递延期间不做超期判定:第 max_hold_days 行恰为一字板 → 递延到打开日,
        # hold_days 可超过 max_hold_days
        kline = _kline(
            [
                _day("2026-07-02", 88, 88, high=88, low=88),  # 一字触及止损,递延
                _day("2026-07-03", 88, 88, high=88, low=88),  # 第 2 行(=max_hold_days)仍一字
                _day("2026-07-04", 85, 86),  # 打开,以开盘价结算
            ]
        )
        result = evaluate_decision(_decision(), kline, None, max_hold_days=2)
        assert result.status == "hit_stop"
        assert result.settle_price == 85.0
        assert result.hold_days == 3  # > max_hold_days


class TestBenchCloseSortGuard:
    """#57 子项3:_bench_close_on_or_before 不得隐式依赖调用方排序——乱序输入
    仍取「date 或之前最后一个收盘」(此前 iloc[-1] 拿到的是物理末行,乱序即错)。"""

    def test_unsorted_benchmark_picks_latest_eligible(self):
        bench = pd.DataFrame(
            {
                "日期": ["2026-08-11", "2026-08-01", "2026-08-12"],
                "收盘": [2.0, 1.0, 3.0],
            }
        )
        # 升序语义下 ≤08-11 的最后收盘是 08-11 的 2.0;乱序物理末行是 08-01 的 1.0
        assert _bench_close_on_or_before(bench, "2026-08-11") == 2.0

    def test_sorted_input_unchanged(self):
        bench = pd.DataFrame(
            {
                "日期": ["2026-08-01", "2026-08-11", "2026-08-12"],
                "收盘": [1.0, 2.0, 3.0],
            }
        )
        assert _bench_close_on_or_before(bench, "2026-08-11") == 2.0
