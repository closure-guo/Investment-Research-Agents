"""Δ3 Task 2: cohort universe 登记文件 + 分层抽样(全程 stub client,不联网)。

Delta add-forward-paper-trading-cohort;Requirement「Cohort 标的池登记与版本化」。
"""

import json

import pytest

from finance_agent.outcome.cohort.universe import (
    Constituent,
    Universe,
    build_universe,
    load_universe,
    write_universe,
)

# ── stub client:只实现 build_universe 消费的三个方法,零网络 ──

_INDUSTRIES = (
    "白酒",
    "银行",
    "医药",
    "电子",
    "汽车",
    "地产",
    "券商",
    "煤炭",
    "钢铁",
    "食品饮料",
    "计算机",
    "电力",
)
# 12 只标的,总市值(亿元)横跨三档(分位切点 ~266.67 / ~666.67)
_CAPS = (1000.0, 900.0, 800.0, 700.0, 600.0, 500.0, 400.0, 300.0, 200.0, 100.0, 50.0, 10.0)


class StubClient:
    """记录调用次数的 stub;每 ticker 只应被询价/询行业一次(缓存断言)。"""

    def __init__(self, tickers: list[str] | None = None, n_stocks: int = 12):
        self._tickers = tickers if tickers is not None else [f"60{i:04d}" for i in range(n_stocks)]
        self.industry_calls: list[str] = []
        self.quote_calls: list[str] = []

    def fetch_index_constituents(self, index_code: str = "000300") -> list[dict[str, str]]:
        return [{"ticker": t, "name": f"标的{idx}"} for idx, t in enumerate(self._tickers)]

    def fetch_industry(self, stock_code: str) -> dict:
        self.industry_calls.append(stock_code)
        idx = self._tickers.index(stock_code)
        return {"industry": _INDUSTRIES[idx % len(_INDUSTRIES)]}

    def fetch_stock_quote(self, stock_code: str) -> dict:
        self.quote_calls.append(stock_code)
        idx = self._tickers.index(stock_code)
        return {"market_cap": _CAPS[idx % len(_CAPS)]}


def _valid_payload() -> dict:
    return {
        "version": "v1",
        "effective_date": "2026-09-23",
        "seed": 42,
        "strata": {"by": "industry+market_cap", "n": 2},
        "constituents": [
            {
                "ticker": "600519",
                "name": "贵州茅台",
                "industry": "白酒",
                "market_cap_bucket": "large",
            },
            {
                "ticker": "000001",
                "name": "平安银行",
                "industry": "银行",
                "market_cap_bucket": "mid",
            },
        ],
    }


class TestLoadUniverse:
    def test_parses_valid_file(self, tmp_path):
        path = tmp_path / "universe-v1.json"
        path.write_text(json.dumps(_valid_payload(), ensure_ascii=False), encoding="utf-8")

        u = load_universe(path)

        assert isinstance(u, Universe)
        assert u.version == "v1" and u.effective_date == "2026-09-23" and u.seed == 42
        assert u.strata["by"] == "industry+market_cap" and u.strata["n"] == 2
        assert len(u.constituents) == 2
        assert u.constituents[0] == Constituent(
            ticker="600519", name="贵州茅台", industry="白酒", market_cap_bucket="large"
        )

    @pytest.mark.parametrize(
        "missing", ["version", "effective_date", "seed", "strata", "constituents"]
    )
    def test_missing_required_key_raises_with_field_name(self, tmp_path, missing):
        payload = _valid_payload()
        payload.pop(missing)
        path = tmp_path / "universe-v1.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(ValueError, match=missing):
            load_universe(path)

    def test_missing_constituent_field_raises_with_field_name(self, tmp_path):
        payload = _valid_payload()
        payload["constituents"][0].pop("market_cap_bucket")
        path = tmp_path / "universe-v1.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(ValueError, match="market_cap_bucket"):
            load_universe(path)


class TestBuildUniverse:
    def test_returns_registry_shape(self):
        data = build_universe("v1", n=10, seed=42, client=StubClient())

        assert data["version"] == "v1"
        assert data["seed"] == 42
        assert data["strata"] == {"by": "industry+market_cap", "n": 10}
        assert data["effective_date"]  # 非空 ISO 日期
        assert len(data["constituents"]) == 10

    def test_same_seed_reproduces_same_constituents(self):
        first = build_universe("v1", n=10, seed=42, client=StubClient())
        second = build_universe("v1", n=10, seed=42, client=StubClient())

        assert [c["ticker"] for c in first["constituents"]] == [
            c["ticker"] for c in second["constituents"]
        ]
        assert first["constituents"] == second["constituents"]

    def test_constituents_are_subset_without_duplicates(self):
        stub = StubClient()
        data = build_universe("v1", n=10, seed=42, client=stub)

        tickers = [c["ticker"] for c in data["constituents"]]
        assert len(set(tickers)) == 10  # 无放回
        assert set(tickers) <= set(stub._tickers)

    def test_strata_dimensions_populated(self):
        data = build_universe("v1", n=10, seed=42, client=StubClient())

        for c in data["constituents"]:
            assert c["industry"]  # 行业非空
            assert c["market_cap_bucket"] in {"large", "mid", "small"}
            assert c["ticker"] and c["name"]

    def test_stratified_coverage_spans_multiple_strata(self):
        data = build_universe("v1", n=10, seed=42, client=StubClient())

        keys = {(c["industry"], c["market_cap_bucket"]) for c in data["constituents"]}
        # 12 只 × 12 个不同行业 + 市值三档 → 应覆盖多个层(而非集中在一层)
        assert len(keys) >= 5

    def test_enrichment_is_cached_per_ticker(self):
        stub = StubClient()
        build_universe("v1", n=10, seed=42, client=stub)

        assert len(stub.industry_calls) == 12  # 每 ticker 只取一次
        assert len(stub.quote_calls) == 12
        assert len(set(stub.industry_calls)) == 12

    def test_n_greater_than_pool_raises(self):
        with pytest.raises(ValueError, match="n"):
            build_universe("v1", n=10, seed=42, client=StubClient(n_stocks=3))


class TestWriteUniverse:
    def test_writes_json_roundtrip(self, tmp_path):
        path = tmp_path / "universe-v1.json"
        data = build_universe("v1", n=10, seed=42, client=StubClient())

        write_universe(data, path)

        assert load_universe(path).version == "v1"
        assert len(load_universe(path).constituents) == 10

    def test_refuses_overwrite_within_version_lifecycle(self, tmp_path):
        path = tmp_path / "universe-v1.json"
        data = build_universe("v1", n=10, seed=42, client=StubClient())
        write_universe(data, path)

        with pytest.raises(FileExistsError):
            write_universe(data, path)
        # 原文件未被改写
        assert load_universe(path).constituents == tuple(
            Constituent(**c) for c in data["constituents"]
        )

    def test_force_allows_overwrite(self, tmp_path):
        path = tmp_path / "universe-v1.json"
        write_universe(build_universe("v1", n=10, seed=42, client=StubClient()), path)
        other = build_universe("v1", n=10, seed=7, client=StubClient())

        write_universe(other, path, force=True)

        assert load_universe(path).seed == 7
