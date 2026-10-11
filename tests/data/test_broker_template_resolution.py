"""券商模板解析回归（issue #238 上游归因修复，2026-10-10）。

真实事故链：券商（601066）新浪资产负债表模板的权益列名为
「归属于母公司的股东权益合计」（多一个「的」），不在候选清单 → 列名归一化
未全部命中 → 遗留列位回退误触发 → 制造业利润表标定的列位 50 把资产负债表
的「资产总计」改名成「归母净利润」——资产总计列被吞，图表层资产负债序列
六天六跑全 None（600519/601818 同期正常）。

修复契约（chart-data-integrity R1 既有语义的合规实现）：
1. 候选清单补券商变体，列名归一化优先命中；
2. 列位回退按报表类型标定范围生效（利润表 50 / 资产负债表 137），
   statement 未声明时不回退——回退只服务「列名损坏」场景，不得跨表误伤。
fixture 为真实拉取的券商年度资产负债表（sh601066，60 行 × 111 列）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from finance_agent.data.akshare_client import AKShareClient

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> pd.DataFrame:
    return pd.read_csv(_FIXTURES / name)


@pytest.fixture
def client() -> AKShareClient:
    return AKShareClient()


class TestBrokerTemplateResolution:
    """券商资产负债表模板（601066 真实 fixture）：资产总计不得被列位回退吞掉。"""

    def test_broker_balance_total_assets_preserved(self, client):
        raw = _load("sina_balance_601066_broker.csv")
        assert "资产总计" in raw.columns  # 前置：raw 模板确实有资产总计
        df = client._rename_parent_cols(raw, statement="资产负债表")
        assert "资产总计" in df.columns
        assert df["资产总计"].equals(raw["资产总计"])

    def test_broker_balance_equity_resolved_by_name(self, client):
        raw = _load("sina_balance_601066_broker.csv")
        df = client._rename_parent_cols(raw, statement="资产负债表")
        assert "归母所有者权益" in df.columns
        assert df["归母所有者权益"].equals(raw["归属于母公司的股东权益合计"])

    def test_broker_balance_no_bogus_netprofit_column(self, client):
        """资产负债表不得出现「归母净利润」列（列位 50 回退跨表误伤的产物）。"""
        raw = _load("sina_balance_601066_broker.csv")
        assert "归母净利润" not in raw.columns
        df = client._rename_parent_cols(raw, statement="资产负债表")
        assert "归母净利润" not in df.columns


class TestPositionalFallbackScoping:
    """列位回退按报表类型标定：回退只允许命中本表标定列位，不得跨表。"""

    def _corrupted(self) -> pd.DataFrame:
        cols = [f"col_{i}" for i in range(140)]
        return pd.DataFrame({c: [float(i)] for i, c in enumerate(cols)})

    def test_income_statement_fallback_uses_position_50_only(self, client):
        df = client._rename_parent_cols(self._corrupted(), statement="利润表")
        assert df["归母净利润"].tolist() == [50.0]
        assert "归母所有者权益" not in df.columns

    def test_balance_statement_fallback_uses_position_137_only(self, client):
        df = client._rename_parent_cols(self._corrupted(), statement="资产负债表")
        assert df["归母所有者权益"].tolist() == [137.0]
        assert "归母净利润" not in df.columns

    def test_cashflow_statement_never_positional_fallback(self, client):
        df = client._rename_parent_cols(self._corrupted(), statement="现金流量表")
        assert "归母净利润" not in df.columns
        assert "归母所有者权益" not in df.columns

    def test_unknown_statement_no_fallback(self, client):
        """statement 未声明 → 不回退（回退只服务列名损坏场景，需知道表型才可标定）。"""
        df = client._rename_parent_cols(self._corrupted())
        assert "归母净利润" not in df.columns
        assert "归母所有者权益" not in df.columns
