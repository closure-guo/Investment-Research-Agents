"""运维配置与运行历史存储(delta add-eval-ops-console Task 1)。

两张表:
- ``ops_config``:键值配置(表值优先于 env,见 ``get_cohort_settings``);
- ``job_runs``:任务运行历史——插入即 running,终态由 ``finish_job_run`` 更新,
  summary 存 JSON(超 8192 字符退化为显式裁剪标记,读取侧永远是合法 dict)。

连接参数与 ``outcome/track_record/model.py::_connect`` 同款(WAL + busy_timeout=15s +
check_same_thread=False),db_path 调用期注入;不 import 其私有名,本包自建 ``_connect``。
所有公开函数入口自带幂等 DDL(``init_ops``):下游(Task 2/3)漏调 init 也不会
no such table,重复建表零副作用。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── cohort 配置键(表键名与 env 名分离:表键 snake_case,env 沿用历史大写)──
COHORT_ENABLED_KEY = "cohort_enabled"
COHORT_HOUR_KEY = "cohort_hour"
COHORT_MINUTE_KEY = "cohort_minute"

COHORT_ENV_ENABLED = "COHORT_ENABLED"
COHORT_ENV_HOUR = "COHORT_HOUR"
COHORT_ENV_MINUTE = "COHORT_MINUTE"

# 表/env 都无值时:关闭,18:00(与 scheduler 历史默认一致)
COHORT_DEFAULT_ENABLED = False
COHORT_DEFAULT_HOUR = 18
COHORT_DEFAULT_MINUTE = 0

# job_runs.kind / status 取值域(Task 2/3 复用,前端按此渲染徽章)
JOB_KINDS = ("scheduled", "manual", "config-change")
JOB_STATUSES = ("running", "ok", "failed", "skipped-disabled")

_MAX_SUMMARY_CHARS = 8192

OPS_DDL = """
CREATE TABLE IF NOT EXISTS ops_config (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_runs (
  run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id      TEXT NOT NULL,
  kind        TEXT NOT NULL,          -- scheduled | manual | config-change
  source      TEXT NOT NULL DEFAULT 'scheduled',
  status      TEXT NOT NULL,          -- running | ok | failed | skipped-disabled
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  summary     TEXT,                   -- JSON(超 8192 字符退化为裁剪标记)
  error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_runs_job_started ON job_runs(job_id, started_at DESC);
"""


def _default_db_path() -> Path:
    return Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    """短连接(与 track_record/model.py::_connect 同款参数);不在此处建表。"""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_ops(db_path: str | Path | None = None) -> None:
    """建两表 + 索引;幂等(重复调用不报错,旧库直接升级)。"""
    conn = _connect(db_path)
    try:
        conn.executescript(OPS_DDL)
        conn.commit()
    finally:
        conn.close()


# ── ops_config:键值配置(表值优先,是运维界面的唯一写入点)──
def get_config(key: str, db_path: str | Path | None = None) -> str | None:
    init_ops(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM ops_config WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row is not None else None
    finally:
        conn.close()


def set_config(key: str, value: str, db_path: str | Path | None = None) -> None:
    """写入/覆盖配置项(同键 upsert,不产生第二行)。"""
    init_ops(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO ops_config (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


# ── cohort 配置解析:表值 → env → 默认(逐键独立回退)──
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


def _parse_bool(raw: str | None, *, label: str) -> bool | None:
    """解析布尔字符串;None 或缺省 → None(表示「未配置」,由调用方回退)。"""
    if raw is None:
        return None
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    logger.warning("%s 非布尔值(%r),按未配置处理", label, raw)
    return None


def _env_int(name: str, *, lo: int, hi: int) -> int | None:
    """读整型 env:缺省 → None;非数值/越界 → WARN + None(scheduler._env_int 同款语义)。"""
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("%s 非数值(%r),回退默认", name, raw)
        return None
    if not lo <= value <= hi:
        logger.warning("%s 越界(%s),回退默认", name, value)
        return None
    return value


def _resolve_bool(table_key: str, env_name: str, default: bool, db_path: Any) -> tuple[bool, str]:
    raw = get_config(table_key, db_path)
    if raw is not None:
        parsed = _parse_bool(raw, label=table_key)
        if parsed is not None:
            return parsed, "table"
    parsed = _parse_bool(os.getenv(env_name), label=env_name)
    if parsed is not None:
        return parsed, "env"
    return default, "default"


def _resolve_int(
    table_key: str, env_name: str, default: int, *, lo: int, hi: int, db_path: Any
) -> tuple[int, str]:
    raw = get_config(table_key, db_path)
    if raw is not None:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning("%s 表值非数值(%r),回退 env/默认", table_key, raw)
        else:
            if lo <= value <= hi:
                return value, "table"
            logger.warning("%s 表值越界(%s),回退 env/默认", table_key, value)
    env_value = _env_int(env_name, lo=lo, hi=hi)
    if env_value is not None:
        return env_value, "env"
    return default, "default"


def get_cohort_settings(db_path: str | Path | None = None) -> dict[str, Any]:
    """cohort 开关/时刻解析:逐键「表值 → env → 默认」,默认 (False, 18, 0)。

    ``source``:任一键取自 ``ops_config`` → ``"ops_config"``(表是运维改过的真源);
    否则任一键取自 env → ``"env"``;全默认 → ``"default"``。
    非法表值/env 值按未配置处理并 WARN,不得炸调用方(运维手滑兜底)。
    """
    enabled, enabled_src = _resolve_bool(
        COHORT_ENABLED_KEY, COHORT_ENV_ENABLED, COHORT_DEFAULT_ENABLED, db_path
    )
    hour, hour_src = _resolve_int(
        COHORT_HOUR_KEY, COHORT_ENV_HOUR, COHORT_DEFAULT_HOUR, lo=0, hi=23, db_path=db_path
    )
    minute, minute_src = _resolve_int(
        COHORT_MINUTE_KEY, COHORT_ENV_MINUTE, COHORT_DEFAULT_MINUTE, lo=0, hi=59, db_path=db_path
    )
    sources = {enabled_src, hour_src, minute_src}
    source = "ops_config" if "table" in sources else ("env" if "env" in sources else "default")
    return {"enabled": enabled, "hour": hour, "minute": minute, "source": source}


def bootstrap_cohort_from_env(db_path: str | Path | None = None) -> None:
    """env → 表的一次性引导:仅当表内该键缺失且 env 值合法时写入。

    已有表值不覆盖——运维在界面改过的值重启后保持(重启保持语义);env 只在
    首次引导时生效,后续成为「部署默认」的种子而非每次启动的真相源。
    """
    init_ops(db_path)
    if get_config(COHORT_ENABLED_KEY, db_path) is None:
        enabled = _parse_bool(os.getenv(COHORT_ENV_ENABLED), label=COHORT_ENV_ENABLED)
        if enabled is not None:
            set_config(COHORT_ENABLED_KEY, "1" if enabled else "0", db_path)
    for table_key, env_name, lo, hi in (
        (COHORT_HOUR_KEY, COHORT_ENV_HOUR, 0, 23),
        (COHORT_MINUTE_KEY, COHORT_ENV_MINUTE, 0, 59),
    ):
        if get_config(table_key, db_path) is not None:
            continue
        value = _env_int(env_name, lo=lo, hi=hi)
        if value is not None:
            set_config(table_key, str(value), db_path)


# ── job_runs:运行历史(append + 终态更新,供运维界面与补跑审计)──
def _dump_summary(summary: dict[str, Any] | None) -> str | None:
    """JSON 序列化;超长退化为 ``{"truncated", "original_chars", "preview"}``。

    不得裸截断——半截 JSON 会让读取侧 json.loads 抛错,历史页整页 500。
    """
    if summary is None:
        return None
    text = json.dumps(summary, ensure_ascii=False, default=str)
    if len(text) <= _MAX_SUMMARY_CHARS:
        return text
    budget = _MAX_SUMMARY_CHARS
    while True:
        payload = json.dumps(
            {"truncated": True, "original_chars": len(text), "preview": text[:budget]},
            ensure_ascii=False,
        )
        if len(payload) <= _MAX_SUMMARY_CHARS or budget == 0:
            return payload
        budget //= 2  # 转义可能膨胀,逐次减半直到装得下


def _load_summary(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}  # 老数据/手改行兜底,不炸历史页


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["summary"] = _load_summary(data.get("summary"))
    return data


def insert_job_run(
    job_id: str,
    kind: str,
    status: str,
    *,
    source: str = "scheduled",
    summary: dict[str, Any] | None = None,
    error: str | None = None,
    db_path: str | Path | None = None,
) -> int:
    """开一行运行历史,返回 run_id;终态由 ``finish_job_run`` 更新。"""
    init_ops(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """INSERT INTO job_runs (job_id, kind, source, status, started_at, summary, error)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                kind,
                source,
                status,
                datetime.now().isoformat(),
                _dump_summary(summary),
                error,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def clear_job_runs(db_path: str | Path | None = None) -> None:
    """清空 job_runs 全表（TESTING 造数复位面；E2E 隔离库内使用）。"""
    init_ops(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM job_runs")
        conn.commit()
    finally:
        conn.close()


def set_job_run_timestamps(
    run_id: int,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """改写某运行行的 started_at / finished_at（TESTING 造数面：E2E 需要
    「昨天跑过」「今天 15:41 失败」这类确定性时刻；与单测 _backdate_started_at
    同一语义，供 /api/test/seed 复用）。"""
    init_ops(db_path)
    conn = _connect(db_path)
    try:
        if started_at is not None:
            conn.execute("UPDATE job_runs SET started_at=? WHERE run_id=?", (started_at, run_id))
        if finished_at is not None:
            conn.execute("UPDATE job_runs SET finished_at=? WHERE run_id=?", (finished_at, run_id))
        conn.commit()
    finally:
        conn.close()


def finish_job_run(
    run_id: int,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """落终态 + finished_at;summary/error 为 None 时保留原值(不清空已有信息)。"""
    init_ops(db_path)
    sets = ["status=?", "finished_at=?"]
    params: list[Any] = [status, datetime.now().isoformat()]
    if summary is not None:
        sets.append("summary=?")
        params.append(_dump_summary(summary))
    if error is not None:
        sets.append("error=?")
        params.append(error)
    params.append(run_id)
    # sets 只由上方固定字面量拼装,值全部参数化
    sql = f"UPDATE job_runs SET {', '.join(sets)} WHERE run_id=?"  # noqa: S608
    conn = _connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def list_job_runs(
    job_id: str | None = None,
    *,
    limit: int = 20,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """运行历史,最新在前(job_id 为 None 时跨任务);limit 夹取 [1, 1000]。"""
    init_ops(db_path)
    limit = max(1, min(int(limit), 1000))
    sql = "SELECT * FROM job_runs"  # noqa: S608 — 无外部拼接
    params: list[Any] = []
    if job_id:
        sql += " WHERE job_id=?"
        params.append(job_id)
    sql += " ORDER BY started_at DESC, run_id DESC LIMIT ?"
    params.append(limit)
    conn = _connect(db_path)
    try:
        return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_job_run(run_id: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """按 ``run_id`` 直读一行(``WHERE run_id=?``),不依赖「最新 N 行」窗口。

    运行历史由 ``prune_job_runs`` 按 job 保序,超出列表窗口的旧行仍可经本函数读取
    (单条运行接口不得因窗口上限而 404)。无此行 → None。
    """
    init_ops(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM job_runs WHERE run_id=?", (int(run_id),)).fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        conn.close()


def last_job_run(job_id: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """某任务最近一行(含 running 中的行);无历史 → None。"""
    init_ops(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM job_runs WHERE job_id=? ORDER BY started_at DESC, run_id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        conn.close()


def job_run_statuses_on(job_id: str, day: str, *, db_path: str | Path | None = None) -> list[str]:
    """某任务在 ``day``（规范 ``YYYY-MM-DD``）当天全部运行行的状态（开始时刻升序）。

    ``missed_today``（只问「有没有行」）与启动补跑规划（只问「有没有成功行」）共用
    本读数——一处口径，两处消费，避免状态接口与补偿逻辑各写一套日期过滤而漂移。
    非规范日期 → ``ValueError``（不猜日期，与 ``snapshot._parse_date_param`` 同纪律）。
    """
    start = date.fromisoformat(day)
    if start.isoformat() != day:
        raise ValueError(f"day 必须是规范 YYYY-MM-DD: {day!r}")
    end = (start + timedelta(days=1)).isoformat()
    init_ops(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT status FROM job_runs WHERE job_id=? AND started_at>=? AND started_at<? "
            "ORDER BY started_at, run_id",
            (job_id, start.isoformat(), end),
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        conn.close()


def prune_job_runs(keep_per_job: int = 500, db_path: str | Path | None = None) -> int:
    """每个 job 只保留最新 keep_per_job 行,返回删除行数(历史表防无限膨胀)。

    keep_per_job=0 → 清空历史。
    """
    init_ops(db_path)
    keep = max(0, int(keep_per_job))
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """DELETE FROM job_runs WHERE run_id IN (
                 SELECT run_id FROM (
                   SELECT run_id,
                          ROW_NUMBER() OVER (
                            PARTITION BY job_id ORDER BY started_at DESC, run_id DESC
                          ) AS rn
                   FROM job_runs
                 ) WHERE rn > ?
               )""",
            (keep,),
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()
