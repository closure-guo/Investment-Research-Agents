"""记分卡与机构权重测试（add-analyst-ex-post-verification）。

覆盖：四维分桶、样本不足标记、权重公式夹取、梯队先验、快照持久化。
"""

from __future__ import annotations

from finance_agent.data import analyst_claims_store as store
from finance_agent.outcome.analyst_claims import scorecard


def _seed(
    db,
    key: str,
    *,
    org="中信证券",
    rating="买入",
    claim_type="rating_upgrade",
    industry="白酒",
    excess=0.05,
    depth=None,
):
    store.upsert_claims(
        [
            {
                "claim_key": key,
                "claim_type": claim_type,
                "code": "600519",
                "org": org,
                "published_at": "2026-09-01",
                "rating": rating,
                "horizon_days": 20,
                "industry": industry,
                "region": "domestic",
                "depth_class": depth,
                "source": "cninfo",
            }
        ],
        db_path=db,
    )
    store.upsert_outcome(
        key,
        status="resolved",
        entry_date="2026-09-02",
        entry_price=10.0,
        exit_date="2026-09-30",
        exit_price=10.0 * (1 + excess),
        raw_return=excess,
        benchmark_return=0.0,
        excess_return=excess,
        reason=None,
        db_path=db,
    )


def test_build_scorecard_buckets_and_stats(tmp_path):
    db = tmp_path / "rr.db"
    _seed(db, "a", excess=0.10)
    _seed(db, "b", excess=-0.05)
    rows = scorecard.build_scorecard("2026-10-20", db_path=db)
    org = [r for r in rows if r["bucket_type"] == "org"][0]
    assert org["bucket_key"] == "中信证券"
    assert org["n"] == 2
    assert org["wins"] == 1
    assert abs(org["win_rate"] - 0.5) < 1e-9
    assert abs(org["mean_excess"] - 0.025) < 1e-9
    assert abs(org["median_excess"] - 0.025) < 1e-9
    assert org["reliable"] is False


def test_small_sample_marked_unreliable(tmp_path):
    db = tmp_path / "rr.db"
    _seed(db, "a")
    rows = scorecard.build_scorecard("2026-10-20", db_path=db)
    assert all(r["reliable"] is False for r in rows)


def test_thirty_samples_reliable(tmp_path):
    db = tmp_path / "rr.db"
    for i in range(30):
        _seed(db, f"k{i}", excess=0.01)
    rows = scorecard.build_scorecard("2026-10-20", db_path=db)
    org = [r for r in rows if r["bucket_type"] == "org"][0]
    assert org["n"] == 30
    assert org["reliable"] is True


def test_weight_formula_clamped(tmp_path):
    assert scorecard.weight_for(None) is None
    assert scorecard.weight_for(-0.50) == 0.5
    assert scorecard.weight_for(0.0) == 0.5
    assert abs(scorecard.weight_for(0.025) - 0.75) < 1e-9
    assert scorecard.weight_for(1.00) == 1.5


def test_tier_lookup(tmp_path):
    tiers = {"1": ["中信证券"], "2": ["中金公司"]}
    assert scorecard.tier_for("中信证券", tiers) == "1"
    assert scorecard.tier_for("中金公司", tiers) == "2"
    assert scorecard.tier_for("未知机构", tiers) == "unrated"


def test_load_tiers_from_file(tmp_path):
    path = tmp_path / "tiers.json"
    path.write_text('{"tiers": {"1": ["中信证券"], "2": ["中金公司"]}}', encoding="utf-8")
    tiers = scorecard.load_tiers(path)
    assert tiers["中信证券"] == "1"
    assert scorecard.tier_for("中信证券", tiers) == "1"


def test_persist_and_query(tmp_path):
    db = tmp_path / "rr.db"
    _seed(db, "a", excess=0.10)
    rows = scorecard.build_scorecard("2026-10-20", db_path=db)
    scorecard.persist_scorecard("2026-10-20", rows, db_path=db)
    got = store.list_scorecard(snapshot_date="2026-10-20", bucket_type="org", db_path=db)
    assert len(got) == 1
    assert got[0]["bucket_key"] == "中信证券"
    assert got[0]["tier"] in ("1", "2", "unrated")


def test_depth_bucket_and_weighted_mean(tmp_path):
    db = tmp_path / "rr.db"
    _seed(db, "deep", excess=0.10, depth="deep")
    _seed(db, "comment", excess=-0.10, depth="comment")
    rows = scorecard.build_scorecard("2026-10-20", db_path=db)
    depth_rows = {r["bucket_key"]: r for r in rows if r["bucket_type"] == "depth"}
    assert depth_rows["deep"]["n"] == 1
    assert depth_rows["comment"]["n"] == 1
    org = [r for r in rows if r["bucket_type"] == "org"][0]
    assert abs(org["mean_excess"]) < 1e-9
    assert abs(org["weighted_mean_excess"] - 0.025) < 1e-9
    assert org["deep_n"] == 1
    assert org["region"] == "domestic"
    assert org["score_family"] == "event_study"
