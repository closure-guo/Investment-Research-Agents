"""TDD：退市清单抓取与 ``security_master`` 扩展（delta add-microcap-lane-guardrails T2）。

口径（docs/evals/metrics.md §1.16，逐字）：

- 两所清单合并：沪 ``stock_info_sh_delist``（列 ``公司代码/公司简称/上市日期/暂停上市日期``）
  日期列是「**暂停上市日期**」→ 映射 ``delist_date`` 且 ``source="sh_suspend"`` 供审计；
  深 ``stock_info_sz_delist``（列 ``证券代码/证券简称/上市日期/终止上市日期``）→ ``source="sz_delist"``；
- 抓取失败/空 → ``[]`` + ``logger.error``（明细类约定，不以空表冒充成功）；
- ``security_master`` 增 ``delist_date`` 列（新库建表 + 老库 ALTER 迁移，旧行保留）；
  ``upsert_security_master`` COALESCE 保值；``upsert_delisted_masters`` 幂等。

零网络：全部 ``@patch("finance_agent.data.akshare_client.ak")``。
"""

from __future__ import annotations

import logging
import sqlite3
from unittest.mock import patch

import pandas as pd
import pytest

from finance_agent.data.akshare_client import AKShareClient
from finance_agent.data.snapshot import (
    count_delisted_masters,
    get_security_master,
    init_snapshot_db,
    upsert_delisted_masters,
    upsert_security_master,
)


@pytest.fixture
def client():
    return AKShareClient()


def _sh_frame() -> pd.DataFrame:
    """沪市退市清单实测列形（2026-10-18 本机 akshare 1.18.94）。"""
    return pd.DataFrame(
        {
            "公司代码": ["600001", "600002"],
            "公司简称": ["邯郸钢铁", "退市公司"],
            "上市日期": ["19980101", "20000101"],
            "暂停上市日期": ["20091229", None],
        }
    )


def _sz_frame() -> pd.DataFrame:
    """深市退市清单实测列形（同日实测）。"""
    return pd.DataFrame(
        {
            "证券代码": [1.0],  # float 整数值 → 6 位字符串（000001 型归一）
            "证券简称": ["深市退市"],
            "上市日期": ["19950101"],
            "终止上市日期": ["20050101"],
        }
    )


class TestFetchDelistLists:
    @patch("finance_agent.data.akshare_client.ak")
    def test_merges_sh_and_sz_with_field_mapping(self, mock_ak, client):
        """沪「暂停上市日期」→ delist_date + source=sh_suspend；深「终止上市日期」+ source=sz_delist。"""
        mock_ak.stock_info_sh_delist.return_value = _sh_frame()
        mock_ak.stock_info_sz_delist.return_value = _sz_frame()

        rows = client.fetch_delist_lists()

        assert rows == [
            {
                "code": "600001",
                "name": "邯郸钢铁",
                "list_date": "1998-01-01",
                "delist_date": "2009-12-29",
                "source": "sh_suspend",
            },
            {
                "code": "600002",
                "name": "退市公司",
                "list_date": "2000-01-01",
                "delist_date": None,  # 缺失不编造
                "source": "sh_suspend",
            },
            {
                "code": "000001",
                "name": "深市退市",
                "list_date": "1995-01-01",
                "delist_date": "2005-01-01",
                "source": "sz_delist",
            },
        ]
        assert client.sources_seen["delist"] == {"sh_suspend", "sz_delist"}

    @patch("finance_agent.data.akshare_client.ak")
    def test_one_source_failure_keeps_other_rows_and_logs_error(self, mock_ak, client, caplog):
        """单源失败（None）→ 另一源行照常返回；失败源留 ERROR 痕。"""
        mock_ak.stock_info_sh_delist.return_value = None
        mock_ak.stock_info_sz_delist.return_value = _sz_frame()

        with caplog.at_level(logging.ERROR, logger="finance_agent.data.akshare_client"):
            rows = client.fetch_delist_lists()

        assert [row["code"] for row in rows] == ["000001"]
        assert any("退市清单" in record.message for record in caplog.records)
        assert client.sources_seen["delist"] == {"sz_delist"}

    @patch("finance_agent.data.akshare_client.ak")
    def test_all_sources_fail_returns_empty_with_error(self, mock_ak, client, caplog):
        """全源失败/空 → ``[]`` + ERROR（明细类约定；MUST NOT 以空表冒充成功）。"""
        mock_ak.stock_info_sh_delist.return_value = None
        mock_ak.stock_info_sz_delist.return_value = pd.DataFrame()

        with caplog.at_level(logging.ERROR, logger="finance_agent.data.akshare_client"):
            rows = client.fetch_delist_lists()

        assert rows == []
        assert any("退市清单" in record.message for record in caplog.records)
        assert client.sources_seen["delist"] == {"none"}

    @patch("finance_agent.data.akshare_client.ak")
    def test_missing_code_column_source_is_unusable(self, mock_ak, client):
        """缺代码列 → 该源不可用（不产行）；两源皆不可用 → []。"""
        mock_ak.stock_info_sh_delist.return_value = pd.DataFrame({"公司简称": ["无代码列"]})
        mock_ak.stock_info_sz_delist.return_value = None

        assert client.fetch_delist_lists() == []

    @patch("finance_agent.data.akshare_client.ak")
    def test_missing_delist_date_column_source_is_unusable(self, mock_ak, client, caplog):
        """F4：缺退市日列（列名漂移）→ 该源不可用（不产全 None 日期行）+ ERROR。

        明细类约定：日期列缺失不得降级为「一批无日期行」冒充成功。
        """
        mock_ak.stock_info_sh_delist.return_value = pd.DataFrame(
            {
                "公司代码": ["600001"],
                "公司简称": ["邯郸钢铁"],
                "上市日期": ["19980101"],
                # 缺「暂停上市日期」列（源列名漂移）
            }
        )
        mock_ak.stock_info_sz_delist.return_value = _sz_frame()

        with caplog.at_level(logging.ERROR, logger="finance_agent.data.akshare_client"):
            rows = client.fetch_delist_lists()

        assert [row["code"] for row in rows] == ["000001"]  # 沪源被丢弃，深源照常
        assert any("退市清单" in record.message for record in caplog.records)
        assert client.sources_seen["delist"] == {"sz_delist"}

    @patch("finance_agent.data.akshare_client.ak")
    def test_missing_list_date_column_degrades_with_warning(self, mock_ak, client, caplog):
        """F4：缺上市日期列 → 降级保留行（list_date=None）+ WARN（不丢弃该源）。"""
        mock_ak.stock_info_sh_delist.return_value = pd.DataFrame(
            {
                "公司代码": ["600001"],
                "公司简称": ["邯郸钢铁"],
                "暂停上市日期": ["20091229"],
                # 缺「上市日期」列
            }
        )
        mock_ak.stock_info_sz_delist.return_value = None

        with caplog.at_level(logging.WARNING, logger="finance_agent.data.akshare_client"):
            rows = client.fetch_delist_lists()

        assert rows == [
            {
                "code": "600001",
                "name": "邯郸钢铁",
                "list_date": None,
                "delist_date": "2009-12-29",
                "source": "sh_suspend",
            }
        ]
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    @patch("finance_agent.data.akshare_client.ak")
    def test_delist_date_row_level_missing_stays_none(self, mock_ak, client):
        """行级日期缺失（列在、值为空）仍如实置 None（与列缺失区分）。"""
        mock_ak.stock_info_sh_delist.return_value = pd.DataFrame(
            {
                "公司代码": ["600002"],
                "公司简称": ["缺日期"],
                "上市日期": ["20000101"],
                "暂停上市日期": [None],
            }
        )
        mock_ak.stock_info_sz_delist.return_value = None

        rows = client.fetch_delist_lists()

        assert rows[0]["delist_date"] is None
        assert rows[0]["list_date"] == "2000-01-01"


class TestSecurityMasterDelistDate:
    def test_fresh_db_has_delist_date_and_source_columns(self, tmp_path):
        """F2：主表增 ``delist_date`` 与 ``delist_source``（审计留痕落库）。"""
        db = tmp_path / "snap.db"
        init_snapshot_db(db)

        conn = sqlite3.connect(db)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(security_master)")}
        finally:
            conn.close()
        assert {"delist_date", "delist_source"} <= columns

    def test_legacy_db_migration_preserves_existing_rows(self, tmp_path):
        """老库（无 delist_date/delist_source 列）→ init 幂等 ALTER 补列；旧数据保留、新列 NULL。"""
        db = tmp_path / "legacy_snap.db"
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "CREATE TABLE security_master ("
                "code TEXT PRIMARY KEY, name TEXT, list_date TEXT, updated_at TEXT)"
            )
            conn.execute(
                "INSERT INTO security_master (code, name, list_date, updated_at) "
                "VALUES ('600519', '贵州茅台', '2001-08-27', '2026-10-01T15:30:00')"
            )
            conn.commit()
        finally:
            conn.close()

        init_snapshot_db(db)  # 幂等迁移
        init_snapshot_db(db)  # 再跑一次仍幂等

        master = get_security_master(db)
        assert master["600519"] == {
            "name": "贵州茅台",
            "list_date": "2001-08-27",
            "delist_date": None,
            "delist_source": None,
        }

    def test_upsert_security_master_coalesces_delist_fields(self, tmp_path):
        """写入 delist_date/delist_source；后续缺该字段的部分拉取不得清空已知值（COALESCE 保值）。"""
        db = tmp_path / "snap.db"
        upsert_security_master(
            [
                {
                    "code": "600001",
                    "name": "邯郸钢铁",
                    "list_date": "1998-01-01",
                    "delist_date": "2009-12-29",
                    "delist_source": "sh_suspend",
                }
            ],
            db_path=db,
        )
        # 部分拉取：只带 name（非周一/名单刷新缺字段场景）
        upsert_security_master([{"code": "600001", "name": "邯郸钢铁"}], db_path=db)

        assert get_security_master(db)["600001"] == {
            "name": "邯郸钢铁",
            "list_date": "1998-01-01",
            "delist_date": "2009-12-29",
            "delist_source": "sh_suspend",
        }

    def test_upsert_delisted_masters_persists_source_for_audit(self, tmp_path):
        """F2：``source`` 落库为 ``delist_source``（消费方据此区分沪「暂停上市日期」语义）。"""
        db = tmp_path / "snap.db"
        upsert_delisted_masters(
            [
                {
                    "code": "600001",
                    "name": "邯郸钢铁",
                    "list_date": "1998-01-01",
                    "delist_date": "2009-12-29",
                    "source": "sh_suspend",
                },
                {
                    "code": "000001",
                    "name": "深市退市",
                    "list_date": "1995-01-01",
                    "delist_date": "2005-01-01",
                    "source": "sz_delist",
                },
            ],
            db_path=db,
        )

        master = get_security_master(db)
        assert master["600001"]["delist_source"] == "sh_suspend"
        assert master["000001"]["delist_source"] == "sz_delist"

        # 仅补名字的部分拉取（无 source）→ 不得清空审计来源
        upsert_delisted_masters(
            [
                {
                    "code": "600001",
                    "name": "邯郸钢铁A",
                    "list_date": None,
                    "delist_date": None,
                    "source": None,
                }
            ],
            db_path=db,
        )
        assert get_security_master(db)["600001"]["delist_source"] == "sh_suspend"

    def test_upsert_delisted_masters_idempotent(self, tmp_path):
        """幂等 upsert：重复写同一批不增行；新值覆盖；空 code 跳过。"""
        db = tmp_path / "snap.db"
        rows = [
            {
                "code": "600001",
                "name": "邯郸钢铁",
                "list_date": "1998-01-01",
                "delist_date": "2009-12-29",
                "source": "sh_suspend",
            },
            {
                "code": "",
                "name": "无代码",
                "list_date": None,
                "delist_date": None,
                "source": "sh_suspend",
            },
            {
                "code": "000001",
                "name": "深市退市",
                "list_date": "1995-01-01",
                "delist_date": "2005-01-01",
                "source": "sz_delist",
            },
        ]

        assert upsert_delisted_masters(rows, db_path=db) == 2
        assert upsert_delisted_masters(rows, db_path=db) == 2

        master = get_security_master(db)
        assert set(master) == {"600001", "000001"}
        assert master["000001"]["delist_date"] == "2005-01-01"

        # 重抓后日期修订（如交易所更正）→ 覆盖为新值
        upsert_delisted_masters(
            [
                {
                    "code": "600001",
                    "name": "邯郸钢铁",
                    "list_date": "1998-01-01",
                    "delist_date": "2009-12-30",
                    "source": "sh_suspend",
                }
            ],
            db_path=db,
        )
        assert get_security_master(db)["600001"]["delist_date"] == "2009-12-30"

    def test_upsert_delisted_masters_empty_is_noop(self, tmp_path):
        assert upsert_delisted_masters([], db_path=tmp_path / "snap.db") == 0

    def test_count_delisted_masters(self, tmp_path):
        """门禁判据 helper：``delist_date`` 非空行计数（空库 0）。"""
        db = tmp_path / "snap.db"
        assert count_delisted_masters(db) == 0

        upsert_security_master(
            [{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}], db_path=db
        )
        assert count_delisted_masters(db) == 0  # 存续标的（delist_date NULL）不计

        upsert_delisted_masters(
            [
                {
                    "code": "600001",
                    "name": "邯郸钢铁",
                    "list_date": "1998-01-01",
                    "delist_date": "2009-12-29",
                    "source": "sh_suspend",
                },
                {
                    "code": "600002",
                    "name": "日期缺失不计数",
                    "list_date": "2000-01-01",
                    "delist_date": None,
                    "source": "sh_suspend",
                },
            ],
            db_path=db,
        )
        assert count_delisted_masters(db) == 1
