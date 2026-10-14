"""update-ci-pipeline R4：抖动 run 记录 → 各 spec 抖动率汇总报告（markdown）。

记录 schema 与 `scripts/ci/collect_flaky.py` 输出一致；本文件用最小手写记录
（真实结构）驱动聚合语义：抖动率 = flaky 次数 / 有效 n（该 spec 实际运行的
记录数），同一 run 的多个 attempt 只取最高 attempt 计入。
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.ci.aggregate_flaky import aggregate, build_report, load_records, main

_NOW = "2026-10-14T02:00:00Z"


def _record(
    run_id: str,
    *,
    attempt: int = 1,
    generated_at: str = "2026-10-13T10:00:00Z",
    suites: dict | None = None,
    missing: tuple[str, ...] = (),
) -> dict:
    return {
        "schema": 1,
        "run_id": run_id,
        "run_attempt": attempt,
        "generated_at": generated_at,
        "source": "github-actions",
        "run_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "repo": "o/r",
        "sha": "abc123",
        "branch": "main",
        "event": "pull_request",
        "suites": suites or {},
        "missing_suites": list(missing),
    }


def _suite(
    flaky: int = 0, failed: int = 0, passed: int = 5, skipped: int = 0, spec: str = "x.spec.ts"
):
    counts = {"passed": passed, "flaky": flaky, "failed": failed, "skipped": skipped}
    return {**counts, "total": passed + flaky + failed + skipped, "specs": {spec: dict(counts)}}


def _write(tmp_path: Path, records: list[dict]) -> Path:
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    for i, rec in enumerate(records):
        (records_dir / f"r{i}.json").write_text(json.dumps(rec), encoding="utf-8")
    return records_dir


def test_aggregate_flaky_rate_and_disclosure(tmp_path):
    """spec X 3 条有效记录中 2 条含 flaky → 抖动率 2/3；报告披露窗口与有效 n。"""
    records_dir = _write(
        tmp_path,
        [
            _record("1", suites={"default": _suite(flaky=1)}),
            _record("2", suites={"default": _suite(flaky=1)}),
            _record("3", suites={"default": _suite()}),
        ],
    )
    result = aggregate(load_records(records_dir), now=_NOW, window_days=14)
    spec = result["specs"][0]
    assert spec["spec"] == "x.spec.ts"
    assert spec["valid_runs"] == 3
    assert spec["flaky_runs"] == 2
    assert spec["flaky_rate"] == 2 / 3
    report = build_report(result)
    assert "66.7%" in report
    assert "有效" in report
    assert "2026-09-30" in report  # 窗口起点 = now - 14 天（UTC）
    assert "2026-10-14" in report  # 窗口终点 = now


def test_aggregate_dedups_run_attempts(tmp_path):
    """同一 run 两次 attempt（rerun）只计最高 attempt：attempt2 干净 → 不记 flaky。"""
    records_dir = _write(
        tmp_path,
        [
            _record("7", attempt=1, suites={"default": _suite(flaky=1)}),
            _record("7", attempt=2, suites={"default": _suite()}),
        ],
    )
    result = aggregate(load_records(records_dir), now=_NOW, window_days=14)
    assert result["records"] == 1
    assert result["dropped_attempts"] == 1
    assert result["specs"][0]["valid_runs"] == 1
    assert result["specs"][0]["flaky_runs"] == 0


def test_aggregate_window_filter_disclosed(tmp_path):
    records_dir = _write(
        tmp_path,
        [
            _record("1", suites={"default": _suite(flaky=1)}),
            _record("2", generated_at="2026-09-01T00:00:00Z", suites={"default": _suite()}),
        ],
    )
    result = aggregate(load_records(records_dir), now=_NOW, window_days=14)
    assert result["records"] == 1
    assert result["excluded_out_of_window"] == 1
    assert "超窗排除 1" in build_report(result)


def test_aggregate_missing_suite_not_counted(tmp_path):
    """被前序失败遮挡而未跑的套件不进入有效 n（缺失即无数据，不得记 0 分当通过）。"""
    records_dir = _write(
        tmp_path,
        [
            _record("1", suites={"default": _suite()}, missing=("timeline",)),
            _record("2", suites={"default": _suite()}, missing=("timeline",)),
        ],
    )
    result = aggregate(load_records(records_dir), now=_NOW, window_days=14)
    assert "timeline" not in result["suite_totals"]
    assert {s["suite"] for s in result["specs"]} == {"default"}
    assert "timeline" in build_report(result)  # 报告披露未跑套件


def test_aggregate_empty_records_graceful(tmp_path):
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    result = aggregate(load_records(records_dir), now=_NOW, window_days=14)
    assert result["records"] == 0
    report = build_report(result)
    assert "0" in report
    assert "不足" in report or "无记录" in report


def test_aggregate_invalid_record_file_disclosed(tmp_path):
    records_dir = _write(tmp_path, [_record("1", suites={"default": _suite(flaky=1)})])
    (records_dir / "broken.json").write_text("{半截", encoding="utf-8")
    records = load_records(records_dir)
    result = aggregate(records, now=_NOW, window_days=14)
    assert len(result["invalid_files"]) == 1
    assert "broken" in result["invalid_files"][0]


def test_main_writes_markdown(tmp_path, capsys):
    records_dir = _write(tmp_path, [_record("1", suites={"default": _suite(flaky=1)})])
    out = tmp_path / "summary.md"
    code = main(
        [
            "--records-dir",
            str(records_dir),
            "--out",
            str(out),
            "--days",
            "14",
        ]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "x.spec.ts" in text
    assert "x.spec.ts" in capsys.readouterr().out  # stdout 同步可见（nightly 日志）
