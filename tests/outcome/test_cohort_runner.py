"""Δ3 Task 4：cohort 跑批执行器（runner）。

Delta add-forward-paper-trading-cohort；逐标的串行跑 deep 分析、幂等、预算熔断、
单标失败隔离、usage 真值记账。

**注入缝**：`graph_runner` 参数注入 fake 生成器（不依赖 patch `api.graph`）；
另有一条集成用例 patch `finance_agent.api.graph` 走真实 `_run_graph_streaming`
（验证 runner 对**真实** `report_ready` 事件形态的 json 解析）。

**usage 真值来源**：fake 在生成器内直接向**当前上下文**的 `_usage_acc` 汇入 usage
——这同时锚定「同线程 contextvar 可见」这一硬约束（经 executor/另起线程会静默 0）。
"""

import json
import logging

from finance_agent.outcome.cohort.model import (
    init_cohort_runs,
    insert_cohort_run,
    list_cohort_runs,
)
from finance_agent.outcome.cohort.runner import run_cohort_batch

_RUNNER_LOGGER = "finance_agent.outcome.cohort.runner"


def _sse(event: dict) -> str:
    """与 api._sse 同形：`data: {json}\\n\\n`。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _universe_file(tmp_path, tickers, version="v1"):
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
    path = tmp_path / f"universe-{version}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _feed_usage(usage: dict | None) -> None:
    """把 usage 汇入**当前上下文**的累加器（同线程可见 → 锚定硬约束 1）。"""
    from finance_agent.nodes import _llm_utils

    acc = _llm_utils._usage_acc.get()
    assert acc is not None, "runner 必须在同线程内激活 usage_collector（否则静默 0）"
    acc.add(usage)


_DAY = "2026-09-24"
_USAGE = {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}


def _make_runner(*, usage=_USAGE, fail=(), mode="report_ready", on_call=None):
    """fake graph_runner：记录调用、可抛异常、可只发 error / 不发 report_ready。"""
    calls: list[str] = []

    def runner(ticker, name, req, analysis_id, start_time, session_id=None, llm_config=None):
        calls.append(ticker)
        if on_call is not None:
            on_call(ticker, session_id)
        if ticker in fail:
            raise RuntimeError(f"boom-{ticker}")
        if usage is not None:
            _feed_usage(usage)
        if mode == "report_ready":
            yield _sse(
                {
                    "type": "report_ready",
                    "analysis_id": analysis_id,
                    "session_id": session_id,
                    "report_markdown": "# 报告",
                    "timestamp": "2026-09-24T18:00:00",
                }
            )
        elif mode == "error":
            yield _sse({"type": "error", "node_id": "x", "message": "生成失败"})
        # mode == "empty"：正常结束但无任何事件

    runner.calls = calls
    return runner


def _rows(db, version="v1", day=_DAY):
    return list_cohort_runs(universe_version=version, trade_date=day, db_path=db)


# ── 开关：关闭时零调用 ──────────────────────────────────────────────────────


def test_disabled_flag_returns_zero_calls(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519", "000001"])
    runner = _make_runner()
    monkeypatch.setenv("COHORT_ENABLED", "1")  # 参数优先于 env

    result = run_cohort_batch(
        trade_date=_DAY, universe_path=universe, db_path=db, enabled=False, graph_runner=runner
    )

    assert result["enabled"] is False
    assert result["success"] == 0 and result["failure"] == 0 and result["skipped"] == 0
    assert runner.calls == []
    assert not db.exists()  # 零落库（连库文件都不建）


def test_disabled_by_default_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("COHORT_ENABLED", raising=False)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519"])
    runner = _make_runner()

    result = run_cohort_batch(universe_path=universe, db_path=db, graph_runner=runner)

    assert result["enabled"] is False
    assert runner.calls == []


def test_enabled_env_one_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("COHORT_ENABLED", "1")
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519"])
    runner = _make_runner()

    result = run_cohort_batch(universe_path=universe, db_path=db, graph_runner=runner)

    assert result["enabled"] is True
    assert runner.calls == ["600519"]


# ── 正常：真值记账 ──────────────────────────────────────────────────────────


def test_two_constituents_success_accounting(tmp_path, monkeypatch):
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519", "000001"])

    def on_call(ticker, session_id):
        from finance_agent.session_store import set_session_trace_id

        set_session_trace_id(session_id, f"trace-{ticker}")

    runner = _make_runner(on_call=on_call)
    result = run_cohort_batch(
        trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
    )

    assert runner.calls == ["600519", "000001"]
    assert result["success"] == 2 and result["failure"] == 0 and result["skipped"] == 0
    assert result["universe_version"] == "v1" and result["trade_date"] == "2026-09-24"
    assert result["budget_stopped"] is False

    rows = _rows(db)
    assert len(rows) == 2
    for row in rows:
        assert row["status"] == "success" and row["run_seq"] == 0
        assert row["session_id"]
        assert row["langfuse_trace_id"] == f"trace-{row['ticker']}"
        assert row["llm_calls"] == 1
        assert (row["tokens_prompt"], row["tokens_completion"], row["tokens_total"]) == (
            100,
            40,
            140,
        )
        assert row["trigger_time"]
    assert result["tokens_total"] == 280


# ── usage 契约：真值缺失记 NULL + WARN（不记 0） ────────────────────────────


def test_usage_no_calls_records_null_and_warns(tmp_path, monkeypatch, caplog):
    """acc.calls == 0（未走到任何 LLM 调用）→ tokens_* NULL + WARN。"""
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519"])
    runner = _make_runner(usage=None)

    with caplog.at_level(logging.WARNING, logger=_RUNNER_LOGGER):
        result = run_cohort_batch(
            trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
        )

    assert result["success"] == 1
    row = _rows(db)[0]
    assert row["status"] == "success"
    assert row["llm_calls"] == 0
    assert row["tokens_prompt"] is None
    assert row["tokens_completion"] is None
    assert row["tokens_total"] is None, "真值缺失必须 NULL，不得记 0"
    assert any("NULL" in r.message or "usage" in r.message for r in caplog.records)
    assert result["tokens_total"] == 0


def test_usage_calls_but_zero_tokens_records_null_and_warns(tmp_path, monkeypatch, caplog):
    """calls>0 且 total_tokens==0（provider 未回计数）→ tokens_* NULL + WARN。"""
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519"])

    def runner(ticker, name, req, aid, st, session_id=None, llm_config=None):
        _feed_usage(None)  # calls += 1，tokens 全 0（provider 无计数）
        yield _sse({"type": "report_ready", "session_id": session_id})

    with caplog.at_level(logging.WARNING, logger=_RUNNER_LOGGER):
        result = run_cohort_batch(
            trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
        )

    assert result["success"] == 1
    row = _rows(db)[0]
    assert row["llm_calls"] == 1
    assert row["tokens_total"] is None, "calls>0 但 tokens=0 必须 NULL，不得记 0"
    assert caplog.records


# ── 幂等 ────────────────────────────────────────────────────────────────────


def test_duplicate_success_skipped_without_new_row(tmp_path, monkeypatch):
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519", "000001"])
    init_cohort_runs(db)
    insert_cohort_run(
        {
            "universe_version": "v1",
            "ticker": "600519",
            "trade_date": "2026-09-24",
            "status": "success",
            "run_seq": 0,
            "trigger_time": "2026-09-24T09:00:00",
        },
        db,
    )
    runner = _make_runner()

    result = run_cohort_batch(
        trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
    )

    assert runner.calls == ["000001"], "已 success 的标的不再调用"
    assert result["success"] == 1 and result["skipped"] == 1
    assert result["skipped_reasons"] == {"duplicate": 1}
    assert len(_rows(db)) == 2, "重复跳过不写行（Task 1 幂等语义）"


def test_force_reruns_with_next_seq(tmp_path, monkeypatch):
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519"])
    init_cohort_runs(db)
    insert_cohort_run(
        {
            "universe_version": "v1",
            "ticker": "600519",
            "trade_date": "2026-09-24",
            "status": "success",
            "run_seq": 0,
            "trigger_time": "2026-09-24T09:00:00",
        },
        db,
    )
    runner = _make_runner()

    result = run_cohort_batch(
        trade_date=_DAY,
        universe_path=universe,
        db_path=db,
        enabled=True,
        force=True,
        graph_runner=runner,
    )

    assert runner.calls == ["600519"]
    assert result["success"] == 1 and result["skipped"] == 0
    rows = _rows(db)
    assert [r["run_seq"] for r in rows] == [0, 1], "force 以既有最大 seq+1 另记一行"


# ── 失败隔离 ────────────────────────────────────────────────────────────────


def test_failure_isolation_continues_batch(tmp_path, monkeypatch):
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519", "000001", "300750"])
    runner = _make_runner(fail={"000001"})

    result = run_cohort_batch(
        trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
    )

    assert runner.calls == ["600519", "000001", "300750"], "单标失败不得中断后续标的"
    assert result["success"] == 2 and result["failure"] == 1 and result["skipped"] == 0
    by_ticker = {r["ticker"]: r for r in _rows(db)}
    assert by_ticker["000001"]["status"] == "failure"
    assert "boom-000001" in by_ticker["000001"]["failure_reason"]
    assert by_ticker["300750"]["status"] == "success"


# ── 预算熔断 ────────────────────────────────────────────────────────────────


def test_budget_circuit_breaker_skips_rest(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519", "000001", "300750"])
    runner = _make_runner(
        usage={"prompt_tokens": 800, "completion_tokens": 400, "total_tokens": 1200}
    )

    with caplog.at_level(logging.WARNING, logger=_RUNNER_LOGGER):
        result = run_cohort_batch(
            trade_date=_DAY,
            universe_path=universe,
            db_path=db,
            enabled=True,
            max_tokens=1,
            graph_runner=runner,
        )

    assert runner.calls == ["600519"], "达限后不得再启动新分析"
    assert result["budget_stopped"] is True
    assert result["success"] == 1 and result["skipped"] == 2
    assert result["skipped_reasons"] == {"budget": 2}
    rows = _rows(db)
    assert len(rows) == 3
    skipped = [r for r in rows if r["status"] == "skipped"]
    assert {r["ticker"] for r in skipped} == {"000001", "300750"}
    assert all(r["failure_reason"] == "budget" for r in skipped)
    assert any("预算熔断" in r.message for r in caplog.records)


def test_budget_env_default_used_when_param_absent(tmp_path, monkeypatch):
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    monkeypatch.setenv("COHORT_MAX_TOKENS_PER_RUN", "1")
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519", "000001"])
    runner = _make_runner(usage={"total_tokens": 500})

    result = run_cohort_batch(
        trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
    )

    assert result["budget_stopped"] is True
    assert result["skipped"] == 1


# ── report_ready 判定（严格 json 解析，非子串） ─────────────────────────────


def test_no_report_ready_is_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519"])
    runner = _make_runner(mode="empty")

    result = run_cohort_batch(
        trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
    )

    assert result["failure"] == 1
    row = _rows(db)[0]
    assert row["status"] == "failure"
    assert row["failure_reason"] == "no_report_ready"


def test_error_event_without_report_ready_names_event_type(tmp_path, monkeypatch):
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519"])
    runner = _make_runner(mode="error")

    result = run_cohort_batch(
        trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
    )

    assert result["failure"] == 1
    reason = _rows(db)[0]["failure_reason"]
    assert reason.startswith("no_report_ready")
    assert "error" in reason


def test_substring_report_ready_in_text_is_not_success(tmp_path, monkeypatch):
    """严格解析：普通文本里出现 `report_ready` 字样不得判成功。"""
    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519"])

    def runner(ticker, name, req, aid, st, session_id=None, llm_config=None):
        yield _sse({"type": "node_complete", "output": '有人提到 "report_ready" 字样'})

    runner.calls = []
    result = run_cohort_batch(
        trade_date=_DAY, universe_path=universe, db_path=db, enabled=True, graph_runner=runner
    )

    assert result["failure"] == 1
    assert _rows(db)[0]["failure_reason"] == "no_report_ready"


# ── 集成：真实 _run_graph_streaming（patch api.graph） ──────────────────────


class _FakeGraph:
    def stream(self, state, config=None, stream_mode=None):
        yield ("updates", {"report_writer": {"final_report": "# 报告\n\n正文内容"}})


def test_real_graph_runner_path_writes_row(tmp_path, monkeypatch):
    """不注入 graph_runner → 走真实 api._run_graph_streaming（仅图被打桩）。

    锚定 runner 对**真实** report_ready 事件载荷的解析（字段名/SSE 形态）。
    """
    from unittest.mock import patch

    monkeypatch.setattr("finance_agent.outcome.cohort.runner._live_trace_id", lambda: None)
    db = tmp_path / "c.db"
    universe = _universe_file(tmp_path, ["600519"])

    with patch("finance_agent.api.graph", _FakeGraph()):
        result = run_cohort_batch(trade_date=_DAY, universe_path=universe, db_path=db, enabled=True)

    assert result["success"] == 1
    row = _rows(db)[0]
    assert row["status"] == "success"
    assert row["session_id"]
    # 图被打桩、无真实 LLM 调用 → token 真值缺失，记 NULL（非 0）
    assert row["tokens_total"] is None
