"""Δ3 Task 7 离线端到端验证（零 LLM，脚本化）。

覆盖 delta add-forward-paper-trading-cohort 的跑批→记账→读数闭环，全程不触网、
不调 LLM：注入 fake ``graph_runner``（同线程迭代，usage 走 contextvar 真值通道），
DB 为 tmp 文件，标的池为测试用小登记文件。

四段：
① 开启跑批 2 只 → ``cohort_runs`` 行齐（1 只 usage 真值 / 1 只 usage 缺失 NULL+WARN）、
   predictions 落库（simulating 真实挂点）、``collect_cohort_readings`` 汇总正确。
② 幂等复跑 → 跳过且无新行；``force`` → ``run_seq=1`` 另记。
③ 预算熔断（``max_tokens`` 极小）→ 首只成功、剩余 ``skipped`` / ``budget``。
④ 开关关闭 → **零 LLM 调用 + 零 cohort_runs 行**，日志
   ``cohort 跑批未启用（COHORT_ENABLED != 1）`` 为预期证据。

用法: uv run python tests/scripts/d3_task7_cohort_offline_e2e.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))  # evals.* 包根

# 隔离：session_store._DB_PATH 在 import 期冻结 → 必须在任何 finance_agent import 前设好
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="d3t7-cohort-"))
os.environ["SESSIONS_DB_PATH"] = str(_TMP_ROOT / "sessions.db")
os.environ.pop("COHORT_ENABLED", None)
os.environ.pop("COHORT_MAX_TOKENS_PER_RUN", None)
os.environ.pop("COHORT_UNIVERSE_PATH", None)

from evals.outcome.cohort_readings import collect_cohort_readings  # noqa: E402

from finance_agent.outcome.cohort.model import (  # noqa: E402
    list_cohort_runs,
)
from finance_agent.outcome.cohort.runner import run_cohort_batch  # noqa: E402
from finance_agent.outcome.track_record.model import (  # noqa: E402
    init_track_record_tables,
    insert_prediction,
)

_RUNNER_LOGGER = "finance_agent.outcome.cohort.runner"
_FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _FAILURES.append(label)


class _Capture(logging.Handler):
    """捕获 runner 日志（用于断言「未启用」与 usage 缺失 WARN 的预期证据）。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _universe_file(path: Path, tickers: list[str], version: str = "v1") -> Path:
    payload = {
        "version": version,
        "effective_date": "2026-09-24",
        "seed": 42,
        "strata": {"by": "market_cap(quota)+industry(dedup)", "n": len(tickers), "quota": {}},
        "constituents": [
            {
                "ticker": t,
                "name": f"名称{t}",
                "industry": "行业",
                "market_cap_bucket": "large",
            }
            for t in tickers
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _feed_usage(usage: dict | None) -> None:
    """向当前上下文的 usage 累加器汇入（同线程 contextvar 可见性硬约束的锚点）。"""
    from finance_agent.nodes import _llm_utils

    acc = _llm_utils._usage_acc.get()
    assert acc is not None, "runner 必须在同线程内激活 usage_collector（否则静默 0）"
    acc.add(usage)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _make_runner(*, usage_map: dict[str, dict | None] | None = None):
    """fake graph_runner：置 session trace（模拟真实回写）、按需汇入 usage、发 report_ready。"""
    calls: list[str] = []

    def runner(ticker, name, req, analysis_id, start_time, session_id=None, llm_config=None):
        calls.append(ticker)
        from finance_agent.session_store import set_session_trace_id

        if session_id:
            set_session_trace_id(session_id, f"trace-{ticker}")
        if usage_map and ticker in usage_map and usage_map[ticker] is not None:
            _feed_usage(usage_map[ticker])
        yield _sse({"type": "report_ready", "analysis_id": analysis_id, "session_id": session_id})

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _run_with_patched_trace(**kwargs) -> dict:
    """统一把 ``_live_trace_id`` 打桩为 None（trace 只从 session 回读，确定性）。"""
    with mock.patch("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None):
        return run_cohort_batch(**kwargs)


_DAY = "2026-09-24"


# ── ① 开启跑批 + 记账 + 读数汇总 ────────────────────────────────────────────
def scenario_enabled_run() -> None:
    print("[1] 开启跑批 2 只 → 记账 + predictions + 读数汇总")
    db = _TMP_ROOT / "s1.db"
    init_track_record_tables(db)
    universe = _universe_file(_TMP_ROOT / "u1.json", ["600519", "000001"])
    runner = _make_runner(
        usage_map={"600519": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}}
    )
    cap = _Capture()
    logging.getLogger(_RUNNER_LOGGER).addHandler(cap)
    try:
        result = _run_with_patched_trace(
            trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
        )
    finally:
        logging.getLogger(_RUNNER_LOGGER).removeHandler(cap)

    check("runner 调用 = [600519, 000001]", runner.calls == ["600519", "000001"], f"{runner.calls}")
    check("result success == 2", result["success"] == 2, f"{result['success']}")
    check(
        "result skipped == 0 / budget_stopped False",
        result["skipped"] == 0 and not result["budget_stopped"],
    )

    rows = {
        r["ticker"]: r for r in list_cohort_runs(universe_version="v1", trade_date=_DAY, db_path=db)
    }
    check("cohort_runs 2 行", len(rows) == 2, f"n={len(rows)}")
    a = rows["600519"]
    check(
        "A usage 真值落账 (140 tokens / 1 call)",
        (a["tokens_prompt"], a["tokens_completion"], a["tokens_total"], a["llm_calls"])
        == (100, 40, 140, 1),
        f"{a['tokens_prompt']}/{a['tokens_completion']}/{a['tokens_total']}/{a['llm_calls']}",
    )
    check("A trace 回写落库", a["langfuse_trace_id"] == "trace-600519", f"{a['langfuse_trace_id']}")
    b = rows["000001"]
    check(
        "B usage 缺失 → token NULL（非 0）+ calls=0",
        b["tokens_total"] is None and b["llm_calls"] == 0,
        f"total={b['tokens_total']} calls={b['llm_calls']}",
    )
    check("B 有 usage 缺失 WARN", any("NULL" in m for m in cap.messages), f"{cap.messages}")

    # predictions 落库（模拟真实挂点：经 trace 关联）
    for ticker, direction, status in (
        ("600519", "long", "resolved_win"),
        ("000001", "neutral", "avoidance"),
    ):
        insert_prediction(
            {
                "source_type": "live",
                "symbol": ticker,
                "symbol_name": f"名称{ticker}",
                "direction": direction,
                "entry_price": 100.0,
                "horizon_days": 20,
                "confidence": 0.6,
                "rationale_snapshot": {"action": "watch"},
                "langfuse_trace_id": f"trace-{ticker}",
                "created_at": f"{_DAY}T18:05:00",
            },
            db_path=db,
            status=status,
        )

    readings = collect_cohort_readings(db, universe_version="v1", since=_DAY)
    batch = readings["batch"]
    check("readings planned == 2", batch["planned"] == 2, f"{batch['planned']}")
    check(
        "readings success == 2 / failure 0 / skipped 0",
        batch["success"] == 2 and batch["failure"] == 0 and batch["skipped"] == 0,
    )
    check(
        "readings run_success_rate == 1.0",
        batch["run_success_rate"] == 1.0,
        f"{batch['run_success_rate']}",
    )
    check(
        "readings opinions_persisted == 2",
        batch["opinions_persisted"] == 2,
        f"{batch['opinions_persisted']}",
    )
    check(
        "readings unlinked == 0（含两子桶 0）",
        batch["unlinked"] == 0
        and batch["unlinked_trace_missing"] == 0
        and batch["unlinked_no_opinion"] == 0,
    )
    check(
        "readings settlement_status_counts",
        batch["settlement_status_counts"] == {"resolved_win": 1, "avoidance": 1},
        f"{batch['settlement_status_counts']}",
    )
    check(
        "readings tokens_total_sum == 140",
        batch["tokens_total_sum"] == 140,
        f"{batch['tokens_total_sum']}",
    )
    check(
        "readings tokens_null_runs == 1（B）",
        batch["tokens_null_runs"] == 1,
        f"{batch['tokens_null_runs']}",
    )
    check(
        "readings failure_reasons 空 / skipped_reasons 空",
        batch["failure_reasons"] == {} and batch["skipped_reasons"] == {},
    )
    check("readings rows == 2", len(readings["rows"]) == 2, f"{len(readings['rows'])}")


# ── ② 幂等复跑 + force ──────────────────────────────────────────────────────
def scenario_idempotent_and_force() -> None:
    print("[2] 幂等复跑跳过 + force run_seq 递增")
    db = _TMP_ROOT / "s2.db"
    universe = _universe_file(_TMP_ROOT / "u2.json", ["600519", "000001"])
    runner = _make_runner()  # 不汇 usage（本段只验幂等语义）

    r1 = _run_with_patched_trace(
        trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
    )
    check("首跑 success == 2", r1["success"] == 2, f"{r1['success']}")
    n_after_first = len(list_cohort_runs(universe_version="v1", trade_date=_DAY, db_path=db))
    check("首跑后 2 行", n_after_first == 2, f"n={n_after_first}")

    runner.calls.clear()
    r2 = _run_with_patched_trace(
        trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
    )
    n_after_rerun = len(list_cohort_runs(universe_version="v1", trade_date=_DAY, db_path=db))
    check("复跑零调用（已 success 跳过）", runner.calls == [], f"{runner.calls}")
    check(
        "复跑 skipped_reasons == {duplicate: 2}",
        r2["skipped_reasons"] == {"duplicate": 2},
        f"{r2['skipped_reasons']}",
    )
    check("复跑无新行（仍 2 行）", n_after_rerun == 2, f"n={n_after_rerun}")

    runner.calls.clear()
    r3 = _run_with_patched_trace(
        trade_date=_DAY,
        universe_path=universe,
        db_path=db,
        enabled=True,
        force=True,
        graph_runner=runner,
    )
    rows = list_cohort_runs(universe_version="v1", trade_date=_DAY, db_path=db)
    check("force 重跑两只", runner.calls == ["600519", "000001"], f"{runner.calls}")
    check("force success == 2", r3["success"] == 2, f"{r3['success']}")
    check("force 后 4 行", len(rows) == 4, f"n={len(rows)}")
    seqs = sorted(r["run_seq"] for r in rows)
    check("force 以 run_seq=1 另记", seqs == [0, 0, 1, 1], f"{seqs}")


# ── ③ 预算熔断 ──────────────────────────────────────────────────────────────
def scenario_budget_circuit_breaker() -> None:
    print("[3] 预算熔断 → 剩余 skipped / budget")
    db = _TMP_ROOT / "s3.db"
    universe = _universe_file(_TMP_ROOT / "u3.json", ["600519", "000001", "300750"])
    usage = {"prompt_tokens": 800, "completion_tokens": 400, "total_tokens": 1200}
    runner = _make_runner(usage_map=dict.fromkeys(("600519", "000001", "300750"), usage))
    cap = _Capture()
    logging.getLogger(_RUNNER_LOGGER).addHandler(cap)
    try:
        result = _run_with_patched_trace(
            trade_date=_DAY,
            universe_path=universe,
            db_path=db,
            enabled=True,
            max_tokens=1,  # 极小预算：首只即达限
            graph_runner=runner,
        )
    finally:
        logging.getLogger(_RUNNER_LOGGER).removeHandler(cap)

    check("达限后不再启动新分析（只跑 1 只）", runner.calls == ["600519"], f"{runner.calls}")
    check("budget_stopped True", result["budget_stopped"] is True)
    check(
        "success == 1 / skipped == 2",
        result["success"] == 1 and result["skipped"] == 2,
        f"{result['success']}/{result['skipped']}",
    )
    check(
        "skipped_reasons == {budget: 2}",
        result["skipped_reasons"] == {"budget": 2},
        f"{result['skipped_reasons']}",
    )
    check("熔断 WARN", any("熔断" in m and "budget" in m for m in cap.messages), f"{cap.messages}")

    rows = list_cohort_runs(universe_version="v1", trade_date=_DAY, db_path=db)
    check("cohort_runs 3 行（1 success + 2 skipped）", len(rows) == 3, f"n={len(rows)}")
    skipped = [r for r in rows if r["status"] == "skipped"]
    check(
        "剩余 2 只 status=skipped / reason=budget",
        {r["failure_reason"] for r in skipped} == {"budget"},
        f"{[r['failure_reason'] for r in skipped]}",
    )

    readings = collect_cohort_readings(db, universe_version="v1", since=_DAY)
    batch = readings["batch"]
    check(
        "readings skipped_reasons == {budget: 2}",
        batch["skipped_reasons"] == {"budget": 2},
        f"{batch['skipped_reasons']}",
    )
    check(
        "readings failure_reasons 不含 budget",
        "budget" not in batch["failure_reasons"],
        f"{batch['failure_reasons']}",
    )
    check(
        "readings run_success_rate == 1.0（skipped 不计分母）",
        batch["run_success_rate"] == 1.0,
        f"{batch['run_success_rate']}",
    )


# ── ④ 开关关闭：零调用零行 ──────────────────────────────────────────────────
def scenario_disabled() -> None:
    print("[4] 开关关闭 → 零 LLM 调用 + 零 cohort_runs 行")
    db = _TMP_ROOT / "s4.db"
    universe = _universe_file(_TMP_ROOT / "u4.json", ["600519", "000001"])
    runner = _make_runner()
    cap = _Capture()
    logging.getLogger(_RUNNER_LOGGER).addHandler(cap)
    try:
        result = _run_with_patched_trace(
            trade_date=_DAY, universe_path=universe, db_path=db, enabled=False, graph_runner=runner
        )
    finally:
        logging.getLogger(_RUNNER_LOGGER).removeHandler(cap)

    check("result enabled False", result["enabled"] is False)
    check("零 LLM 调用", runner.calls == [], f"{runner.calls}")
    check("零 cohort_runs 行（连库文件都未建）", not db.exists(), f"exists={db.exists()}")
    check(
        "预期证据日志「cohort 跑批未启用（COHORT_ENABLED != 1）」",
        any("cohort 跑批未启用（COHORT_ENABLED != 1）" in m for m in cap.messages),
        f"{cap.messages}",
    )

    # env 缺省（未设 COHORT_ENABLED）同样默认关
    db2 = _TMP_ROOT / "s4b.db"
    runner2 = _make_runner()
    os.environ.pop("COHORT_ENABLED", None)
    r2 = _run_with_patched_trace(
        trade_date=_DAY, universe_path=universe, db_path=db2, graph_runner=runner2
    )
    check(
        "env 未设 → 默认关（零调用）",
        r2["enabled"] is False and runner2.calls == [] and not db2.exists(),
    )


def main() -> int:
    # runner 的「未启用」是 INFO 级；默认 effective level=WARNING 会先滤掉 → 显式放行
    logging.getLogger(_RUNNER_LOGGER).setLevel(logging.INFO)
    print("=" * 72)
    print("Δ3 Task 7 离线端到端（零 LLM）：cohort 跑批 → 记账 → 读数")
    print("=" * 72)
    scenario_enabled_run()
    scenario_idempotent_and_force()
    scenario_budget_circuit_breaker()
    scenario_disabled()
    print("=" * 72)
    if _FAILURES:
        print(f"结论: FAIL（{len(_FAILURES)} 项）")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print("结论: PASS（全部核对项通过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
