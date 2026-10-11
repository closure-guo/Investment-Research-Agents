"""SQLite session store — 永久存储分析会话。

会话 = 一次股票深度分析 + 后续追问。
SQLite 单文件，WAL 模式，永久保留直到用户手动删除。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# DB 路径支持 SESSIONS_DB_PATH 覆盖，默认 data/sessions.db。
# 必需性：E2E 测试后端（TESTING=1，独立端口）若与 docker 生产后端共用同一
# SQLite 文件，两个进程并发写会破坏 WAL 与主库的一致性——实测导致主库文件
# 被 WAL 帧覆盖、SQLite 魔数丢失，报 "file is not a database" 且数据不可恢复。
# 测试环境必须注入独立路径隔离。
_DB_PATH = Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))

_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# 事件落库并发重试：seq 分配虽已原子化，仍可能遇到写锁竞争（多 writer 高频写）
_EVENT_APPEND_MAX_RETRIES = 5
_EVENT_APPEND_RETRY_SLEEP = 0.05

# 瞬态 SQLite 错误模式（delta add-event-delivery-resilience spec「SQLite 瞬态错误重试」）：
# unable to open database file = bind mount 瞬断；locked = busy_timeout 覆盖不到的
# 连接期锁；disk I/O error = 存储瞬态。非瞬态错误不重试立即抛出。
_DB_TRANSIENT_PATTERNS = ("unable to open database file", "database is locked", "disk i/o error")
# 连接重试 6 次：尝试点 t≈0/0.1/0.3/0.7/1.5/3.1（指数退避 0.1s 基数，
# 末次失败不再 sleep），覆盖 spec 假设的 ~2s bind mount 自愈窗口且留余量
_DB_CONNECT_MAX_RETRIES = 6
_DB_CONNECT_RETRY_BASE_SLEEP = 0.1

# created_at 列曾被旧版代码错写为 session_type 的值（'chat' / 'analysis'），
# 这些值无法被 Date 解析，前端显示 "Invalid Date"。这里集中兜底。
_BAD_CREATED_AT_VALUES = {"chat", "analysis", "deep", "quick", ""}


def _is_valid_iso(ts: str | None) -> bool:
    """判断 created_at 是否为可被前端 Date 解析的合法时间字符串。"""
    if not ts or not isinstance(ts, str):
        return False
    if ts in _BAD_CREATED_AT_VALUES:
        return False
    try:
        datetime.fromisoformat(ts)
        return True
    except ValueError:
        return False


def _normalize_created_at(ts: str | None) -> str:
    """返回可安全交给前端 Date 的 created_at；非法值回退为 epoch。"""
    if _is_valid_iso(ts):
        return ts  # type: ignore[return-value]
    # 无法还原原始时间，用 epoch 兜底（前端可识别并显示"未知时间"）
    return "1970-01-01T00:00:00"


def _get_db() -> sqlite3.Connection:
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(_DB_CONNECT_MAX_RETRIES):
        try:
            conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            # 并发写等待锁而非立即抛 "database is locked"（流式 token 高频落库场景必需）
            conn.execute("PRAGMA busy_timeout=15000")
            return conn
        except sqlite3.OperationalError as exc:
            if not any(p in str(exc).lower() for p in _DB_TRANSIENT_PATTERNS):
                raise
            last_exc = exc
            # 末次尝试失败后不再 sleep（重试耗尽即抛，不做无意义的收尾等待）
            if attempt < _DB_CONNECT_MAX_RETRIES - 1:
                time.sleep(_DB_CONNECT_RETRY_BASE_SLEEP * (2**attempt))
    raise last_exc  # type: ignore[misc]


def init_db() -> None:
    """Initialize sessions table and migrate schema."""
    conn = _get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            stock_code   TEXT,
            stock_name   TEXT,
            display_name TEXT,
            status       TEXT DEFAULT 'completed',
            focus        TEXT DEFAULT '',
            pending_intent TEXT DEFAULT '',
            report_markdown  TEXT,
            chart_data       TEXT,
            analyst_reports  TEXT,
            agent_process    TEXT,
            analyst_summaries TEXT,
            chat_history     TEXT DEFAULT '[]',
            created_at   TEXT NOT NULL,
            duration_ms  INTEGER DEFAULT 0
        )
        """
    )
    # 迁移：为旧表添加缺失列
    for _col, ddl in [
        ("session_type", "ALTER TABLE sessions ADD COLUMN session_type TEXT DEFAULT 'analysis'"),
        ("focus", "ALTER TABLE sessions ADD COLUMN focus TEXT DEFAULT ''"),
        ("pending_intent", "ALTER TABLE sessions ADD COLUMN pending_intent TEXT DEFAULT ''"),
        ("pipeline_snapshot", "ALTER TABLE sessions ADD COLUMN pipeline_snapshot TEXT"),
        ("pipeline_timelines", "ALTER TABLE sessions ADD COLUMN pipeline_timelines TEXT"),
        ("failure_reason", "ALTER TABLE sessions ADD COLUMN failure_reason TEXT"),
        # 管线触发锚点：管线启动时 chat_history 中最后一条 user 消息索引 + 1，
        # 供前端历史重建定位报告消息插入位置（NULL = 旧会话，前端回退第一个 user 后）
        ("pipeline_anchor", "ALTER TABLE sessions ADD COLUMN pipeline_anchor INTEGER"),
        # 报告文件产物（md/docx 等导出路径）：恢复会话后前端可还原导出入口。
        # 对应 delta spec: update-file-export-entry
        ("file_paths", "ALTER TABLE sessions ADD COLUMN file_paths TEXT"),
        # 结构化引用数组（add-citation-display）：JSON 文本，NULL = 旧会话/未校验
        ("citations", "ALTER TABLE sessions ADD COLUMN citations TEXT"),
        # 最近一次运行关联的 Langfuse trace（add-user-feedback）：反馈端点按 session 解析
        ("langfuse_trace_id", "ALTER TABLE sessions ADD COLUMN langfuse_trace_id TEXT"),
        # 终稿决策 JSON（add-report-revision-view）：随 update_session_report 落库，
        # 供同标的下一份报告的「距上次报告」增量摘要回溯；NULL = 无终稿/旧会话
        ("final_trade_decision", "ALTER TABLE sessions ADD COLUMN final_trade_decision TEXT"),
    ]:
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute(ddl)

    # 迁移：旧表的 stock_code / stock_name 可能为 NOT NULL；SQLite 不支持 ALTER COLUMN，需要重建表
    _relax_stock_columns(conn)

    # 迁移：修复历史脏数据——created_at 被旧版代码错写为 session_type 值（'chat'/'analysis'）。
    # 无法还原真实时间，回退为 epoch 占位（前端识别为"未知时间"，不再显示 Invalid Date）。
    _repair_bad_created_at(conn)

    # 迁移：清理 epoch 占位的脏数据会话。这些会话原始时间已永久丢失，
    # 保留只会显示"未知时间"且排序错乱（ORDER BY created_at DESC 把它们堆在末尾）。
    # 启动时幂等删除。必须在 _repair_bad_created_at 之后执行。
    _purge_epoch_sessions(conn)

    # 事件日志表：每个 SSE 事件按 session 内单调递增 seq 落库，作为断线重放的事实源。
    # 对应 delta spec: resume-stream-on-session-switch Task 1.1
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            seq         INTEGER NOT NULL,
            event_json  TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            UNIQUE (session_id, seq)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_events_session ON session_events(session_id, seq)"
    )

    # 启动 reconcile：残留 running 会话（上次进程退出时未正常结束）置为 interrupted。
    # 对应 delta spec: resume-stream-on-session-switch Task 1.4
    conn.execute("UPDATE sessions SET status = 'interrupted' WHERE status = 'running'")

    conn.commit()
    conn.close()


def _repair_bad_created_at(conn: sqlite3.Connection) -> None:
    """把 created_at 列中的非法值（'chat'/'analysis' 等）回退为 epoch 占位。"""
    placeholders = ",".join("?" for _ in _BAD_CREATED_AT_VALUES)
    conn.execute(
        f"UPDATE sessions SET created_at = '1970-01-01T00:00:00' "  # noqa: S608 - 列名/表名均为代码内常量，值已参数化
        f"WHERE created_at IN ({placeholders})",
        tuple(_BAD_CREATED_AT_VALUES),
    )


def _purge_epoch_sessions(conn: sqlite3.Connection) -> None:
    """删除 created_at 为 epoch 占位的脏数据会话。

    这些会话的原始时间已永久丢失（旧版代码列错位写入），保留只会显示"未知时间"
    且排序错乱。启动时幂等清理。必须在 _repair_bad_created_at 之后执行。
    """
    conn.execute("DELETE FROM sessions WHERE created_at = '1970-01-01T00:00:00'")


def _relax_stock_columns(conn: sqlite3.Connection) -> None:
    """如果 stock_code 或 stock_name 仍带 NOT NULL 约束，则重建表以移除约束。"""
    # PRAGMA table_info 返回列：cid, name, type, notnull, dflt_value, pk
    cols = {row[1]: row[3] for row in conn.execute("PRAGMA table_info(sessions)")}
    if cols.get("stock_code") == 1 or cols.get("stock_name") == 1:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions_new (
                session_id   TEXT PRIMARY KEY,
                stock_code   TEXT,
                stock_name   TEXT,
                display_name TEXT,
                status       TEXT DEFAULT 'completed',
                focus        TEXT DEFAULT '',
                pending_intent TEXT DEFAULT '',
                report_markdown  TEXT,
                chart_data       TEXT,
                analyst_reports  TEXT,
                agent_process    TEXT,
                analyst_summaries TEXT,
                chat_history     TEXT DEFAULT '[]',
                created_at   TEXT NOT NULL,
                duration_ms  INTEGER DEFAULT 0,
                session_type TEXT DEFAULT 'analysis'
            )
            """
        )
        conn.execute("INSERT INTO sessions_new SELECT * FROM sessions")
        conn.execute("DROP TABLE sessions")
        conn.execute("ALTER TABLE sessions_new RENAME TO sessions")


def create_session(
    stock_code: str = "",
    stock_name: str = "",
    report_markdown: str = "",
    chart_data: dict | None = None,
    analyst_reports: dict | None = None,
    agent_process: dict | None = None,
    analyst_summaries: dict | None = None,
    duration_ms: int = 0,
    session_type: str = "analysis",
    display_name: str | None = None,
    status: str = "completed",
) -> str:
    """Create a new session record, return session_id."""
    session_id = str(uuid.uuid4())[:12]
    now = datetime.now().isoformat()
    if display_name is None:
        display_name = f"{stock_name} {datetime.now().strftime('%m-%d %H:%M')}"

    conn = _get_db()
    conn.execute(
        """
        INSERT INTO sessions
            (session_id, stock_code, stock_name, display_name, status,
             report_markdown, chart_data, analyst_reports, agent_process,
             analyst_summaries, chat_history, created_at, duration_ms, session_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)
        """,
        (
            session_id,
            stock_code,
            stock_name,
            display_name,
            status,
            report_markdown,
            json.dumps(chart_data or {}, ensure_ascii=False),
            json.dumps(analyst_reports or {}, ensure_ascii=False, default=str),
            json.dumps(agent_process or {}, ensure_ascii=False, default=str),
            json.dumps(analyst_summaries or {}, ensure_ascii=False, default=str),
            now,
            duration_ms,
            session_type,
        ),
    )
    conn.commit()
    conn.close()
    return session_id


def update_session_for_clarify(
    session_id: str,
    stock_code: str | None = None,
    stock_name: str | None = None,
    display_name: str | None = None,
    focus: str | None = None,
    pending_intent: str | None = None,
    status: str | None = None,
) -> bool:
    """更新澄清阶段的 session 字段。"""
    updates = []
    values = []
    if stock_code is not None:
        updates.append("stock_code = ?")
        values.append(stock_code)
    if stock_name is not None:
        updates.append("stock_name = ?")
        values.append(stock_name)
    if display_name is not None:
        updates.append("display_name = ?")
        values.append(display_name)
    if focus is not None:
        updates.append("focus = ?")
        values.append(focus)
    if pending_intent is not None:
        updates.append("pending_intent = ?")
        values.append(pending_intent)
    if status is not None:
        updates.append("status = ?")
        values.append(status)
    if not updates:
        return True

    conn = _get_db()
    cur = conn.execute(
        f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?",  # noqa: S608
        (*values, session_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def append_session_event(session_id: str, event: dict) -> int:
    """将 SSE 事件追加到会话的事件日志，返回分配的 seq（会话内从 1 单调递增）。

    对应 delta spec: resume-stream-on-session-switch Task 1.2。
    事件先落库再 fan-out 给订阅者，保证断线重放的事实源不依赖进程内存。

    并发安全：seq 分配与插入必须原子。此前实现为 `SELECT MAX(seq)` + `INSERT`
    两步非原子，并发写（流式 token 高频落库）时多个 writer 读到同一 max_seq，
    UNIQUE 约束让后到的 INSERT 失败 —— 事件永久丢失，症状为流式文本随机缺整个
    token。现改为 BEGIN IMMEDIATE 事务内 `INSERT ... SELECT MAX(seq)+1` 单条语句，
    并对锁竞争/冲突做有限重试。
    """
    eventJson = json.dumps(event, ensure_ascii=False, default=str)
    createdAt = datetime.now().isoformat()
    lastError: Exception | None = None

    for _attempt in range(_EVENT_APPEND_MAX_RETRIES):
        conn = _get_db()
        try:
            # BEGIN IMMEDIATE：立即取写锁，避免 SELECT/INSERT 之间被其他 writer 插入
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO session_events (session_id, seq, event_json, created_at) "
                "SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ? "
                "FROM session_events WHERE session_id = ?",
                (session_id, eventJson, createdAt, session_id),
            )
            conn.commit()
            # 取回本次实际写入的 seq
            row = conn.execute(
                "SELECT seq FROM session_events WHERE rowid = ?", (cur.lastrowid,)
            ).fetchone()
            return int(row["seq"]) if row else 0
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as e:
            lastError = e
            with contextlib.suppress(Exception):
                conn.rollback()
            time.sleep(_EVENT_APPEND_RETRY_SLEEP)
        finally:
            with contextlib.suppress(Exception):
                conn.close()

    logger.error("append_session_event 重试耗尽，事件丢失: %s", lastError)
    raise lastError if lastError else RuntimeError("append_session_event failed")


def append_session_events(session_id: str, events: list[dict]) -> list[int]:
    """批量写入多个 SSE 事件到事件日志，返回分配的 seq 列表（会话内从 1 单调递增）。

    语义与 append_session_event 一致（seq 原子分配 + 锁竞争重试），区别是
    整批共用一个连接/事务：单条一次事务在 Windows Docker 卷上实测 ~76ms
    （fsync 主导），高频 thinking_token 逐条落库会把消费端限速到事件积压
    （线上 601700 深研会话永远 running 的根因）。批量合并 fsync 后吞吐
    提升一个数量级以上，且「先落库再 fan-out」契约由调用方保持。
    """
    if not events:
        return []
    payload = [
        (session_id, json.dumps(ev, ensure_ascii=False, default=str), datetime.now().isoformat())
        for ev in events
    ]
    lastError: Exception | None = None

    for _attempt in range(_EVENT_APPEND_MAX_RETRIES):
        conn = _get_db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS base FROM session_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            base = int(row["base"])
            conn.executemany(
                "INSERT INTO session_events (session_id, seq, event_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                [(sid, base + i + 1, js, ts) for i, (sid, js, ts) in enumerate(payload)],
            )
            conn.commit()
            return list(range(base + 1, base + 1 + len(payload)))
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as e:
            lastError = e
            with contextlib.suppress(Exception):
                conn.rollback()
            time.sleep(_EVENT_APPEND_RETRY_SLEEP)
        finally:
            with contextlib.suppress(Exception):
                conn.close()

    logger.error("append_session_events 重试耗尽，%d 条事件丢失: %s", len(events), lastError)
    raise lastError if lastError else RuntimeError("append_session_events failed")


def list_session_events(session_id: str, after_seq: int = 0) -> list[dict]:
    """返回会话中 seq > after_seq 的事件列表，按 seq 升序。

    每行包含 seq、event_json、created_at 字段（created_at 供回放时按
    chat_history 的 ts 注入 user_message 排序）。对应 delta spec Task 1.2。
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT seq, event_json, created_at FROM session_events "
        "WHERE session_id = ? AND seq > ? ORDER BY seq ASC",
        (session_id, after_seq),
    ).fetchall()
    conn.close()
    return [
        {"seq": r["seq"], "event_json": r["event_json"], "created_at": r["created_at"]}
        for r in rows
    ]


def has_terminal_event(session_id: str) -> bool:
    """检查 journal 中是否已有终态事件（done/interrupted/error）。

    用于 publish 的 CAS 检查，避免重复写入终态事件。
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT event_json FROM session_events WHERE session_id = ? ORDER BY seq DESC",
        (session_id,),
    ).fetchall()
    conn.close()
    terminalTypes = {"done", "interrupted", "error"}
    for row in rows:
        try:
            event: dict = json.loads(row["event_json"])
            if event.get("type") in terminalTypes:
                return True
        except (json.JSONDecodeError, TypeError):
            continue
    return False


def get_max_event_seq(session_id: str) -> int:
    """返回会话 journal 中最大 seq，无事件时返回 0。

    用于追问时 POST /api/analyze 设置 after_seq，跳过历史事件重放
    （避免上一轮 done 终态事件导致 SSE 流提前终止）。
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT MAX(seq) as max_seq FROM session_events WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    if row and row["max_seq"] is not None:
        return int(row["max_seq"])
    return 0


def get_terminal_event(session_id: str) -> dict | None:
    """返回 journal 中最后一条终态事件（done/interrupted/error），无则返回 None。

    用于 cancel 幂等：无活跃任务时返回终态而非 404。
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT seq, event_json FROM session_events WHERE session_id = ? ORDER BY seq DESC",
        (session_id,),
    ).fetchall()
    conn.close()
    terminalTypes = {"done", "interrupted", "error"}
    for row in rows:
        try:
            event: dict = json.loads(row["event_json"])
            if event.get("type") in terminalTypes:
                event["seq"] = row["seq"]
                return event
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def update_session_report(
    session_id: str,
    report_markdown: str = "",
    chart_data: dict | None = None,
    analyst_reports: dict | None = None,
    agent_process: dict | None = None,
    analyst_summaries: dict | None = None,
    file_paths: dict | None = None,
    duration_ms: int = 0,
    status: str = "completed",
    citations: list[dict] | None = None,
    final_trade_decision: dict | None = None,
) -> bool:
    """更新 session 的报告数据和状态。

    用于管线启动时先创建 running session，完成后再回填报告。
    file_paths 记录报告文件产物路径（md/docx 等），供恢复会话还原导出入口。
    citations 为结构化引用数组（add-citation-display），None = 未校验（旧路径兼容）。
    final_trade_decision 为终稿决策（add-report-revision-view）：真实管线中是
    TradeDecision pydantic 对象，落库前统一 model_dump 成 dict 再 JSON 序列化
    （直接 json.dumps 走 default=str 会落 repr 串，回溯侧不可解析——实跑实证）；
    None 落 NULL——管线阻断/未审批时 MUST NOT 以 trader 层 plan 冒充终稿。
    """
    if final_trade_decision is not None and hasattr(final_trade_decision, "model_dump"):
        final_trade_decision = final_trade_decision.model_dump()
    conn = _get_db()
    cur = conn.execute(
        """
        UPDATE sessions SET
            report_markdown = ?,
            chart_data = ?,
            analyst_reports = ?,
            agent_process = ?,
            analyst_summaries = ?,
            file_paths = ?,
            duration_ms = ?,
            status = ?,
            citations = ?,
            final_trade_decision = ?
        WHERE session_id = ?
        """,
        (
            report_markdown,
            json.dumps(chart_data or {}, ensure_ascii=False),
            json.dumps(analyst_reports or {}, ensure_ascii=False, default=str),
            json.dumps(agent_process or {}, ensure_ascii=False, default=str),
            json.dumps(analyst_summaries or {}, ensure_ascii=False, default=str),
            json.dumps(file_paths or {}, ensure_ascii=False),
            duration_ms,
            status,
            json.dumps(citations, ensure_ascii=False) if citations is not None else None,
            json.dumps(final_trade_decision, ensure_ascii=False, default=str)
            if final_trade_decision is not None
            else None,
            session_id,
        ),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_previous_completed_session(stock_code: str, current_session_id: str | None) -> dict | None:
    """回溯同标的上一份已完成报告（add-report-revision-view）。

    返回最近一条 status='completed' 且非 current_session_id 的会话（created_at 降序），
    携带增量摘要渲染所需的结构化字段子集：
    session_id / created_at / final_trade_decision（解析后 dict，NULL 归一为 None）/
    kpi（chart_data.kpi 关键指标子 dict）。
    无符合条件的历史行返回 None。running/failed 会话 MUST NOT 被返回。
    查询失败（DB 不可读等）降级返回 None 并按首份报告处理，不阻断管线。
    """
    try:
        conn = _get_db()
        row = conn.execute(
            """
            SELECT session_id, created_at, final_trade_decision, chart_data
            FROM sessions
            WHERE stock_code = ? AND status = 'completed' AND session_id <> ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (stock_code, current_session_id or ""),
        ).fetchone()
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("回溯上一份报告查询失败，按首份报告处理: %s", exc)
        return None
    if row is None:
        return None
    decision = None
    if row["final_trade_decision"]:
        try:
            decision = json.loads(row["final_trade_decision"])
        except (json.JSONDecodeError, TypeError):
            decision = None
    kpi: dict = {}
    if row["chart_data"]:
        try:
            kpi = json.loads(row["chart_data"]).get("kpi") or {}
        except (json.JSONDecodeError, TypeError, AttributeError):
            kpi = {}
    return {
        "session_id": row["session_id"],
        "created_at": row["created_at"],
        "final_trade_decision": decision,
        "kpi": kpi,
    }


def update_session_status(session_id: str, status: str, failure_reason: str | None = None) -> bool:
    """更新 session 状态（如 running -> failed）。可选写入 failure_reason。"""
    conn = _get_db()
    if failure_reason is not None:
        cur = conn.execute(
            "UPDATE sessions SET status = ?, failure_reason = ? WHERE session_id = ?",
            (status, failure_reason, session_id),
        )
    else:
        cur = conn.execute(
            "UPDATE sessions SET status = ? WHERE session_id = ?",
            (status, session_id),
        )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def set_session_trace_id(session_id: str, trace_id: str | None) -> bool:
    """持久化 session 最近一次运行关联的 Langfuse trace（add-user-feedback）。

    反馈端点按 session 解析该 trace 落 score;trace_id 为 None 时不覆盖旧值,
    返回是否写入。
    """
    if not trace_id:
        return False
    conn = _get_db()
    try:
        cur = conn.execute(
            "UPDATE sessions SET langfuse_trace_id = ? WHERE session_id = ?",
            (trace_id, session_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_session_trace_id(session_id: str) -> str | None:
    """读取 session 最近一次运行关联的 Langfuse trace_id；无则返回 None。"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT langfuse_trace_id FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def create_chat_session(display_name: str) -> str:
    """创建快速模式对话 session，返回 session_id。

    快速模式无股票代码/报告，仅记录 chat_history。
    display_name 通常为用户问题摘要。
    """
    return create_session(
        stock_code="",
        stock_name="",
        display_name=display_name,
        session_type="chat",
    )


def list_sessions() -> list[dict[str, Any]]:
    """List all sessions (metadata only, no report body)."""
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT session_id, stock_code, stock_name, display_name, status,
               focus, pending_intent, created_at, duration_ms, session_type,
               failure_reason,
               length(report_markdown) as report_len
        FROM sessions
        ORDER BY created_at DESC
        """
    ).fetchall()
    conn.close()
    # 防御性兜底：即便迁移漏网或外部写入脏数据，前端也拿不到非法 created_at
    result = []
    for r in rows:
        d = dict(r)
        d["created_at"] = _normalize_created_at(d.get("created_at"))
        result.append(d)
    return result


def get_session(session_id: str) -> dict[str, Any] | None:
    """Get full session by id."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not row:
        conn.close()
        return None
    d = dict(row)
    # 查询事件 journal 最大 seq，供前端断点续传使用
    seqRow = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM session_events WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    d["last_seq"] = seqRow["max_seq"] if seqRow else 0
    for key in (
        "chart_data",
        "analyst_reports",
        "agent_process",
        "analyst_summaries",
        "chat_history",
        "pipeline_timelines",
    ):
        if d.get(key):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                d[key] = json.loads(d[key])
    # file_paths：报告文件产物（update-file-export-entry）。与其它 JSON 列同款解析，
    # 但 NULL/缺失（旧会话）一律回退 {}，保证 API 恒定返回该键，前端可安全读取。
    raw_file_paths = d.get("file_paths")
    d["file_paths"] = {}
    if raw_file_paths:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            d["file_paths"] = json.loads(raw_file_paths)
    # citations：结构化引用数组（add-citation-display）。NULL/解析失败 → None
    # （旧会话缺省，前端不渲染引用标记）
    raw_citations = d.get("citations")
    d["citations"] = None
    if raw_citations:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            parsed = json.loads(raw_citations)
            if isinstance(parsed, list):
                d["citations"] = parsed
    return d


def rename_session(session_id: str, display_name: str) -> bool:
    """Rename a session's display name."""
    conn = _get_db()
    cur = conn.execute(
        "UPDATE sessions SET display_name = ? WHERE session_id = ?",
        (display_name, session_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_session(session_id: str) -> bool:
    """Delete a session and cascade-delete its event journal."""
    conn = _get_db()
    cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    # 级联删除事件日志（对应 delta spec Task 1.3）
    conn.execute("DELETE FROM session_events WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def clear_all_sessions() -> int:
    """清空全部会话及其事件日志，返回删除的会话数。"""
    conn = _get_db()
    try:
        n = int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        conn.execute("DELETE FROM session_events")
        conn.execute("DELETE FROM sessions")
        conn.commit()
        return n
    finally:
        conn.close()


def append_chat(
    session_id: str,
    role: str,
    content: str,
    thinking: str | None = None,
    tool_calls: list | None = None,
    agent_timeline: list | None = None,
) -> None:
    """Append a chat message to session's chat_history.

    Args:
        session_id: 会话 ID
        role: 消息角色（user/assistant）
        content: 最终回复文本
        thinking: 可选的 agent 思考过程原文（用于历史会话回显）
        tool_calls: 可选的工具调用记录列表（含 name/args/result_text/done）
        agent_timeline: 可选的结构化时序（TimelineItem 数组：思考/搜索/工具调用交错），
            供前端原样恢复（不再走拍平近似）
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT chat_history FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        conn.close()
        return
    try:
        history = json.loads(row["chat_history"]) if row["chat_history"] else []
    except (json.JSONDecodeError, TypeError):
        history = []
    entry: dict = {"role": role, "content": content, "ts": datetime.now().isoformat()}
    if thinking:
        entry["thinking"] = thinking
    if tool_calls:
        entry["tool_calls"] = tool_calls
    if agent_timeline:
        entry["agentTimeline"] = agent_timeline
    history.append(entry)
    conn.execute(
        "UPDATE sessions SET chat_history = ? WHERE session_id = ?",
        (json.dumps(history, ensure_ascii=False), session_id),
    )
    conn.commit()
    conn.close()


def set_pipeline_anchor(session_id: str) -> None:
    """将管线触发锚点持久化到 sessions.pipeline_anchor 列。

    锚点 = chat_history 中最后一条 role='user' 条目的索引 + 1，
    即"触发本轮分析的用户消息之后"，供前端历史重建定位报告消息插入位置。
    锚定 user 消息而非取 chat_history 长度，避免 ReAct 路径 assistant 在途
    增量 upsert 导致锚点随持久化时机抖动。chat_history 无 user 消息时不写。
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT chat_history FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        conn.close()
        return
    try:
        history = json.loads(row["chat_history"]) if row["chat_history"] else []
    except (json.JSONDecodeError, TypeError):
        history = []
    # 从末尾查找最后一条 user 消息，锚点 = 其索引 + 1
    anchor = 0
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "user":
            anchor = i + 1
            break
    if anchor == 0:
        # chat_history 无 user 消息，不写锚点
        conn.close()
        return
    conn.execute(
        "UPDATE sessions SET pipeline_anchor = ? WHERE session_id = ?",
        (anchor, session_id),
    )
    conn.commit()
    conn.close()


def upsert_chat(
    session_id: str,
    role: str,
    content: str,
    thinking: str | None = None,
    tool_calls: list | None = None,
    agent_timeline: list | None = None,
) -> None:
    """upsert 语义的 chat 持久化：查找最后一条指定 role 的消息，存在则更新，无则追加。

    用于运行中增量持久化：每 10 秒将 collector 内容 upsert 到 chat_history，
    避免用户中途切走后 assistant 回复内容丢失。
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT chat_history FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        conn.close()
        return
    try:
        history = json.loads(row["chat_history"]) if row["chat_history"] else []
    except (json.JSONDecodeError, TypeError):
        history = []

    # 从末尾查找最后一条指定 role 的消息。
    # 仅当它位于最后一条 user 消息之后（即属于当前轮次）时才更新；
    # 否则说明新一轮已开始（本轮 user 已落库、assistant 尚未产生），
    # 必须追加新条目——否则新一轮的增量持久化会覆盖上一轮的 assistant 内容，
    # 导致历史重建后本轮内容串到上一轮位置、上一轮内容丢失
    lastUserIdx = -1
    if role != "user":
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                lastUserIdx = i
                break
    found = False
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == role and i > lastUserIdx:
            history[i]["content"] = content
            if thinking is not None:
                history[i]["thinking"] = thinking
            if tool_calls is not None:
                history[i]["tool_calls"] = tool_calls
            if agent_timeline is not None:
                history[i]["agentTimeline"] = agent_timeline
            history[i]["ts"] = datetime.now().isoformat()
            found = True
            break

    if not found:
        entry: dict = {"role": role, "content": content, "ts": datetime.now().isoformat()}
        if thinking:
            entry["thinking"] = thinking
        if tool_calls:
            entry["tool_calls"] = tool_calls
        if agent_timeline:
            entry["agentTimeline"] = agent_timeline
        history.append(entry)

    conn.execute(
        "UPDATE sessions SET chat_history = ? WHERE session_id = ?",
        (json.dumps(history, ensure_ascii=False), session_id),
    )
    conn.commit()
    conn.close()


def update_pipeline_snapshot(session_id: str, snapshot: dict) -> bool:
    """持久化管线进度快照（JSON）。返回是否更新到行。"""
    conn = _get_db()
    cur = conn.execute(
        "UPDATE sessions SET pipeline_snapshot = ? WHERE session_id = ?",
        (json.dumps(snapshot, ensure_ascii=False), session_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def update_pipeline_timelines(session_id: str, timelines: dict) -> bool:
    """持久化管线节点时序（JSON：{node: [TimelineItem]}）。返回是否更新到行。"""
    return update_pipeline_timelines_json(session_id, json.dumps(timelines, ensure_ascii=False))


def update_pipeline_timelines_json(session_id: str, payload_json: str) -> bool:
    """以预序列化 JSON 写入管线时序（写放大治理，fix-timeline-write-amplification）。

    调用方（两条管线消费路径）序列化一次并测得字节数——自适应中间写间隔的
    输入（timeline_persist_interval），避免为测字节数重复 dumps。
    """
    conn = _get_db()
    cur = conn.execute(
        "UPDATE sessions SET pipeline_timelines = ? WHERE session_id = ?",
        (payload_json, session_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0
