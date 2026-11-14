"""精选池自动深度分析编排（add-auto-pool-analysis-daily-brief）。

职责边界：

- **本模块只做同步编排**：读当日成功池、截断、时效性校验、建会话、写幂等表、调用
  ``enqueue``。真正的队列补位与管线执行在 API 进程的主事件循环里（``AnalyzeQueue`` +
  ``PipelineRunner``），由 ``api.submit_pool_analysis_batch`` 桥接。这样自动批次与人工
  批次共用同一套 SSE/journal 与并发上限。
- **池新鲜度**：名单取自最近成功批次，且批次日须为「当日或前一交易日」（按快照
  交易日历判定；无历不猜，退回仅当日）。只认当日会把深夜补跑出的池永久挡在门外
  （issue #456 缺陷 B：10-21~23 连续三天池分析空转）；更旧的池仍 ``skipped_stale_pool``
  ——选股连续失败多日时不得拿过期池冒充当日结果。
- **幂等键 = (trade_date, symbol)**：同一天重复跑（手动补跑/重试）对 ``queued`` /
  ``completed`` 组合不重复建会话、不重复入队、不重复烧分析；失败行
  （``failed`` / ``dispatch_failed`` / ``session_failed``）重派是唯一例外——一律创建
  **全新会话**重新入队，旧 ``session_id`` 不復用（终态会话不被快路径重置为 running，
  复用入队即被队列终态轮询判死，issue #546；10-28 恢复实操 9 只秒灭实证），旧会话
  原样保留作审计，运行行 ``session_id`` 由幂等 upsert 更新为新会话。
- **失败留痕**：建会话失败 / 入队失败分别记 ``session_failed`` / ``dispatch_failed``，不静默
  吞掉；当日汇报会读到这些行。``reconcile_pool_analysis_runs`` 在每次编排前把已终结
  会话的终态回写到运行行（issue #456 缺陷 A：会话完成而行停 ``queued``，运维侧无法
  据此判断链路是否在工作）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.screening.freshness import screening_freshness_removals
from finance_agent.screening.pipeline import latest_successful_pool, read_snapshot_calendar
from finance_agent.session_store import (
    append_chat,
    append_session_event,
    create_session,
    get_session,
    set_pipeline_anchor,
    update_session_for_clarify,
)

logger = logging.getLogger("finance_agent.screening.pool_analysis")

AUTO_POOL_DISPLAY_SUFFIX = "自动选股分析"
AUTO_POOL_MICROCAP_DISPLAY_SUFFIX = "小票池自动分析"
DEFAULT_MAX_SYMBOLS = 10
MAX_SYMBOLS_CAP = 100

# 池归属常量（add-microcap-lane-analysis）：运行行标注来源档位；列不进主键，
# (trade_date, symbol) 全局幂等键保持不变——跨池去重靠它实现。
POOL_MAIN = "main"
POOL_MICROCAP = "microcap"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pool_analysis_runs (
  trade_date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  session_id TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  pool TEXT NOT NULL DEFAULT 'main',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, symbol)
)
"""

# 运行表增量列（ALTER 补齐；模式照抄 screening/store.py 的 _ensure_batch_columns）。
_RUNS_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("pool", "ALTER TABLE pool_analysis_runs ADD COLUMN pool TEXT NOT NULL DEFAULT 'main'"),
)


def _default_db_path() -> Path:
    """screening 库路径（调用期读 env，测试经 SCREENING_DB_PATH 覆盖）。"""
    return Path(os.getenv("SCREENING_DB_PATH", "data/screening.db"))


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_pool_analysis_runs(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(_SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pool_analysis_runs)")}
        for name, ddl in _RUNS_COLUMN_MIGRATIONS:
            if name not in columns:
                conn.execute(ddl)
        conn.commit()


def _now() -> str:
    return datetime.now().isoformat()


def record_pool_analysis_run(
    trade_date: str,
    symbol: str,
    session_id: str,
    status: str = "queued",
    error: str | None = None,
    *,
    pool: str = POOL_MAIN,
    db_path: str | Path | None = None,
) -> None:
    """写入/更新一条自动分析运行行（同 (trade_date, symbol) 幂等 upsert）。

    ON CONFLICT 更新不含 ``pool``——首次写入后归属不变，防重派改池。
    """
    init_pool_analysis_runs(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pool_analysis_runs
                (trade_date, symbol, session_id, status, error, pool, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, symbol) DO UPDATE SET
                session_id = excluded.session_id,
                status = excluded.status,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (trade_date, symbol, session_id, status, error, pool, now, now),
        )
        conn.commit()


def get_pool_analysis_run(
    trade_date: str, symbol: str, *, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    init_pool_analysis_runs(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT trade_date, symbol, session_id, status, error, pool, created_at, updated_at "
            "FROM pool_analysis_runs WHERE trade_date = ? AND symbol = ?",
            (trade_date, symbol),
        ).fetchone()
    return dict(row) if row else None


def get_pool_by_session(session_id: str, *, db_path: str | Path | None = None) -> str | None:
    """按会话 id 反查运行行池归属（add-microcap-lane-analysis T2）。

    命中返回 POOL_MAIN / POOL_MICROCAP；无行返回 None（调用方回退 main）。
    """
    init_pool_analysis_runs(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT pool FROM pool_analysis_runs WHERE session_id = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            (str(session_id or "").strip(),),
        ).fetchone()
    return str(row["pool"]) if row else None


def list_pool_analysis_runs(
    trade_date: str,
    *,
    pool: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    init_pool_analysis_runs(db_path)
    sql = (
        "SELECT trade_date, symbol, session_id, status, error, pool, created_at, updated_at "
        "FROM pool_analysis_runs WHERE trade_date = ?"
    )
    params: list[str] = [trade_date]
    if pool is not None:
        sql += " AND pool = ?"
        params.append(pool)
    sql += " ORDER BY symbol"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def create_analysis_session(symbol: str, *, display_name: str, source: str | None = None) -> str:
    """创建独立分析会话（序列与人工端点一致；source 仅自动批次写入）。"""
    session_id = create_session(
        stock_code="",
        stock_name="",
        display_name=display_name,
        status="clarifying",
        session_type="analysis",
    )
    append_chat(session_id, "user", f"对 {symbol} 进行深度分析")
    set_pipeline_anchor(session_id)
    update_session_for_clarify(
        session_id,
        stock_code=symbol,
        stock_name="",
        status="running",
    )
    event: dict[str, Any] = {
        "type": "session_created",
        "session_id": session_id,
        "display_name": display_name,
        "timestamp": _now(),
    }
    if source:
        event["source"] = source
    append_session_event(session_id, event)
    return session_id


def _resolve_max_symbols(max_symbols: int | None) -> int:
    if max_symbols is None:
        raw = os.getenv("AUTO_POOL_ANALYSIS_MAX_SYMBOLS", str(DEFAULT_MAX_SYMBOLS))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "AUTO_POOL_ANALYSIS_MAX_SYMBOLS 非法（%r），回退 %d", raw, DEFAULT_MAX_SYMBOLS
            )
            value = DEFAULT_MAX_SYMBOLS
        max_symbols = value
    return max(1, min(MAX_SYMBOLS_CAP, int(max_symbols)))


# 会话终态 → 运行行状态的映射（对账用；在跑态不在映射内、保持原行状态）
_SESSION_TERMINAL_STATUS: dict[str, str] = {
    "completed": "completed",
    "failed": "failed",
    "interrupted": "failed",
}


def reconcile_pool_analysis_runs(
    trade_date: str | None = None, *, db_path: str | Path | None = None
) -> int:
    """把已终结会话的终态回写到 ``pool_analysis_runs`` 行，返回回写行数。

    ``trade_date=None`` 扫全表（历史僵尸行一并治愈）；否则只扫该分析日。
    只碰 ``queued`` / ``dispatch_failed`` 且带 ``session_id`` 的行：会话 ``completed``
    → 行 ``completed``；``failed``/``interrupted`` → 行 ``failed``（error 取会话的
    ``failure_reason``）。在跑态（running/clarifying）保持原样。幂等：终态行不再进
    本函数的候选集。读会话失败只记日志（对账是自愈动作，不得阻断编排）。
    """
    healed = 0
    try:
        if trade_date is None:
            init_pool_analysis_runs(db_path)
            with _connect(db_path) as conn:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT trade_date, symbol, session_id, status FROM pool_analysis_runs"
                    ).fetchall()
                ]
        else:
            rows = list_pool_analysis_runs(trade_date, db_path=db_path)
        for row in rows:
            if row["status"] not in ("queued", "dispatch_failed"):
                continue
            session_id = str(row["session_id"] or "")
            if not session_id:
                continue
            session = get_session(session_id)
            if session is None:
                continue
            mapped = _SESSION_TERMINAL_STATUS.get(str(session.get("status") or ""))
            if mapped is None:
                continue
            error = str(session.get("failure_reason") or "") or None if mapped == "failed" else None
            run_day = str(row["trade_date"])
            record_pool_analysis_run(
                run_day, row["symbol"], session_id, mapped, error, db_path=db_path
            )
            healed += 1
    except Exception:  # noqa: BLE001 - 对账失败不阻断编排
        logger.exception("池分析运行行对账失败（忽略）")
        return healed
    if healed:
        logger.warning("池分析运行行对账回写 %d 条（%s）", healed, trade_date)
    return healed


def _accept_batch_date(batch_date: str, day: str, calendar: list[str] | None) -> bool:
    """批次日可接受 = 当日，或（有历且）恰为 ``day`` 的前一交易日。

    无历不猜：日历缺失/不可解析时退回「仅当日」的旧行为（fail-closed）。
    """
    if batch_date == day:
        return True
    if not calendar:
        return False
    earlier = [d for d in calendar if d < day]
    return bool(earlier) and earlier[-1] == batch_date


def _rank_key(row: dict[str, Any]) -> tuple[int, str]:
    return int(row.get("rank") or 10**9), str(row.get("symbol") or "")


def _select_main(pool_rows: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """主档选样（legacy，先截断后查 existing）：rank 升序取前 cap 名整体入选。

    当日是否已有 queued/completed 运行行不在此处判定，交由编排循环体逐行跳过
    （issue #456 幂等）——已分析行占用配额，同日重跑为纯 no-op（started=0），
    rank 之后的新标的轮不到：单日处理量被首次 cap 封顶，对调度双发有隐性成本
    保护。主档行为必须与 add-microcap-lane-analysis 之前逐字节一致（主档回归
    红线），不得随小票通道的选样语义联动变化。
    """
    return sorted(pool_rows, key=_rank_key)[:cap]


def _select_microcap(
    day: str,
    pool_rows: list[dict[str, Any]],
    cap: int,
    pool: str,
    db_path: str | Path | None,
) -> tuple[list[dict[str, Any]], int]:
    """小票选样（跳过 + 顺延补位，补位以本池自有行数封顶）。

    窗口段：rank 升序扫前 cap 名，当日已有 queued/completed 运行行（任意池）
    → 跳过、计入 skipped、不占配额（跨池去重靠 (trade_date, symbol) 全局幂等键）；
    failed/dispatch_failed/session_failed/无行 → 入选。
    补位段：own_count = 本池当日运行行数（``list_pool_analysis_runs(day, pool=)``）；
    rank cap 名之后按序扫描，每名先查 ``len(selected) + own_count >= cap`` 即止
    ——重跑时 own_count 已记满本池当日处理量，补位自动归零（不双烧）；首跑窗口
    被主档重叠占位时按序补足至 cap。返回 (selected, skipped_existing)。
    """
    ordered = sorted(pool_rows, key=_rank_key)
    selected: list[dict[str, Any]] = []
    skipped = 0
    for row in ordered[:cap]:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        existing = get_pool_analysis_run(day, symbol, db_path=db_path)
        if existing is not None and existing.get("status") in ("queued", "completed"):
            skipped += 1
            continue
        selected.append(row)
    own_count = len(list_pool_analysis_runs(day, pool=pool, db_path=db_path))
    for row in ordered[cap:]:
        if len(selected) + own_count >= cap:
            break
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        existing = get_pool_analysis_run(day, symbol, db_path=db_path)
        if existing is not None and existing.get("status") in ("queued", "completed"):
            continue
        selected.append(row)
    return selected, skipped


def prepare_pool_analysis_batch(
    *,
    as_of: str | None = None,
    max_symbols: int | None = None,
    enqueue: Callable[[str, str], bool] | None = None,
    db_path: str | Path | None = None,
    screening_db_path: str | Path | None = None,
    pool: str = POOL_MAIN,
) -> dict[str, Any]:
    """读取当日池并准备自动分析批次；返回可审计摘要。

    ``pool`` 指定池档位（``'main'`` 主档 / ``'microcap'`` 小票通道，
    add-microcap-lane-analysis）：决定读哪一档 ``latest_successful_pool``、会话
    标题后缀（``小票池自动分析``）与会话事件 ``source``；运行行归属列随之落
    ``pool``。跨池去重以 ``(trade_date, symbol)`` 全局幂等键为准（pool 不参与）。

    选样按池档位分支（add-microcap-lane-analysis T5 修正）：主档保持「先截断后查
    existing」的 legacy 语义——前 cap 名整体入选、已有 ``queued``/``completed``
    运行行的标的在循环体逐行跳过（计入 ``skipped_existing``），同日重跑为纯
    no-op；小票通道为「跳过 + 顺延补位」且补位以本池自有行数封顶——窗口段跳过
    queued/completed（任意池）不占配额、由 rank 下一名补足 N，补位段以
    ``own_count``（本池当日运行行数）封顶，重跑不双烧。``failed`` /
    ``dispatch_failed`` / ``session_failed`` 行放行入选，由下方循环体重派。

    ``enqueue(symbol, session_id)`` 返回 True 表示已交给队列；False/异常记
    ``dispatch_failed`` 留痕，补跑重派一律创建全新会话（issue #546：终态会话
    MUST NOT 被复用入队——快路径不重置会话终态，复用即被队列终态轮询判死）。
    """
    day = as_of or datetime.now().strftime("%Y-%m-%d")
    cap = _resolve_max_symbols(max_symbols)
    result: dict[str, Any] = {
        "trade_date": day,
        "batch_date": None,
        "stale_batch": None,
        "pool_size": 0,
        "selected": 0,
        "started": 0,
        "started_symbols": [],
        "skipped_existing": 0,
        "reconciled": 0,
        "removed": [],
        "dispatch_failed": 0,
    }
    # 全表对账（含历史僵尸行）；结果计数随摘要披露
    result["reconciled"] = reconcile_pool_analysis_runs(None, db_path=db_path)

    successful = latest_successful_pool(db_path=screening_db_path, pool=pool)
    if successful is None:
        result["status"] = "skipped_empty_pool"
        return result

    batch = successful.get("batch") or {}
    batch_date = str(batch.get("trade_date") or "")
    # 池行改名 pool_rows（add-microcap-lane-analysis T5）：形参 pool 已指池档位，
    # 不再复用同名局部变量装池行——原实现的重绑定会把 list 传进 record 的 pool 列。
    pool_rows = list(successful.get("pool") or [])
    result["batch_date"] = batch_date
    result["pool_size"] = len(pool_rows)

    if not _accept_batch_date(batch_date, day, read_snapshot_calendar()):
        result["status"] = "skipped_stale_pool"
        return result
    if batch_date != day:
        result["stale_batch"] = batch_date
        logger.warning(
            "池分析接受前一交易日的补跑池（批次 %s，分析日 %s）；时效性重校验照常执行",
            batch_date,
            day,
        )
    if not pool_rows:
        result["status"] = "skipped_empty_pool"
        return result

    # 选样按池档位分支（add-microcap-lane-analysis T5 修正）：
    # - 主档：legacy「先截断后查 existing」——前 cap 名整体入选，已有
    #   queued/completed 运行行的标的在下方循环体逐行跳过（重跑纯 no-op）；
    #   主档行为与 add-microcap-lane-analysis 之前逐字节一致（主档回归红线）。
    # - 小票：窗口跳过 + 顺延补位，补位以本池自有行数封顶（重跑不双烧）；
    #   补位不足时 selected 如实披露实际数。
    if pool == POOL_MICROCAP:
        selected, skipped_existing = _select_microcap(day, pool_rows, cap, pool, db_path)
        result["skipped_existing"] += skipped_existing
    else:
        selected = _select_main(pool_rows, cap)
    result["selected"] = len(selected)

    removed = screening_freshness_removals(selected, batch_date)
    result["removed"] = removed
    removed_symbols = {str(item.get("symbol") or "") for item in removed}

    # 会话标题后缀与 source 按池档位切换（add-microcap-lane-analysis T5）：
    # 小票批标题带「小票池自动分析」、source=auto_pool_microcap，与主档批次区分。
    display_suffix = (
        AUTO_POOL_MICROCAP_DISPLAY_SUFFIX if pool == POOL_MICROCAP else AUTO_POOL_DISPLAY_SUFFIX
    )
    source = "auto_pool" if pool == POOL_MAIN else "auto_pool_microcap"

    for row in selected:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol or symbol in removed_symbols:
            continue
        existing = get_pool_analysis_run(day, symbol, db_path=db_path)
        if existing is not None and existing.get("status") in ("queued", "completed"):
            # queued = 已建会话待入队/在跑；completed = 当日已完成（对账后可见）——
            # 两者都不重复建会话、不重复烧分析（issue #456）。
            result["skipped_existing"] += 1
            continue
        # 重派（failed/dispatch_failed/session_failed 行）一律创建全新会话：终态会话
        # 从不被快路径重置为 running，复用入队即被队列终态轮询（TERMINAL_STATES）
        # 判死——10-28 opencode 宕机恢复实操 9 只复用会话秒灭（issue #546）。旧会话
        # 原样保留作审计；下方 record_pool_analysis_run 的 upsert 把运行行
        # session_id 更新为新会话。
        session_id = ""
        if not session_id:
            # 会话标题带股票名（消费 selection_pool.stock_name，PR #520 的展示字段）；
            # 缺名回退纯代码，与池行缺名的 warning 口径一致（pipeline.py 同款兜底）。
            stock_name = str(row.get("stock_name") or "").strip()
            if stock_name:
                display_name = f"{stock_name}({symbol}) {display_suffix}"
            else:
                display_name = f"{symbol} {display_suffix}"
            try:
                session_id = create_analysis_session(
                    symbol,
                    display_name=display_name,
                    source=source,
                )
            except Exception as exc:  # noqa: BLE001 -- 单标的会话失败不阻断整批
                logger.exception("自动选股分析建会话失败 %s", symbol)
                record_pool_analysis_run(
                    day, symbol, "", "session_failed", str(exc), pool=pool, db_path=db_path
                )
                result["dispatch_failed"] += 1
                continue
            record_pool_analysis_run(
                day, symbol, session_id, "queued", None, pool=pool, db_path=db_path
            )

        if enqueue is None:
            record_pool_analysis_run(
                day,
                symbol,
                session_id,
                "dispatch_failed",
                "enqueue unavailable",
                pool=pool,
                db_path=db_path,
            )
            result["dispatch_failed"] += 1
            continue
        try:
            dispatched = bool(enqueue(symbol, session_id))
        except Exception as exc:  # noqa: BLE001 -- 入队失败留痕，补跑重派走全新会话
            logger.exception("自动选股分析入队失败 %s", symbol)
            record_pool_analysis_run(
                day, symbol, session_id, "dispatch_failed", str(exc), pool=pool, db_path=db_path
            )
            result["dispatch_failed"] += 1
            continue
        if not dispatched:
            record_pool_analysis_run(
                day,
                symbol,
                session_id,
                "dispatch_failed",
                "enqueue returned false",
                pool=pool,
                db_path=db_path,
            )
            result["dispatch_failed"] += 1
            continue
        record_pool_analysis_run(
            day, symbol, session_id, "queued", None, pool=pool, db_path=db_path
        )
        result["started"] += 1
        result["started_symbols"].append(symbol)

    result["status"] = "ok"
    return result


def _submit(**kwargs: Any) -> dict[str, Any]:
    """惰性导入 api，避免 outcome/screening ↔ api 的导入期环。"""
    from finance_agent import api

    return api.submit_pool_analysis_batch(**kwargs)


# ── 前置顺延护栏（add-pool-analysis-defer-guard，issue #578）──────────────────
# 16:22 触发与选股收尾只剩 38~60 秒余量（11-10/11-11 两日实证）；小票档更结构性
# 错位（16:47 触发 vs 前置 16:45 起跑、17:04~17:17 收尾）。前置 job 当日仍在跑时
# 顺延重查，等待耗尽照常提交（stale 降级路径不变）；前置不在跑零等待（深夜补跑
# 不空等）。信号读 sessions 库 job_runs 最新行（与 daily brief 直读同款先例）。


DEFER_JOB_MAIN = "screening_daily"
DEFER_JOB_MICROCAP = "microcap_screening_daily"
DEFER_INTERVAL_DEFAULT = 300
DEFER_ROUNDS_MAIN_DEFAULT = 3
DEFER_ROUNDS_MICROCAP_DEFAULT = 8


def _sessions_db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path) if db_path else Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def _read_prerequisite_status(
    job_id: str, day: str, db_path: str | Path | None = None
) -> str | None:
    """前置 job 当日 ``job_runs`` 最新行状态；读失败 fail-open 返回 None（不在跑）。"""
    try:
        conn = sqlite3.connect(_sessions_db_path(db_path), check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute(
                "SELECT status FROM job_runs WHERE job_id = ? AND substr(started_at, 1, 10) = ?"
                " ORDER BY run_id DESC LIMIT 1",
                (job_id, day),
            ).fetchone()
        finally:
            conn.close()
        return str(row["status"]) if row else None
    except sqlite3.Error:
        logger.warning("前置 job 状态读取失败（按不在跑放行）: job_id=%s day=%s", job_id, day)
        return None


def _resolve_defer_config(rounds_env: str, default_rounds: int) -> tuple[bool, int, int]:
    """解析护栏开关/间隔/轮数；非法值回退默认并告警。返回 (enabled, interval, rounds)。"""
    enabled = os.getenv("AUTO_POOL_ANALYSIS_DEFER_ENABLED", "1").strip() != "0"
    interval = DEFER_INTERVAL_DEFAULT
    raw = os.getenv("AUTO_POOL_ANALYSIS_DEFER_INTERVAL_SECONDS")
    if raw is not None:
        try:
            interval = int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "AUTO_POOL_ANALYSIS_DEFER_INTERVAL_SECONDS 非法（%r），回退 %d",
                raw,
                DEFER_INTERVAL_DEFAULT,
            )
    if interval <= 0:
        logger.warning(
            "AUTO_POOL_ANALYSIS_DEFER_INTERVAL_SECONDS<=0，回退 %d", DEFER_INTERVAL_DEFAULT
        )
        interval = DEFER_INTERVAL_DEFAULT
    rounds = default_rounds
    raw = os.getenv(rounds_env)
    if raw is not None:
        try:
            rounds = int(raw)
        except (TypeError, ValueError):
            logger.warning("%s 非法（%r），回退 %d", rounds_env, raw, default_rounds)
    if rounds <= 0:
        logger.warning("%s<=0，回退 %d", rounds_env, default_rounds)
        rounds = default_rounds
    return enabled, interval, rounds


def wait_for_batch_prerequisite(
    *,
    prerequisite_job_id: str,
    day: str,
    interval_seconds: int,
    max_rounds: int,
    sleep: Callable[[float], None] | None = None,
    status_reader: Callable[[str, str], str | None] | None = None,
) -> dict[str, Any]:
    """前置选股 job 当日仍在跑则顺延重查；返回可审计的 defer 摘要。

    ``outcome``：``fresh_ready``（前置已收尾）/ ``not_running``（当日无行或读取
    fail-open）/ ``prereq_failed``（前置终态非 ok）/ ``gave_up_still_running``
    （等待耗尽仍 running，调用方照常提交、stale 降级路径不变）。
    """
    reader = status_reader or _read_prerequisite_status
    rest = sleep or time.sleep

    def _finish(outcome: str, rounds: int, waited: int) -> dict[str, Any]:
        return {
            "prerequisite": prerequisite_job_id,
            "outcome": outcome,
            "rounds": rounds,
            "waited_seconds": waited,
        }

    try:
        status = reader(prerequisite_job_id, day)
    except Exception:  # noqa: BLE001 - 注入 reader 抛错同样 fail-open
        status = None
    rounds = 0
    waited = 0
    while status == "running" and rounds < max_rounds:
        rest(interval_seconds)
        waited += interval_seconds
        rounds += 1
        try:
            status = reader(prerequisite_job_id, day)
        except Exception:  # noqa: BLE001
            status = None
    if status == "running":
        return _finish("gave_up_still_running", rounds, waited)
    if status is None:
        outcome = "not_running"
    elif status == "ok":
        outcome = "fresh_ready"
    else:
        outcome = "prereq_failed"
    return _finish(outcome, rounds, waited)


def _run_defer_guard(
    *, prerequisite_job_id: str, rounds_env: str, default_rounds: int
) -> dict[str, Any]:
    """入口侧护栏：解析 env 配置后执行等待；关闭时返回 ``outcome=disabled``。"""
    enabled, interval, rounds = _resolve_defer_config(rounds_env, default_rounds)
    if not enabled:
        return {
            "prerequisite": prerequisite_job_id,
            "outcome": "disabled",
            "rounds": 0,
            "waited_seconds": 0,
        }
    return wait_for_batch_prerequisite(
        prerequisite_job_id=prerequisite_job_id,
        day=datetime.now().strftime("%Y-%m-%d"),
        interval_seconds=interval,
        max_rounds=rounds,
    )


def run_pool_analysis_daily() -> dict[str, Any]:
    """日批入口：开关/上限解析后，把批次准备提交到 API 主事件循环。

    提交前执行前置顺延护栏（issue #578）：选股日批当日仍在跑时顺延等待，
    摘要 ``defer`` 字段披露轮数与等待秒数。
    """
    if os.getenv("AUTO_POOL_ANALYSIS_ENABLED", "1").strip() == "0":
        return {"status": "skipped_disabled"}
    defer = _run_defer_guard(
        prerequisite_job_id=DEFER_JOB_MAIN,
        rounds_env="AUTO_POOL_ANALYSIS_DEFER_MAX_ROUNDS",
        default_rounds=DEFER_ROUNDS_MAIN_DEFAULT,
    )
    result = _submit(max_symbols=_resolve_max_symbols(None))
    result["defer"] = defer
    return result


def _resolve_microcap_max_symbols(max_symbols: int | None) -> int:
    if max_symbols is None:
        raw = os.getenv("AUTO_POOL_MICROCAP_MAX_SYMBOLS", "10")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning("AUTO_POOL_MICROCAP_MAX_SYMBOLS 非法（%r），回退 10", raw)
            value = 10
        max_symbols = value
    return max(1, min(MAX_SYMBOLS_CAP, int(max_symbols)))


def run_pool_analysis_microcap_daily() -> dict[str, Any]:
    """日批入口（小票池，add-microcap-lane-analysis）：开关解析后提交 API 主循环。

    前置顺延护栏同主档（issue #578）：前置为 ``microcap_screening_daily``
    （16:45 起跑、17:04~17:17 收尾），默认轮次 8（16:47+8×5min=17:27 覆盖
    P99+10min）。
    """
    if os.getenv("AUTO_POOL_MICROCAP_ENABLED", "1").strip() == "0":
        return {"status": "skipped_disabled"}
    defer = _run_defer_guard(
        prerequisite_job_id=DEFER_JOB_MICROCAP,
        rounds_env="AUTO_POOL_MICROCAP_DEFER_MAX_ROUNDS",
        default_rounds=DEFER_ROUNDS_MICROCAP_DEFAULT,
    )
    result = _submit(pool=POOL_MICROCAP, max_symbols=_resolve_microcap_max_symbols(None))
    result["defer"] = defer
    return result
