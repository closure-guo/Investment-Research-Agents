"""策略/宏观研报观点聚合测试（add-macro-strategy-report-context）。

覆盖：30 天窗口、最多 3 篇、行业方向聚合与分歧、单机构标注、空态/陈旧。
"""

from __future__ import annotations

from finance_agent.data import research_reports as rr
from finance_agent.data import research_reports_store as store


def _extraction(**over):
    row = {
        "broker": "UBS",
        "analyst": "James",
        "report_date": "2026-10-19",
        "report_type": "策略报告",
        "stocks": [],
        "core_logic": ["外资情绪谨慎"],
        "catalysts": ["政策响应"],
        "risks": ["地产硬着陆"],
        "summary": "外资谨慎，偏好银行。",
        "sector_views": [
            {"industry": "银行", "direction": "看多", "rationale": "净息差企稳"},
            {"industry": "电池", "direction": "看空", "rationale": "价格战"},
        ],
        "market_view": {"sentiment": "谨慎", "positioning": "仓位较 6 月减轻"},
        "sources": [],
    }
    row.update(over)
    return row


class TestStrategyDigest:
    def _seed(self, db, key, **over):
        store.upsert_extraction(key, _extraction(**over), db_path=db)

    def test_selects_recent_strategy_and_macro_only(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, "a", report_date="2026-10-19")
        self._seed(db, "b", report_date="2026-10-18", report_type="宏观研究", broker="中信证券")
        self._seed(db, "c", report_date="2026-10-17", broker="华泰证券")
        self._seed(db, "d", report_date="2026-10-16", broker="国泰海通")
        self._seed(db, "old", report_date="2026-09-01")
        self._seed(db, "stock", report_date="2026-10-20", report_type="个股研报")
        digest = rr.get_strategy_digest(as_of="2026-10-20", db_path=db)
        assert digest is not None
        assert digest["report_count"] == 3
        assert digest["latest_report_date"] == "2026-10-19"
        assert set(digest["brokers"]) == {"UBS", "中信证券", "华泰证券"}

    def test_sector_aggregation_and_disagreement(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, "a", report_date="2026-10-19")
        self._seed(
            db,
            "b",
            report_date="2026-10-18",
            broker="中信证券",
            sector_views=[
                {"industry": "银行", "direction": "看空", "rationale": "息差压力"},
            ],
        )
        digest = rr.get_strategy_digest(as_of="2026-10-20", db_path=db)
        bank = digest["sector_views"]["银行"]
        assert bank["directions"] == {"看多": 1, "看空": 1}
        assert set(bank["rationales"]) == {"净息差企稳", "息差压力"}
        assert digest["disagreements"] == ["银行"]

    def test_single_broker_flag(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, "a", report_date="2026-10-19")
        self._seed(db, "b", report_date="2026-10-18")
        digest = rr.get_strategy_digest(as_of="2026-10-20", db_path=db)
        assert digest["single_broker"] is True

    def test_stale_returns_none(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, "a", report_date="2026-08-01")
        assert rr.get_strategy_digest(as_of="2026-10-20", db_path=db) is None

    def test_empty_returns_none(self, tmp_path):
        db = tmp_path / "rr.db"
        assert rr.get_strategy_digest(as_of="2026-10-20", db_path=db) is None

    def test_format_contains_anti_anchoring_and_sector(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, "a")
        digest = rr.get_strategy_digest(as_of="2026-10-20", db_path=db)
        text = rr.format_strategy_digest(digest)
        assert "卖方策略观点" in text
        assert "银行" in text
        assert "净息差企稳" in text
        assert "不得直接作为结论依据" in text
