"""研报触发关联分析测试（add-report-triggered-analysis）。

覆盖：范围/时效过滤、单篇与日预算上限、幂等、当日去重、投递失败、默认关闭。
"""

from __future__ import annotations

from finance_agent.data import research_reports_store as rr_store
from finance_agent.screening import pool_analysis
from finance_agent.screening import report_trigger as rt


def _doc(db, key, stocks, *, report_date="2026-10-20", report_type="个股研报"):
    rr_store.upsert_extraction(
        key,
        {
            "broker": "中信证券",
            "report_date": report_date,
            "report_type": report_type,
            "stocks": stocks,
            "core_logic": [],
            "catalysts": [],
            "risks": [],
            "summary": "s",
        },
        db_path=db,
    )


def _stock(code):
    return {"code": code, "name": code}


def _prepare(tmp_path, **over):
    kwargs = {
        "research_db_path": tmp_path / "rr.db",
        "screening_db_path": tmp_path / "screening.db",
        "execution_db_path": tmp_path / "execution.db",
        "events_db_path": tmp_path / "events.db",
        "as_of": "2026-10-20",
    }
    kwargs.update(over)
    return rt.prepare_report_analysis_batch(**kwargs)


class TestFilters:
    def test_dry_run_filters_scope_and_stale(self, tmp_path):
        db = tmp_path / "rr.db"
        _doc(db, "d1", [_stock("600519")])
        _doc(db, "d2", [_stock("300750")])
        _doc(db, "d3", [_stock("600519")], report_date="2026-09-01")
        _doc(db, "d4", [])
        result = _prepare(
            tmp_path,
            doc_keys=["d1", "d2", "d3", "d4"],
            dry_run=True,
            scope_symbols={"600519"},
            max_age_days=7,
        )
        assert result["status"] == "dry_run"
        assert result["would_trigger"] == [{"doc_key": "d1", "symbol": "600519"}]
        assert result["skipped"]["stale"] == 1
        assert result["skipped"]["out_of_scope"] == 1
        assert result["triggered"] == 0

    def test_strategy_report_without_stocks_triggers_nothing(self, tmp_path):
        db = tmp_path / "rr.db"
        _doc(db, "s1", [], report_type="策略报告")
        result = _prepare(tmp_path, doc_keys=["s1"], dry_run=True, scope_symbols={"600519"})
        assert result["candidates"] == 0
        assert result["triggered"] == 0
        assert result["would_trigger"] == []


class TestCapsAndIdempotency:
    def test_per_report_cap(self, tmp_path):
        db = tmp_path / "rr.db"
        _doc(db, "d1", [_stock(str(600000 + i)) for i in range(5)])
        calls = []
        result = _prepare(
            tmp_path,
            doc_keys=["d1"],
            scope_symbols={str(600000 + i) for i in range(5)},
            max_symbols=3,
            enqueue=lambda symbol, sid: calls.append(symbol) or True,
        )
        assert result["triggered"] == 3
        assert result["skipped"]["per_report_cap"] == 2
        assert len(calls) == 3

    def test_idempotent_same_doc_symbol(self, tmp_path):
        db = tmp_path / "rr.db"
        _doc(db, "d1", [_stock("600519")])
        calls = []
        first = _prepare(
            tmp_path,
            doc_keys=["d1"],
            scope_symbols={"600519"},
            enqueue=lambda symbol, sid: calls.append((symbol, sid)) or True,
        )
        second = _prepare(
            tmp_path,
            doc_keys=["d1"],
            scope_symbols={"600519"},
            enqueue=lambda symbol, sid: calls.append((symbol, sid)) or True,
        )
        assert first["triggered"] == 1
        assert second["skipped"]["duplicate"] == 1
        assert len(calls) == 1

    def test_daily_cap_shared_with_pool_analysis(self, tmp_path):
        db = tmp_path / "rr.db"
        _doc(db, "d1", [_stock("600519")])
        screening_db = tmp_path / "screening.db"
        for i in range(3):
            pool_analysis.record_pool_analysis_run(
                "2026-10-20", f"60000{i}", f"s{i}", "queued", None, db_path=screening_db
            )
        result = _prepare(
            tmp_path,
            doc_keys=["d1"],
            scope_symbols={"600519"},
            daily_max=3,
            enqueue=lambda symbol, sid: True,
        )
        assert result["triggered"] == 0
        assert result["skipped"]["daily_cap"] == 1

    def test_already_analyzed_today_skips(self, tmp_path):
        db = tmp_path / "rr.db"
        _doc(db, "d1", [_stock("600519")])
        screening_db = tmp_path / "screening.db"
        pool_analysis.record_pool_analysis_run(
            "2026-10-20", "600519", "s0", "queued", None, db_path=screening_db
        )
        result = _prepare(
            tmp_path,
            doc_keys=["d1"],
            scope_symbols={"600519"},
            enqueue=lambda symbol, sid: True,
        )
        assert result["triggered"] == 0
        assert result["skipped"]["already_analyzed_today"] == 1

    def test_dispatch_failure_recorded(self, tmp_path):
        db = tmp_path / "rr.db"
        _doc(db, "d1", [_stock("600519")])
        result = _prepare(
            tmp_path,
            doc_keys=["d1"],
            scope_symbols={"600519"},
            enqueue=lambda symbol, sid: False,
        )
        assert result["triggered"] == 1
        assert result["failed"] == 1
        run = rt.get_run("d1", "600519", db_path=tmp_path / "screening.db")
        assert run["status"] == "dispatch_failed"


class TestDisabled:
    def test_disabled_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPORT_TRIGGER_ENABLED", "0")
        db = tmp_path / "rr.db"
        _doc(db, "d1", [_stock("600519")])
        result = rt.run_report_trigger_daily(
            research_db_path=db,
            screening_db_path=tmp_path / "screening.db",
            scope_symbols={"600519"},
            enqueue=lambda symbol, sid: True,
        )
        assert result["status"] == "disabled"
        assert rt.get_run("d1", "600519", db_path=tmp_path / "screening.db") is None
