"""_fetch_peers 自动选取 + 勾稽告警注入测试（complete-peer-pipeline，issue #21 残余）。

- 自动选取：无显式 peer_codes 时行业成分市值 Top5 排除自身；显式恒优先；
  成分不可得保持「不抓取」降级。
- 勾稽告警：validation_warnings 非空注入 FA/MA context，空不注入，原文不改写。
"""

from __future__ import annotations

import pandas as pd

from finance_agent.nodes.analysts import _build_fundamental_context, _build_macro_context
from finance_agent.nodes.fetch import _fetch_peers


class _Ak:
    def __init__(self, cons=None):
        self._cons = cons
        self.peer_calls: list[list[str]] = []
        self.cons_calls: list[str] = []

    def fetch_peer_data(self, codes):
        self.peer_calls.append(list(codes))
        return pd.DataFrame(
            {
                "name": ["x"] * len(codes),
                "code": codes,
                "PE": [10.0] * len(codes),
                "PB": [1.0] * len(codes),
            }
        )

    def fetch_industry_constituents(self, industry):
        self.cons_calls.append(industry)
        return self._cons


class TestAutoSelectPeers:
    def test_explicit_peer_codes_take_priority(self):
        cons = [
            {"code": "000858", "name": "五粮液", "total_mv": 5e11},
            {"code": "600519", "name": "贵州茅台", "total_mv": 2e12},
        ]
        ak = _Ak(cons=cons)
        _fetch_peers(ak, "600519", {"peer_codes": ["000858"]}, {"industry": "白酒"})
        assert ak.peer_calls == [["000858"]], "显式列表恒优先，不得追加自动选取"
        assert ak.cons_calls == []

    def test_auto_select_top5_excludes_self(self):
        cons = [{"code": f"{600000 + i}", "name": f"s{i}", "total_mv": float(i)} for i in range(8)]
        cons.append({"code": "600519", "name": "贵州茅台", "total_mv": 1e13})  # 自身，市值最大
        ak = _Ak(cons=cons)
        result = _fetch_peers(ak, "600519", {"peer_codes": None}, {"industry": "白酒"})
        assert result is not None
        called = ak.peer_calls[0]
        assert "600519" not in called, "自动选取必须排除主标的自身"
        assert len(called) == 5, "Top5"

    def test_cons_failure_keeps_no_fetch_semantics(self):
        ak = _Ak(cons=None)
        result = _fetch_peers(ak, "600519", {"peer_codes": None}, {"industry": "白酒"})
        assert result is None
        assert ak.peer_calls == [], "成分不可得 → 不触发同业抓取调用"

    def test_missing_industry_keeps_no_fetch_semantics(self):
        ak = _Ak(cons=[{"code": "000858", "name": "五粮液", "total_mv": 5e11}])
        result = _fetch_peers(ak, "600519", {"peer_codes": None}, {})
        assert result is None
        assert ak.cons_calls == [] and ak.peer_calls == []


class TestValidationWarningsInjection:
    def _state(self, warnings):
        return {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "validation_warnings": warnings,
        }

    def test_fa_and_macro_context_contain_warnings_verbatim(self):
        state = self._state(["利润表存在较大营业外收支"])
        fa = _build_fundamental_context(state)
        ma = _build_macro_context(state)
        for ctx in (fa, ma):
            assert "勾稽校验告警（机生，供交叉核对）" in ctx
            assert "利润表存在较大营业外收支" in ctx

    def test_empty_warnings_not_injected(self):
        for ctx in (
            _build_fundamental_context(self._state([])),
            _build_macro_context(self._state([])),
        ):
            assert "勾稽校验告警" not in ctx

    def test_skip_warning_injected_verbatim(self):
        state = self._state(["勾稽校验跳过：三大报表数据缺失"])
        for ctx in (
            _build_fundamental_context(state),
            _build_macro_context(state),
        ):
            assert "勾稽校验跳过：三大报表数据缺失" in ctx
