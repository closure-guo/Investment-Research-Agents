"""data/market_caliber 取价口径 seam 测试（add-settlement-price-caliber，C3）。

口径决策从调用方收回：结算=hfq fail-closed，盯市=qfq 近似披露；
基准对齐/日期归一唯一实现（#57 排序防御随迁）。
"""

import pandas as pd
import pytest

from finance_agent.data.market_caliber import (
    benchmark_close_on_or_before,
    monitor_series,
    normalize_dates,
    settlement_series,
)


class _RecordingClient:
    """记录 fetch_kline 调用参数的合成 client（可注入异常）。"""

    def __init__(self, frame: pd.DataFrame | None = None, error: Exception | None = None):
        self.calls: list[tuple[str, int, str]] = []
        self._frame = frame
        self._error = error

    def fetch_kline(self, code: str, days: int, *, adjust: str = "qfq") -> pd.DataFrame:
        self.calls.append((code, days, adjust))
        if self._error is not None:
            raise self._error
        assert self._frame is not None
        return self._frame


def _frame():
    return pd.DataFrame({"日期": ["2026-11-20", "2026-11-21"], "收盘": [10.0, 10.5]})


class TestSettlementSeries:
    def test_locks_hfq_adjust(self):
        """结算口径锁定 hfq：复权参数 SHALL NOT 由调用方指定。"""
        client = _RecordingClient(_frame())
        out = settlement_series(client, "600519.SH", days=60)
        assert out is not None
        assert client.calls == [("600519.SH", 60, "hfq")]

    def test_fail_closed_raises_without_fallback(self):
        """fail-closed：异常上抛（调用方既有缺失处置承接）；SHALL NOT 静默降级 qfq 再试。"""
        client = _RecordingClient(error=RuntimeError("sina down"))
        with pytest.raises(RuntimeError):
            settlement_series(client, "600519.SH", days=60)
        assert len(client.calls) == 1, "只调一次（hfq），SHALL NOT 换口径重试"


class TestMonitorSeries:
    def test_locks_qfq_adjust(self):
        """盯市口径 qfq（近似口径显式命名，与结算 hfq 分离）。"""
        client = _RecordingClient(_frame())
        out = monitor_series(client, "600519.SH", days=30)
        assert out is not None
        assert client.calls == [("600519.SH", 30, "qfq")]


class TestBenchmarkClose:
    def test_in_order_pick_last_close_on_or_before(self):
        bench = pd.DataFrame(
            {"日期": ["2026-11-18", "2026-11-19", "2026-11-20"], "收盘": [9.8, 9.9, 10.0]}
        )
        assert benchmark_close_on_or_before(bench, "2026-11-19") == pytest.approx(9.9)

    def test_out_of_order_still_correct(self):
        """#57 排序防御随迁：乱序输入按日期取最后收盘，不拿物理末行。"""
        bench = pd.DataFrame(
            {"日期": ["2026-11-20", "2026-11-18", "2026-11-19"], "收盘": [10.0, 9.8, 9.9]}
        )
        assert benchmark_close_on_or_before(bench, "2026-11-19") == pytest.approx(9.9)

    def test_before_range_returns_none(self):
        bench = pd.DataFrame({"日期": ["2026-11-18"], "收盘": [9.8]})
        assert benchmark_close_on_or_before(bench, "2026-11-01") is None


class TestNormalizeDates:
    def test_str_date_passthrough(self):
        df = pd.DataFrame({"日期": ["2026-11-20", "2026-11-21"]})
        out = normalize_dates(df)
        assert list(out["日期"]) == ["2026-11-20", "2026-11-21"]

    def test_timestamp_and_date_objects_normalized(self):
        df = pd.DataFrame(
            {"日期": [pd.Timestamp("2026-11-20"), pd.Timestamp("2026-11-21 15:00:00")]}
        )
        out = normalize_dates(df)
        assert list(out["日期"]) == ["2026-11-20", "2026-11-21"]

    def test_does_not_mutate_caller_frame(self):
        df = pd.DataFrame({"日期": [pd.Timestamp("2026-11-20")]})
        normalize_dates(df)
        assert not isinstance(df["日期"].iloc[0], str)
