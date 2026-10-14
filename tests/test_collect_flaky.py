"""update-ci-pipeline R4：Playwright JSON 报告 → 抖动统计 run 记录。

夹具 `tests/fixtures/playwright-json/probe-flaky-report.json` 由 playwright 1.62.0
一次性探针实跑产出（passed/skipped/flaky/failed 四终态各一例），仅剥离堆栈等噪音，
结构保真——逐 test 分类必须与报告内 stats 块一致（本文件首个用例即此断言）。
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.ci.collect_flaky import (
    build_meta_from_env,
    classify_test,
    collect_records,
    main,
    summarize_report,
)

FIXTURE = Path(__file__).parent / "fixtures" / "playwright-json" / "probe-flaky-report.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_summarize_probe_fixture_matches_stats_block():
    """真实探针报告：逐 test 分类结果必须与 playwright 自算的 stats 完全一致。"""
    report = _load_fixture()
    summary = summarize_report(report)
    stats = report["stats"]
    assert summary["passed"] == stats["expected"]
    assert summary["flaky"] == stats["flaky"]
    assert summary["failed"] == stats["unexpected"]
    assert summary["skipped"] == stats["skipped"]
    assert summary["total"] == 4


def test_summarize_spec_breakdown_by_file():
    summary = summarize_report(_load_fixture())
    assert summary["specs"]["probe-flaky.spec.ts"] == {
        "passed": 1,
        "flaky": 1,
        "failed": 1,
        "skipped": 1,
    }


def test_classify_flaky_from_results_when_status_missing():
    """test 级 status 缺失（旧版报告/异常截断）时按 attempts 反推。"""
    test = {"results": [{"status": "failed", "retry": 0}, {"status": "passed", "retry": 1}]}
    assert classify_test(test) == "flaky"


def test_classify_missing_status_no_results_is_skipped():
    assert classify_test({}) == "skipped"


def test_summarize_nested_suites_and_multi_project():
    """嵌套 describe 无 file 字段 → 继承父套件 file；多 project 同 spec 两条 test 各计一次。"""
    report = {
        "suites": [
            {
                "file": "a.spec.ts",
                "specs": [],
                "suites": [
                    {
                        "title": "描述块",
                        "specs": [{"tests": [{"status": "expected"}, {"status": "expected"}]}],
                    }
                ],
            }
        ]
    }
    summary = summarize_report(report)
    assert summary["passed"] == 2
    assert summary["specs"]["a.spec.ts"]["passed"] == 2


def test_collect_records_reads_present_and_flags_missing(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "default.json").write_text(json.dumps(_load_fixture()), encoding="utf-8")
    record = collect_records(
        reports,
        expect=["default", "timeline"],
        meta={"run_id": "1", "run_attempt": 1, "source": "local"},
    )
    assert set(record["suites"]) == {"default"}
    assert record["suites"]["default"]["flaky"] == 1
    assert record["missing_suites"] == ["timeline"]


def test_collect_records_invalid_report_disclosed_not_raised(tmp_path):
    """半截/损坏报告不炸收数步骤（前面套件已红，统计不能一并丢失）——记 invalid 名单。"""
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "timeline.json").write_text("{半截", encoding="utf-8")
    record = collect_records(reports, expect=["timeline"], meta={"run_id": "1"})
    assert record["suites"] == {}
    assert len(record["invalid_reports"]) == 1
    assert "timeline" in record["invalid_reports"][0]


def test_build_meta_from_env_github():
    meta = build_meta_from_env(
        {
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_SHA": "abc123",
            "GITHUB_REF_NAME": "main",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "o/r",
        }
    )
    assert meta["run_id"] == "123"
    assert meta["run_attempt"] == 2
    assert meta["run_url"] == "https://github.com/o/r/actions/runs/123"
    assert meta["source"] == "github-actions"


def test_build_meta_from_env_local_fallback():
    meta = build_meta_from_env({})
    assert meta["source"] == "local"
    assert meta["run_id"]
    assert meta["run_url"] is None


def test_main_writes_record_file(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "default.json").write_text(json.dumps(_load_fixture()), encoding="utf-8")
    out = tmp_path / "out"
    code = main(
        [
            "--reports-dir",
            str(reports),
            "--out-dir",
            str(out),
            "--expect",
            "default,timeline",
            "--run-id",
            "42",
        ]
    )
    assert code == 0
    files = sorted(out.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert data["run_id"] == "42"
    assert data["missing_suites"] == ["timeline"]
