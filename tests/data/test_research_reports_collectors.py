"""研报采集器测试（add-research-report-dataplane）：归一化/分页/限流/日批编排。

全部离线：HTTP 与 akshare 调用均被 monkeypatch，不触网。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from types import SimpleNamespace

import pandas as pd

from finance_agent.data import research_reports as rr
from finance_agent.data import research_reports_store as store


def _stock_raw(**over):
    raw = {
        "title": "宝信软件2026三季报点评：IDC 业务回暖",
        "stockCode": "600845",
        "stockName": "宝信软件",
        "orgSName": "国元证券",
        "researcher": "张三",
        "publishDate": "2026-10-19 00:00:00.000",
        "emRatingName": "买入",
        "industryName": "IT服务Ⅱ",
        "encodeUrl": "abc+/=",
        "infoCode": "AP202610191830663643",
        "predictThisYearEps": 0.57,
        "predictNextYearEps": 0.68,
        "predictNextTwoYearEps": 0.79,
        "predictThisYearPe": 30.0,
        "predictNextYearPe": 25.0,
    }
    raw.update(over)
    return raw


class TestNormalize:
    def test_stock_row(self):
        row = rr.normalize_em_row(_stock_raw(), "stock", current_year=2026)
        assert row["report_type"] == "stock"
        assert row["code"] == "600845"
        assert row["org"] == "国元证券"
        assert row["analyst"] == "张三"
        assert row["publish_date"] == "2026-10-19"
        assert row["rating"] == "买入"
        assert row["eps_forecasts"] == {"2026": 0.57, "2027": 0.68, "2028": 0.79}
        assert row["pe_forecasts"] == {"2026": 30.0, "2027": 25.0}
        assert "AP202610191830663643" in row["pdf_url"]
        assert "encodeUrl=abc%2B%2F%3D" in row["detail_url"]

    def test_industry_row_has_no_stock_fields(self):
        row = rr.normalize_em_row(
            {
                "title": "银行业月报",
                "industryName": "银行Ⅱ",
                "orgSName": "华宝证券",
                "publishDate": "2026-10-19 00:00:00.000",
                "encodeUrl": "zzz",
            },
            "industry",
            current_year=2026,
        )
        assert row["report_type"] == "industry"
        assert row["industry"] == "银行Ⅱ"
        assert row["code"] is None

    def test_strategy_row_detail_url(self):
        row = rr.normalize_em_row(
            {
                "title": "策略周报",
                "orgSName": "华宝证券",
                "publishDate": "2026-10-19 00:00:00.000",
                "encodeUrl": "q",
            },
            "strategy",
            current_year=2026,
        )
        assert row["detail_url"].startswith("https://data.eastmoney.com/report/zw_strategy")

    def test_blank_title_dropped_by_store(self, tmp_path):
        db = tmp_path / "rr.db"
        assert store.upsert_reports([rr.normalize_em_row({}, "stock", 2026)], db_path=db) == 0


class TestPagination:
    def test_paginates_all_pages(self, monkeypatch):
        calls = []

        def fake_get(url, params, **kw):
            calls.append(dict(params))
            page = int(params["pageNo"])
            return {
                "TotalPage": 2,
                "currentYear": 2026,
                "data": [_stock_raw(title=f"报告{page}")],
            }

        monkeypatch.setattr(rr, "_em_get", fake_get)
        rows = rr.fetch_market_reports("stock", date(2026, 10, 17), date(2026, 10, 19))
        assert len(rows) == 2
        assert {c["pageNo"] for c in calls} == {"1", "2"}

    def test_empty_window_returns_empty(self, monkeypatch):
        monkeypatch.setattr(rr, "_em_get", lambda *a, **k: {"TotalPage": 0, "data": []})
        assert rr.fetch_market_reports("strategy", date(2026, 10, 18), date(2026, 10, 19)) == []


class TestRateLimiter:
    def test_enforces_min_interval(self):
        sleeps: list[float] = []
        clock = {"t": 0.0}

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock["t"] += seconds

        limiter = rr.EMRateLimiter(
            min_interval=1.0, jitter=0.0, sleep=fake_sleep, clock=lambda: clock["t"]
        )
        limiter.wait()
        limiter.wait()
        assert sleeps and sleeps[0] >= 0.99


class TestAkshareCollectors:
    def test_rating_changes_mapping(self, monkeypatch):
        df = pd.DataFrame(
            {
                "证券代码": ["600845"],
                "证券简称": ["宝信软件"],
                "发布日期": ["2026-10-16"],
                "研究机构简称": ["国盛证券"],
                "研究员名称": ["李四"],
                "投资评级": ["买入"],
                "是否首次评级": ["不是首次评级"],
                "评级变化": ["维持"],
                "前一次投资评级": ["买入"],
                "目标价格-下限": [None],
                "目标价格-上限": [None],
            }
        )
        monkeypatch.setattr(rr, "_call_ak", lambda *a, **k: df)
        rows = rr.fetch_rating_changes(date(2026, 10, 16))
        assert rows[0]["org"] == "国盛证券"
        assert rows[0]["rating_change"] == "维持"
        assert rows[0]["is_first_rating"] is False

    def test_forecast_snapshot_dynamic_year_columns(self, monkeypatch):
        year = date.today().year
        df = pd.DataFrame(
            {
                "代码": ["600845"],
                "名称": ["宝信软件"],
                "研报数": [4],
                "机构投资评级(近六个月)-买入": [3.0],
                "机构投资评级(近六个月)-增持": [1.0],
                "机构投资评级(近六个月)-中性": [0.0],
                "机构投资评级(近六个月)-减持": [0.0],
                "机构投资评级(近六个月)-卖出": [0.0],
                # 上一年列仍在表内（东财实测保留上一年）→ 必须从当年起算
                f"{year - 1}预测每股收益": [0.50],
                f"{year}预测每股收益": [0.57],
                f"{year + 1}预测每股收益": [0.68],
                f"{year + 2}预测每股收益": [0.79],
                f"{year + 3}预测每股收益": [0.90],
            }
        )
        monkeypatch.setattr(rr, "_call_ak", lambda *a, **k: df)
        rows = rr.fetch_forecast_snapshot()
        assert rows[0]["eps_y1_year"] == year
        assert rows[0]["eps_y1"] == 0.57
        assert rows[0]["eps_y3"] == 0.79
        assert "eps_y4" not in rows[0]  # 只取当年起三年
        assert rows[0]["rating_buy"] == 3


class TestRunDaily:
    def _patch_ok(self, monkeypatch, reports=None):
        payload = (
            reports if reports is not None else [rr.normalize_em_row(_stock_raw(), "stock", 2026)]
        )
        monkeypatch.setattr(rr, "fetch_market_reports", lambda *a, **k: payload)
        monkeypatch.setattr(rr, "fetch_rating_changes", lambda *a, **k: [])
        monkeypatch.setattr(rr, "fetch_forecast_snapshot", lambda *a, **k: [])
        monkeypatch.setattr(rr, "fetch_sina_counts", lambda *a, **k: {"company": 3})

    def test_run_daily_upserts_and_records(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        self._patch_ok(monkeypatch)
        summary = rr.run_daily(as_of=date(2026, 10, 19), db_path=db)
        assert summary["reports_rows"] >= 1
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM research_reports").fetchone()[0] >= 1
        finally:
            conn.close()
        run = store.latest_run("research_reports_daily", db_path=db)
        assert run["status"] == "ok"

    def test_all_five_fail_marks_failed_but_others_run(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"

        def boom(*a, **k):
            raise RuntimeError("eastmoney unreachable")

        monkeypatch.setattr(rr, "fetch_market_reports", boom)
        monkeypatch.setattr(rr, "fetch_rating_changes", lambda *a, **k: [])
        monkeypatch.setattr(rr, "fetch_forecast_snapshot", lambda *a, **k: [])
        monkeypatch.setattr(rr, "fetch_sina_counts", lambda *a, **k: {"company": 0})
        rr.run_daily(as_of=date(2026, 10, 19), db_path=db)
        assert store.latest_run("research_reports_daily", db_path=db)["status"] == "failed"
        assert store.latest_run("rating_changes_daily", db_path=db)["status"] == "empty"


class TestConsensusForAnalysis:
    def test_uses_db_snapshot_first(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        store.upsert_earnings_forecasts(
            "2026-10-19",
            [
                {
                    "code": "600845",
                    "report_count": 4,
                    "rating_buy": 3,
                    "eps_y1": 0.57,
                    "eps_y1_year": 2026,
                    "source": "eastmoney",
                }
            ],
            db_path=db,
        )
        monkeypatch.setattr(
            rr,
            "fetch_forecast_snapshot",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应直拉")),
        )
        got = rr.get_consensus_for_analysis("600845", db_path=db)
        assert got["status"] == "covered"

    def test_falls_back_to_direct_when_no_snapshot(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        monkeypatch.setattr(
            rr,
            "fetch_forecast_snapshot",
            lambda *a, **k: [{"code": "600845", "report_count": 1, "eps_y1": 0.5}],
        )
        got = rr.get_consensus_for_analysis("600845", db_path=db)
        assert got["status"] == "covered"
        assert got["eps_y1"] == 0.5

    def test_missing_when_fallback_fails(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        monkeypatch.setattr(
            rr,
            "fetch_forecast_snapshot",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net")),
        )
        assert rr.get_consensus_for_analysis("600845", db_path=db)["status"] == "missing"


class TestInboxIngest:
    def test_filename_metadata_inference(self):
        assert rr.infer_metadata_from_filename("国信证券-金融工程日报：沪指低开-261019") == (
            "国信证券",
            "2026-10-19",
        )
        assert rr.infer_metadata_from_filename("策略周报-20261019") == (
            "策略周报",
            "2026-10-19",
        )
        assert rr.infer_metadata_from_filename("某报告") == (None, None)

    def test_ingest_dedup_and_missing_dir(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        missing = tmp_path / "no-such-dir"
        got = rr.ingest_inbox(missing, db_path=db)
        assert got["scanned"] == 0
        assert store.latest_run("research_report_inbox_daily", db_path=db)["status"] == "empty"

        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "国信证券-金融工程日报-261019.pdf").write_bytes(b"%PDF fake-1")
        (inbox / "开源证券-因子周报-261018.pdf").write_bytes(b"%PDF fake-2")
        monkeypatch.setattr(rr, "extract_pdf_text", lambda path: ("解析文本", 2))
        first = rr.ingest_inbox(inbox, db_path=db, extract=False)
        assert first["ingested"] == 2 and first["skipped"] == 0
        second = rr.ingest_inbox(inbox, db_path=db, extract=False)
        assert second["ingested"] == 0 and second["skipped"] == 2
        docs = store.list_documents(db_path=db)
        assert len(docs) == 2
        assert docs[0]["usage_policy"] == "personal_research"

    def test_parse_failure_is_failed_run(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "坏文件.pdf").write_bytes(b"not a pdf")
        monkeypatch.setattr(
            rr, "extract_pdf_text", lambda path: (_ for _ in ()).throw(ValueError("bad pdf"))
        )
        got = rr.ingest_inbox(inbox, db_path=db, extract=False)
        assert got["ingested"] == 0 and got["errors"]
        assert store.latest_run("research_report_inbox_daily", db_path=db)["status"] == "failed"


class TestExtraction:
    def _payload(self):
        return {
            "broker": "国信证券",
            "analyst": "张三",
            "report_date": "2026-10-19",
            "report_type": "个股研报",
            "stocks": [
                {
                    "code": "600845",
                    "name": "宝信软件",
                    "rating": "推荐",
                    "rating_normalized": "",
                    "target_price": "30.5",
                    "eps_forecasts": {"2026": "0.57", "bad": "x"},
                    "industry": "IT服务",
                },
                {
                    "code": "SH600000",
                    "name": "浦发银行",
                    "rating": "看空",
                    "rating_normalized": "未知",
                    "target_price": None,
                    "eps_forecasts": {},
                },
            ],
            "core_logic": ["逻辑A", " 逻辑A ", "逻辑B"],
            "catalysts": ["催化"],
            "risks": ["风险"],
            "summary": "摘要",
        }

    def test_coerce_extraction_is_type_safe(self):
        data = rr._coerce_extraction(self._payload())
        first, second = data["stocks"]
        assert first["code"] == "600845"
        assert first["rating_normalized"] == "买入"  # 推荐 → 买入
        assert first["target_price"] == 30.5
        assert first["eps_forecasts"] == {"2026": 0.57}
        assert first["industry"] == "IT服务"
        assert second["code"] is None  # 非 6 位代码不猜
        # 模型给「未知」时退回原始评级做确定性映射：看空 → 卖出
        assert second["rating_normalized"] == "卖出"
        assert data["core_logic"] == ["逻辑A", "逻辑B"]  # 去重
        assert data["report_date"] == "2026-10-19"

    def test_extract_document_persists_via_gateway(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        store.upsert_document(
            {
                "doc_key": "k1",
                "filename": "a.pdf",
                "text": "研报全文",
                "page_count": 1,
                "char_count": 4,
                "title": "a",
            },
            db_path=db,
        )
        monkeypatch.setattr(
            "finance_agent.prompts.loader.load_prompt_with_meta",
            lambda name: SimpleNamespace(
                template="抽取 {{document_text}}", prompt_name=name, prompt_version=7
            ),
        )
        calls = []

        def fake_complete(messages, **kwargs):
            calls.append(kwargs)
            return json.dumps(self._payload(), ensure_ascii=False), {"model": "fake-model"}

        monkeypatch.setattr("finance_agent.llm.gateway.complete_text", fake_complete)
        got = rr.extract_document("k1", db_path=db)
        assert got["broker"] == "国信证券"
        assert got["prompt_version"] == "7"
        assert calls[0]["purpose"] == "report_extraction"
        stored = store.get_extraction("k1", db_path=db)
        assert json.loads(stored["stocks_json"])[0]["code"] == "600845"
        # 已有抽取时不重复调用
        rr.extract_document("k1", db_path=db)
        assert len(calls) == 1

    def test_ingest_extraction_failure_degrades_but_keeps_doc(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "国信证券-金融工程日报-261019.pdf").write_bytes(b"%PDF fake")
        monkeypatch.setattr(rr, "extract_pdf_text", lambda path: ("全文", 1))
        monkeypatch.setattr(
            rr, "extract_document", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("llm down"))
        )
        got = rr.ingest_inbox(inbox, db_path=db, extract=True)
        assert got["ingested"] == 1 and got["errors"]
        assert len(store.list_documents(db_path=db)) == 1
        assert store.latest_run("research_report_inbox_daily", db_path=db)["status"] == "degraded"


class TestMerge:
    def _seed(self, db):
        store.upsert_extraction(
            "d1",
            {
                "broker": "国信证券",
                "report_date": "2026-09-21",
                "report_type": "个股研报",
                "stocks": [
                    {
                        "code": "600845",
                        "name": "宝信软件",
                        "rating": "买入",
                        "rating_normalized": "买入",
                        "target_price": 30.0,
                        "eps_forecasts": {"2026": 0.5},
                    }
                ],
                "core_logic": ["IDC回暖"],
                "catalysts": [],
                "risks": ["竞争加剧"],
                "summary": "s1",
            },
            db_path=db,
        )
        store.upsert_extraction(
            "d2",
            {
                "broker": "开源证券",
                "report_date": "2026-10-19",
                "report_type": "个股研报",
                "stocks": [
                    {
                        "code": "600845",
                        "name": "宝信软件",
                        "rating": "增持",
                        "rating_normalized": "增持",
                        "target_price": 40.0,
                        "eps_forecasts": {"2026": 0.7, "2027": 0.9},
                    }
                ],
                "core_logic": ["IDC回暖", "国产化"],
                "catalysts": ["新订单"],
                "risks": ["竞争加剧"],
                "summary": "s2",
            },
            db_path=db,
        )

    def test_merge_by_stock_aggregates(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db)
        merged = rr.merge_reports_for_stock("600845", db_path=db)
        assert merged["report_count"] == 2
        assert merged["brokers"] == ["国信证券", "开源证券"]
        assert merged["latest_report_date"] == "2026-10-19"
        assert merged["ratings"]["买入"] == 1 and merged["ratings"]["增持"] == 1
        assert merged["target_price"] == {"min": 30.0, "median": 35.0, "max": 40.0, "count": 2}
        assert merged["eps_forecasts"]["2026"]["median"] == 0.6
        assert merged["eps_forecasts"]["2027"]["count"] == 1
        assert merged["core_logic"] == ["IDC回暖", "国产化"]
        assert merged["risks"] == ["竞争加剧"]
        assert rr.merge_reports_for_stock("999999", db_path=db) is None

    def test_format_merge_digest(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db)
        text = rr.format_merge_digest(rr.merge_reports_for_stock("600845", db_path=db))
        assert "手工投喂研报合并" in text
        assert "评级分布" in text and "目标价" in text
        assert "核心逻辑" in text and "国产化" in text
        assert "不得直接作为你的结论依据" in text
