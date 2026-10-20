"""研报数据面存储（add-research-report-dataplane）。

四张业务表 + 运行记录表，独立库 ``data/research_reports.db``（env
``RESEARCH_REPORTS_DB_PATH`` 覆盖，调用期读取——沿用 snapshot.py 的连接纪律：
WAL + busy_timeout + 短连接 + 测试经 db_path 显式注入）。

- ``research_reports``：五类研报元数据（期次键 = sha1(标题|机构|发布日期)）；
- ``rating_changes``：巨潮投资评级事件（键 = 标的|机构|日期|评级|前次评级）；
- ``earnings_forecasts``：东财全市场盈利预测快照（键 = 快照日期+标的）；
- ``analysts``：机构×分析师档案（随日批重建聚合）；
- ``collection_runs``：每次采集运行的状态/行数（空窗 ok、失败 failed、降级 degraded）。

合规边界：本模块不含任何全文/PDF 内容列——只存元数据与链接字符串。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = "data/research_reports.db"
# 断更判定阈值：曾有覆盖但 N 天无新报 → stale（先验值，暴露在覆盖状态输出里）
STALE_DAYS = 90
# 近 30 日计数窗口
RECENT_DAYS = 30

RESEARCH_REPORTS_DDL = """
CREATE TABLE IF NOT EXISTS research_reports (
  report_key     TEXT PRIMARY KEY,
  report_type    TEXT NOT NULL,
  title          TEXT NOT NULL,
  org            TEXT,
  analyst        TEXT,
  publish_date   TEXT NOT NULL,
  code           TEXT,
  name           TEXT,
  industry       TEXT,
  rating         TEXT,
  eps_forecasts  TEXT,
  pe_forecasts   TEXT,
  pdf_url        TEXT,
  detail_url     TEXT,
  source         TEXT NOT NULL,
  first_seen_at  TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rr_code_date ON research_reports(code, publish_date);
CREATE INDEX IF NOT EXISTS idx_rr_date_type ON research_reports(publish_date, report_type);

CREATE TABLE IF NOT EXISTS rating_changes (
  event_key       TEXT PRIMARY KEY,
  code            TEXT NOT NULL,
  name            TEXT,
  publish_date    TEXT NOT NULL,
  org             TEXT NOT NULL,
  analyst         TEXT,
  rating          TEXT,
  is_first_rating INTEGER,
  rating_change   TEXT,
  prev_rating     TEXT,
  target_low      REAL,
  target_high     REAL,
  source          TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rc_code_date ON rating_changes(code, publish_date);

CREATE TABLE IF NOT EXISTS earnings_forecasts (
  snapshot_date   TEXT NOT NULL,
  code            TEXT NOT NULL,
  name            TEXT,
  report_count    INTEGER,
  rating_buy      INTEGER,
  rating_add      INTEGER,
  rating_neutral  INTEGER,
  rating_reduce   INTEGER,
  rating_sell     INTEGER,
  eps_y1          REAL,
  eps_y2          REAL,
  eps_y3          REAL,
  eps_y1_year     INTEGER,
  eps_y2_year     INTEGER,
  eps_y3_year     INTEGER,
  source          TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  PRIMARY KEY (snapshot_date, code)
);
CREATE INDEX IF NOT EXISTS idx_ef_code_date ON earnings_forecasts(code, snapshot_date);

CREATE TABLE IF NOT EXISTS analysts (
  org             TEXT NOT NULL,
  analyst         TEXT NOT NULL,
  covered_codes   INTEGER NOT NULL DEFAULT 0,
  reports_90d     INTEGER NOT NULL DEFAULT 0,
  rating_buy      INTEGER NOT NULL DEFAULT 0,
  rating_add      INTEGER NOT NULL DEFAULT 0,
  rating_neutral  INTEGER NOT NULL DEFAULT 0,
  rating_reduce   INTEGER NOT NULL DEFAULT 0,
  rating_sell     INTEGER NOT NULL DEFAULT 0,
  updated_at      TEXT NOT NULL,
  PRIMARY KEY (org, analyst)
);

CREATE TABLE IF NOT EXISTS collection_runs (
  run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  job           TEXT NOT NULL,
  window_start  TEXT,
  window_end    TEXT,
  status        TEXT NOT NULL,
  rows          INTEGER NOT NULL DEFAULT 0,
  detail        TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cr_job_time ON collection_runs(job, run_id);

-- 手工投喂全文（add-research-report-dataplane：个人研究自用；文件内容哈希幂等）
CREATE TABLE IF NOT EXISTS report_documents (
  doc_key       TEXT PRIMARY KEY,
  filename      TEXT NOT NULL,
  file_path     TEXT,
  title         TEXT,
  org           TEXT,
  publish_date  TEXT,
  page_count    INTEGER,
  char_count    INTEGER,
  text          TEXT NOT NULL,
  usage_policy  TEXT NOT NULL DEFAULT 'personal_research',
  source_type   TEXT NOT NULL DEFAULT 'manual_feed',
  ingested_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rd_date ON report_documents(publish_date);

-- 投喂文档的 LLM 结构化抽取（一份文档一行；重抽可覆盖）
CREATE TABLE IF NOT EXISTS report_extractions (
  doc_key         TEXT PRIMARY KEY,
  broker          TEXT,
  analyst         TEXT,
  report_date     TEXT,
  report_type     TEXT,
  stocks_json     TEXT,
  core_logic_json TEXT,
  catalysts_json  TEXT,
  risks_json      TEXT,
  summary         TEXT,
  model           TEXT,
  prompt_version  TEXT,
  extracted_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_re_date ON report_extractions(report_date);
"""

_TITLE_WS = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_db_path() -> Path:
    """默认研报库路径；调用期读取 env（测试/E2E 经 RESEARCH_REPORTS_DB_PATH 覆盖）。"""
    return Path(os.getenv("RESEARCH_REPORTS_DB_PATH", DEFAULT_DB_PATH))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(RESEARCH_REPORTS_DDL)
        conn.commit()
    finally:
        conn.close()
    return path


def report_key(title: str, org: str, publish_date: str) -> str:
    """期次键：标题空白归一 + 机构 + 发布日期 → sha1。"""
    normalized = "|".join(
        (
            _TITLE_WS.sub(" ", (title or "").strip()),
            (org or "").strip(),
            (publish_date or "").strip(),
        )
    )
    return hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()


def _rating_event_key(code: str, org: str, publish_date: str, rating: str, prev: str) -> str:
    normalized = "|".join(
        (
            (code or "").strip(),
            (org or "").strip(),
            (publish_date or "").strip(),
            (rating or "").strip(),
            (prev or "").strip(),
        )
    )
    return hashlib.sha1(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()


def _json_or_none(value: Any) -> str | None:
    if value is None or value == {} or value == []:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def upsert_reports(rows: Iterable[dict], db_path: str | Path | None = None) -> int:
    """幂等写入研报元数据；跨源同键合并 source（逗号分隔）。返回处理行数。"""
    init_db(db_path)
    now = _now_iso()
    payload: list[tuple] = []
    for row in rows:
        title = (row.get("title") or "").strip()
        org = (row.get("org") or "").strip()
        publish_date = (row.get("publish_date") or "").strip()
        if not title or not publish_date:
            continue
        payload.append(
            (
                report_key(title, org, publish_date),
                (row.get("report_type") or "").strip() or "unknown",
                title,
                org,
                (row.get("analyst") or "").strip(),
                publish_date,
                (row.get("code") or "").strip() or None,
                (row.get("name") or "").strip() or None,
                (row.get("industry") or "").strip() or None,
                (row.get("rating") or "").strip() or None,
                _json_or_none(row.get("eps_forecasts")),
                _json_or_none(row.get("pe_forecasts")),
                (row.get("pdf_url") or "").strip() or None,
                (row.get("detail_url") or "").strip() or None,
                (row.get("source") or "").strip() or "unknown",
                now,
                now,
            )
        )
    if not payload:
        return 0
    conn = _connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO research_reports (
              report_key, report_type, title, org, analyst, publish_date, code, name,
              industry, rating, eps_forecasts, pe_forecasts, pdf_url, detail_url,
              source, first_seen_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(report_key) DO UPDATE SET
              report_type=excluded.report_type,
              title=excluded.title,
              org=excluded.org,
              analyst=excluded.analyst,
              publish_date=excluded.publish_date,
              code=excluded.code,
              name=excluded.name,
              industry=excluded.industry,
              rating=excluded.rating,
              eps_forecasts=excluded.eps_forecasts,
              pe_forecasts=excluded.pe_forecasts,
              pdf_url=excluded.pdf_url,
              detail_url=excluded.detail_url,
              source=CASE
                WHEN instr(',' || COALESCE(research_reports.source, '') || ',',
                           ',' || excluded.source || ',') > 0
                THEN research_reports.source
                ELSE COALESCE(research_reports.source || ',', '') || excluded.source
              END,
              updated_at=excluded.updated_at
            """,
            payload,
        )
        conn.commit()
    finally:
        conn.close()
    return len(payload)


def upsert_rating_changes(rows: Iterable[dict], db_path: str | Path | None = None) -> int:
    init_db(db_path)
    now = _now_iso()
    payload = []
    for row in rows:
        code = (row.get("code") or "").strip()
        org = (row.get("org") or "").strip()
        publish_date = (row.get("publish_date") or "").strip()
        if not code or not org or not publish_date:
            continue
        rating = (row.get("rating") or "").strip()
        prev = (row.get("prev_rating") or "").strip()
        first = row.get("is_first_rating")
        payload.append(
            (
                _rating_event_key(code, org, publish_date, rating, prev),
                code,
                (row.get("name") or "").strip() or None,
                publish_date,
                org,
                (row.get("analyst") or "").strip() or None,
                rating or None,
                None if first is None else int(bool(first)),
                (row.get("rating_change") or "").strip() or None,
                prev or None,
                row.get("target_low"),
                row.get("target_high"),
                (row.get("source") or "").strip() or "unknown",
                now,
            )
        )
    if not payload:
        return 0
    conn = _connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO rating_changes (
              event_key, code, name, publish_date, org, analyst, rating,
              is_first_rating, rating_change, prev_rating, target_low, target_high,
              source, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_key) DO UPDATE SET
              name=excluded.name,
              analyst=excluded.analyst,
              rating=excluded.rating,
              is_first_rating=excluded.is_first_rating,
              rating_change=excluded.rating_change,
              target_low=excluded.target_low,
              target_high=excluded.target_high,
              source=excluded.source,
              updated_at=excluded.updated_at
            """,
            payload,
        )
        conn.commit()
    finally:
        conn.close()
    return len(payload)


def upsert_earnings_forecasts(
    snapshot_date: str, rows: Iterable[dict], db_path: str | Path | None = None
) -> int:
    init_db(db_path)
    now = _now_iso()
    payload = []
    for row in rows:
        code = (row.get("code") or "").strip()
        if not code:
            continue
        payload.append(
            (
                snapshot_date,
                code,
                (row.get("name") or "").strip() or None,
                row.get("report_count"),
                row.get("rating_buy"),
                row.get("rating_add"),
                row.get("rating_neutral"),
                row.get("rating_reduce"),
                row.get("rating_sell"),
                row.get("eps_y1"),
                row.get("eps_y2"),
                row.get("eps_y3"),
                row.get("eps_y1_year"),
                row.get("eps_y2_year"),
                row.get("eps_y3_year"),
                (row.get("source") or "").strip() or "unknown",
                now,
            )
        )
    if not payload:
        return 0
    conn = _connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO earnings_forecasts (
              snapshot_date, code, name, report_count, rating_buy, rating_add,
              rating_neutral, rating_reduce, rating_sell, eps_y1, eps_y2, eps_y3,
              eps_y1_year, eps_y2_year, eps_y3_year, source, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_date, code) DO UPDATE SET
              name=excluded.name,
              report_count=excluded.report_count,
              rating_buy=excluded.rating_buy,
              rating_add=excluded.rating_add,
              rating_neutral=excluded.rating_neutral,
              rating_reduce=excluded.rating_reduce,
              rating_sell=excluded.rating_sell,
              eps_y1=excluded.eps_y1,
              eps_y2=excluded.eps_y2,
              eps_y3=excluded.eps_y3,
              eps_y1_year=excluded.eps_y1_year,
              eps_y2_year=excluded.eps_y2_year,
              eps_y3_year=excluded.eps_y3_year,
              source=excluded.source,
              updated_at=excluded.updated_at
            """,
            payload,
        )
        conn.commit()
    finally:
        conn.close()
    return len(payload)


def refresh_analysts(as_of: date | None = None, db_path: str | Path | None = None) -> int:
    """重建分析师档案（机构×分析师；覆盖标的数/近 90 日报告数/评级分布）。"""
    init_db(db_path)
    cutoff = ((as_of or date.today()) - timedelta(days=90)).isoformat()
    now = _now_iso()
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM analysts")
        conn.execute(
            """
            INSERT INTO analysts (
              org, analyst, covered_codes, reports_90d, rating_buy, rating_add,
              rating_neutral, rating_reduce, rating_sell, updated_at
            )
            SELECT org, analyst,
                   COUNT(DISTINCT code),
                   SUM(CASE WHEN publish_date >= ? THEN 1 ELSE 0 END),
                   SUM(CASE WHEN publish_date >= ? AND rating='买入' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN publish_date >= ? AND rating='增持' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN publish_date >= ? AND rating='中性' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN publish_date >= ? AND rating='减持' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN publish_date >= ? AND rating='卖出' THEN 1 ELSE 0 END),
                   ?
            FROM research_reports
            WHERE org IS NOT NULL AND org != '' AND analyst IS NOT NULL AND analyst != ''
            GROUP BY org, analyst
            """,
            (cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, now),
        )
        changed = conn.total_changes
        conn.commit()
    finally:
        conn.close()
    return int(changed)


def record_run(
    job: str,
    window_start: str | None,
    window_end: str | None,
    status: str,
    rows: int,
    detail: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO collection_runs (job, window_start, window_end, status, rows, detail, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (job, window_start, window_end, status, int(rows), detail, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def latest_run(job: str, db_path: str | Path | None = None) -> dict | None:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT job, window_start, window_end, status, rows, detail, created_at"
            " FROM collection_runs WHERE job=? ORDER BY run_id DESC LIMIT 1",
            (job,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def document_exists(doc_key: str, db_path: str | Path | None = None) -> bool:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT 1 FROM report_documents WHERE doc_key=?", (doc_key,)).fetchone()
    finally:
        conn.close()
    return row is not None


def upsert_document(document: dict, db_path: str | Path | None = None) -> bool:
    """投喂文档入库（内容哈希幂等）：已存在返回 False，新入库返回 True。

    不覆盖既有行——同一文件哈希的重复投喂是 no-op（保留首次入库时间）。
    """
    init_db(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO report_documents (
              doc_key, filename, file_path, title, org, publish_date, page_count,
              char_count, text, usage_policy, source_type, ingested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                document["doc_key"],
                document.get("filename") or "",
                document.get("file_path"),
                document.get("title"),
                document.get("org"),
                document.get("publish_date"),
                document.get("page_count"),
                document.get("char_count"),
                document.get("text") or "",
                document.get("usage_policy") or "personal_research",
                document.get("source_type") or "manual_feed",
                _now_iso(),
            ),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def list_documents(limit: int = 50, db_path: str | Path | None = None) -> list[dict]:
    """投喂文档列表（不含正文字段，避免把全文带进上下文/接口）。"""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT doc_key, filename, file_path, title, org, publish_date, page_count,"
            " char_count, usage_policy, source_type, ingested_at"
            " FROM report_documents ORDER BY ingested_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def get_document(doc_key: str, db_path: str | Path | None = None) -> dict | None:
    """按内容哈希取单篇投喂文档（含全文；供后续抽取/检索使用）。"""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM report_documents WHERE doc_key=?", (doc_key,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def coverage_status(
    code: str, stale_days: int = STALE_DAYS, db_path: str | Path | None = None
) -> dict:
    """覆盖状态三态：active / stale / no_coverage / collection_failed。

    「采集失败」只在「无任何行且最近一次研报日批失败」时给出——有历史行时
    采集失败不影响该标的的覆盖读数（老数据仍是事实）。
    """
    init_db(db_path)
    recent_cutoff = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()
    conn = _connect(db_path)
    try:
        agg = conn.execute(
            "SELECT COUNT(*) AS total, MAX(publish_date) AS latest,"
            " SUM(CASE WHEN publish_date >= ? THEN 1 ELSE 0 END) AS recent"
            " FROM research_reports WHERE code=?",
            (recent_cutoff, code),
        ).fetchone()
    finally:
        conn.close()
    total = int(agg["total"] or 0)
    latest = agg["latest"]
    recent = int(agg["recent"] or 0)
    if total == 0:
        run = latest_run("research_reports_daily", db_path=db_path)
        status = "collection_failed" if run and run.get("status") == "failed" else "no_coverage"
        return {
            "status": status,
            "stock_code": code,
            "latest_report_date": None,
            "reports_30d": 0,
            "total_reports": 0,
            "stale_days_threshold": stale_days,
            "latest_run_status": run.get("status") if run else None,
        }
    try:
        latest_date = date.fromisoformat(str(latest)[:10])
        age_days = (date.today() - latest_date).days
    except (TypeError, ValueError):
        age_days = stale_days + 1
    status = "stale" if age_days > stale_days else "active"
    return {
        "status": status,
        "stock_code": code,
        "latest_report_date": str(latest)[:10] if latest else None,
        "reports_30d": recent,
        "total_reports": total,
        "stale_days_threshold": stale_days,
        "age_days": age_days,
    }


def get_consensus(code: str, db_path: str | Path | None = None) -> dict:
    """一致预期三态：covered / no_coverage / missing（数据面尚未跑过快照）。"""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        latest = conn.execute("SELECT MAX(snapshot_date) FROM earnings_forecasts").fetchone()[0]
        if not latest:
            return {"status": "missing", "stock_code": code}
        row = conn.execute(
            "SELECT * FROM earnings_forecasts WHERE snapshot_date=? AND code=?",
            (latest, code),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"status": "no_coverage", "stock_code": code, "snapshot_date": latest}
    data = dict(row)
    data["status"] = "covered"
    data["stock_code"] = code
    return data


def upsert_extraction(
    doc_key: str,
    extraction: dict,
    *,
    model: str | None = None,
    prompt_version: str | int | None = None,
    db_path: str | Path | None = None,
) -> None:
    """写入一份文档的抽取结果（重复抽取覆盖旧行，抽出的事实以最新为准）。"""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO report_extractions (
              doc_key, broker, analyst, report_date, report_type, stocks_json,
              core_logic_json, catalysts_json, risks_json, summary, model,
              prompt_version, extracted_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(doc_key) DO UPDATE SET
              broker=excluded.broker,
              analyst=excluded.analyst,
              report_date=excluded.report_date,
              report_type=excluded.report_type,
              stocks_json=excluded.stocks_json,
              core_logic_json=excluded.core_logic_json,
              catalysts_json=excluded.catalysts_json,
              risks_json=excluded.risks_json,
              summary=excluded.summary,
              model=excluded.model,
              prompt_version=excluded.prompt_version,
              extracted_at=excluded.extracted_at
            """,
            (
                doc_key,
                extraction.get("broker"),
                extraction.get("analyst"),
                extraction.get("report_date"),
                extraction.get("report_type"),
                _json_or_none(extraction.get("stocks")),
                _json_or_none(extraction.get("core_logic")),
                _json_or_none(extraction.get("catalysts")),
                _json_or_none(extraction.get("risks")),
                extraction.get("summary"),
                model,
                None if prompt_version is None else str(prompt_version),
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_extraction(doc_key: str, db_path: str | Path | None = None) -> dict | None:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM report_extractions WHERE doc_key=?", (doc_key,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def list_extractions(limit: int = 500, db_path: str | Path | None = None) -> list[dict]:
    """抽取结果列表（JSON 字段保持字符串；合并层负责解析）。"""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM report_extractions ORDER BY report_date DESC, extracted_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]
