"""事件式主张抽取测试（add-analyst-ex-post-verification）。

覆盖：评级方向推导、首次评级、EPS 修正、目标价变动边界、幂等。
"""

from __future__ import annotations

import sqlite3

from finance_agent.data import research_reports_store as rr_store
from finance_agent.outcome.analyst_claims import extract


def _rating(**over):
    row = {
        "code": "600519",
        "name": "贵州茅台",
        "publish_date": "2026-10-20",
        "org": "中信证券",
        "analyst": "张三",
        "rating": "买入",
        "is_first_rating": False,
        "rating_change": "维持",
        "prev_rating": "增持",
        "target_low": None,
        "target_high": None,
        "source": "cninfo",
    }
    row.update(over)
    return row


def _report(**over):
    row = {
        "report_type": "stock",
        "title": "贵州茅台三季报点评",
        "org": "中信证券",
        "analyst": "张三",
        "publish_date": "2026-10-20",
        "code": "600519",
        "name": "贵州茅台",
        "industry": "白酒",
        "rating": "买入",
        "eps_forecasts": {"2026": 60.0, "2027": 65.0},
        "pe_forecasts": {"2026": 25.0},
        "source": "eastmoney",
    }
    row.update(over)
    return row


def _claims(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM analyst_claims")]
    finally:
        conn.close()


class TestRatingClaims:
    def test_upgrade(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_rating_changes([_rating(prev_rating="增持", rating="买入")], db_path=db)
        result = extract.generate_claims(db_path=db)
        rows = _claims(db)
        assert result["rating"] == 1
        assert rows[0]["claim_type"] == "rating_upgrade"
        assert rows[0]["prev_rating"] == "增持"
        assert rows[0]["rating"] == "买入"
        assert rows[0]["horizon_days"] == 20

    def test_downgrade(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_rating_changes([_rating(prev_rating="买入", rating="增持")], db_path=db)
        extract.generate_claims(db_path=db)
        assert _claims(db)[0]["claim_type"] == "rating_downgrade"

    def test_maintain_is_control_group(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_rating_changes([_rating(prev_rating="买入", rating="买入")], db_path=db)
        extract.generate_claims(db_path=db)
        assert _claims(db)[0]["claim_type"] == "rating_maintain"

    def test_initiation_does_not_fabricate_prev(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_rating_changes(
            [_rating(is_first_rating=True, prev_rating=None, rating="增持")], db_path=db
        )
        extract.generate_claims(db_path=db)
        row = _claims(db)[0]
        assert row["claim_type"] == "rating_initiation"
        assert row["prev_rating"] is None

    def test_unknown_rating_skipped(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_rating_changes([_rating(prev_rating="未知", rating="买入")], db_path=db)
        result = extract.generate_claims(db_path=db)
        assert result["rating"] == 0
        assert _claims(db) == []

    def test_generation_idempotent(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_rating_changes([_rating()], db_path=db)
        extract.generate_claims(db_path=db)
        extract.generate_claims(db_path=db)
        assert len(_claims(db)) == 1


class TestEpsClaims:
    def test_revision_up(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_reports(
            [
                _report(publish_date="2026-09-01", eps_forecasts={"2026": 58.0}),
                _report(
                    publish_date="2026-10-20",
                    title="贵州茅台三季报点评（上调）",
                    eps_forecasts={"2026": 60.0},
                ),
            ],
            db_path=db,
        )
        result = extract.generate_claims(db_path=db)
        rows = [r for r in _claims(db) if r["claim_type"].startswith("eps_")]
        assert result["eps"] == 1
        assert rows[0]["claim_type"] == "eps_revision_up"
        assert rows[0]["eps_year"] == 2026
        assert rows[0]["eps_value"] == 60.0
        assert rows[0]["prev_eps_value"] == 58.0
        assert rows[0]["horizon_days"] == 60

    def test_revision_down(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_reports(
            [
                _report(publish_date="2026-09-01", eps_forecasts={"2026": 60.0}),
                _report(
                    publish_date="2026-10-20",
                    title="贵州茅台三季报点评（下调）",
                    eps_forecasts={"2026": 58.0},
                ),
            ],
            db_path=db,
        )
        extract.generate_claims(db_path=db)
        assert _claims(db)[0]["claim_type"] == "eps_revision_down"

    def test_equal_eps_no_claim(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_reports(
            [
                _report(publish_date="2026-09-01", eps_forecasts={"2026": 60.0}),
                _report(
                    publish_date="2026-10-20",
                    title="贵州茅台三季报点评（维持）",
                    eps_forecasts={"2026": 60.0},
                ),
            ],
            db_path=db,
        )
        extract.generate_claims(db_path=db)
        assert _claims(db) == []

    def test_first_coverage_no_eps_claim(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_reports([_report(eps_forecasts={"2026": 60.0})], db_path=db)
        extract.generate_claims(db_path=db)
        assert _claims(db) == []

    def test_eps_inherits_depth(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_reports(
            [
                _report(
                    publish_date="2026-09-01",
                    eps_forecasts={"2026": 58.0},
                    depth_class="deep",
                ),
                _report(
                    publish_date="2026-10-20",
                    title="贵州茅台深度报告（上调）",
                    eps_forecasts={"2026": 60.0},
                    depth_class="deep",
                ),
            ],
            db_path=db,
        )
        extract.generate_claims(db_path=db)
        eps_rows = [r for r in _claims(db) if r["claim_type"].startswith("eps_")]
        assert eps_rows[0]["depth_class"] == "deep"
        assert eps_rows[0]["region"] == "domestic"


class TestTargetClaims:
    def test_target_up_requires_previous_target(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_rating_changes(
            [
                _rating(publish_date="2026-09-01", target_low=100.0, target_high=100.0),
                _rating(
                    publish_date="2026-10-20",
                    prev_rating="买入",
                    rating="买入",
                    target_low=120.0,
                    target_high=120.0,
                ),
            ],
            db_path=db,
        )
        extract.generate_claims(db_path=db)
        target_rows = [r for r in _claims(db) if r["claim_type"].startswith("target_")]
        assert len(target_rows) == 1
        assert target_rows[0]["claim_type"] == "target_price_up"
        assert target_rows[0]["target_price"] == 120.0
        assert target_rows[0]["prev_target_price"] == 100.0

    def test_target_without_previous_is_skipped(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_rating_changes([_rating(target_low=120.0, target_high=120.0)], db_path=db)
        extract.generate_claims(db_path=db)
        assert [r for r in _claims(db) if r["claim_type"].startswith("target_")] == []

    def test_target_midpoint_of_range(self, tmp_path):
        db = tmp_path / "rr.db"
        rr_store.upsert_rating_changes(
            [
                _rating(publish_date="2026-09-01", target_low=90.0, target_high=110.0),
                _rating(
                    publish_date="2026-10-20",
                    target_low=100.0,
                    target_high=140.0,
                ),
            ],
            db_path=db,
        )
        extract.generate_claims(db_path=db)
        target_rows = [r for r in _claims(db) if r["claim_type"].startswith("target_")]
        assert target_rows[0]["target_price"] == 120.0
        assert target_rows[0]["prev_target_price"] == 100.0
