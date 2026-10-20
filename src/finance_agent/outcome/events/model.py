"""业绩预告事件线存储层（delta add-event-driven-lane T2）。

新库 ``data/events.db``（env ``EVENTS_DB_PATH`` **调用期**读，测试/E2E 注入隔离路径），
与 ``outcome/track_record/model.py``、``outcome/regime/model.py`` 同款 SQLite 纪律
（WAL + busy_timeout 短连接，row_factory=Row）。

四表（口径见 docs/evals/metrics.md §1.15）：
- ``earnings_forecasts``：预告记录，唯一键 ``(code, report_period, source_record_id)``
  （``source_record_id`` = ``预测指标|公告日期`` 稳定复合键，重抓幂等覆盖为最新值）；
  ``available_at`` = 公告日期（时点截断的依据，MUST NOT 前视）。
- ``event_triage``：分诊结果，``forecast_key`` 主键（同键重跑覆盖 = 幂等，零重复）。
- ``event_watchlist``：观察清单，``forecast_key`` 唯一（outperform 去重：同键已有条目
  不重复插入）。
- ``event_journal``：独立记账（入场=T+1 开盘口径；horizon 默认 5 个交易日；顺延原因
  留痕 ``note=deferred:xxx``；参考价缺失保持 open + 留痕，MUST NOT 折算 0）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS earnings_forecasts (
  code             TEXT NOT NULL,
  report_period    TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  name             TEXT,
  forecast_type    TEXT,
  change_low       REAL,
  change_high      REAL,
  announce_date    TEXT,
  available_at     TEXT,
  source           TEXT NOT NULL DEFAULT 'eastmoney',
  PRIMARY KEY (code, report_period, source_record_id)
);
CREATE INDEX IF NOT EXISTS idx_forecasts_available ON earnings_forecasts(available_at);

CREATE TABLE IF NOT EXISTS event_triage (
  forecast_key       TEXT PRIMARY KEY,
  verdict            TEXT NOT NULL CHECK (verdict IN ('outperform','in_line','risk')),
  reason             TEXT NOT NULL,
  evidence_json      TEXT NOT NULL DEFAULT '[]',
  model              TEXT,
  prompt_fingerprint TEXT,
  triaged_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_watchlist (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  forecast_key TEXT NOT NULL UNIQUE,
  source       TEXT NOT NULL DEFAULT 'earnings',
  status       TEXT NOT NULL DEFAULT 'pending',
  session_id   TEXT,
  created_at   TEXT NOT NULL,
  note         TEXT
);

CREATE TABLE IF NOT EXISTS event_journal (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  forecast_key TEXT NOT NULL UNIQUE,
  source       TEXT NOT NULL DEFAULT 'earnings',
  symbol       TEXT NOT NULL,
  entry_date   TEXT,
  entry_price  REAL,
  horizon_days INTEGER NOT NULL DEFAULT 5,
  status       TEXT NOT NULL DEFAULT 'open',
  settle_date  TEXT,
  settle_price REAL,
  raw_return   REAL,
  note         TEXT,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_status ON event_journal(status);
CREATE INDEX IF NOT EXISTS idx_journal_entry ON event_journal(entry_date);
"""

# 观察清单状态（pending=待分析；analyzing=已发起深度分析并关联 session）
WATCHLIST_STATUS_PENDING = "pending"
WATCHLIST_STATUS_ANALYZING = "analyzing"

# 预告来源缺省（DDL 与 upsert 双处同源；抓取器恒显式传 ``eastmoney``）
DEFAULT_SOURCE = "eastmoney"

# 记账状态（open=未到期；settled=已结算）
JOURNAL_STATUS_OPEN = "open"
JOURNAL_STATUS_SETTLED = "settled"

# 已结算读数读取上限（防无界查询；scorecards 装载器消费）
_MAX_SETTLED_LIMIT = 10_000


def _default_db_path() -> Path:
    """默认事件库路径；**调用期**读取 env（测试/E2E 经 ``EVENTS_DB_PATH`` 覆盖）。"""
    return Path(os.getenv("EVENTS_DB_PATH", "data/events.db"))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    """短连接（track_record/model.py 同款纪律）：WAL + busy_timeout，row_factory=Row。"""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_events_db(db_path: str | Path | None = None) -> Path:
    """幂等建四表（+ 转债三表并库迁移链，add-convertible-bond-coverage），返回库路径。

    cb 三表与 ``event_watchlist.note`` 增量列由 ``cb_model.init_cb_db`` 在同一库上
    幂等应用（cb_model 单向依赖本模块的 ``_connect``，故此处**函数内**局部导入，
    避免模块级循环导入）。
    """
    from finance_agent.outcome.events.cb_model import init_cb_db

    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(EVENTS_DDL)
        conn.commit()
    finally:
        conn.close()
    init_cb_db(path)
    return path


def forecast_key(row: Mapping[str, Any]) -> str:
    """预告行 → 稳定复合键 ``code|report_period|source_record_id``（分诊/清单/记账外键）。"""
    return f"{row['code']}|{row['report_period']}|{row['source_record_id']}"


# 预告表列（写入映射；键序 = DDL 列序）
_FORECAST_COLUMNS: tuple[str, ...] = (
    "code",
    "report_period",
    "source_record_id",
    "name",
    "forecast_type",
    "change_low",
    "change_high",
    "announce_date",
    "available_at",
    "source",
)


def upsert_forecasts(
    rows: Iterable[Mapping[str, Any]], *, db_path: str | Path | None = None
) -> int:
    """幂等写入预告记录（``INSERT ... ON CONFLICT(主键) DO UPDATE`` 覆盖为最新值）。

    返回写入行数（= 输入行数；空输入 0，不建库不报错）。``available_at`` 缺省取
    ``announce_date``（可得时点 = 公告日，时点截断依据）；``source`` 缺省
    ``"eastmoney"``。
    """
    payload = [dict(row) for row in rows]
    if not payload:
        return 0
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        columns = ", ".join(_FORECAST_COLUMNS)
        placeholders = ", ".join("?" * len(_FORECAST_COLUMNS))
        updates = ", ".join(
            f"{col}=excluded.{col}"
            for col in _FORECAST_COLUMNS
            if col not in ("code", "report_period", "source_record_id")
        )
        sql = (
            f"INSERT INTO earnings_forecasts ({columns}) VALUES ({placeholders}) "  # noqa: S608
            f"ON CONFLICT(code, report_period, source_record_id) DO UPDATE SET {updates}"
        )
        for row in payload:
            values = [
                (
                    row.get("available_at") or row.get("announce_date")
                    if col == "available_at"
                    else (row.get("source") or DEFAULT_SOURCE)
                    if col == "source"
                    else row.get(col)
                )
                for col in _FORECAST_COLUMNS
            ]
            conn.execute(sql, values)
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def list_untriaged(
    *,
    as_of: str | None = None,
    since: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """未分诊预告（无 ``event_triage`` 行）；``as_of`` 给定时按 ``available_at <= as_of``
    做时点截断（MUST NOT 前视），``since`` 给定时按 ``available_at >= since`` 做分诊
    窗口截断（历史积压不进每日调度批），缺省均不过滤。按 ``available_at``、``code`` 升序。
    """
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        sql = (
            "SELECT f.* FROM earnings_forecasts f "
            "LEFT JOIN event_triage t "
            "ON t.forecast_key = f.code || '|' || f.report_period || '|' || f.source_record_id "
            "WHERE t.forecast_key IS NULL"
        )
        params: list[Any] = []
        if as_of is not None:
            sql += " AND f.available_at <= ?"
            params.append(as_of)
        if since is not None:
            sql += " AND f.available_at >= ?"
            params.append(since)
        sql += " ORDER BY f.available_at ASC, f.code ASC, f.source_record_id ASC"  # noqa: S608
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def record_triage(
    key: str,
    *,
    verdict: str,
    reason: str,
    evidence: Sequence[Mapping[str, Any]] | None = None,
    model: str | None = None,
    prompt_fingerprint: str | None = None,
    triaged_at: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """写入/覆盖一条分诊结果（同 ``forecast_key`` 单行 = 重跑幂等零重复）。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO event_triage
                 (forecast_key, verdict, reason, evidence_json, model, prompt_fingerprint, triaged_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(forecast_key) DO UPDATE SET
                 verdict=excluded.verdict,
                 reason=excluded.reason,
                 evidence_json=excluded.evidence_json,
                 model=excluded.model,
                 prompt_fingerprint=excluded.prompt_fingerprint,
                 triaged_at=excluded.triaged_at""",
            (
                key,
                verdict,
                reason,
                json.dumps(list(evidence or []), ensure_ascii=False),
                model,
                prompt_fingerprint,
                triaged_at or datetime.now().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def insert_watchlist(
    key: str,
    *,
    source: str = "earnings",
    status: str = WATCHLIST_STATUS_PENDING,
    session_id: str | None = None,
    note: str | None = None,
    created_at: str | None = None,
    db_path: str | Path | None = None,
) -> int | None:
    """观察清单插入（同 ``forecast_key`` 去重，原子）：

    ``INSERT ... ON CONFLICT(forecast_key) DO NOTHING``——命中冲突 → ``None``
    （不重复插入、不覆盖既有状态/session）；是否真正插入按 ``rowcount`` 判定
    （先查后插两步在并发下会竞态，单语句原子）。``note`` = 规则化注记
    （cbb 条目倒计时/理由的载体；业绩预告条目恒 None）。
    """
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO event_watchlist
                 (forecast_key, source, status, session_id, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(forecast_key) DO NOTHING""",
            (key, source, status, session_id, note, created_at or datetime.now().isoformat()),
        )
        conn.commit()
        if cursor.rowcount <= 0:
            return None
        return int(cursor.lastrowid or 0)
    finally:
        conn.close()


def update_watchlist_note(key: str, *, note: str, db_path: str | Path | None = None) -> bool:
    """刷新清单条目规则化注记（cbb 倒计时每日重算）；**不动** status/session/created_at。

    返回是否有行被更新（未知键 → ``False``）。
    """
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute("UPDATE event_watchlist SET note=? WHERE forecast_key=?", (note, key))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# 清单读取：清单列 + 预告要素 + 分诊理由/证据 + 转债要素（事件/标的/最新行情）
# （端点只读载荷的单一取数面）。cbb 行经 ``event_key = forecast_key`` 联 `cb_events`；
# 现价取该代码**最新交易日**行情（``cb_quotes`` 相关子查询，索引 idx_cb_quotes_code 支撑）。
_WATCHLIST_SELECT = """
SELECT w.id, w.forecast_key, w.source, w.status, w.session_id, w.created_at, w.note,
       f.code, f.name, f.report_period, f.forecast_type, f.change_low, f.change_high,
       f.announce_date, f.available_at,
       t.verdict, t.reason, t.evidence_json, t.model, t.prompt_fingerprint, t.triaged_at,
       e.code AS cb_code, e.event_type AS cb_event_type,
       e.announce_date AS cb_announce_date, e.remain_scale AS cb_remain_scale,
       m.name AS cb_name, m.premium_rate AS cb_premium_rate,
       q.close AS cb_close
FROM event_watchlist w
LEFT JOIN earnings_forecasts f
  ON f.code || '|' || f.report_period || '|' || f.source_record_id = w.forecast_key
LEFT JOIN event_triage t ON t.forecast_key = w.forecast_key
LEFT JOIN cb_events e ON e.event_key = w.forecast_key
LEFT JOIN cb_master m ON m.code = e.code
LEFT JOIN cb_quotes q
  ON q.code = e.code AND q.trade_date = (SELECT MAX(trade_date) FROM cb_quotes WHERE code = e.code)
"""


def list_watchlist(
    *, source: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """观察清单（含预告要素 / 分诊理由与证据 / 公告日 / 状态 / session_id），按 id 升序。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        sql = _WATCHLIST_SELECT
        params: list[Any] = []
        if source is not None:
            sql += " WHERE w.source = ?"
            params.append(source)
        sql += " ORDER BY w.id ASC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_watchlist_item(item_id: int, *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """单条清单条目（同 ``list_watchlist`` 列形）；未知 id → ``None``。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(_WATCHLIST_SELECT + " WHERE w.id = ?", (item_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def mark_watchlist_analyzing(
    item_id: int, *, session_id: str, db_path: str | Path | None = None
) -> bool:
    """发起深度分析后置状态：``analyzing`` + 关联 session_id。返回是否有行被更新。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE event_watchlist SET status=?, session_id=? WHERE id=?",
            (WATCHLIST_STATUS_ANALYZING, session_id, item_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def insert_journal(
    *,
    forecast_key: str,
    source: str = "earnings",
    symbol: str,
    entry_date: str,
    entry_price: float,
    horizon_days: int = 5,
    status: str = JOURNAL_STATUS_OPEN,
    note: str | None = None,
    db_path: str | Path | None = None,
) -> int:
    """写入一条记账（同 ``forecast_key`` 唯一，重复入场 → ``ValueError``，不静默覆盖）。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO event_journal
                 (forecast_key, source, symbol, entry_date, entry_price, horizon_days,
                  status, note, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                forecast_key,
                source,
                symbol,
                entry_date,
                entry_price,
                int(horizon_days),
                status,
                note,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid or 0)
    except sqlite3.IntegrityError as exc:  # forecast_key 唯一：重复入场
        raise ValueError(f"forecast_key 已有记账行，不重复入场: {forecast_key}") from exc
    finally:
        conn.close()


def list_open_journal(
    *, before_date: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """未结算（``status='open'``）记账行；``before_date`` 过滤 ``entry_date <= before_date``。

    按 ``entry_date``、``id`` 升序（结算遍历顺序确定）。
    """
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM event_journal WHERE status = ?"
        params: list[Any] = [JOURNAL_STATUS_OPEN]
        if before_date is not None:
            sql += " AND entry_date <= ?"
            params.append(before_date)
        sql += " ORDER BY entry_date ASC, id ASC"  # noqa: S608
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_journal(row_id: int, *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """单条记账行；未知 id → ``None``。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM event_journal WHERE id=?", (row_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_outperform_without_journal(*, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """``verdict='outperform'`` 且尚无记账行的预告（记账入场的取数面）。

    返回预告行全字段 + 分诊结论（``verdict``/``triaged_at``），按 ``available_at``、
    ``code`` 升序（入场遍历顺序确定）。
    """
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """SELECT f.*, t.verdict AS verdict, t.triaged_at AS triaged_at
               FROM earnings_forecasts f
               JOIN event_triage t
                 ON t.forecast_key = f.code || '|' || f.report_period || '|' || f.source_record_id
               LEFT JOIN event_journal j ON j.forecast_key = t.forecast_key
               WHERE t.verdict = 'outperform' AND j.forecast_key IS NULL
               ORDER BY f.available_at ASC, f.code ASC, f.source_record_id ASC"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def settle_journal(
    row_id: int,
    *,
    settle_date: str,
    settle_price: float,
    raw_return: float,
    note: str | None = None,
    db_path: str | Path | None = None,
) -> bool:
    """结算一条记账：``open`` → ``settled`` + 结算日/结算价/收益；返回是否有行被更新。

    仅 ``status='open'`` 的行可结算（幂等：重复调用第二次返回 ``False``）。
    """
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """UPDATE event_journal
               SET status=?, settle_date=?, settle_price=?, raw_return=?, note=?, updated_at=?
               WHERE id=? AND status=?""",
            (
                JOURNAL_STATUS_SETTLED,
                settle_date,
                settle_price,
                raw_return,
                note,
                datetime.now().isoformat(),
                row_id,
                JOURNAL_STATUS_OPEN,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_journal_note(row_id: int, *, note: str, db_path: str | Path | None = None) -> bool:
    """更新记账留痕（如参考价缺失的 ``settle_pending:xxx`` 重试原因）；返回命中与否。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE event_journal SET note=?, updated_at=? WHERE id=?",
            (note, datetime.now().isoformat(), row_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def list_settled_journal(
    limit: int = _MAX_SETTLED_LIMIT, *, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """已结算记账读数（成绩单 event 族装载器消费）。

    口径：按 ``settle_date`` **降序**（保留近期）取前 ``limit`` 行——超读取上限时
    丢最旧、不静默丢最新。
    """
    limit = max(1, min(int(limit), _MAX_SETTLED_LIMIT))
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM event_journal WHERE status = ? "
            "ORDER BY settle_date DESC, id DESC LIMIT ?",
            (JOURNAL_STATUS_SETTLED, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_all_events_state(db_path: str | Path | None = None) -> dict[str, int]:
    """清空事件线四表（TESTING seed ``events_reset`` 造数复位用）；返回删除行数。

    入口幂等建表（库文件不存在时建成空表，不抛），库路径缺省**调用期**读 env。
    """
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        counts: dict[str, int] = {}
        for table in (
            "event_journal",
            "event_watchlist",
            "event_triage",
            "earnings_forecasts",
        ):
            cursor = conn.execute(f"DELETE FROM {table}")  # noqa: S608 - 表名来自模块常量
            counts[table] = int(cursor.rowcount)
        conn.commit()
        return counts
    finally:
        conn.close()
