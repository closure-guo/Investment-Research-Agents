"""update-ci-pipeline R4：Playwright JSON 报告 → 抖动统计 run 记录（stdlib-only）。

CI 收数步骤（stub 作业末尾，``if: always()``）::

    python scripts/ci/collect_flaky.py \\
        --reports-dir tests/e2e/playwright/flaky-reports \\
        --out-dir reports/e2e-flaky \\
        --expect eval-ops,track-record,default,timeline

输入：各套件以 ``PLAYWRIGHT_JSON_OUTPUT_NAME=flaky-reports/<suite>.json`` +
``--reporter=json`` 产出的报告（OUTPUT_NAME 相对 configDir 解析，已由
playwright 1.62 实跑实证；reporter 自动建父目录）。输出：
``<out-dir>/<run_id>-<attempt>.json`` 一条 run 记录（schema=1），供 nightly
``aggregate_flaky.py`` 汇总。

收数步骤是**旁路**：损坏/缺失报告记名单（``invalid_reports`` / ``missing_suites``）
不抛错——前面套件已红时，统计仍要尽可能把已产出的数据收走。
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = 1
CATEGORIES = ("passed", "flaky", "failed", "skipped")

# playwright JSON 报告的 test 级 status → 本仓四分类（与 list reporter 文案同源：
# expected=passed / flaky / unexpected=failed / skipped）
_STATUS_MAP = {"expected": "passed", "flaky": "flaky", "unexpected": "failed", "skipped": "skipped"}


def _empty_counts() -> dict[str, int]:
    return dict.fromkeys(CATEGORIES, 0)


def classify_test(test: Mapping[str, Any]) -> str:
    """单个 test 条目 → 四分类；status 缺失时按 attempts 反推（旧版/截断报告防御）。"""
    status = test.get("status")
    if isinstance(status, str) and status in _STATUS_MAP:
        return _STATUS_MAP[status]
    results = test.get("results") or []
    if not results:
        return "skipped"
    final = results[-1].get("status")
    if final == "skipped":
        return "skipped"
    if final == "passed":
        earlier_all_passed = all(r.get("status") == "passed" for r in results[:-1])
        return "passed" if earlier_all_passed else "flaky"
    return "failed"


def summarize_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Playwright JSON 报告 → 四分类总计 + 按 spec 文件分桶（嵌套 describe 继承 file）。"""
    totals = _empty_counts()
    per_file: dict[str, dict[str, int]] = {}

    def walk(suite: Mapping[str, Any], inherited_file: str | None) -> None:
        suite_file = suite.get("file") or inherited_file
        for spec in suite.get("specs") or []:
            spec_file = spec.get("file") or suite_file or "<unknown>"
            bucket = per_file.setdefault(spec_file, _empty_counts())
            for test in spec.get("tests") or []:
                cat = classify_test(test)
                totals[cat] += 1
                bucket[cat] += 1
        for child in suite.get("suites") or []:
            walk(child, suite_file)

    for suite in report.get("suites") or []:
        walk(suite, None)
    summary = {**totals, "total": sum(totals.values()), "specs": per_file}
    return summary


def build_meta_from_env(env: Mapping[str, str]) -> dict[str, Any]:
    """GitHub Actions 环境变量 → run 元数据；本地运行回落到时间戳 run_id。"""
    run_id = env.get("GITHUB_RUN_ID")
    server = env.get("GITHUB_SERVER_URL")
    repo = env.get("GITHUB_REPOSITORY")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if run_id and server and repo else None
    try:
        attempt = int(env.get("GITHUB_RUN_ATTEMPT") or 0)
    except ValueError:
        attempt = 0
    if not run_id:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return {
        "run_id": str(run_id),
        "run_attempt": attempt,
        "source": "github-actions" if env.get("GITHUB_RUN_ID") else "local",
        "run_url": run_url,
        "repo": repo,
        "sha": env.get("GITHUB_SHA"),
        "branch": env.get("GITHUB_REF_NAME"),
        "event": env.get("GITHUB_EVENT_NAME"),
    }


def collect_records(
    reports_dir: Path,
    *,
    expect: Sequence[str] = (),
    meta: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """收数：读 ``<suite>.json`` 报告 → 一条 run 记录（含缺失/损坏披露）。"""
    meta = dict(meta or {})
    suites: dict[str, dict[str, Any]] = {}
    invalid: list[str] = []
    if reports_dir.is_dir():
        for path in sorted(reports_dir.glob("*.json")):
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(report, dict) or "suites" not in report:
                    raise ValueError("非 playwright JSON 报告（缺 suites）")
                suites[path.stem] = summarize_report(report)
            except Exception as exc:  # noqa: BLE001 - 旁路：损坏报告记名单不抛
                invalid.append(f"{path.name}: {type(exc).__name__}: {exc}")
    ts = (now or datetime.now(UTC)).astimezone(UTC)
    return {
        "schema": SCHEMA,
        "generated_at": ts.isoformat().replace("+00:00", "Z"),
        "run_id": str(meta.get("run_id") or ""),
        "run_attempt": int(meta.get("run_attempt") or 0),
        "source": meta.get("source") or "local",
        "run_url": meta.get("run_url"),
        "repo": meta.get("repo"),
        "sha": meta.get("sha"),
        "branch": meta.get("branch"),
        "event": meta.get("event"),
        "suites": suites,
        "missing_suites": [name for name in expect if name not in suites],
        "invalid_reports": invalid,
    }


def _summary_line(record: Mapping[str, Any]) -> str:
    parts = []
    for name, counts in record["suites"].items():
        parts.append(
            f"{name}(p{counts['passed']}/f{counts['flaky']}/x{counts['failed']}/s{counts['skipped']})"
        )
    missing = ",".join(record["missing_suites"]) or "-"
    invalid = ",".join(record["invalid_reports"]) or "-"
    return (
        f"[collect-flaky] run={record['run_id']}-{record['run_attempt']} "
        f"suites={' '.join(parts) or '-'} missing={missing} invalid={invalid}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Playwright JSON 报告 → 抖动统计 run 记录")
    parser.add_argument("--reports-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--expect", default="", help="逗号分隔的预期套件名（缺失即记 missing）")
    parser.add_argument("--run-id", default=None, help="覆盖 run_id（本地复核用）")
    args = parser.parse_args(argv)

    meta = build_meta_from_env(dict(os.environ))
    if args.run_id:
        meta["run_id"] = args.run_id
    expect = [s.strip() for s in args.expect.split(",") if s.strip()]
    record = collect_records(args.reports_dir, expect=expect, meta=meta)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{record['run_attempt']}" if record["run_attempt"] else ""
    out = args.out_dir / f"{record['run_id']}{suffix}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(_summary_line(record))
    print(f"[collect-flaky] 记录已落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
