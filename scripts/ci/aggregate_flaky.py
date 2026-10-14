"""update-ci-pipeline R4：抖动 run 记录 → 各 spec 抖动率汇总报告（stdlib-only）。

nightly 聚合步骤::

    python scripts/ci/aggregate_flaky.py \\
        --records-dir reports/e2e-flaky/records \\
        --out reports/e2e-flaky/summary-$(date -u +%F).md --days 14

输入：``collect_flaky.py`` 产出的 run 记录（nightly 经 ``gh run download``
拉取最近若干次 E2E run 的 ``e2e-flaky`` artifact，落在 records/<run_id>/）。
输出：markdown 汇总（stdout + ``--out`` 文件）。

口径（与 spec「抖动统计落盘与汇总」一致）：
- 统计窗口 = now - ``--days`` → now（UTC），按记录 ``generated_at`` 过滤；
- 同一 run 的多个 attempt（rerun）只取最高 attempt——rerun 多为失败触发，
  重复计入会虚增抖动/失败率；
- 有效 n = 该 spec 实际出现的记录数；被前序失败遮挡而未跑的套件不计 0 分；
- 抖动率 = 含 flaky 的记录数 / 有效 n；失败率 = 含 failed 的记录数 / 有效 n。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

CATEGORIES = ("passed", "flaky", "failed", "skipped")


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def load_records(records_dir: Path) -> dict[str, Any]:
    """递归读全部 run 记录；损坏/非记录文件记 ``invalid_files`` 不抛错。"""
    records: list[dict[str, Any]] = []
    invalid: list[str] = []
    if records_dir.is_dir():
        for path in sorted(records_dir.rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or "run_id" not in data:
                    raise ValueError("非 run 记录（缺 run_id）")
                records.append(data)
            except Exception as exc:  # noqa: BLE001 - 汇总步骤旁路：坏文件记名单
                invalid.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return {"records": records, "invalid_files": invalid}


def aggregate(
    loaded: Mapping[str, Any], *, now: str | datetime, window_days: int
) -> dict[str, Any]:
    """记录集合 → 窗口内按 spec/套件聚合（去 rerun、披露排除与缺失）。"""
    now_dt = _parse_ts(now) if isinstance(now, str) else now.astimezone(UTC)
    if now_dt is None:
        raise ValueError(f"now 不可解析: {now!r}")
    cutoff = now_dt - timedelta(days=window_days)

    invalid = list(loaded.get("invalid_files") or [])
    in_window: list[dict[str, Any]] = []
    excluded = 0
    for rec in loaded.get("records") or []:
        ts = _parse_ts(rec.get("generated_at"))
        if ts is None:
            invalid.append(f"run {rec.get('run_id')}: generated_at 不可解析")
            continue
        if ts >= cutoff:
            in_window.append(rec)
        else:
            excluded += 1

    best: dict[str, dict[str, Any]] = {}
    dropped = 0
    for rec in in_window:
        key = str(rec.get("run_id"))
        cur = best.get(key)
        if cur is None:
            best[key] = rec
            continue
        dropped += 1
        if int(rec.get("run_attempt") or 0) > int(cur.get("run_attempt") or 0):
            best[key] = rec
    records = list(best.values())

    suite_totals: dict[str, dict[str, int]] = {}
    per_spec: dict[tuple[str, str], dict[str, int]] = {}
    missing_counts: dict[str, int] = {}
    for rec in records:
        for name in rec.get("missing_suites") or []:
            missing_counts[name] = missing_counts.get(name, 0) + 1
        for suite, counts in (rec.get("suites") or {}).items():
            st = suite_totals.setdefault(suite, {"runs": 0, **dict.fromkeys(CATEGORIES, 0)})
            st["runs"] += 1
            for cat in CATEGORIES:
                st[cat] += int(counts.get(cat) or 0)
            for spec_file, spec_counts in (counts.get("specs") or {}).items():
                bucket = per_spec.setdefault(
                    (suite, spec_file), {"valid_runs": 0, "flaky_runs": 0, "failed_runs": 0}
                )
                bucket["valid_runs"] += 1
                if int(spec_counts.get("flaky") or 0) > 0:
                    bucket["flaky_runs"] += 1
                if int(spec_counts.get("failed") or 0) > 0:
                    bucket["failed_runs"] += 1

    specs = [
        {
            "suite": suite,
            "spec": spec_file,
            **bucket,
            "flaky_rate": bucket["flaky_runs"] / bucket["valid_runs"],
            "fail_rate": bucket["failed_runs"] / bucket["valid_runs"],
        }
        for (suite, spec_file), bucket in per_spec.items()
    ]
    specs.sort(key=lambda s: (-s["flaky_rate"], -s["fail_rate"], s["spec"]))

    return {
        "window_days": window_days,
        "window_start": cutoff.date().isoformat(),
        "window_end": now_dt.date().isoformat(),
        "records": len(records),
        "excluded_out_of_window": excluded,
        "dropped_attempts": dropped,
        "invalid_files": invalid,
        "suite_totals": suite_totals,
        "specs": specs,
        "missing_counts": missing_counts,
    }


def _pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def build_report(result: Mapping[str, Any]) -> str:
    """聚合结果 → markdown（披露窗口 / 有效 n / 排除项，供 #328 类归因）。"""
    lines: list[str] = [
        "# E2E 抖动汇总报告",
        "",
        f"- 统计窗口：{result['window_start']} → {result['window_end']}"
        f"（UTC，近 {result['window_days']} 天）",
        f"- 有效记录：{result['records']} 条"
        f"（超窗排除 {result['excluded_out_of_window']}；同 run rerun 去重 {result['dropped_attempts']}）",
        f"- 无效/不可解析文件：{len(result['invalid_files'])}",
        "- 口径：抖动率 = 含 flaky 的记录数 / 有效 n；有效 n = 该 spec 实际运行的记录数"
        "（被前序失败遮挡而未跑的套件不计入，不记 0 分当通过）",
        "",
    ]
    for item in result["invalid_files"]:
        lines.append(f"  - invalid: {item}")
    if result["records"] == 0:
        lines += ["", "窗口内无记录（样本不足，暂无可归因读数）。", ""]
        return "\n".join(lines) + "\n"

    noisy = [s for s in result["specs"] if s["flaky_runs"] or s["failed_runs"]]
    clean = [s for s in result["specs"] if not (s["flaky_runs"] or s["failed_runs"])]
    lines += ["## 各 spec 抖动率（含抖动或失败者）", ""]
    if noisy:
        lines += [
            "| spec | 套件 | 有效 n | flaky 次数 | 抖动率 | 失败次数 | 失败率 |",
            "|---|---|---|---|---|---|---|",
        ]
        for s in noisy:
            lines.append(
                f"| {s['spec']} | {s['suite']} | {s['valid_runs']} | {s['flaky_runs']} "
                f"| {_pct(s['flaky_rate'])} | {s['failed_runs']} | {_pct(s['fail_rate'])} |"
            )
    else:
        lines.append("窗口内无抖动/失败 spec。")
    if clean:
        lines += ["", f"其余 {len(clean)} 个 spec 窗口内零抖动零失败（有效 n ≥ 1）。"]

    lines += [
        "",
        "## 各套件总计",
        "",
        "| 套件 | 有效 n | passed | flaky | failed | skipped |",
        "|---|---|---|---|---|---|",
    ]
    for suite, st in sorted(result["suite_totals"].items()):
        lines.append(
            f"| {suite} | {st['runs']} | {st['passed']} | {st['flaky']} | {st['failed']} | {st['skipped']} |"
        )

    if result["missing_counts"]:
        lines += ["", "## 未运行套件（被前序失败遮挡）", ""]
        for suite, count in sorted(result["missing_counts"].items()):
            lines.append(f"- {suite}：{count} 条记录中缺失")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抖动 run 记录 → 各 spec 抖动率汇总报告")
    parser.add_argument("--records-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--days", type=int, default=14, help="统计窗口（天，默认 14）")
    args = parser.parse_args(argv)

    loaded = load_records(args.records_dir)
    result = aggregate(loaded, now=datetime.now(UTC), window_days=args.days)
    report = build_report(result)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"[aggregate-flaky] 报告已落盘: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
