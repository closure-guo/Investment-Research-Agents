"""TDD：含退市标的的池可见性（delta add-microcap-lane-guardrails T3，防幸存者偏差回归）。

口径（docs/evals/metrics.md §1.16，binding）：

- 对时点 ``t``，标的可见当且仅当 ``list_date ≤ t ≤ delist_date``（含边界）；
- ``delist_date`` 缺失 → 按「未退市」处理；``list_date`` 缺失/不可解析 → 下界不约束
  （不因缺数据静默剔除）；
- 退市后即使有残留行情行（补采越界/数据源漂移），也不得进池 → 本回归在
  **可见性逻辑移除**时必红（t<退市日 含退市标的、t>退市日 不含）。

零网络：tmp 快照库经 ``write_snapshot_rows`` 造行，``read_universe`` 读出。
"""

from __future__ import annotations

import pytest

from finance_agent.data.snapshot import (
    SnapshotError,
    get_security_master,
    read_universe,
    upsert_security_master,
    write_snapshot_rows,
)
from finance_agent.data.universe import (
    filter_snapshot_rows_for_date,
    universe_visible,
)

_DELISTED = "600001"  # 邯郸钢铁：1998-01-01 上市，2020-06-30 退市
_LIVE = "600519"  # 贵州茅台：存续标的
_DELIST_DATE = "2020-06-30"
_BEFORE = "2020-06-29"  # t < 退市日
_AFTER = "2021-01-04"  # t > 退市日


def _row(code: str, *, name: str = "") -> dict:
    """最小快照行（只填主键与必要字段；可见性只吃 code 与主表窗口）。"""
    return {
        "code": code,
        "name": name,
        "close_raw": 10.0,
        "close_qfq": 10.0,
        "volume": 100.0,
        "amount": 1_000_000.0,
    }


def _seed_master(db) -> None:
    upsert_security_master(
        [
            {
                "code": _DELISTED,
                "name": "退市样本",
                "list_date": "1998-01-01",
                "delist_date": _DELIST_DATE,
            },
            {"code": _LIVE, "name": "存续样本", "list_date": "2001-08-27"},
        ],
        db_path=db,
    )


class TestSurvivorshipBiasRegression:
    """核心回归：含退市标的的池（快照行 + 主表窗口）。"""

    def test_pool_before_delist_date_contains_delisted_stock(self, tmp_path):
        """t < 退市日：退市标的在池中（无此逻辑时被静默剔除 = 幸存者偏差）。"""
        db = tmp_path / "snap.db"
        _seed_master(db)
        # 退市前一日：退市标的仍有行情行（历史补采产物）
        write_snapshot_rows(_BEFORE, [_row(_DELISTED, name="退市样本"), _row(_LIVE)], db_path=db)

        rows = read_universe(_BEFORE, db_path=db)
        master = get_security_master(db)

        pool = filter_snapshot_rows_for_date(rows, master, _BEFORE)

        assert [row["code"] for row in pool] == [_DELISTED, _LIVE]

    def test_pool_after_delist_date_excludes_delisted_stock(self, tmp_path):
        """t > 退市日：退市标的不在池中（即使当日残留其行情行——补采越界/源漂移）。"""
        db = tmp_path / "snap.db"
        _seed_master(db)
        write_snapshot_rows(_AFTER, [_row(_DELISTED), _row(_LIVE)], db_path=db)

        rows = read_universe(_AFTER, db_path=db)
        master = get_security_master(db)

        pool = filter_snapshot_rows_for_date(rows, master, _AFTER)

        assert [row["code"] for row in pool] == [_LIVE]

    def test_delist_date_boundary_is_inclusive(self, tmp_path):
        """边界日 t == 退市日仍可见（口径「≤」，含当日）。"""
        db = tmp_path / "snap.db"
        _seed_master(db)
        write_snapshot_rows(_DELIST_DATE, [_row(_DELISTED)], db_path=db)

        rows = read_universe(_DELIST_DATE, db_path=db)

        pool = filter_snapshot_rows_for_date(rows, get_security_master(db), _DELIST_DATE)

        assert [row["code"] for row in pool] == [_DELISTED]


class TestUniverseVisible:
    def test_missing_delist_date_means_live(self):
        """delist_date 缺失 = 未退市：远未来时点仍可见。"""
        assert universe_visible({"code": "600519", "list_date": "2001-08-27"}, "2099-01-01")

    def test_list_date_boundary_inclusive(self):
        row = {"code": "600519", "list_date": "2001-08-27", "delist_date": None}
        assert universe_visible(row, "2001-08-27") is True
        assert universe_visible(row, "2001-08-26") is False

    def test_missing_list_date_does_not_drop_row(self):
        """上市日缺失/不可解析 → 下界不约束（不因缺数据静默剔除）。"""
        assert universe_visible({"code": "600001", "list_date": None}, "2020-01-01")
        assert universe_visible({"code": "600001", "list_date": "2020/01/01"}, "2020-01-01")

    def test_unparseable_delist_date_treated_as_missing(self):
        """退市日不可解析 → 视为缺失（「未退市」上界不约束）：缺失不编造、不静默剔除。

        与 ``list_date`` 同纪律（下界同理）；调用方对已知退市但日期缺失的标的按
        「如实标注缺失」披露（metrics.md §1.16）。
        """
        assert (
            universe_visible(
                {"code": "600001", "list_date": "1998-01-01", "delist_date": "2020/06/30"},
                "2021-01-04",
            )
            is True
        )

    def test_unknown_master_row_is_visible(self):
        """主表无该 code → 无退市信息，不静默剔除（保留并在上游如实标注）。"""
        assert universe_visible(None, "2026-10-18") is True

    def test_non_canonical_t_rejected(self):
        with pytest.raises(SnapshotError):
            universe_visible({"code": "600519"}, "2026-1-4")


class TestFilterSnapshotRowsForDate:
    def test_rows_without_code_are_kept(self):
        """行 code 缺失 → 不可判退市，保留（不静默剔除）。"""
        pool = filter_snapshot_rows_for_date([{"code": ""}, {"code": None}], {}, "2026-10-18")
        assert len(pool) == 2
