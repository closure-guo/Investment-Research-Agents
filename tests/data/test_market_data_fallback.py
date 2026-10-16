"""add-market-data-fallback：全市场 spot / 历史日线双源回退（先红后绿 TDD）。

覆盖三件事（全部零真实网络，网络经 ``@patch("finance_agent.data.akshare_client.ak")``
注入）；真实可达性由 tests/validation/2026-10-15-add-market-data-fallback-validation.md
的手工实证记录：

1. ``_map_sina_spot_to_em`` 纯函数：列映射 / 缺列保形 / 空表 / 代码去前缀 /
   成交量股→手 / 停牌 0 值→NaN（夹具取真实新浪响应形状，2026-10-15 实抓）；
2. ``fetch_market_spot`` 回退链：东财失败/空表/缺列 → 新浪；两源皆失败 → None；
3. ``fetch_symbol_history_raw_qfq``（回填）同款新浪回退；
4. 日批成功摘要 ``spot_source`` 如实记录实际生效源。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from finance_agent.data.akshare_client import AKShareClient, _map_sina_spot_to_em
from finance_agent.data.snapshot import SnapshotError, get_all_meta, read_universe
from finance_agent.data.snapshot_job import run_market_snapshot_batch

# 日批实际消费的 spot 列（snapshot.build_snapshot_rows/_spot_row/compute_rebase_factors
# 逐列核对）→ 映射输出必须恰为这 10 列
_EM_CONTRACT_COLUMNS = [
    "代码",
    "名称",
    "最新价",
    "涨跌幅",
    "今开",
    "最高",
    "最低",
    "昨收",
    "成交量",
    "成交额",
]


def _sina_spot_frame() -> pd.DataFrame:
    """真实新浪 spot 响应形状的小夹具（列名/前缀/停牌 0 值按 2026-10-15 实抓形态）。"""
    return pd.DataFrame(
        {
            "代码": ["sh600519", "sz000001", "bj920000", "sz000016"],
            "名称": ["贵州茅台", "平安银行", "安徽凤凰", "*ST康佳A"],
            "最新价": [1274.89, 11.5, 12.98, 0.0],  # 停牌行新浪给 0.0（东财给 NaN）
            "涨跌额": [4.89, 0.2, -0.35, 0.0],
            "涨跌幅": [0.385, 1.77, -2.626, 0.0],
            "买入": [1274.88, 11.50, 12.96, 0.0],
            "卖出": [1274.90, 11.51, 12.98, 0.0],
            "昨收": [1270.00, 11.30, 13.33, 2.46],
            "今开": [1270.00, 11.40, 13.01, 0.0],
            "最高": [1280.00, 11.60, 13.30, 0.0],
            "最低": [1268.00, 11.20, 12.77, 0.0],
            "成交量": [2657276.0, 1234500.0, 385607.0, 0.0],  # 单位：股
            "成交额": [3.393316e9, 1.42e7, 5019718.0, 0.0],
            "时间戳": ["15:00:00", "15:00:00", "15:01:32", "15:00:00"],
        }
    )


def _em_spot_frame() -> pd.DataFrame:
    """东财 spot 形状的小夹具（主源可用路径用）。"""
    return pd.DataFrame(
        {
            "代码": ["600519", "000001"],
            "名称": ["贵州茅台", "平安银行"],
            "最新价": [1274.89, 11.5],
            "涨跌幅": [0.39, 1.77],
            "今开": [1270.0, 11.4],
            "最高": [1280.0, 11.6],
            "最低": [1268.0, 11.2],
            "昨收": [1270.0, 11.3],
            "成交量": [26572.76, 12345.0],
            "成交额": [3.393316e9, 1.42e7],
        }
    )


class TestMapSinaSpotToEm:
    """纯函数列映射（无 IO）：新浪列形 → 日批期望的东财列形。"""

    def test_real_shape_fixture_maps_to_em_contract_columns(self):
        """列集合恰为日批 10 列；同名列透传；多出的列丢弃。"""
        out = _map_sina_spot_to_em(_sina_spot_frame())

        assert list(out.columns) == _EM_CONTRACT_COLUMNS
        assert "时间戳" not in out.columns and "买入" not in out.columns
        assert out["名称"].tolist() == ["贵州茅台", "平安银行", "安徽凤凰", "*ST康佳A"]
        assert out["最新价"].tolist()[:3] == [1274.89, 11.5, 12.98]
        assert out["涨跌幅"].tolist()[:3] == [0.385, 1.77, -2.626]
        assert out["今开"].tolist()[:3] == [1270.0, 11.4, 13.01]
        assert out["最高"].tolist()[:3] == [1280.0, 11.6, 13.3]
        assert out["最低"].tolist()[:3] == [1268.0, 11.2, 12.77]
        assert out["昨收"].tolist() == [1270.0, 11.3, 13.33, 2.46]
        assert out["成交额"].tolist() == pytest.approx([3.393316e9, 1.42e7, 5019718.0, 0.0])

    def test_code_prefix_stripped_to_six_digits(self):
        """sh/sz/bj 前缀必须去掉：日批/证券主表/复权基准全按 6 位匹配。"""
        out = _map_sina_spot_to_em(_sina_spot_frame())

        assert out["代码"].tolist() == ["600519", "000001", "920000", "000016"]

    def test_volume_converted_shares_to_lots(self):
        """新浪成交量单位为股、东财为手 → ÷100（混表口径一致）。"""
        out = _map_sina_spot_to_em(_sina_spot_frame())

        assert out["成交量"].tolist() == pytest.approx([26572.76, 12345.0, 3856.07, 0.0])

    def test_zero_price_becomes_nan_for_suspension_synthesis(self):
        """停牌行（新浪 0.0）→ NaN：0 值直通会写出 close_raw=0 污染 qfq 序列。"""
        out = _map_sina_spot_to_em(_sina_spot_frame())

        assert pd.isna(out.iloc[3]["最新价"])
        assert out.iloc[3]["昨收"] == 2.46  # 复权检测仍可用交易所昨收

    def test_missing_source_columns_keep_shape_with_nan(self):
        """上游列名漂移（缺列）→ 补 NaN 保持列存在（不改变列集合）。"""
        frame = pd.DataFrame({"代码": ["sh600519"], "名称": ["贵州茅台"], "最新价": [1274.89]})

        out = _map_sina_spot_to_em(frame)

        assert list(out.columns) == _EM_CONTRACT_COLUMNS
        for col in ("涨跌幅", "今开", "最高", "最低", "昨收", "成交量", "成交额"):
            assert out[col].isna().all(), col

    def test_empty_frame_returns_empty_with_em_columns(self):
        """空表 → 空表 + 完整列形（调用方按空表走失败语义）。"""
        for empty in (pd.DataFrame(), _sina_spot_frame().iloc[0:0]):
            out = _map_sina_spot_to_em(empty)
            assert out.empty
            assert list(out.columns) == _EM_CONTRACT_COLUMNS

    def test_non_numeric_values_coerced_to_nan(self):
        """非数值（"-"/空串等占位）→ NaN，不得抛异常（上游形态不可控）。"""
        frame = pd.DataFrame(
            {
                "代码": ["sh600519"],
                "名称": ["贵州茅台"],
                "最新价": ["-"],
                "成交量": ["-"],
                "昨收": [1270.0],
            }
        )

        out = _map_sina_spot_to_em(frame)

        assert pd.isna(out.iloc[0]["最新价"])
        assert pd.isna(out.iloc[0]["成交量"])
        assert out.iloc[0]["昨收"] == 1270.0

    def test_input_frame_not_mutated(self):
        """纯函数：不得改写调用方传入的新浪原表（日批外可能复用同一帧）。"""
        frame = _sina_spot_frame()

        _map_sina_spot_to_em(frame)

        assert frame["代码"].tolist() == ["sh600519", "sz000001", "bj920000", "sz000016"]
        assert frame["成交量"].tolist() == [2657276.0, 1234500.0, 385607.0, 0.0]
        assert "时间戳" in frame.columns


class TestFetchMarketSpotFallback:
    """fetch_market_spot：东财 → 新浪 → None（零真实网络）。"""

    @pytest.fixture
    def client(self):
        return AKShareClient()

    @patch("finance_agent.data.akshare_client.ak")
    def test_eastmoney_success_returns_frame_without_touching_sina(self, mock_ak, client):
        spot = _em_spot_frame()
        mock_ak.stock_zh_a_spot_em.return_value = spot

        out = client.fetch_market_spot()

        assert out is spot
        mock_ak.stock_zh_a_spot.assert_not_called()
        assert client.sources_seen["market_spot"] == {"eastmoney"}
        assert client.last_spot_source == "eastmoney"

    @patch("finance_agent.data.akshare_client.ak")
    @pytest.mark.parametrize(
        "em_result",
        [
            None,
            pd.DataFrame(),
            pd.DataFrame({"名称": ["贵州茅台"]}),
            pd.DataFrame({"代码": [None, None], "名称": ["a", "b"]}),  # 空壳表（代码全空）
        ],
        ids=["none", "empty", "missing-code-column", "all-blank-codes"],
    )
    def test_eastmoney_failure_falls_back_to_mapped_sina(self, mock_ak, client, em_result):
        mock_ak.stock_zh_a_spot_em.return_value = em_result
        mock_ak.stock_zh_a_spot.return_value = _sina_spot_frame()

        out = client.fetch_market_spot()

        assert out is not None
        assert list(out.columns) == _EM_CONTRACT_COLUMNS
        assert out["代码"].tolist() == ["600519", "000001", "920000", "000016"]
        assert out["成交量"].tolist() == pytest.approx([26572.76, 12345.0, 3856.07, 0.0])
        assert pd.isna(out.iloc[3]["最新价"])
        mock_ak.stock_zh_a_spot.assert_called_once()
        assert client.sources_seen["market_spot"] == {"sina"}
        assert client.last_spot_source == "sina"

    @patch("finance_agent.data.akshare_client.ak")
    def test_both_sources_fail_returns_none_with_alarm(self, mock_ak, client):
        mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
        mock_ak.stock_zh_a_spot.return_value = pd.DataFrame()

        assert client.fetch_market_spot() is None
        assert client.sources_seen["market_spot"] == {"none"}
        assert client.last_spot_source is None

    @patch("finance_agent.data.akshare_client.ak")
    def test_sina_without_code_column_returns_none(self, mock_ak, client):
        """新浪缺「代码」列（接口漂移）→ None，MUST NOT 返回残缺表。"""
        mock_ak.stock_zh_a_spot_em.return_value = None
        mock_ak.stock_zh_a_spot.return_value = pd.DataFrame({"名称": ["贵州茅台"]})

        assert client.fetch_market_spot() is None
        assert client.sources_seen["market_spot"] == {"none"}

    @patch("finance_agent.data.akshare_client.ak")
    def test_sina_all_codes_blank_returns_none(self, mock_ak, client):
        """代码列存在但全空（空壳表）→ None（不静默写空快照）。"""
        mock_ak.stock_zh_a_spot_em.return_value = None
        mock_ak.stock_zh_a_spot.return_value = pd.DataFrame(
            {"代码": [None, None], "名称": ["a", "b"]}
        )

        assert client.fetch_market_spot() is None


class TestFetchSymbolHistoryRawQfqFallback:
    """回填取数 fetch_symbol_history_raw_qfq：东财双口径 → 新浪双口径 → None。"""

    @pytest.fixture
    def client(self):
        return AKShareClient()

    @staticmethod
    def _sina_pair():
        raw = pd.DataFrame(
            {
                "date": [date(2026, 10, 13), date(2026, 10, 10)],  # 乱序：出口必须排序
                "open": [10.0, 9.9],
                "close": [10.5, 9.8],
                "high": [10.6, 10.0],
                "low": [9.9, 9.7],
                "volume": [120000.0, 100000.0],  # 单位：股
                "amount": [1250000.0, 980000.0],
                "outstanding_share": [1e9, 1e9],
                "turnover": [0.0001, 0.0001],
            }
        )
        qfq = pd.DataFrame(
            {
                "date": [date(2026, 10, 10), date(2026, 10, 13), date(2026, 10, 14)],
                "open": [9.0, 9.6, 9.7],
                "close": [9.0, 9.6, 9.7],
                "high": [9.2, 9.8, 9.9],
                "low": [8.9, 9.5, 9.6],
                "volume": [100000.0, 120000.0, 80000.0],
                "amount": [900000.0, 1152000.0, 776000.0],
                "outstanding_share": [1e9, 1e9, 1e9],
                "turnover": [0.0001, 0.0001, 0.0001],
            }
        )
        return raw, qfq

    @patch("finance_agent.data.akshare_client.ak")
    def test_eastmoney_failure_falls_back_to_sina_pair(self, mock_ak, client):
        raw, qfq = self._sina_pair()
        mock_ak.stock_zh_a_hist.return_value = None  # 东财 raw 腿失败即短路
        mock_ak.stock_zh_a_daily.side_effect = [raw, qfq]

        out = client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261014")

        assert out is not None
        assert list(out.columns) == ["日期", "开盘", "收盘_raw", "收盘_qfq", "成交量", "成交额"]
        assert list(out["日期"]) == [date(2026, 10, 10), date(2026, 10, 13)]  # 10-14 仅 qfq 有
        assert list(out["开盘"]) == [9.9, 10.0]
        assert list(out["收盘_raw"]) == [9.8, 10.5]
        assert list(out["收盘_qfq"]) == [9.0, 9.6]
        assert list(out["成交量"]) == [1000.0, 1200.0]  # 股 → 手（÷100）
        assert list(out["成交额"]) == [980000.0, 1250000.0]  # 元，raw 腿直通（取 raw 口径）

        assert mock_ak.stock_zh_a_hist.call_count == 1  # raw 失败不浪费 qfq 腿
        calls = mock_ak.stock_zh_a_daily.call_args_list
        assert [c.kwargs["adjust"] for c in calls] == ["", "qfq"]
        assert [c.kwargs["symbol"] for c in calls] == ["sh600519", "sh600519"]
        assert [c.kwargs["start_date"] for c in calls] == ["20261001", "20261001"]
        assert [c.kwargs["end_date"] for c in calls] == ["20261014", "20261014"]

    @patch("finance_agent.data.akshare_client.ak")
    def test_sina_symbol_prefix_per_exchange(self, mock_ak, client):
        """深市/北交所代码转新浪前缀（sz/bj），与 fetch_kline 回退链同规则。"""
        raw, qfq = self._sina_pair()
        mock_ak.stock_zh_a_hist.return_value = None
        mock_ak.stock_zh_a_daily.side_effect = [raw, qfq]

        client.fetch_symbol_history_raw_qfq("000001", "20261001", "20261014")

        assert mock_ak.stock_zh_a_daily.call_args_list[0].kwargs["symbol"] == "sz000001"

    @patch("finance_agent.data.akshare_client.ak")
    def test_eastmoney_qfq_leg_failure_falls_back_to_sina(self, mock_ak, client):
        em_raw = pd.DataFrame(
            {
                "日期": [date(2026, 10, 10)],
                "开盘": [9.9],
                "收盘": [9.8],
                "成交量": [1000.0],
                "成交额": [2_000_000.0],
            }
        )
        raw, qfq = self._sina_pair()
        mock_ak.stock_zh_a_hist.side_effect = [em_raw, None]  # 东财 qfq 腿失败
        mock_ak.stock_zh_a_daily.side_effect = [raw, qfq]

        out = client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261014")

        assert out is not None
        assert list(out["收盘_qfq"]) == [9.0, 9.6]  # 来自新浪腿
        assert mock_ak.stock_zh_a_hist.call_count == 2
        assert mock_ak.stock_zh_a_daily.call_count == 2

    @patch("finance_agent.data.akshare_client.ak")
    def test_eastmoney_success_includes_amount_six_columns(self, mock_ak, client):
        """东财双口径成功：出口六列契约（成交额取 raw 腿，元直通）。"""
        em_raw = pd.DataFrame(
            {
                "日期": [date(2026, 10, 10), date(2026, 10, 13)],
                "开盘": [9.9, 10.0],
                "收盘": [9.8, 10.5],
                "成交量": [1000.0, 1200.0],
                "成交额": [2_000_000.0, 2_600_000.0],
            }
        )
        em_qfq = pd.DataFrame(
            {
                "日期": [date(2026, 10, 10), date(2026, 10, 13)],
                "收盘": [9.0, 9.6],
            }
        )
        mock_ak.stock_zh_a_hist.side_effect = [em_raw, em_qfq]

        out = client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261014")

        assert out is not None
        assert list(out.columns) == ["日期", "开盘", "收盘_raw", "收盘_qfq", "成交量", "成交额"]
        assert list(out["成交额"]) == [2_000_000.0, 2_600_000.0]
        assert mock_ak.stock_zh_a_daily.call_count == 0  # 主源成功不触回退

    @patch("finance_agent.data.akshare_client.ak")
    def test_sina_missing_amount_returns_none(self, mock_ak, client):
        """新浪 raw 腿缺 amount 列 → None（不产出半口径数据，spec 场景「源缺成交额按失败处理」）。"""
        raw, qfq = self._sina_pair()
        raw = raw.drop(columns=["amount"])
        mock_ak.stock_zh_a_hist.return_value = None
        mock_ak.stock_zh_a_daily.side_effect = [raw, qfq]

        assert client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261014") is None

    @patch("finance_agent.data.akshare_client.ak")
    def test_both_sources_fail_returns_none(self, mock_ak, client):
        mock_ak.stock_zh_a_hist.return_value = None
        mock_ak.stock_zh_a_daily.return_value = None

        assert client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261014") is None

    @patch("finance_agent.data.akshare_client.ak")
    def test_sina_missing_required_columns_returns_none(self, mock_ak, client):
        """新浪 raw 腿缺 成交量 列 → None（不产出半口径数据）。"""
        mock_ak.stock_zh_a_hist.return_value = None
        mock_ak.stock_zh_a_daily.return_value = pd.DataFrame(
            {"date": [date(2026, 10, 10)], "open": [9.9], "close": [9.8]}
        )

        assert client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261014") is None
        assert mock_ak.stock_zh_a_daily.call_count == 1  # raw 缺列即短路，不取 qfq 腿

    @patch("finance_agent.data.akshare_client.ak")
    def test_sina_pair_without_date_intersection_returns_none(self, mock_ak, client):
        raw, qfq = self._sina_pair()
        qfq = qfq[qfq["date"] == date(2026, 10, 14)]  # 与 raw 无交集
        mock_ak.stock_zh_a_hist.return_value = None
        mock_ak.stock_zh_a_daily.side_effect = [raw, qfq]

        assert client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261014") is None


def _today_beijing() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


class _NoSourceReportClient:
    """未上报生效源的假客户端（旧实现/第三方注入）：摘要 spot_source 应为 None。"""

    def __init__(self, spot):
        self.spot = spot

    def fetch_trade_calendar(self):
        return None

    def fetch_market_spot(self):
        return self.spot

    def fetch_listing_info(self):
        return None


class TestBatchSummarySpotSource:
    """日批成功摘要 spot_source：如实记录实际生效源（端到端，零真实网络）。"""

    @patch("finance_agent.data.akshare_client.ak")
    def test_summary_reports_sina_when_eastmoney_down(self, mock_ak, tmp_path):
        """东财失败 + 新浪成功 → 摘要 spot_source=sina，且行由映射后的真实形态数据写入。"""
        db = tmp_path / "snap.db"
        mock_ak.stock_zh_a_spot_em.return_value = None
        mock_ak.stock_zh_a_spot.return_value = _sina_spot_frame()
        mock_ak.tool_trade_date_hist_sina.return_value = pd.DataFrame(
            {"trade_date": ["2026-10-15"]}
        )
        mock_ak.stock_info_sh_name_code.return_value = None
        mock_ak.stock_info_sz_name_code.return_value = None
        mock_ak.stock_info_bj_name_code.return_value = None
        client = AKShareClient()

        out = run_market_snapshot_batch("2026-10-15", client=client, db_path=db)

        assert out["spot_source"] == "sina"
        # 停牌行（新浪 0.0 → NaN）无昨行不可承价 → 不入行；其余 3 行落库
        assert out["written"] == 3
        rows = {row["code"]: row for row in read_universe("2026-10-15", db_path=db)}
        assert set(rows) == {"600519", "000001", "920000"}
        assert rows["600519"]["close_raw"] == 1274.89
        assert rows["600519"]["volume"] == pytest.approx(26572.76)
        assert rows["600519"]["pct_chg"] == 0.385

    def test_summary_spot_source_none_when_client_does_not_report(self, tmp_path):
        db = tmp_path / "snap.db"
        spot = _em_spot_frame()
        client = _NoSourceReportClient(spot=spot)

        out = run_market_snapshot_batch("2026-10-15", client=client, db_path=db)

        assert out["spot_source"] is None
        assert out["calendar"] == "unavailable"
        assert out["written"] == 2

    @patch("finance_agent.data.akshare_client.ak")
    def test_batch_raises_and_marks_error_when_both_sources_down(self, mock_ak, tmp_path):
        """spec 场景：两源皆失败 → 日批抛 SnapshotError + 写失败标记，MUST NOT 写空快照。"""
        db = tmp_path / "snap.db"
        mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
        mock_ak.stock_zh_a_spot.return_value = pd.DataFrame()
        mock_ak.tool_trade_date_hist_sina.return_value = pd.DataFrame(
            {"trade_date": ["2026-10-15"]}
        )
        client = AKShareClient()

        with pytest.raises(SnapshotError, match="全市场行情拉取失败"):
            run_market_snapshot_batch("2026-10-15", client=client, db_path=db)

        meta = get_all_meta(db)
        assert meta["last_error"] == "SnapshotError: 全市场行情拉取失败"
        assert meta["last_error_date"] == _today_beijing()
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM market_snapshot").fetchone()[0] == 0
        finally:
            conn.close()
