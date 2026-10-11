"""行业成分股抓取测试（complete-peer-pipeline，issue #21 残余 story 2）。

fetch_industry_constituents：东财行业板块成分主源 + 共享缓存（24h）+
optional 降级（失败/空返回 None 不写缓存）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from finance_agent.data import akshare_client as ac
from finance_agent.data.akshare_client import AKShareClient


def _cons_df() -> pd.DataFrame:
    """东财行业板块成分表形态（乱序总市值，含自身 600519）。"""
    return pd.DataFrame(
        {
            "代码": ["000858", "600519", "000568", "603369", "000596"],
            "名称": ["五粮液", "贵州茅台", "泸州老窖", "今世缘", "古井贡酒"],
            "总市值": [5.0e11, 2.0e12, 3.0e11, 1.0e11, 0.5e11],
        }
    )


class _FakeCache:
    def __init__(self):
        self.store: dict = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, data, ttl_seconds=None, expire_at=None, **kw):
        self.store[key] = data


@pytest.fixture
def fake_cache(monkeypatch):
    cache = _FakeCache()
    monkeypatch.setattr(ac, "get_shared_cache", lambda: cache)
    return cache


def test_parses_and_sorts_by_market_cap_desc(monkeypatch, fake_cache):
    calls = {"n": 0}

    def fake_cons(symbol):
        calls["n"] += 1
        assert symbol == "白酒"
        return _cons_df()

    monkeypatch.setattr(ac.ak, "stock_board_industry_cons_em", fake_cons)
    rows = AKShareClient().fetch_industry_constituents("白酒")
    assert rows is not None
    codes = [r["code"] for r in rows]
    assert codes[0] == "600519"  # 总市值最大
    assert codes == sorted(
        codes, key=lambda c: -next(r["total_mv"] for r in rows if r["code"] == c)
    )
    assert all(set(r) >= {"code", "name", "total_mv"} for r in rows)
    assert calls["n"] == 1


def test_second_call_served_from_cache(monkeypatch, fake_cache):
    calls = {"n": 0}

    def fake_cons(symbol):
        calls["n"] += 1
        return _cons_df()

    monkeypatch.setattr(ac.ak, "stock_board_industry_cons_em", fake_cons)
    client = AKShareClient()
    first = client.fetch_industry_constituents("白酒")
    second = client.fetch_industry_constituents("白酒")
    assert calls["n"] == 1, "24h 缓存内同行业不得重复发起网络抓取"
    assert first == second


def test_failure_returns_none_and_not_cached(monkeypatch, fake_cache):
    def broken(symbol):
        raise OSError(101, "Network is unreachable")

    monkeypatch.setattr(ac.ak, "stock_board_industry_cons_em", broken)
    assert AKShareClient().fetch_industry_constituents("白酒") is None
    assert fake_cache.store == {}, "失败结果不得写缓存"


def test_empty_df_returns_none(monkeypatch, fake_cache):
    monkeypatch.setattr(ac.ak, "stock_board_industry_cons_em", lambda symbol: pd.DataFrame())
    assert AKShareClient().fetch_industry_constituents("白酒") is None
