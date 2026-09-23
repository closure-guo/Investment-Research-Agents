"""回测编排聚合逻辑测试：注入 fake replay_fn，不调 LLM/网络。"""

import pandas as pd
import pytest
from evals.backtest.run_backtest import _trade_daily_returns, run_backtest


def _kline(closes: list[float], start_date: str = "2024-01-01") -> pd.DataFrame:
    dates = pd.date_range(start_date, periods=len(closes), freq="D").strftime("%Y-%m-%d").tolist()
    return pd.DataFrame(
        {
            "日期": dates,
            "开盘": closes,
            "收盘": closes,
            "最高": [c * 1.01 for c in closes],
            "最低": [c * 0.99 for c in closes],
        }
    )


def _closes(n: int, up: float, down: float, start: float = 10.0) -> list[float]:
    """交替 up/down 日涨幅的收盘序列（控制 Sharpe 量级）。"""
    closes = [start]
    for i in range(1, n):
        closes.append(closes[-1] * (1.0 + (up if i % 2 else down)))
    return closes


def _settlement(settle_date: str, settle_price: float) -> dict:
    return {
        "status": "expired",
        "settle_date": settle_date,
        "settle_price": settle_price,
        "hold_days": 10,
        "decision_return": 0.05,
        "benchmark_return": None,
        "decision_excess": None,
        "decision_hit": True,
    }


def _outcome(
    code: str,
    decision_date: str,
    *,
    action: str,
    settle_date: str,
    settle_price: float,
    entry_price: float,
    agreement: float = 1.0,
) -> dict:
    """形如 replay_with_consistency（透传修复后）的返回。"""
    return {
        "code": code,
        "decision_date": decision_date,
        "actions": [action] * 3,
        "agreement": agreement,
        "settlement": _settlement(settle_date, settle_price),
        "entry_price": entry_price,
        "action": action,
        "snapshot_metadata": {},
    }


def _fake_replay(outcomes: dict[str, dict], calls: list | None = None):
    def replay(code, decision_date, *, n=3, full_kline=None, full_benchmark=None):
        if calls is not None:
            calls.append(
                {
                    "code": code,
                    "decision_date": decision_date,
                    "n": n,
                    "full_kline": full_kline,
                    "full_benchmark": full_benchmark,
                }
            )
        return outcomes[code]

    return replay


class TestTradeDailyReturns:
    def _dates(self, n: int) -> list[str]:
        return pd.date_range("2024-01-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist()

    def test_buy_rising_window_with_settle_price_override(self):
        kline = _kline([100.0, 110.0, 121.0, 108.9, 118.0])
        result = {
            "settlement": _settlement("2024-01-04", 112.0),
            "entry_price": 100.0,
            "action": "buy",
            "decision_date": "2024-01-01",
        }
        rets = _trade_daily_returns(result, kline)
        # 持有 T+1..settle：[110, 121, 108.9→按结算价 112 修正]
        assert rets == pytest.approx([0.10, 0.10, 112.0 / 121.0 - 1.0])

    def test_sell_negates_returns(self):
        kline = _kline([100.0, 110.0, 121.0, 108.9, 118.0])
        buy = _trade_daily_returns(
            {
                "settlement": _settlement("2024-01-04", 112.0),
                "entry_price": 100.0,
                "action": "buy",
                "decision_date": "2024-01-01",
            },
            kline,
        )
        sell = _trade_daily_returns(
            {
                "settlement": _settlement("2024-01-04", 112.0),
                "entry_price": 100.0,
                "action": "sell",
                "decision_date": "2024-01-01",
            },
            kline,
        )
        assert sell == pytest.approx([-r for r in buy])

    def test_entry_price_taken_from_result(self):
        kline = _kline([100.0, 110.0, 121.0])
        rets = _trade_daily_returns(
            {
                "settlement": _settlement("2024-01-02", 110.0),
                "entry_price": 105.0,
                "action": "buy",
                "decision_date": "2024-01-01",
            },
            kline,
        )
        assert rets == pytest.approx([110.0 / 105.0 - 1.0])

    def test_no_settlement_empty(self):
        kline = _kline([100.0, 110.0])
        assert (
            _trade_daily_returns(
                {
                    "settlement": None,
                    "entry_price": 100.0,
                    "action": "buy",
                    "decision_date": "2024-01-01",
                },
                kline,
            )
            == []
        )

    def test_nonpositive_entry_empty(self):
        kline = _kline([100.0, 110.0])
        assert (
            _trade_daily_returns(
                {
                    "settlement": _settlement("2024-01-02", 110.0),
                    "entry_price": 0.0,
                    "action": "buy",
                    "decision_date": "2024-01-01",
                },
                kline,
            )
            == []
        )

    def test_settle_on_decision_date_empty(self):
        # 结算日不晚于决策日（数据异常）→ 空窗口 → []
        kline = _kline([100.0, 110.0])
        assert (
            _trade_daily_returns(
                {
                    "settlement": _settlement("2024-01-01", 100.0),
                    "entry_price": 100.0,
                    "action": "buy",
                    "decision_date": "2024-01-01",
                },
                kline,
            )
            == []
        )

    @pytest.mark.parametrize("action", ["hold", "watch", "neutral", "unknown", ""])
    def test_non_executable_action_returns_empty(self, action):
        """Δ2 终审 B：非 buy/sell（hold/watch→neutral）不产生方向化 system 收益（§1.8②）。"""
        kline = _kline([100.0, 110.0, 121.0])
        assert (
            _trade_daily_returns(
                {
                    "settlement": _settlement("2024-01-02", 110.0),
                    "entry_price": 100.0,
                    "action": action,
                    "decision_date": "2024-01-01",
                },
                kline,
            )
            == []
        )


class TestRunBacktest:
    def _setup(self, up: float, down: float, *, actions: tuple[str, str] = ("buy", "sell")):
        """2 只一致标的（bull/bear）+ 1 只低一致率标的（sideways）。"""
        n = 90
        closes = _closes(n, up, down)
        dates = pd.date_range("2024-01-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist()
        decision_date, settle_date = dates[30], dates[40]
        settle_price = closes[40]
        sample = [
            {"code": "600000", "regime": "bull", "decision_date": decision_date},
            {"code": "600001", "regime": "bear", "decision_date": decision_date},
            {"code": "600002", "regime": "sideways", "decision_date": decision_date},
        ]
        outcomes = {
            "600000": _outcome(
                "600000",
                decision_date,
                action=actions[0],
                settle_date=settle_date,
                settle_price=settle_price,
                entry_price=closes[30],
            ),
            "600001": _outcome(
                "600001",
                decision_date,
                action=actions[1],
                settle_date=settle_date,
                settle_price=settle_price,
                entry_price=closes[30],
            ),
            # 一致率 1/3 < 2/3：剔除绩效、单独披露
            "600002": _outcome(
                "600002",
                decision_date,
                action="buy",
                settle_date=settle_date,
                settle_price=settle_price,
                entry_price=closes[30],
                agreement=0.3333,
            ),
        }
        klines = {c: _kline(closes) for c in outcomes}
        return sample, outcomes, klines, settle_price

    def test_aggregation_excludes_low_consistency(self):
        sample, outcomes, klines, _ = self._setup(up=0.014, down=-0.010)
        calls: list[dict] = []
        report = run_backtest(sample, klines, repeats=3, replay_fn=_fake_replay(outcomes, calls))
        assert report["n_sample"] == 3
        assert report["n_consistent"] == 2
        excluded = report["consistency"]["excluded_low_consistency"]
        assert [e["code"] for e in excluded] == ["600002"]
        assert excluded[0]["agreement"] == 0.3333
        assert excluded[0]["actions"] == ["buy", "buy", "buy"]
        assert report["consistency"]["mean_agreement"] == round((1.0 + 1.0 + 0.3333) / 3, 4)
        # replay_fn 收到全量 K 线通道与 repeats
        assert all(c["n"] == 3 for c in calls)
        assert all(c["full_kline"] is klines[c["code"]] for c in calls)

    def test_consistency_discloses_per_symbol_agreement(self):
        # spec「一致率报告」：报告 SHALL 含各标的方向一致率与全池均值。
        # per_symbol 覆盖全部标的（含被剔除的），与 mean_agreement /
        # excluded_low_consistency 并存披露。
        sample, outcomes, klines, _ = self._setup(up=0.014, down=-0.010)
        report = run_backtest(sample, klines, replay_fn=_fake_replay(outcomes))
        per_symbol = report["consistency"]["per_symbol"]
        assert [p["code"] for p in per_symbol] == ["600000", "600001", "600002"]
        by_code = {p["code"]: p for p in per_symbol}
        assert by_code["600000"] == {
            "code": "600000",
            "regime": "bull",
            "agreement": 1.0,
            "actions": ["buy", "buy", "buy"],
        }
        assert by_code["600001"]["regime"] == "bear"
        assert by_code["600001"]["actions"] == ["sell", "sell", "sell"]
        assert by_code["600001"]["agreement"] == 1.0
        # 一致率 1/3 被剔除的标的也在 per_symbol 中披露（不因剔除而隐去）
        assert by_code["600002"]["agreement"] == 0.3333
        assert by_code["600002"]["regime"] == "sideways"
        # 既有披露字段保持不变
        assert report["consistency"]["mean_agreement"] == round((1.0 + 1.0 + 0.3333) / 3, 4)
        assert [e["code"] for e in report["consistency"]["excluded_low_consistency"]] == ["600002"]

    def test_perf_table_covers_system_and_four_baselines(self):
        sample, outcomes, klines, _ = self._setup(up=0.014, down=-0.010)
        report = run_backtest(sample, klines, replay_fn=_fake_replay(outcomes))
        assert set(report["perf_table"]) == {"system", "buy_hold", "macd", "kdj", "rsi"}
        for metrics in report["perf_table"].values():
            assert set(metrics) == {"CR", "ARR", "Sharpe", "MDD"}
        assert report["best_baseline"] in {"buy_hold", "macd", "kdj", "rsi"}
        assert set(report["perf_by_regime"]) == {"bull", "bear"}
        assert report["sanity"] == "valid"
        assert not report["conclusion"].startswith("invalid")
        assert set(report["block_length_sensitivity"]) == {"10", "20", "40"}

    def test_neutral_action_excluded_from_system_aggregation(self):
        """Δ2 终审 B：hold/watch（neutral）整条排除出 system 聚合（§1.8②），报告披露排除数。"""
        sample, outcomes, klines, _ = self._setup(up=0.014, down=-0.010, actions=("buy", "hold"))
        report = run_backtest(sample, klines, replay_fn=_fake_replay(outcomes))
        assert report["n_consistent"] == 2  # 一致率不受影响（方向仍一致）
        assert report["methodology"]["excluded_non_executable"] == 1  # 600001 hold 被排除
        assert "排除" in report["methodology"]["system_population"]
        # 系统序列只剩 600000(buy) 的收益 → 与「同时含 hold 反向收益」的旧口径不同
        only_buy_sample, only_buy_outcomes, only_buy_klines, _ = self._setup(
            up=0.014, down=-0.010, actions=("buy", "sell")
        )
        only_buy_outcomes = {"600000": only_buy_outcomes["600000"]}
        only_buy_klines = {"600000": only_buy_klines["600000"]}
        report_buy = run_backtest(
            [only_buy_sample[0]], only_buy_klines, replay_fn=_fake_replay(only_buy_outcomes)
        )
        assert report["perf_table"]["system"] == report_buy["perf_table"]["system"]

    def test_paired_ci_truncation_recorded(self):
        sample, outcomes, klines, _ = self._setup(up=0.014, down=-0.010)
        report = run_backtest(sample, klines, replay_fn=_fake_replay(outcomes))
        ci = report["sharpe_excess_ci"]
        assert ci is not None and len(ci) == 2 and ci[0] <= ci[1]
        trunc = report["methodology"]["ci_truncation"]
        assert trunc["used"] == min(trunc["system_len"], trunc["baseline_len"])
        assert trunc["system_len"] > 0 and trunc["baseline_len"] > 0

    def test_high_sharpe_without_note_marked_invalid(self):
        # 同向交替 +2%/+1% → Sharpe ≈ 45 > 3，无 sanity note → invalid
        sample, outcomes, klines, _ = self._setup(up=0.02, down=0.01, actions=("buy", "buy"))
        report = run_backtest(sample, klines, replay_fn=_fake_replay(outcomes))
        assert report["perf_table"]["system"]["Sharpe"] > 3
        assert report["sanity"] == "invalid"
        assert report["conclusion"].startswith("invalid")

    def test_high_sharpe_with_note_valid(self):
        sample, outcomes, klines, _ = self._setup(up=0.02, down=0.01, actions=("buy", "buy"))
        report = run_backtest(
            sample,
            klines,
            sanity_note="样本期横跨牛熊；MDD 18%；月度换手",
            replay_fn=_fake_replay(outcomes),
        )
        assert report["sanity"] == "valid"
        assert not report["conclusion"].startswith("invalid")

    def test_no_consistent_samples_degrades_gracefully(self):
        sample, outcomes, klines, _ = self._setup(up=0.014, down=-0.010)
        low = {code: {**out, "agreement": 0.3333} for code, out in outcomes.items()}
        report = run_backtest(sample, klines, replay_fn=_fake_replay(low))
        assert report["n_consistent"] == 0
        assert report["perf_table"]["system"] == {"CR": 0.0, "ARR": 0.0, "Sharpe": 0.0, "MDD": 0.0}
        assert report["sharpe_excess_ci"] is None
        assert report["block_length_sensitivity"] is None
        assert report["conclusion"] == "样本不足，无法判定"
        assert report["perf_by_regime"] == {}

    def test_missing_kline_skipped_without_crash(self):
        sample, outcomes, klines, _ = self._setup(up=0.014, down=-0.010)
        klines.pop("600001")  # 一致标的缺 K 线 → 跳过不崩
        report = run_backtest(sample, klines, replay_fn=_fake_replay(outcomes))
        assert report["n_consistent"] == 2
        assert set(report["perf_by_regime"]) == {"bull"}

    def test_report_serializable(self):
        import json

        sample, outcomes, klines, _ = self._setup(up=0.014, down=-0.010)
        report = run_backtest(sample, klines, replay_fn=_fake_replay(outcomes))
        assert json.loads(json.dumps(report, ensure_ascii=False))["n_sample"] == 3


class TestFetchCaliber:
    """delta add-backtest-leakage-controls：回测取数显式后复权（hfq）。

    run_backtest 的结算/基线都基于全量 K 线，须 as-of 保真；管线输入（nodes/fetch）
    保持 qfq。此处钉死 main() 对个股 K 线显式传 hfq（指数无复权概念，不传）。
    """

    def test_main_fetches_klines_with_hfq(self, monkeypatch, tmp_path):
        import sys

        import evals.backtest.run_backtest as rb

        calls: list[tuple[str, str]] = []

        class FakeClient:
            def fetch_index_kline(self, code, days=None):
                return _kline([100.0] * 5)

            def fetch_kline(self, code, days=None, *, adjust="qfq"):
                calls.append((code, adjust))
                return _kline([100.0] * 5)

        monkeypatch.setattr("finance_agent.data.akshare_client.AKShareClient", FakeClient)
        monkeypatch.setattr(rb, "stratified_sample", lambda ik, codes, per_regime: [])
        monkeypatch.setattr(rb, "run_backtest", lambda *a, **k: {"ok": True})
        monkeypatch.chdir(tmp_path)  # 报告写盘落在 tmp（不污染仓库）
        monkeypatch.setattr(sys, "argv", ["run_backtest.py", "--codes", "600000", "600001"])

        rb.main()

        assert calls == [("600000", "hfq"), ("600001", "hfq")]


class TestBaselineHorizonAlignment:
    """refine: 基线按系统相同持有窗口切片（原 1500 日全窗口 vs 单笔持有期不可比）。"""

    def _kline(self, dates, closes):
        import pandas as pd

        return pd.DataFrame({"日期": dates, "收盘": closes})

    def test_buy_hold_window_returns_match_kline_daily_returns(self):
        from evals.backtest.run_backtest import _baseline_window_returns

        kline = self._kline(
            ["2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"],
            [100.0, 105.0, 100.0, 110.0, 115.0],
        )
        # 持有窗口 (2023-01-02, 2023-01-05] → 3 个交易日
        rets = _baseline_window_returns(kline, "buy_hold", "2023-01-02", "2023-01-05")
        # buy_hold 全程持仓：收益 = 逐日 pct_change
        assert len(rets) == 3
        assert abs(rets[0] - (100 / 105 - 1)) < 1e-9  # 01-03: 100/105-1
        assert abs(rets[1] - (110 / 100 - 1)) < 1e-9  # 01-04
        assert abs(rets[2] - (115 / 110 - 1)) < 1e-9  # 01-05

    def test_baseline_window_shorter_than_full_kline(self):
        from evals.backtest.run_backtest import _baseline_window_returns

        kline = self._kline(
            [f"2023-01-0{i}" for i in range(1, 10)],
            [100.0 + i for i in range(9)],
        )
        full = _baseline_window_returns(kline, "buy_hold", "2023-01-01", "2023-01-09")
        window = _baseline_window_returns(kline, "buy_hold", "2023-01-04", "2023-01-06")
        assert len(full) == 8
        assert len(window) == 2  # 只含 01-05, 01-06
