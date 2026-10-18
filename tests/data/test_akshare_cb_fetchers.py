"""可转债抓取映射测试（delta add-convertible-bond-coverage T1/T2）：字段映射与单位归一。

口径权威：docs/evals/metrics.md §1.17 + 变更 design.md「数据源与字段」。
接口实测（本机 akshare 1.18.94，2026-10-18）：
- ``ak.bond_zh_cov()`` 列 ``债券代码/债券简称/正股代码/转股价/转股溢价率/发行规模/信用评级``
  （1063 行；**无剩余规模列**；`转股溢价率` 单位 %、`发行规模` 单位 亿元）；
- ``ak.bond_cb_redeem_jsl()`` 列 ``代码/名称/强赎触发价/强赎触发比/强赎天计数/强赎状态/
  最后交易日/剩余规模/…``（317 行；`剩余规模` 单位亿元，交叉核验：金田转债 规模 15.000 /
  剩余规模 9.905，与 ``bond_zh_cov.发行规模`` 同口径；``强赎天计数`` 样本形如 ``"12/15 | 30"``）；
- ``ak.bond_zh_hs_cov_spot()`` 列 ``symbol/name/trade/amount/code/…``（320 行；`trade` 元、`amount` 元）；
- ``ak.bond_cb_adj_logs_jsl(symbol=…)`` 逐代码（下修历史；无记录 → 空表，非失败）。

本文件全部用 fake DataFrame，零网络。
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pandas as pd

from finance_agent.data.akshare_client import AKShareClient

_LOGGER = "finance_agent.data.akshare_client"


def _cov_df(rows: list[dict]) -> pd.DataFrame:
    """``bond_zh_cov`` 真实列形 fake（列名逐字对齐实探）。"""
    return pd.DataFrame(
        {
            "债券代码": [r.get("code") for r in rows],
            "债券简称": [r.get("name") for r in rows],
            "申购日期": [None] * len(rows),
            "申购代码": [None] * len(rows),
            "申购上限": [None] * len(rows),
            "正股代码": [r.get("stock_code") for r in rows],
            "正股简称": [None] * len(rows),
            "正股价": [None] * len(rows),
            "转股价": [r.get("convert_price") for r in rows],
            "转股价值": [None] * len(rows),
            "债现价": [r.get("bond_price") for r in rows],
            "转股溢价率": [r.get("premium_rate") for r in rows],
            "原股东配售-股权登记日": [None] * len(rows),
            "原股东配售-每股配售额": [None] * len(rows),
            "发行规模": [r.get("issue_scale") for r in rows],
            "中签号发布日": [None] * len(rows),
            "中签率": [None] * len(rows),
            "上市时间": [None] * len(rows),
            "信用评级": [r.get("rating") for r in rows],
        }
    )


def _redeem_df(rows: list[dict]) -> pd.DataFrame:
    """``bond_cb_redeem_jsl`` 真实列形 fake。"""
    return pd.DataFrame(
        {
            "代码": [r.get("code") for r in rows],
            "名称": [r.get("name") for r in rows],
            "现价": [r.get("price") for r in rows],
            "正股代码": [None] * len(rows),
            "正股名称": [None] * len(rows),
            "规模": [r.get("issue_scale") for r in rows],
            "剩余规模": [r.get("remain_scale") for r in rows],
            "转股起始日": [None] * len(rows),
            "最后交易日": [r.get("last_trade_date") for r in rows],
            "到期日": [None] * len(rows),
            "转股价": [None] * len(rows),
            "强赎触发比": [r.get("trigger_ratio") for r in rows],
            "强赎触发价": [r.get("trigger_price") for r in rows],
            "正股价": [None] * len(rows),
            "强赎价": [None] * len(rows),
            "强赎天计数": [r.get("count_text") for r in rows],
            "强赎条款": [None] * len(rows),
            "强赎状态": [r.get("status") for r in rows],
        }
    )


def _spot_df(rows: list[dict]) -> pd.DataFrame:
    """``bond_zh_hs_cov_spot`` 真实列形 fake（新浪英文列）。"""
    return pd.DataFrame(
        {
            "symbol": [r.get("symbol") for r in rows],
            "name": [r.get("name") for r in rows],
            "trade": [r.get("trade") for r in rows],
            "pricechange": [None] * len(rows),
            "changepercent": [None] * len(rows),
            "buy": [None] * len(rows),
            "sell": [None] * len(rows),
            "settlement": [None] * len(rows),
            "open": [None] * len(rows),
            "high": [None] * len(rows),
            "low": [None] * len(rows),
            "volume": [None] * len(rows),
            "amount": [r.get("amount") for r in rows],
            "code": [r.get("code") for r in rows],
            "ticktime": [None] * len(rows),
        }
    )


def _adj_df(rows: list[dict]) -> pd.DataFrame:
    """``bond_cb_adj_logs_jsl`` 真实列形 fake。"""
    return pd.DataFrame(
        {
            "转债名称": [r.get("name") for r in rows],
            "股东大会日": [r.get("meeting_date") for r in rows],
            "下修前转股价": [r.get("before") for r in rows],
            "下修后转股价": [r.get("after") for r in rows],
            "新转股价生效日期": [r.get("effective_date") for r in rows],
            "下修底价": [r.get("floor") for r in rows],
        }
    )


class TestFetchCbMaster:
    def test_maps_fields_and_keeps_source_units(self):
        """字段映射 + 单位原样（溢价率 %、价格元）；剩余规模主表源无此列 → None 不估算。"""
        df = _cov_df(
            [
                {
                    "code": 113046,
                    "name": "金田转债",
                    "stock_code": 601609,
                    "convert_price": 10.18,
                    "premium_rate": 1.63,
                    "bond_price": 101.332,
                    "issue_scale": 15.0,
                    "rating": "AA+",
                }
            ]
        )
        client = AKShareClient()
        with patch("finance_agent.data.akshare_client.ak.bond_zh_cov", return_value=df):
            rows = client.fetch_cb_master()
        assert rows == [
            {
                "code": "113046",
                "name": "金田转债",
                "stock_code": "601609",
                "convert_price": 10.18,
                "premium_rate": 1.63,
                "remain_scale": None,
                "rating": "AA+",
            }
        ], "发行规模 15.0 亿元 MUST NOT 冒充剩余规模"
        assert client.sources_seen["cb_master"] == {"eastmoney"}

    def test_missing_values_stay_none_and_rows_without_code_skipped(self, caplog):
        """未上市/缺要素行如实 NULL（转股价/溢价率/评级）；无代码行不可落库 → 跳过留痕。"""
        df = _cov_df(
            [
                {"code": 118078, "name": "航亚转债", "convert_price": None, "premium_rate": None},
                {"code": None, "name": "坏行"},
            ]
        )
        with (
            patch("finance_agent.data.akshare_client.ak.bond_zh_cov", return_value=df),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            rows = AKShareClient().fetch_cb_master()
        assert len(rows) == 1
        assert rows[0]["convert_price"] is None and rows[0]["premium_rate"] is None
        assert rows[0]["rating"] is None and rows[0]["remain_scale"] is None
        assert "跳过" in caplog.text

    def test_failure_returns_empty_and_logs_error(self, caplog):
        def _boom():
            raise ConnectionRefusedError(111, "connection refused")

        client = AKShareClient()
        with (
            patch("finance_agent.data.akshare_client.ak.bond_zh_cov", side_effect=_boom),
            caplog.at_level(logging.ERROR, logger=_LOGGER),
        ):
            rows = client.fetch_cb_master()
        assert rows == []
        assert "可转债标的拉取失败" in caplog.text
        assert client.sources_seen["cb_master"] == {"none"}


class TestFetchCbQuotes:
    def test_maps_price_and_amount_and_skips_zero_trade(self, caplog):
        """``trade`` → close（元）/ ``amount`` → amount（元）；零成交/缺价行跳过（MUST NOT 折算 0）。"""
        df = _spot_df(
            [
                {
                    "symbol": "sh110076",
                    "name": "华海转债",
                    "code": 110076,
                    "trade": 109.948,
                    "amount": 95_687_931.0,
                },
                {
                    "symbol": "sh110072",
                    "name": "广汇转债",
                    "code": 110072,
                    "trade": 0.0,
                    "amount": 0.0,
                },
                {
                    "symbol": "sz128136",
                    "name": "立讯转债",
                    "code": 128136,
                    "trade": None,
                    "amount": 12.0,
                },
            ]
        )
        with (
            patch("finance_agent.data.akshare_client.ak.bond_zh_hs_cov_spot", return_value=df),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            rows = AKShareClient().fetch_cb_quotes()
        assert rows == [{"code": "110076", "close": 109.948, "amount": 95_687_931.0}]
        assert "跳过" in caplog.text

    def test_records_source_seen_and_failure(self, caplog):
        client = AKShareClient()
        with patch(
            "finance_agent.data.akshare_client.ak.bond_zh_hs_cov_spot",
            return_value=_spot_df([{"code": 110076, "trade": 1.0, "amount": 1.0}]),
        ):
            client.fetch_cb_quotes()
        assert client.sources_seen["cb_quotes"] == {"sina"}

        def _boom():
            raise ConnectionRefusedError(111, "connection refused")

        client2 = AKShareClient()
        with (
            patch("finance_agent.data.akshare_client.ak.bond_zh_hs_cov_spot", side_effect=_boom),
            caplog.at_level(logging.ERROR, logger=_LOGGER),
        ):
            assert client2.fetch_cb_quotes() == []
        assert client2.sources_seen["cb_quotes"] == {"none"}
        assert "可转债行情拉取失败" in caplog.text


class TestFetchCbRedeemEvents:
    def test_maps_fields_keeps_remain_scale_unit_and_raw(self):
        df = _redeem_df(
            [
                {
                    "code": 113046,
                    "name": "金田转债",
                    "issue_scale": 15.0,
                    "remain_scale": 9.905,
                    "last_trade_date": "2026-10-20",
                    "trigger_ratio": 130,
                    "trigger_price": 13.234,
                    "count_text": "12/15 | 30",
                    "status": "已公告强赎",
                }
            ]
        )
        client = AKShareClient()
        with patch("finance_agent.data.akshare_client.ak.bond_cb_redeem_jsl", return_value=df):
            rows = client.fetch_cb_redeem_events()
        assert len(rows) == 1
        row = rows[0]
        assert row["code"] == "113046" and row["name"] == "金田转债"
        assert row["remain_scale"] == 9.905, "剩余规模单位 = 亿元，原样落库不换算"
        assert row["last_trade_date"] == "2026-10-20"
        assert row["trigger_ratio"] == 130.0 and row["trigger_price"] == 13.234
        assert row["count_text"] == "12/15 | 30", "天计数原样文本（解析由状态推导层负责）"
        assert row["redeem_status"] == "已公告强赎"
        raw = json.loads(row["raw_json"])
        assert raw["强赎状态"] == "已公告强赎" and raw["剩余规模"] == 9.905, "源行逐字留痕"
        assert "规模" in raw, "发行规模亦在原始留痕里（审计可辨）"

    def test_date_normalization_and_missing_rows_skipped(self, caplog):
        import datetime as dt

        df = _redeem_df(
            [
                {"code": "110076", "name": "华海转债", "last_trade_date": dt.date(2026, 10, 27)},
                {"code": None, "name": "坏行"},
            ]
        )
        with (
            patch("finance_agent.data.akshare_client.ak.bond_cb_redeem_jsl", return_value=df),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            rows = AKShareClient().fetch_cb_redeem_events()
        assert [r["code"] for r in rows] == ["110076"]
        assert rows[0]["last_trade_date"] == "2026-10-27"
        assert rows[0]["count_text"] is None, "缺值如实 None（不折算空串/0）"
        assert "跳过" in caplog.text

    def test_failure_returns_empty_and_logs_error(self, caplog):
        def _boom():
            raise ConnectionRefusedError(111, "connection refused")

        client = AKShareClient()
        with (
            patch("finance_agent.data.akshare_client.ak.bond_cb_redeem_jsl", side_effect=_boom),
            caplog.at_level(logging.ERROR, logger=_LOGGER),
        ):
            assert client.fetch_cb_redeem_events() == []
        assert client.sources_seen["cb_redeem"] == {"none"}
        assert "强赎事件拉取失败" in caplog.text


class TestFetchCbAdjLogs:
    def test_maps_adjustment_history(self):
        df = _adj_df(
            [
                {
                    "name": "广汇转债",
                    "meeting_date": "2024-05-21",
                    "before": 4.03,
                    "after": 1.5,
                    "effective_date": "2024-05-23",
                    "floor": 1.5,
                }
            ]
        )
        client = AKShareClient()
        with patch("finance_agent.data.akshare_client.ak.bond_cb_adj_logs_jsl", return_value=df):
            rows = client.fetch_cb_adj_logs("110072")
        assert len(rows) == 1
        row = rows[0]
        assert row["code"] == "110072" and row["name"] == "广汇转债"
        assert row["meeting_date"] == "2024-05-21"
        assert row["convert_price_before"] == 4.03 and row["convert_price_after"] == 1.5
        assert row["effective_date"] == "2024-05-23"
        raw = json.loads(row["raw_json"])
        assert raw["下修底价"] == 1.5 and raw["下修前转股价"] == 4.03
        assert client.sources_seen["cb_adj"] == {"jisilu"}

    def test_empty_history_is_not_a_failure(self):
        """无下修记录 → 空表（源可达），MUST NOT 记 none（与请求失败区分）。"""
        client = AKShareClient()
        with patch(
            "finance_agent.data.akshare_client.ak.bond_cb_adj_logs_jsl",
            return_value=pd.DataFrame(),
        ):
            assert client.fetch_cb_adj_logs("113046") == []
        assert client.sources_seen["cb_adj"] == {"jisilu"}
        assert client.last_cb_adj_failed is False

    def test_request_failure_leaves_last_call_flag(self, caplog):
        """单次调用成败经 ``last_cb_adj_failed`` 留痕（逐代码循环的失败判据）。"""

        def _boom(code):
            raise ConnectionRefusedError(111, "connection refused")

        client = AKShareClient()
        with (
            patch("finance_agent.data.akshare_client.ak.bond_cb_adj_logs_jsl", side_effect=_boom),
            caplog.at_level(logging.ERROR, logger=_LOGGER),
        ):
            assert client.fetch_cb_adj_logs("113046") == []
        assert client.sources_seen["cb_adj"] == {"none"}
        assert client.last_cb_adj_failed is True
        assert "下修历史拉取失败" in caplog.text
