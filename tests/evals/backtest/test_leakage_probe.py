"""知识泄漏探针测试（零网络零 LLM）：三层题命中率 / 未知占比 / 可复现性。

假客户端构造后复权 K 线与新闻；假 llm 按题型返回构造 JSON。
题型判别靠模板中的稳定标记（与 `leakage_probe` 模板同源）：
方向题含「上涨还是下跌」、幅度题含「涨跌幅落在哪个区间」、事件题含「重大事件」。
"""

import json
import re
from collections.abc import Callable

import pandas as pd
import pytest
from evals.backtest.leakage_probe import run_leakage_probe

from finance_agent.data.akshare_client import SETTLEMENT_ADJUST

DECISION_DATE = "2023-01-03"
WINDOW_DAYS = 20
N_DATES = 60
NEUTRAL_NEWS = [{"title": "公司召开股东大会", "datetime": "2022-12-20 09:00:00", "source": "东财"}]

_KIND_MARKERS = (
    ("direction", "上涨还是下跌"),
    ("magnitude", "涨跌幅落在哪个区间"),
    ("event", "重大事件"),
)


def _kind_of(prompt: str) -> str:
    for kind, marker in _KIND_MARKERS:
        if marker in prompt:
            return kind
    raise AssertionError(f"未知题型 prompt: {prompt[:80]}")


def _dates(n: int = N_DATES) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start=DECISION_DATE, periods=n)]


def _closes(dates: list[str], total_ret: float, window_days: int = WINDOW_DAYS) -> list[float]:
    """决策日后 window_days 个交易日累计 total_ret，其后保持不变（窗口真值确定）。"""
    start_idx, end_idx = 1, window_days  # 窗口 = dates[1:window_days+1]
    closes = [100.0] * len(dates)
    for i in range(start_idx, end_idx + 1):
        closes[i] = 100.0 * (1.0 + total_ret * (i - start_idx) / (window_days - 1))
    for i in range(end_idx + 1, len(dates)):
        closes[i] = closes[end_idx]
    return closes


class _FakeClient:
    """假 AKShare 客户端：记录 fetch_kline 的 adjust；rel 为相对基准超额。"""

    def __init__(
        self,
        rel_by_code: dict[str, float],
        *,
        index_ret: float = 0.0,
        news: list[dict] | None = None,
        index_empty: bool = False,
        n_dates: int = N_DATES,
    ) -> None:
        self.dates = _dates(n_dates)
        self.rel_by_code = rel_by_code
        self.index_ret = index_ret
        self.news = NEUTRAL_NEWS if news is None else news
        self.index_empty = index_empty
        self.adjusts: list[tuple[str, str]] = []

    def fetch_kline(self, stock_code: str, days: int = 250, *, adjust: str = "qfq") -> pd.DataFrame:
        self.adjusts.append((stock_code, adjust))
        rel = self.rel_by_code.get(stock_code, 0.0)
        return pd.DataFrame({"日期": self.dates, "收盘": _closes(self.dates, self.index_ret + rel)})

    def fetch_index_kline(self, index_code: str, days: int = 250) -> pd.DataFrame:
        if self.index_empty:
            return pd.DataFrame()
        return pd.DataFrame({"日期": self.dates, "收盘": _closes(self.dates, self.index_ret)})

    def fetch_news(self, stock_code: str, limit: int = 20) -> list[dict]:
        return list(self.news)


def _all_answers(
    codes: list[str], direction: str, bucket: str, event: str
) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for code in codes:
        out[(code, "direction")] = direction
        out[(code, "magnitude")] = bucket
        out[(code, "event")] = event
    return out


def _fake_llm(answers: dict[tuple[str, str], str]) -> Callable[[str], str]:
    """answers 缺键 → 拒答（非 JSON），用于构造未知样本。"""

    def _llm(prompt: str) -> str:
        m = re.search(r"\d{6}", prompt)
        kind = _kind_of(prompt)
        value = answers.get((m.group(0) if m else "", kind))
        if value is None:
            return "我无法确定这个问题的答案。"
        if kind == "direction":
            return json.dumps({"direction": value}, ensure_ascii=False)
        if kind == "magnitude":
            return json.dumps({"bucket": value}, ensure_ascii=False)
        return json.dumps({"event": value}, ensure_ascii=False)

    return _llm


def _tickers(result: dict) -> set[str]:
    return {d["ticker"] for d in result["details"]}


class TestAllCorrect:
    def test_hit_rate_one_and_downgraded(self):
        codes = ["600519", "000858", "600036"]
        client = _FakeClient(dict.fromkeys(codes, 0.01))
        llm = _fake_llm(_all_answers(codes, "up", "持平", "无"))
        res = run_leakage_probe(codes, DECISION_DATE, n_tickers=3, client=client, llm=llm)

        assert res["probe_n"] == 3
        assert res["questions_per_ticker"] == 3
        assert res["direction_hit_rate"] == pytest.approx(1.0)
        assert res["magnitude_hit_rate"] == pytest.approx(1.0)
        assert res["event_hit_rate"] == pytest.approx(1.0)
        assert res["unknown_ratio"] == pytest.approx(0.0)
        assert res["threshold"] == pytest.approx(0.60)
        assert res["downgraded"] is True
        assert len(res["details"]) == 9

    def test_kline_fetched_with_settlement_adjust(self):
        codes = ["600519"]
        client = _FakeClient({codes[0]: 0.01})
        run_leakage_probe(
            codes,
            DECISION_DATE,
            n_tickers=1,
            client=client,
            llm=_fake_llm(_all_answers(codes, "up", "持平", "无")),
        )
        assert client.adjusts
        assert all(adjust == SETTLEMENT_ADJUST for _, adjust in client.adjusts)


class TestAllWrong:
    def test_hit_rate_zero_not_downgraded(self):
        codes = ["600519", "000858", "600036"]
        client = _FakeClient(dict.fromkeys(codes, 0.01))
        llm = _fake_llm(_all_answers(codes, "down", "大跌", "有"))
        res = run_leakage_probe(codes, DECISION_DATE, n_tickers=3, client=client, llm=llm)

        assert res["direction_hit_rate"] == pytest.approx(0.0)
        assert res["magnitude_hit_rate"] == pytest.approx(0.0)
        assert res["event_hit_rate"] == pytest.approx(0.0)
        assert res["unknown_ratio"] == pytest.approx(0.0)
        assert res["downgraded"] is False


class TestUnknown:
    def test_half_refused_excluded_from_denominator(self):
        codes = ["600519", "000858"]
        client = _FakeClient(dict.fromkeys(codes, 0.01))
        # 只给一只标的答案 → 另一只三题全部拒答
        llm = _fake_llm(_all_answers(["000858"], "up", "持平", "无"))
        res = run_leakage_probe(codes, DECISION_DATE, n_tickers=2, client=client, llm=llm)

        assert res["unknown_ratio"] == pytest.approx(0.5)
        # 命中率分母只含可解析样本（1 个），不因拒答被拉低
        assert res["direction_hit_rate"] == pytest.approx(1.0)
        assert res["magnitude_hit_rate"] == pytest.approx(1.0)
        assert res["event_hit_rate"] == pytest.approx(1.0)

    def test_all_unparseable_gives_none_not_zero(self):
        codes = ["600519"]
        client = _FakeClient({codes[0]: 0.01})
        res = run_leakage_probe(
            codes,
            DECISION_DATE,
            n_tickers=1,
            client=client,
            llm=lambda _prompt: "抱歉，我不知道。",
        )
        assert res["direction_hit_rate"] is None
        assert res["magnitude_hit_rate"] is None
        assert res["event_hit_rate"] is None
        assert res["unknown_ratio"] == pytest.approx(1.0)
        assert res["downgraded"] is False

    def test_valid_json_but_refusal_enum_counts_unknown(self):
        code = "600519"
        client = _FakeClient({code: 0.01})
        # 合法 JSON 但枚举值非法（拒答语义）→ 计未知，不折算答错
        llm = _fake_llm(_all_answers([code], "不确定", "持平", "无"))
        res = run_leakage_probe([code], DECISION_DATE, n_tickers=1, client=client, llm=llm)

        assert res["unknown_ratio"] == pytest.approx(1 / 3)
        assert res["direction_hit_rate"] is None
        assert res["magnitude_hit_rate"] == pytest.approx(1.0)


class TestEventTruthUnavailable:
    def test_empty_news_counts_unknown_not_wrong(self):
        code = "600519"
        client = _FakeClient({code: 0.01}, news=[])
        llm = _fake_llm(_all_answers([code], "up", "持平", "无"))
        res = run_leakage_probe([code], DECISION_DATE, n_tickers=1, client=client, llm=llm)

        assert res["unknown_ratio"] == pytest.approx(1 / 3)
        assert res["event_hit_rate"] is None  # 真值不可得 → 不按答错
        assert res["direction_hit_rate"] == pytest.approx(1.0)
        events = [d for d in res["details"] if d["kind"] == "event"]
        assert events and all(d["unknown"] for d in events)
        assert all(d["truth"] is None for d in events)


class TestTruthDegradation:
    def test_benchmark_unavailable_degrades_price_questions_only(self):
        code = "600519"
        client = _FakeClient({code: 0.01}, index_empty=True)
        llm = _fake_llm(_all_answers([code], "up", "持平", "无"))
        res = run_leakage_probe([code], DECISION_DATE, n_tickers=1, client=client, llm=llm)

        assert res["direction_hit_rate"] is None
        assert res["magnitude_hit_rate"] is None
        assert res["event_hit_rate"] == pytest.approx(1.0)
        assert res["unknown_ratio"] == pytest.approx(2 / 3)

    def test_incomplete_window_all_unknown(self):
        code = "600519"
        client = _FakeClient({code: 0.01}, n_dates=10)  # 决策日后不足 window_days
        llm = _fake_llm(_all_answers([code], "up", "持平", "无"))
        res = run_leakage_probe([code], DECISION_DATE, n_tickers=1, client=client, llm=llm)

        assert res["direction_hit_rate"] is None
        assert res["unknown_ratio"] == pytest.approx(1.0)


class TestThresholdBoundary:
    def test_rate_equal_threshold_not_downgraded(self):
        codes = [f"60000{i}" for i in range(5)]
        client = _FakeClient(dict.fromkeys(codes, 0.01))
        answers = _all_answers(codes, "up", "持平", "无")
        for code in codes[3:]:  # 5 题答对 3 题 → 0.60
            answers[(code, "direction")] = "down"
        res = run_leakage_probe(
            codes, DECISION_DATE, n_tickers=5, client=client, llm=_fake_llm(answers)
        )

        assert res["direction_hit_rate"] == pytest.approx(0.60)
        assert res["downgraded"] is False


class TestReproducibility:
    def test_same_seed_same_sample(self):
        codes = [f"60{i:04d}" for i in range(20)]
        client = _FakeClient(dict.fromkeys(codes, 0.01))
        llm = _fake_llm(_all_answers(codes, "up", "持平", "无"))
        first = run_leakage_probe(
            codes, DECISION_DATE, n_tickers=5, seed=42, client=client, llm=llm
        )
        second = run_leakage_probe(
            codes, DECISION_DATE, n_tickers=5, seed=42, client=client, llm=llm
        )

        assert first["probe_n"] == second["probe_n"] == 5
        assert _tickers(first) == _tickers(second)
        assert _tickers(first) <= set(codes)


class TestEmptyInput:
    def test_no_codes(self):
        res = run_leakage_probe(
            [], DECISION_DATE, n_tickers=5, client=_FakeClient({}), llm=_fake_llm({})
        )
        assert res["probe_n"] == 0
        assert res["unknown_ratio"] == pytest.approx(0.0)
        assert res["direction_hit_rate"] is None
        assert res["downgraded"] is False
        assert res["details"] == []
