"""cohort 跑批执行器（delta add-forward-paper-trading-cohort）。

逐标的**串行**跑一次 deep 分析（fast path ``api._run_graph_streaming``），把每次
运行的真值（session / langfuse trace / token usage）落 ``cohort_runs``。

**同线程约束（硬约束）**：usage 经 ``contextvars`` 传递，必须**同线程直接迭代**
生成器——``with usage_collector() as acc: for chunk in runner(...)``。**不得**经
``loop.run_in_executor`` / 自起 ``threading.Thread``（不继承 contextvar →
``_usage_acc.get()`` 返回 None → **静默** 0 calls / 0 tokens，见
``nodes._llm_utils.usage_collector`` docstring）。

**usage 真值非估算（硬约束）**：``acc.calls > 0 and acc.total_tokens == 0``
（provider 未回计数）或 ``acc.calls == 0``（未走到任何 LLM 调用）→ token 记账
``NULL`` + WARNING，**不记 0**（0 会被读成「零成本」，NULL 才表达「真值缺失」）。
有真值则如实记 ``prompt/completion/total`` 与 ``calls``。

**已知低估场景（本任务不修，待 Δ3 Task 7 与 Langfuse usage_details 对账）**：
截断重试时 ``finished`` 事件只承载**末段** usage，二次截断走 error 分支不发
``finished`` → 该次调用的 usage 可能被低估甚至漏计。故 ``cohort_runs`` 的 token
数是「LLM 网关上报 usage 的下界」；Task 7 导出入指标前须与 Langfuse 逐 trace 的
``usage_details`` 对账。本模块不改 gateway 事件契约。

**幂等**：键 ``(universe_version, ticker, trade_date)``。已 ``success`` → 跳过
（不写行，与 Task 1 语义一致）；``force=True`` 绕过跳过，并以
``run_seq = 既有最大 seq + 1`` 另记一行。失败/预算跳过行同样取 next seq，
避免 UNIQUE 冲突。

**失败隔离**：单标的异常 / 无 ``report_ready`` 只记该行 ``failure``，批内其余
标的继续。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.nodes._llm_utils import UsageAccumulator, usage_collector
from finance_agent.outcome.cohort.model import (
    init_cohort_runs,
    insert_cohort_run,
    list_cohort_runs,
)
from finance_agent.outcome.cohort.universe import Constituent, load_universe

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSE_PATH = Path("data/cohort/universe-v1.json")
_DEFAULT_MAX_TOKENS = 2_000_000


def run_cohort_batch(
    *,
    universe_path: str | Path | None = None,
    db_path: str | Path | None = None,
    trade_date: str | None = None,
    force: bool = False,
    max_tokens: int | None = None,
    enabled: bool | None = None,
    graph_runner: Callable[..., Iterator[str]] | None = None,
) -> dict[str, Any]:
    """探班池内逐标的串行跑 deep 分析；幂等键 (version, ticker, trade_date)。

    ``enabled`` 参数优先，否则 ``COHORT_ENABLED == "1"``（默认关）；关闭时**零调用**
    直接返回。``max_tokens`` 参数优先，否则 ``COHORT_MAX_TOKENS_PER_RUN``
    （默认 2_000_000）；累计 ``spent`` 达限后剩余标的记 ``skipped/budget``，
    **已启动的分析正常完成**。``graph_runner`` 为注入缝（默认
    ``api._run_graph_streaming``），供测试注入 fake 生成器。
    """
    if enabled is None:
        enabled = os.getenv("COHORT_ENABLED") == "1"
    if not enabled:
        logger.info("cohort 跑批未启用（COHORT_ENABLED != 1）")
        return _empty_result()

    universe = load_universe(universe_path or DEFAULT_UNIVERSE_PATH)
    day = trade_date or datetime.now().strftime("%Y-%m-%d")
    budget = (
        max_tokens
        if max_tokens is not None
        else int(os.getenv("COHORT_MAX_TOKENS_PER_RUN", str(_DEFAULT_MAX_TOKENS)))
    )

    init_cohort_runs(db_path)
    existing = _successful_tickers(universe.version, day, db_path)

    result: dict[str, Any] = {
        "enabled": True,
        "universe_version": universe.version,
        "trade_date": day,
        "success": 0,
        "failure": 0,
        "skipped": 0,
        "skipped_reasons": {},
        "tokens_total": 0,
        "budget_stopped": False,
    }
    skipped_reasons: dict[str, int] = {}
    spent = 0
    budget_stopped = False

    for constituent in universe.constituents:
        if not force and constituent.ticker in existing:
            _count_skip(skipped_reasons, "duplicate")
            result["skipped"] += 1
            continue
        if spent >= budget:
            budget_stopped = True
            _record_skipped(universe.version, constituent.ticker, day, "budget", db_path)
            _count_skip(skipped_reasons, "budget")
            result["skipped"] += 1
            continue

        outcome = _run_one(constituent, graph_runner=graph_runner)
        outcome["run_seq"] = _next_seq(universe.version, constituent.ticker, day, db_path)
        _record(universe.version, constituent.ticker, day, outcome, db_path)
        spent += outcome.get("tokens_total") or 0
        result["tokens_total"] = spent
        result["success" if outcome["status"] == "success" else "failure"] += 1

    result["skipped_reasons"] = skipped_reasons
    result["budget_stopped"] = budget_stopped
    if budget_stopped:
        logger.warning("cohort 单轮预算熔断（spent=%s >= budget=%s）", spent, budget)
    return result


def _empty_result() -> dict[str, Any]:
    return {
        "enabled": False,
        "universe_version": None,
        "trade_date": None,
        "success": 0,
        "failure": 0,
        "skipped": 0,
        "skipped_reasons": {},
        "tokens_total": 0,
        "budget_stopped": False,
    }


def _count_skip(counter: dict[str, int], reason: str) -> None:
    counter[reason] = counter.get(reason, 0) + 1


def _successful_tickers(version: str, day: str, db_path: str | Path | None) -> set[str]:
    """该 (version, trade_date) 下已 ``success`` 的 ticker 集合（幂等跳过依据）。"""
    return {
        row["ticker"]
        for row in list_cohort_runs(universe_version=version, trade_date=day, db_path=db_path)
        if row["status"] == "success"
    }


def _next_seq(version: str, ticker: str, day: str, db_path: str | Path | None) -> int:
    """该键下下一可用 ``run_seq``（既有最大 seq + 1；无既有行则 0）。"""
    seqs = [
        row["run_seq"]
        for row in list_cohort_runs(universe_version=version, trade_date=day, db_path=db_path)
        if row["ticker"] == ticker
    ]
    return max(seqs, default=-1) + 1


def _run_one(
    constituent: Constituent,
    *,
    graph_runner: Callable[..., Iterator[str]] | None,
) -> dict[str, Any]:
    """单标的：建 session → 同线程迭代 fast path 生成器（收集 usage）→ 判 report_ready。"""
    from finance_agent.api import AnalyzeRequest, _run_graph_streaming

    session_id = _create_session(constituent)
    req = AnalyzeRequest(
        query=f"深度分析{constituent.name}",
        stock_code=constituent.ticker,
        stock_name=constituent.name,
    )
    runner = graph_runner or _run_graph_streaming
    trigger = datetime.now().isoformat()
    report_ready = False
    error_event: dict[str, Any] | None = None
    trace_id: str | None = None

    try:
        # 硬约束：同线程直接迭代（contextvar 才可见；executor/新线程会静默 0）
        with usage_collector() as acc:
            for chunk in runner(
                constituent.ticker,
                constituent.name,
                req,
                uuid.uuid4().hex,
                time.time(),
                session_id=session_id,
            ):
                event = _parse_sse_event(chunk)
                if event is None:
                    continue
                if event.get("type") == "report_ready":
                    report_ready = True
                    trace_id = _live_trace_id() or trace_id
                elif event.get("type") == "error":
                    error_event = event
        usage = _usage_fields(acc, constituent.ticker)
    except Exception as exc:  # noqa: BLE001 - 单标失败隔离
        logger.warning("cohort 单标失败 %s: %s", constituent.ticker, exc)
        return {
            "status": "failure",
            "failure_reason": str(exc)[:200],
            "session_id": session_id,
            "langfuse_trace_id": None,
            "trigger_time": trigger,
        }

    resolved_trace = trace_id or _session_trace_id(session_id)
    if not report_ready:
        return {
            "status": "failure",
            "failure_reason": _no_report_ready_reason(error_event),
            "session_id": session_id,
            "langfuse_trace_id": resolved_trace,
            "trigger_time": trigger,
            **usage,
        }
    return {
        "status": "success",
        "session_id": session_id,
        "langfuse_trace_id": resolved_trace,
        "trigger_time": trigger,
        **usage,
    }


def _no_report_ready_reason(error_event: dict[str, Any] | None) -> str:
    """无 report_ready 的失败原因；观测到 error 事件时附事件类型与消息（便于归因）。"""
    if error_event is None:
        return "no_report_ready"
    message = str(error_event.get("message") or "")[:160]
    return f"no_report_ready(error: {message})" if message else "no_report_ready(error)"


def _usage_fields(acc: UsageAccumulator, ticker: str) -> dict[str, Any]:
    """usage 记账契约：真值缺失（无调用 / provider 未回计数）→ NULL + WARN，不记 0。"""
    if acc.calls == 0:
        logger.warning(
            "cohort %s: 未记录到任何 LLM 调用，token 记账为 NULL（真值缺失，非 0）", ticker
        )
        return {
            "llm_calls": 0,
            "tokens_prompt": None,
            "tokens_completion": None,
            "tokens_total": None,
        }
    if acc.total_tokens == 0:
        logger.warning(
            "cohort %s: calls=%d 但 provider usage 为 0，token 记账为 NULL（真值缺失，非 0）",
            ticker,
            acc.calls,
        )
        return {
            "llm_calls": acc.calls,
            "tokens_prompt": None,
            "tokens_completion": None,
            "tokens_total": None,
        }
    return {
        "llm_calls": acc.calls,
        "tokens_prompt": acc.prompt_tokens,
        "tokens_completion": acc.completion_tokens,
        "tokens_total": acc.total_tokens,
    }


def _parse_sse_event(chunk: str) -> dict[str, Any] | None:
    """严格解析 SSE 行 ``data: {json}\\n\\n`` → 事件 dict；非 SSE / 非法 JSON → None。

    不用子串匹配判 ``report_ready``：普通文本（报告正文、节点 output）含该字样
    时不得误判成功。
    """
    if not isinstance(chunk, str):
        return None
    body: str | None = None
    for line in chunk.splitlines():
        if line.startswith("data:"):
            body = line[len("data:") :].strip()
            break
    if not body:
        return None
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _create_session(constituent: Constituent) -> str:
    """建 session（session_store 用模块级 ``_DB_PATH``；不接收 db_path）。"""
    from finance_agent.session_store import create_session

    return create_session(
        stock_code=constituent.ticker,
        stock_name=constituent.name,
        status="running",
        session_type="analysis",
    )


def _session_trace_id(session_id: str) -> str | None:
    """session 表最近一次运行的 trace id（fast path 未必写；缺失返回 None）。"""
    try:
        from finance_agent.session_store import get_session_trace_id

        return get_session_trace_id(session_id)
    except Exception:  # noqa: BLE001 - trace 关联为尽力而为
        return None


def _live_trace_id() -> str | None:
    """当前 Langfuse 上下文的 trace id（须在 root span 存活期内取；无则 None）。"""
    try:
        from finance_agent.langfuse_tracing import get_langfuse

        client = get_langfuse()
        return client.get_current_trace_id() if client is not None else None
    except Exception:  # noqa: BLE001 - trace 关联为尽力而为
        return None


def _record(
    version: str,
    ticker: str,
    day: str,
    outcome: dict[str, Any],
    db_path: str | Path | None,
) -> None:
    insert_cohort_run(
        {
            "universe_version": version,
            "ticker": ticker,
            "trade_date": day,
            "status": outcome["status"],
            "failure_reason": outcome.get("failure_reason"),
            "run_seq": outcome.get("run_seq", 0),
            "session_id": outcome.get("session_id"),
            "langfuse_trace_id": outcome.get("langfuse_trace_id"),
            "llm_calls": outcome.get("llm_calls"),
            "tokens_prompt": outcome.get("tokens_prompt"),
            "tokens_completion": outcome.get("tokens_completion"),
            "tokens_total": outcome.get("tokens_total"),
            "trigger_time": outcome["trigger_time"],
        },
        db_path,
    )


def _record_skipped(
    version: str, ticker: str, day: str, reason: str, db_path: str | Path | None
) -> None:
    insert_cohort_run(
        {
            "universe_version": version,
            "ticker": ticker,
            "trade_date": day,
            "status": "skipped",
            "failure_reason": reason,
            "run_seq": _next_seq(version, ticker, day, db_path),
            "trigger_time": datetime.now().isoformat(),
        },
        db_path,
    )
