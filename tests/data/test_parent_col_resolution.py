"""TDD tests: 归母口径列按列名归一化解析（add-chart-data-integrity R1）。

背景（2026-10-05 光大银行 601818 报告事故）：
新浪报表模板随行业不同列序不同——银行利润表「归属于母公司的净利润」在
列 44，银行资产负债表「归属于母公司股东的权益」在列 141；而
`_rename_parent_cols` 按列位 50/137 硬编码改名，把银行模板的
「盈余公积转入」改名成「归母净利润」（值空→回退合并净利润）、
「未分配利润」改名成「归母所有者权益」（光大 2021 = 1559.68 亿，
真实归母权益约 4700 亿），ROE 因此画出 27.98%。

修复契约：列名候选归一化匹配优先、列位映射降级为列名损坏时的回退。
fixtures 为真实拉取的年度报表（601818 银行模板 / 600519 制造业模板）。
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


class TestBankTemplateResolution:
    """银行模板：真实归母列在列位 44/141，列位 50/137 是错误列。"""

    def test_bank_income_parent_netprofit_resolved_by_name(self, client):
        raw = _load("sina_income_601818_bank.csv")
        df = client._rename_parent_cols(raw)
        assert "归母净利润" in df.columns
        # 真实归母净利润列名为「归属于母公司的净利润」（列 44）
        assert df["归母净利润"].equals(raw["归属于母公司的净利润"])

    def test_bank_income_not_taking_reserve_transfer(self, client):
        """列位 50 在银行模板是「盈余公积转入」，不得被当作归母净利润。"""
        raw = _load("sina_income_601818_bank.csv")
        df = client._rename_parent_cols(raw)
        assert not df["归母净利润"].equals(raw["盈余公积转入"])

    def test_bank_balance_parent_equity_resolved_by_name(self, client):
        raw = _load("sina_balance_601818_bank.csv")
        df = client._rename_parent_cols(raw)
        assert "归母所有者权益" in df.columns
        # 真实归母权益列名为「归属于母公司股东的权益」（列 141）
        assert df["归母所有者权益"].equals(raw["归属于母公司股东的权益"])

    def test_bank_balance_not_taking_undistributed_profit(self, client):
        """列位 137 在银行模板是「未分配利润」（2021=1559.68亿），不得充当归母权益。"""
        raw = _load("sina_balance_601818_bank.csv")
        df = client._rename_parent_cols(raw)
        assert not df["归母所有者权益"].equals(raw["未分配利润"])


class TestMfgTemplateRegression:
    """制造业模板（列位 50/137 恰为真实列）：行为必须与既有实现一致。"""

    def test_mfg_income_parent_netprofit(self, client):
        raw = _load("sina_income_600519_mfg.csv")
        df = client._rename_parent_cols(raw)
        assert df["归母净利润"].equals(raw["归属于母公司所有者的净利润"])

    def test_mfg_balance_parent_equity(self, client):
        raw = _load("sina_balance_600519_mfg.csv")
        df = client._rename_parent_cols(raw)
        assert df["归母所有者权益"].equals(raw["归属于母公司股东权益合计"])


class TestNormalizationAndFallback:
    def test_whitespace_variant_of_real_column_matched(self, client):
        """真实列名带空白变体（线上「编码差异」的常见形态）时按归一化命中。"""
        raw = pd.DataFrame(
            {
                "报告日": ["20241231", "20231231"],
                "营业收入": [1000.0, 900.0],
                "归属于母公司股东的净利润\u3000": [168.0, 151.0],
            }
        )
        df = client._rename_parent_cols(raw)
        assert "归母净利润" in df.columns
        assert df["归母净利润"].tolist() == [168.0, 151.0]

    def test_whitespace_variant_of_standard_name_canonicalized(self, client):
        """标准列名自身带空白变体时应归一化为规范名，保证下游 row.get 命中。"""
        raw = pd.DataFrame(
            {
                "报告日": ["20241231"],
                " 归母净利润 ": [168.0],
            }
        )
        df = client._rename_parent_cols(raw)
        assert "归母净利润" in df.columns
        assert df["归母净利润"].tolist() == [168.0]

    def test_positional_fallback_when_names_corrupted(self, client):
        """列名全部损坏（候选无一命中）→ 回退本表标定列位，兼容原修复场景。

        列位按报表类型标定（issue #238：利润表 50 / 资产负债表 137，
        跨表套用曾吞掉券商资产负债表「资产总计」），statement 未声明不回退。
        """
        cols = [f"col_{i}" for i in range(140)]
        data = {c: [float(i)] for i, c in enumerate(cols)}
        income = client._rename_parent_cols(pd.DataFrame(data), statement="利润表")
        assert income["归母净利润"].tolist() == [50.0]
        assert "归母所有者权益" not in income.columns
        balance = client._rename_parent_cols(pd.DataFrame(data), statement="资产负债表")
        assert balance["归母所有者权益"].tolist() == [137.0]
        assert "归母净利润" not in balance.columns

    def test_standard_cols_present_is_noop(self, client):
        """已有规范列名时幂等不改写。"""
        raw = pd.DataFrame(
            {
                "报告日": ["20241231"],
                "归母净利润": [168.0],
                "归母所有者权益": [600.0],
            }
        )
        df = client._rename_parent_cols(raw)
        assert df["归母净利润"].tolist() == [168.0]
        assert df["归母所有者权益"].tolist() == [600.0]
