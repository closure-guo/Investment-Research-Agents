"""akshare_client 新增数据源 fetcher 测试（add-analyst-data-coverage Task 1，TDD 先行）。

覆盖：公告（cninfo）、研报（东财）、限售解禁（东财）、大宗交易（东财按区间拉取后个股过滤）。
全部 mock _call_ak，离线运行。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from finance_agent.data.akshare_client import AKShareClient


class TestFetchAnnouncements:
    def test_normalizes_columns_and_limits(self):
        df = pd.DataFrame(
            {
                "公告标题": ["2026年半年度报告", "关于回购股份的公告"],
                "公告日期": ["2026-08-15", "2026-07-01"],
                "公告类型": ["财务报告", "股份回购"],
                "网址": ["http://a/1", "http://a/2"],
            }
        )
        with patch("finance_agent.data.akshare_client._call_ak", return_value=df):
            rows = AKShareClient().fetch_announcements("600519", days=180)
        assert len(rows) == 2
        assert rows[0]["title"] == "2026年半年度报告"
        assert rows[0]["date"] == "2026-08-15"
        assert rows[0]["category"] == "财务报告"
        assert rows[0]["url"] == "http://a/1"

    def test_none_returns_empty(self):
        """_call_ak 失败语义为返回 None（非抛异常），fetcher 降级空列表。"""
        with patch("finance_agent.data.akshare_client._call_ak", return_value=None):
            assert AKShareClient().fetch_announcements("600519") == []

    def test_empty_df_returns_empty(self):
        with patch("finance_agent.data.akshare_client._call_ak", return_value=pd.DataFrame()):
            assert AKShareClient().fetch_announcements("600519") == []


class TestFetchResearchReports:
    """契约测试：fixture 取自真实东财接口响应快照（fail as_20261020）。

    接口列名漂移（机构/日期改名）时，本类断言必红——add-research-report-dataplane
    的根因就是 fixture 自造了 research机构/报告日期/目标价 三个不存在的列名。
    """

    @staticmethod
    def _snapshot_df():
        fixture_path = Path(__file__).parent / "fixtures" / "research_report_em_snapshot.json"
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        return payload, pd.DataFrame(payload["rows"], columns=payload["columns"])

    def test_real_snapshot_columns_pin_interface_shape(self):
        payload, _ = self._snapshot_df()
        columns = payload["columns"]
        assert "机构" in columns, "东财接口机构列改名——更新映射并刷新 fixture"
        assert "日期" in columns, "东财接口日期列改名——更新映射并刷新 fixture"
        assert "目标价" not in columns, "该接口不提供目标价；若新增需改契约"

    def test_normalizes_fields_from_real_snapshot(self):
        payload, df = self._snapshot_df()
        with patch("finance_agent.data.akshare_client._call_ak", return_value=df):
            rows = AKShareClient().fetch_research_reports(payload["symbol"])
        assert len(rows) == len(payload["rows"])
        for row in rows:
            assert row["org"], "机构不得为空（真实快照含机构列）"
            assert re.match(r"^\d{4}-\d{2}-\d{2}", row["publish_date"]), "发布日期缺失"
            assert row["title"]
            assert "target_price" not in row
        first = rows[0]
        assert first["rating"] == payload["rows"][0]["东财评级"]
        eps_years = {int(year) for year in first["eps_forecasts"]}
        assert eps_years and all(2020 <= year <= 2100 for year in eps_years)

    def test_missing_optional_columns_degrade_without_crash(self):
        df = pd.DataFrame({"报告名称": ["x"], "机构": ["某证券"], "日期": ["2026-08-20"]})
        with patch("finance_agent.data.akshare_client._call_ak", return_value=df):
            rows = AKShareClient().fetch_research_reports("600519")
        assert rows[0]["org"] == "某证券"
        assert rows[0]["publish_date"] == "2026-08-20"
        assert rows[0]["eps_forecasts"] == {}
        assert rows[0]["recent_report_count"] is None

    def test_none_returns_empty(self):
        """_call_ak 失败语义为返回 None（非抛异常），fetcher 降级空列表。"""
        with patch("finance_agent.data.akshare_client._call_ak", return_value=None):
            assert AKShareClient().fetch_research_reports("600519") == []


class TestFetchShareUnlock:
    def test_normalizes_fields(self):
        df = pd.DataFrame(
            {
                "解禁时间": ["2026-10-09"],
                "解禁数量": [120000000],
                "实际解禁数量": [110000000],
                "实际解禁市值": [14300000000],
                "占解禁前流通市值比例": [1.23],
            }
        )
        with patch("finance_agent.data.akshare_client._call_ak", return_value=df):
            rows = AKShareClient().fetch_share_unlock("600519")
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-10-09"
        assert rows[0]["shares"] == 120000000
        assert rows[0]["market_value"] == 14300000000
        assert rows[0]["pct_float"] == 1.23

    def test_none_returns_empty(self):
        """_call_ak 失败语义为返回 None（非抛异常），fetcher 降级空列表。"""
        with patch("finance_agent.data.akshare_client._call_ak", return_value=None):
            assert AKShareClient().fetch_share_unlock("600519") == []


class TestFetchBlockTrades:
    def test_filters_by_stock_code(self):
        df = pd.DataFrame(
            {
                "交易日期": ["2026-09-10", "2026-09-10", "2026-09-09"],
                "股票代码": ["600519", "300750", "600519"],
                "成交价格": [1270.0, 210.0, 1265.0],
                "成交量": [100000, 50000, 200000],
                "成交金额": [127000000.0, 10500000.0, 253000000.0],
                "溢价率": [-0.5, 1.2, 0.0],
                "买方营业部": ["机构专用", "某营业部", "机构专用"],
                "卖方营业部": ["某营业部", "某营业部", "某营业部"],
            }
        )
        with patch("finance_agent.data.akshare_client._call_ak", return_value=df) as mock:
            rows = AKShareClient().fetch_block_trades("600519", days=30)
        assert len(rows) == 2  # 仅 600519 的两笔
        assert rows[0]["price"] == 1270.0
        assert rows[0]["premium"] == -0.5
        assert rows[0]["buyer"] == "机构专用"
        # 按区间拉取：start/end 传入接口
        kwargs = mock.call_args.kwargs
        assert "start_date" in kwargs and "end_date" in kwargs

    def test_missing_column_variant_returns_empty(self):
        """信源返回非空 df 但缺「股票代码」列（空数据日变体），按契约降级 []，不抛 KeyError（#178）。"""
        df = pd.DataFrame({"交易日期": ["2026-09-10"], "成交价格": [1270.0]})
        with patch("finance_agent.data.akshare_client._call_ak", return_value=df):
            assert AKShareClient().fetch_block_trades("600519") == []

    def test_none_returns_empty(self):
        """_call_ak 失败语义为返回 None（非抛异常），fetcher 降级空列表。"""
        with patch("finance_agent.data.akshare_client._call_ak", return_value=None):
            assert AKShareClient().fetch_block_trades("600519") == []


class TestCitationEchoSources:
    """四新信源标题进回声匹配源集合（Task 7，TDD 先行）。"""

    def test_announcement_title_echo_pass(self):
        from finance_agent.citation import Claim, _verify_textual

        state = {
            "announcements": [{"title": "2026年半年度报告", "date": "2026-08-15"}],
            "research_reports": [],
            "share_unlock": [],
            "block_trades": [],
        }
        claim = Claim(
            claim_type="entity",
            source_type="data",
            field_ref="announcements.0.title",
            stated_value="2026年半年度报告",
            interpretation="公司发布中报",
            metric_name=None,
            period=None,
            direction=None,
        )
        result = _verify_textual(claim, state)
        assert result.status == "PASS"

    def test_unlock_date_echo(self):
        from finance_agent.citation import Claim, _verify_textual

        state = {
            "announcements": [],
            "research_reports": [],
            "share_unlock": [{"date": "2026-10-09"}],
            "block_trades": [],
        }
        claim = Claim(
            claim_type="entity",
            source_type="data",
            field_ref="share_unlock.0.date",
            stated_value="2026-10-09 限售解禁",
            interpretation="存在解禁压力",
            metric_name=None,
            period=None,
            direction=None,
        )
        result = _verify_textual(claim, state)
        assert result.status == "PASS"

    def test_unmatched_is_unverifiable_text(self):
        from finance_agent.citation import Claim, _verify_textual

        state = {
            "announcements": [],
            "research_reports": [],
            "share_unlock": [],
            "block_trades": [],
        }
        claim = Claim(
            claim_type="entity",
            source_type="data",
            field_ref="announcements.0.title",
            stated_value="凭空捏造的公告标题",
            interpretation="x",
            metric_name=None,
            period=None,
            direction=None,
        )
        result = _verify_textual(claim, state)
        assert result.status == "UNVERIFIABLE"
