"""行业研报聚合测试（add-industry-report-context）。

覆盖：归一层级剥离、别名、时点剔除、窗口、条数上限、三态（ok/stale/unmatched）、
空库 None、异常 fail-open、渲染披露。
"""

from __future__ import annotations

from pathlib import Path

from finance_agent.data import research_reports as rr
from finance_agent.data import research_reports_store as store

VOCAB_PATH = Path("tests/fixtures/industry_report_vocab.tsv")


def _industry_row(**over):
    row = {
        "report_type": "industry",
        "title": "动力电池行业跟踪：装机同比回升",
        "org": "东吴证券",
        "analyst": "曾朵红",
        "publish_date": "2026-10-20",
        "industry": "电池",
        "depth_class": "comment",
        "source": "eastmoney",
    }
    row.update(over)
    return row


class TestNormalizeIndustry:
    def test_strips_level_suffix(self):
        assert rr.normalize_industry("白酒Ⅱ") == "白酒"
        assert rr.normalize_industry("银行 II") == "银行"
        assert rr.normalize_industry("航天装备Ⅱ") == "航天装备"

    def test_nfkc_and_whitespace(self):
        assert rr.normalize_industry("ＩＴ服务") == "IT服务"
        assert rr.normalize_industry("  电 池 ") == "电池"
        assert rr.normalize_industry(None) == ""
        assert rr.normalize_industry("") == ""

    def test_alias_values_exist_in_vocab_snapshot(self):
        raw = [
            line.split("\t")[0]
            for line in VOCAB_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        vocab = {rr.normalize_industry(name) for name in raw}
        for key, value in rr.INDUSTRY_ALIASES.items():
            assert rr.normalize_industry(value) in vocab, f"别名 {key}->{value} 不在实测词表内"


class TestGetIndustryDigest:
    def _seed(self, db, **over):
        store.upsert_reports([_industry_row(**over)], db_path=db)

    def test_ok_aggregation_sorted_desc(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, title="A", publish_date="2026-10-18")
        self._seed(db, title="B", publish_date="2026-10-20", org="中信证券")
        self._seed(db, title="C", publish_date="2026-10-19")
        digest = rr.get_industry_digest("电池", as_of="2026-10-21", db_path=db)
        assert digest["status"] == "ok"
        assert digest["report_count"] == 3
        assert [d["publish_date"] for d in digest["documents"]] == [
            "2026-10-20",
            "2026-10-19",
            "2026-10-18",
        ]
        assert digest["latest_report_date"] == "2026-10-20"
        assert set(digest["brokers"]) == {"东吴证券", "中信证券"}
        assert digest["industry"] == "电池"

    def test_future_report_excluded(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, title="now", publish_date="2026-10-20")
        self._seed(db, title="future", publish_date="2026-10-25")
        digest = rr.get_industry_digest("电池", as_of="2026-10-21", db_path=db)
        assert digest["report_count"] == 1
        assert [d["title"] for d in digest["documents"]] == ["now"]

    def test_level_suffix_matches(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, industry="白酒Ⅱ", title="白酒动销数据")
        digest = rr.get_industry_digest("白酒", as_of="2026-10-21", db_path=db)
        assert digest["status"] == "ok"
        assert digest["report_count"] == 1

    def test_alias_matches(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, industry="保险Ⅱ", title="寿险新单回暖")
        digest = rr.get_industry_digest("多元化保险", as_of="2026-10-21", db_path=db)
        assert digest["status"] == "ok"
        assert digest["report_count"] == 1

    def test_stale_when_all_out_of_window(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, title="old", publish_date="2026-06-01")
        digest = rr.get_industry_digest("电池", as_of="2026-10-21", db_path=db)
        assert digest["status"] == "stale"
        assert digest["documents"] == []
        assert digest["latest_report_date"] == "2026-06-01"
        assert digest["age_days"] > 90

    def test_unmatched_is_not_none(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db, industry="通信设备", title="通信设备行业周报")
        digest = rr.get_industry_digest("通信服务", as_of="2026-10-21", db_path=db)
        assert digest is not None
        assert digest["status"] == "unmatched"
        assert digest["documents"] == []

    def test_empty_db_returns_none(self, tmp_path):
        db = tmp_path / "rr.db"
        assert rr.get_industry_digest("电池", as_of="2026-10-21", db_path=db) is None

    def test_empty_industry_returns_none(self, tmp_path):
        db = tmp_path / "rr.db"
        self._seed(db)
        assert rr.get_industry_digest("", as_of="2026-10-21", db_path=db) is None
        assert rr.get_industry_digest(None, as_of="2026-10-21", db_path=db) is None

    def test_max_reports_cap(self, tmp_path):
        db = tmp_path / "rr.db"
        for day in range(1, 11):
            self._seed(db, title=f"r{day}", publish_date=f"2026-10-{day:02d}")
        digest = rr.get_industry_digest("电池", as_of="2026-10-21", db_path=db, max_reports=3)
        assert digest["report_count"] == 3

    def test_broken_db_fails_open(self, tmp_path):
        broken = tmp_path / "as_dir"
        broken.mkdir()
        assert rr.get_industry_digest("电池", as_of="2026-10-21", db_path=broken) is None


class TestFormatIndustryDigest:
    def _digest(self, **over):
        digest = {
            "industry": "电池",
            "status": "ok",
            "report_count": 2,
            "brokers": ["东吴证券"],
            "latest_report_date": "2026-10-20",
            "age_days": 1,
            "documents": [
                {
                    "report_key": "k1",
                    "title": "动力电池行业跟踪：装机同比回升",
                    "org": "东吴证券",
                    "analyst": "曾朵红",
                    "publish_date": "2026-10-20",
                    "depth_class": "comment",
                }
            ],
        }
        digest.update(over)
        return digest

    def test_ok_format_discloses_metadata_only(self):
        text = rr.format_industry_digest(self._digest())
        assert "同行业研报" in text
        assert "仅元数据" in text
        assert "动力电池行业跟踪" in text
        assert "不得直接作为结论依据" in text

    def test_stale_format_shows_age_not_titles(self):
        digest = self._digest(status="stale", documents=[], report_count=0)
        text = rr.format_industry_digest(digest)
        assert "断更" in text
        assert "2026-10-20" in text
        assert "动力电池行业跟踪" not in text

    def test_unmatched_format_never_says_no_reports(self):
        text = rr.format_industry_digest(
            self._digest(status="unmatched", documents=[], report_count=0, industry="通信服务")
        )
        assert "通信服务" in text
        assert "无对应行业口径" in text
        assert "无研报" not in text

    def test_long_title_truncated(self):
        long_title = "动力电池" * 40
        digest = self._digest(documents=[{**self._digest()["documents"][0], "title": long_title}])
        text = rr.format_industry_digest(digest)
        assert "…" in text
        assert long_title not in text

    def test_none_returns_empty(self):
        assert rr.format_industry_digest(None) == ""
