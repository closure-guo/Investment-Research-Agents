"""行业门类回填脚本单测（extend-factor-library-coverage §9，零网络：stub client）。

覆盖：(a) 缺省标的池 = ``industry_l1`` 为空的在册代码（已有值不重取）；
(b) 只写非空行业、幂等；(c) 探针成功率门禁（低于阈值整批中止、不写库）；
(d) 单只取数异常只计数不中断；(e) 主表为空且未显式指定标的 → 退 2。
"""

from __future__ import annotations

import pytest
from scripts.backfill_industry import main, run_backfill

from finance_agent.data.snapshot import (
    get_security_master,
    init_snapshot_db,
    update_security_master_industry,
    upsert_security_master,
)

_CODES = ["600001", "600002", "600003", "600004", "600005"]


class FakeIndustryClient:
    """只实现 ``fetch_industry`` 的 stub 客户端（零网络）。"""

    def __init__(self, *, data=None, fail=()):
        self.data = data or {}
        self.fail = set(fail)
        self.calls: list[str] = []

    def fetch_industry(self, code: str) -> dict:
        self.calls.append(code)
        if code in self.fail:
            raise RuntimeError("cninfo down")
        return self.data.get(code, {})


def _seed_db(tmp_path):
    db = tmp_path / "snap.db"
    init_snapshot_db(db)
    upsert_security_master(
        [{"code": code, "name": code, "list_date": "2020-01-02"} for code in _CODES]
        + [{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}],
        db_path=db,
    )
    update_security_master_industry({"600519": "食品饮料"}, db_path=db)
    return db


def test_backfill_writes_missing_only_and_idempotent(tmp_path):
    db = _seed_db(tmp_path)
    client = FakeIndustryClient(data={code: {"industry": "电子"} for code in _CODES})
    summary = run_backfill(db_path=db, client=client)
    assert summary["attempted"] == len(_CODES)
    assert summary["found"] == len(_CODES)
    assert summary["written"] == len(_CODES)
    assert "600519" not in client.calls  # 已有行业值不重取
    master = get_security_master(db_path=db)
    assert master["600001"]["industry_l1"] == "电子"
    assert master["600519"]["industry_l1"] == "食品饮料"  # 既有值不被覆盖
    # 重跑：已无缺行业代码 → attempted 0
    rerun = run_backfill(db_path=db, client=FakeIndustryClient())
    assert rerun["attempted"] == 0
    assert rerun["written"] == 0


def test_only_non_empty_industry_written(tmp_path):
    db = _seed_db(tmp_path)
    client = FakeIndustryClient(
        data={
            "600001": {"industry": "电子"},
            "600002": {"industry": ""},
            "600003": {},
            "600004": {"name": "无行业字段"},
        }
    )
    summary = run_backfill(db_path=db, client=client)
    assert summary["found"] == 1
    assert summary["written"] == 1
    master = get_security_master(db_path=db)
    assert master["600001"]["industry_l1"] == "电子"
    assert master["600002"]["industry_l1"] is None


def test_probe_below_threshold_aborts_without_write(tmp_path):
    db = _seed_db(tmp_path)
    client = FakeIndustryClient(
        data={"600001": {"industry": "电子"}, "600002": {"industry": "银行"}}
    )
    summary = run_backfill(db_path=db, probe=5, min_success_rate=0.9, client=client)
    assert summary["aborted"] is True
    assert summary["probe_success_rate"] == pytest.approx(2 / 5)
    assert summary["written"] == 0
    master = get_security_master(db_path=db)
    assert all(master[code]["industry_l1"] is None for code in _CODES)


def test_probe_pass_then_full_backfill(tmp_path):
    db = _seed_db(tmp_path)
    client = FakeIndustryClient(data={code: {"industry": "电子"} for code in _CODES})
    summary = run_backfill(db_path=db, probe=2, client=client)
    assert summary["aborted"] is False
    assert summary["probe_success_rate"] == pytest.approx(1.0)
    assert summary["written"] == len(_CODES)


def test_lookup_exception_counted_not_fatal(tmp_path):
    db = _seed_db(tmp_path)
    client = FakeIndustryClient(
        data={code: {"industry": "电子"} for code in _CODES},
        fail=("600001",),
    )
    summary = run_backfill(db_path=db, client=client)
    assert summary["failed"] == 1
    assert summary["written"] == len(_CODES) - 1
    master = get_security_master(db_path=db)
    assert master["600001"]["industry_l1"] is None


def test_empty_master_without_symbols_exits_2(tmp_path):
    db = tmp_path / "empty.db"
    init_snapshot_db(db)
    assert main(["--db", str(db)]) == 2
