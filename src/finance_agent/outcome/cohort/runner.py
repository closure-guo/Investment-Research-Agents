"""cohort 跑批执行器（delta add-forward-paper-trading-cohort）。

逐标的**串行**跑一次 deep 分析（fast path ``api._run_graph_streaming``），把每次
运行的真值（session / langfuse trace / token usage）落 ``cohort_runs``。

**上下文传播（硬约束的准确表述）**：usage 经 ``contextvars`` 传递。LangGraph 的
并行分支经 executor ``copy_context`` 会传播本 contextvar，故 fan-out 内部的 LLM
调用**正常汇入**（并发 ``add`` 由 ``UsageAccumulator`` 实例级锁保护）。真正失效的
是**外部**用 ``loop.run_in_executor`` / 裸 ``threading.Thread`` 起线程执行**整个
生成器**（不复制 context → ``_usage_acc.get()`` 返回 None → **静默** 0 calls /
0 tokens）。**runner 因此必须直接同线程迭代生成器**：
``with usage_collector() as acc: for chunk in runner(...)``，不得把整个生成器交给
executor / 另起线程（详见 ``nodes._llm_utils.usage_collector`` docstring）。

**usage 真值非估算（硬约束）**：``acc.calls > 0 and acc.total_tokens == 0``
（provider 未回计数）或 ``acc.calls == 0``（未走到任何 LLM 调用）→ token 记账
``NULL`` + WARNING，**不记 0**（0 会被读成「零成本」，NULL 才表达「真值缺失」）。
有真值则如实记 ``prompt/completion/total`` 与 ``calls``——**失败路径同样记**：
异常退出时若累加器已有真实计数（跑了 N 次调用后失败），照实并入该行，且计入
本轮 ``spent``（贵的失败不得逃出预算）。

**未知 usage 熔断（Δ3T4 审查 I3）**：真值缺失（NULL）时 ``spent`` **不增长**，
故单靠 token 预算无法防「provider 一直不回计数」导致的无限跑。故另设
``_UNKNOWN_USAGE_LIMIT``：累计 ``_UNKNOWN_USAGE_LIMIT`` 次「usage 真值缺失」的
运行后停止剩余标的，记 ``failure_reason="budget_unknown"`` + WARNING——把
「无真值」与「零消耗」显式区分开（NULL 不计入 ``spent``，但计入未知计数）。

**已知低估场景（本任务不修，待 Δ3 Task 7 与 Langfuse usage_details 对账）**：
截断重试时 ``finished`` 事件只承载**末段** usage，二次截断走 error 分支不发
``finished`` → 该次调用的 usage 可能被低估甚至漏计。故 ``cohort_runs`` 的 token
数是「LLM 网关上报 usage 的下界」；Task 7 导出入指标前须与 Langfuse 逐 trace 的
``usage_details`` 对账。本模块不改 gateway 事件契约。

**幂等**：键 ``(universe_version, ticker, trade_date)``。已 ``success`` → 跳过
（不写行，与 Task 1 语义一致）；``force=True`` 绕过跳过，并以
``run_seq = 既有最大 seq + 1`` 另记一行。失败/预算跳过行同样取 next seq，
避免 UNIQUE 冲突（已失败标的非 force 重跑也得新 seq，不撞唯一约束）。

**失败隔离（Δ3T4 审查 I2）**：单标的从「建 session」到「落账」全链路都在
每标的同一 ``try`` 内——session 存储异常、SQLite 冲突均只影响该标的（尽力记
failure 行；记不上则 WARNING），批次继续跑下一标的。

**trace 关联（Δ3T4 审查 I4）**：``predictions`` 表**无 ``session_id`` 列**，Task 6
的 join 只能靠 ``langfuse_trace_id``。故解析到 trace 后**回写**
``session_store.set_session_trace_id(session_id, trace)``，使会话侧也持久化；
再读回兜底（``get_session_trace_id``）以防 live 取值缺失。

**failure_reason 为稳定码（Δ3T4 审查 M1）**：取值限于 ``FAILURE_CODES``，
异常消息只进日志，不进 DB 字段（Task 6 的 ``failure_reasons`` 聚合键必须有限）。
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
# 累计「usage 真值缺失」运行数达此限 → 停止剩余标的（budget_unknown）
_UNKNOWN_USAGE_LIMIT = 3
# failure_reason 稳定码全集（Task 6 聚合键必须有限；异常消息只进日志）。
# 注：`duplicate` 仅体现在 run_cohort_batch 的返回值 skipped_reasons（重复分支
# continue，**不写行**），不会出现在 cohort_runs.failure_reason / 读数 skipped_reasons 桶。
FAILURE_CODES = ("no_report_ready", "exception", "budget", "budget_unknown", "duplicate")


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

    池路径解析：``universe_path`` 参数优先，否则 ``COHORT_UNIVERSE_PATH`` 环境变量，
    最后回退 ``DEFAULT_UNIVERSE_PATH``（``data/cohort/universe-v1.json``）。

    ``enabled`` 参数优先，否则 ``COHORT_ENABLED == "1"``（默认关）；关闭时**零调用**
    直接返回。``max_tokens`` 参数优先，否则 ``COHORT_MAX_TOKENS_PER_RUN``
    （默认 2_000_000，非数值时 WARN 回退默认）；累计 ``spent`` 达限或未知 usage
    达 ``_UNKNOWN_USAGE_LIMIT`` 后剩余标的记 ``skipped``（``budget`` /
    ``budget_unknown``），**已启动的分析正常完成**。``graph_runner`` 为注入缝
    （默认 ``api._run_graph_streaming``），供测试注入 fake 生成器。
    """
    if enabled is None:
        enabled = os.getenv("COHORT_ENABLED") == "1"
    if not enabled:
        logger.info("cohort 跑批未启用（COHORT_ENABLED != 1）")
        return _empty_result()

    universe = load_universe(
        universe_path or os.getenv("COHORT_UNIVERSE_PATH") or DEFAULT_UNIVERSE_PATH
    )
    day = trade_date or datetime.now().strftime("%Y-%m-%d")
    budget = _resolve_budget(max_tokens)

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
    unknown_usage_runs = 0
    stop_reason: str | None = None

    for constituent in universe.constituents:
        if not force and constituent.ticker in existing:
            _count_skip(skipped_reasons, "duplicate")
            result["skipped"] += 1
            continue
        if stop_reason is not None:
            _record_skipped_safely(universe.version, constituent.ticker, day, stop_reason, db_path)
            _count_skip(skipped_reasons, stop_reason)
            result["skipped"] += 1
            continue

        outcome = _run_one(constituent, graph_runner=graph_runner)  # 不抛（内部隔离）
        _record_outcome_safely(universe.version, constituent.ticker, day, outcome, db_path)
        tokens = outcome.get("tokens_total")
        spent += tokens or 0
        result["tokens_total"] = spent
        if tokens is None:
            unknown_usage_runs += 1
        result["success" if outcome["status"] == "success" else "failure"] += 1

        if spent >= budget:
            stop_reason = "budget"
        elif unknown_usage_runs >= _UNKNOWN_USAGE_LIMIT:
            stop_reason = "budget_unknown"
        if stop_reason is not None:
            result["budget_stopped"] = True
            logger.warning(
                "cohort 单轮熔断（reason=%s spent=%s budget=%s unknown_usage_runs=%s）",
                stop_reason,
                spent,
                budget,
                unknown_usage_runs,
            )

    result["skipped_reasons"] = skipped_reasons
    return result


def _resolve_budget(max_tokens: int | None) -> int:
    """预算：参数优先；env 非数值时 WARN 回退默认（ops 手滑不得中止整批）。"""
    if max_tokens is not None:
        return max_tokens
    raw = os.getenv("COHORT_MAX_TOKENS_PER_RUN")
    if raw is None:
        return _DEFAULT_MAX_TOKENS
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "COHORT_MAX_TOKENS_PER_RUN 非数值（%r），回退默认 %d", raw, _DEFAULT_MAX_TOKENS
        )
        return _DEFAULT_MAX_TOKENS


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
    """单标的：建 session → 同线程迭代 fast path 生成器（收集 usage）→ 判 report_ready。

    全链路（建 session / 迭代 / 落账前的 trace 回写）在本函数内隔离：**绝不抛出**，
    失败一律返回 ``status="failure"`` 的结果 dict。
    """
    from finance_agent.api import AnalyzeRequest, _run_graph_streaming

    trigger = datetime.now().isoformat()
    acc: UsageAccumulator | None = None
    session_id: str | None = None
    report_ready = False
    error_event: dict[str, Any] | None = None
    trace_id: str | None = None

    try:
        session_id = _create_session(constituent)
        req = AnalyzeRequest(
            query=f"深度分析{constituent.name}",
            stock_code=constituent.ticker,
            stock_name=constituent.name,
        )
        runner = graph_runner or _run_graph_streaming

        # 硬约束：直接同线程迭代（不经 executor/裸 Thread——那些不复制 contextvar，
        # _usage_acc.get() 返回 None → 静默 0；fan-out 内部经 copy_context 传播可正常汇入）
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
        _persist_session_trace(session_id, trace_id)
        if not report_ready:
            _log_no_report_ready(constituent.ticker, error_event)
            return {
                "status": "failure",
                "failure_reason": "no_report_ready",
                "session_id": session_id,
                "langfuse_trace_id": trace_id or _session_trace_id(session_id),
                "trigger_time": trigger,
                **usage,
            }
        return {
            "status": "success",
            "session_id": session_id,
            "langfuse_trace_id": trace_id or _session_trace_id(session_id),
            "trigger_time": trigger,
            **usage,
        }
    except Exception as exc:  # noqa: BLE001 - 单标失败隔离（绝不外抛）
        logger.warning("cohort 单标失败 %s: %s", constituent.ticker, exc, exc_info=True)
        # 失败路径仍如实记真值：acc 可能已有真实计数（跑了 N 次调用后失败）
        usage = _usage_fields(acc, constituent.ticker) if acc is not None else _null_usage_fields()
        _persist_session_trace(session_id, trace_id)
        return {
            "status": "failure",
            "failure_reason": "exception",  # 稳定码；异常消息只进日志
            "session_id": session_id,
            "langfuse_trace_id": trace_id or _session_trace_id(session_id),
            "trigger_time": trigger,
            **usage,
        }


def _log_no_report_ready(ticker: str, error_event: dict[str, Any] | None) -> None:
    """无 report_ready 的告警（事件类型/消息只进日志，不进 DB 稳定码）。"""
    if error_event is None:
        logger.warning("cohort %s: 生成器结束但无 report_ready", ticker)
        return
    logger.warning(
        "cohort %s: 无 report_ready（末事件 type=%s message=%s）",
        ticker,
        error_event.get("type"),
        str(error_event.get("message") or "")[:160],
    )


def _usage_fields(acc: UsageAccumulator, ticker: str) -> dict[str, Any]:
    """usage 记账契约：真值缺失（无调用 / provider 未回计数）→ NULL + WARN，不记 0。"""
    if acc.calls == 0:
        logger.warning(
            "cohort %s: 未记录到任何 LLM 调用，token 记账为 NULL（真值缺失，非 0）", ticker
        )
        return _null_usage_fields(llm_calls=0)
    if acc.total_tokens == 0:
        logger.warning(
            "cohort %s: calls=%d 但 provider usage 为 0，token 记账为 NULL（真值缺失，非 0）",
            ticker,
            acc.calls,
        )
        return _null_usage_fields(llm_calls=acc.calls)
    return {
        "llm_calls": acc.calls,
        "tokens_prompt": acc.prompt_tokens,
        "tokens_completion": acc.completion_tokens,
        "tokens_total": acc.total_tokens,
    }


def _null_usage_fields(llm_calls: int | None = None) -> dict[str, Any]:
    """真值缺失的记账字段（token 全 NULL；``llm_calls`` 未知时亦 NULL）。"""
    return {
        "llm_calls": llm_calls,
        "tokens_prompt": None,
        "tokens_completion": None,
        "tokens_total": None,
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


def _persist_session_trace(session_id: str | None, trace_id: str | None) -> None:
    """把 trace 回写 session（``predictions`` 无 session_id 列，Task 6 join 靠 trace）。"""
    if not session_id or not trace_id:
        return
    try:
        from finance_agent.session_store import set_session_trace_id

        set_session_trace_id(session_id, trace_id)
    except Exception as exc:  # noqa: BLE001 - trace 关联为尽力而为
        logger.warning("cohort 回写 session trace_id 失败 %s: %s", session_id, exc)


def _session_trace_id(session_id: str | None) -> str | None:
    """读回 session 侧 trace id（本模块已回写；亦兼容他处预设值）。"""
    if not session_id:
        return None
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


def _record_outcome_safely(
    version: str,
    ticker: str,
    day: str,
    outcome: dict[str, Any],
    db_path: str | Path | None,
) -> None:
    """落账（含 run_seq 计算）隔离：SQLite 冲突/写失败只 WARN，不中止批次。"""
    try:
        outcome["run_seq"] = _next_seq(version, ticker, day, db_path)
        _record(version, ticker, day, outcome, db_path)
    except Exception as exc:  # noqa: BLE001 - 记账失败不得中止整批
        logger.warning("cohort 记账失败（跳过该行，批次继续）%s: %s", ticker, exc)


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


def _record_skipped_safely(
    version: str, ticker: str, day: str, reason: str, db_path: str | Path | None
) -> None:
    """预算/未知 usage 跳过行落账隔离（写失败只 WARN，批次继续）。"""
    try:
        _record_skipped(version, ticker, day, reason, db_path)
    except Exception as exc:  # noqa: BLE001 - 记账失败不得中止整批
        logger.warning("cohort 跳过行记账失败（批次继续）%s: %s", ticker, exc)
