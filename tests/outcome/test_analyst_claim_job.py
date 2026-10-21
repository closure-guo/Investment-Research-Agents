"""事后检验日批 job 测试（add-analyst-ex-post-verification）。

覆盖：生成→结算→记分卡→运行记录全链、部分失败 degraded、战绩表零写入。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from finance_agent.data import analyst_claims_store as claims_store
from finance_agent.data import research_reports_store as rr_store
from finance_agent.outcome.analyst_claims import job


def _calendar(start: str, count: int) -> list[str]:
    current = date.fromisoformat(start)
    days: list[str] = []
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _seed(db):
    rr_store.upsert_rating_changes(
        [
            {
                "code": "600519",
                "name": "贵州茅台",
                "publish_date": "2026-10-20",
                "org": "中信证券",
                "analyst": "张三",
                "rating": "买入",
                "is_first_rating": False,
                "rating_change": "维持",
                "prev_rating": "增持",
                "target_low": 120.0,
                "target_high": 120.0,
                "source": "cninfo",
            }
        ],
        db_path=db,
    )
    rr_store.upsert_reports(
        [
            {
                "report_type": "stock",
                "title": "贵州茅台三季报点评",
                "org": "中信证券",
                "analyst": "张三",
                "publish_date": "2026-10-20",
                "code": "600519",
                "name": "贵州茅台",
                "industry": "白酒",
                "rating": "买入",
                "eps_forecasts": {"2026": 60.0},
                "source": "eastmoney",
            }
        ],
        db_path=db,
    )


def _bar(**over):
    row = {"open": 10.0, "close_qfq": 10.0, "close_raw": 10.0, "is_suspended": 0}
    row.update(over)
    return row


def test_job_full_chain(tmp_path):
    db = tmp_path / "rr.db"
    _seed(db)
    calendar = _calendar("2026-10-21", 22)
    prices = {
        calendar[0]: _bar(),
        calendar[20]: _bar(close_qfq=11.0),
    }
    result = job.run_analyst_claim_settle_daily(
        as_of=calendar[-1],
        db_path=db,
        calendar=calendar,
        stock_prices=lambda code, d: prices.get(d),
        benchmark_closes={calendar[0]: 100.0, calendar[20]: 102.0},
    )
    assert result["status"] == "ok"
    assert result["generated"]["total"] >= 1
    assert result["settled"] == 1
    rows = claims_store.list_scorecard(db_path=db)
    assert {r["bucket_type"] for r in rows} == {
        "org",
        "rating",
        "claim_type",
        "industry",
        "depth",
    }
    run = rr_store.latest_run("analyst_claim_settle_daily", db_path=db)
    assert run["status"] == "ok"
    assert run["rows"] == 1


def test_job_degraded_on_settlement_error(tmp_path):
    db = tmp_path / "rr.db"
    _seed(db)
    calendar = _calendar("2026-10-21", 22)

    def broken(code, d):
        if d == calendar[0]:
            raise RuntimeError("snapshot locked")
        return _bar(close_qfq=11.0)

    result = job.run_analyst_claim_settle_daily(
        as_of=calendar[-1],
        db_path=db,
        calendar=calendar,
        stock_prices=broken,
        benchmark_closes={calendar[0]: 100.0, calendar[20]: 102.0},
    )
    assert result["status"] == "degraded"
    assert result["errors"] == 1
    run = rr_store.latest_run("analyst_claim_settle_daily", db_path=db)
    assert run["status"] == "degraded"


def test_job_does_not_write_predictions(tmp_path):
    db = tmp_path / "rr.db"
    _seed(db)
    calendar = _calendar("2026-10-21", 22)
    before = (
        sqlite3.connect(db)
        .execute("SELECT COUNT(*) FROM sqlite_master WHERE name='predictions'")
        .fetchone()[0]
    )
    job.run_analyst_claim_settle_daily(
        as_of=calendar[-1],
        db_path=db,
        calendar=calendar,
        stock_prices=lambda code, d: _bar(close_qfq=11.0),
        benchmark_closes={calendar[0]: 100.0, calendar[20]: 102.0},
    )
    after = (
        sqlite3.connect(db)
        .execute("SELECT COUNT(*) FROM sqlite_master WHERE name='predictions'")
        .fetchone()[0]
    )
    assert before == after == 0
