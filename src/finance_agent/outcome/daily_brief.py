"""每日 LLM 运营汇报（add-auto-pool-analysis-daily-brief）。

设计要点：

- **事实与生成分离**：``collect_daily_facts`` 只读汇总当日真实数据（每个来源独立降级，
  缺表/缺库记 ``warnings``，不阻断整份汇报）；``run_daily_brief`` 把事实 JSON 交给
  ``purpose="daily_brief"`` 的 LLM 生成中文 Markdown。
- **禁止编造**：prompt 只允许引用事实；缺数据写「无数据」。
- **失败不粉饰**：LLM 失败时写确定性降级摘要（顶部标注失败）并抛错，让日批任务记 failed
  并走既有重试；MUST NOT 把空报告标成成功。
- **幂等**：``daily_briefs`` 以 ``brief_date`` 为主键 upsert；同一天补跑覆盖旧行与旧文件。
- 报告文件写顶层 ``reports/``（既有 ``GET /api/files`` 只扫描顶层）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.llm.gateway import complete_text
from finance_agent.prompts.loader import load_prompt_with_meta

logger = logging.getLogger("finance_agent.outcome.daily_brief")

_BRIEF_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_briefs (
  brief_date TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  markdown TEXT NOT NULL,
  model TEXT,
  prompt_version TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def _sessions_db(db_path: str | Path | None = None) -> Path:
    return Path(db_path) if db_path else Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def _screening_db(db_path: str | Path | None = None) -> Path:
    return Path(db_path) if db_path else Path(os.getenv("SCREENING_DB_PATH", "data/screening.db"))


def _execution_db(db_path: str | Path | None = None) -> Path:
    return Path(db_path) if db_path else Path(os.getenv("EXECUTION_DB_PATH", "data/execution.db"))


def _factor_db(db_path: str | Path | None = None) -> Path:
    return (
        Path(db_path)
        if db_path
        else Path(os.getenv("FACTOR_LIBRARY_DB_PATH", "data/factor_library.db"))
    )


def _connect_read(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_write(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _read(path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _connect_read(path) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _read_one(path: Path, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = _read(path, sql, params)
    return rows[0] if rows else None


def _action_from_snapshot(raw: object) -> str | None:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    action = payload.get("action")
    return str(action) if action else None


def _screening_facts(path: Path, as_of: str) -> dict[str, Any]:
    batch = _read_one(
        path,
        "SELECT trade_date, pool, status, error, started_at, finished_at, l1_passed, "
        "l2_top, l3_keep, pool_size, selection_mode FROM screening_batches "
        "WHERE trade_date = ? AND pool = 'main'",
        (as_of,),
    )
    pool = _read(
        path,
        "SELECT symbol, rank, composite_score, consecutive_days, l3_verdict, l3_reason "
        "FROM selection_pool WHERE trade_date = ? AND pool = 'main' "
        "ORDER BY rank LIMIT 10",
        (as_of,),
    )
    runs: list[dict[str, Any]] = []
    try:
        raw_runs = _read(
            path,
            "SELECT symbol, session_id, status, error FROM pool_analysis_runs "
            "WHERE trade_date = ? ORDER BY symbol",
            (as_of,),
        )
        # 库内 status 是「派发状态」（queued=已入队 / dispatch_failed / session_failed），
        # 不是会话终态；改名后喂给 LLM，避免与 sessions.status 混淆成矛盾。
        runs = [
            {
                "symbol": row.get("symbol"),
                "session_id": row.get("session_id"),
                "dispatch_status": (
                    "enqueued" if row.get("status") == "queued" else row.get("status")
                ),
                "error": row.get("error"),
            }
            for row in raw_runs
        ]
    except sqlite3.Error as exc:
        logger.warning("读取 pool_analysis_runs 失败（%s）", exc)
    removals: list[dict[str, Any]] = []
    try:
        removals = _read(
            path,
            "SELECT symbol, reason, detail FROM screening_removals "
            "WHERE trade_date = ? ORDER BY symbol",
            (as_of,),
        )
    except sqlite3.Error as exc:
        logger.warning("读取 screening_removals 失败（%s）", exc)
    return {
        "batch": batch,
        "pool_top": pool,
        "auto_analysis_runs": runs,
        "removals": removals,
    }


def _session_statuses(sessions_db: Path, session_ids: list[str]) -> dict[str, str]:
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    rows = _read(
        sessions_db,
        f"SELECT session_id, status, display_name FROM sessions "  # noqa: S608 - 占位符已参数化
        f"WHERE session_id IN ({placeholders})",
        tuple(session_ids),
    )
    return {str(row["session_id"]): str(row["status"]) for row in rows}


def _prediction_facts(path: Path, as_of: str) -> dict[str, Any]:
    created = _read(
        path,
        "SELECT symbol, direction, status, created_at, rationale_snapshot, session_id "
        "FROM predictions WHERE substr(created_at, 1, 10) = ?",
        (as_of,),
    )
    resolved = _read(
        path,
        "SELECT symbol, direction, status, raw_return, resolved_at FROM predictions "
        "WHERE substr(coalesce(resolved_at, ''), 1, 10) = ?",
        (as_of,),
    )
    open_total = _read_one(path, "SELECT COUNT(*) AS n FROM predictions WHERE status = 'open'")
    created_actions: dict[str, int] = {}
    for row in created:
        action = _action_from_snapshot(row.get("rationale_snapshot")) or "unknown"
        created_actions[action] = created_actions.get(action, 0) + 1
    resolved_counts: dict[str, int] = {}
    for row in resolved:
        status = str(row.get("status") or "unknown")
        resolved_counts[status] = resolved_counts.get(status, 0) + 1
    directional = [
        {
            "symbol": row.get("symbol"),
            "status": row.get("status"),
            "raw_return": row.get("raw_return"),
        }
        for row in resolved
        if row.get("status") in ("resolved_win", "resolved_loss")
    ]
    return {
        "created_today": len(created),
        "created_actions": created_actions,
        "resolved_today": len(resolved),
        "resolved_status_counts": resolved_counts,
        "directional_settlements": directional,
        "open_total": int(open_total["n"]) if open_total else 0,
    }


def _execution_facts(path: Path, as_of: str) -> dict[str, Any]:
    orders = _read(
        path,
        "SELECT symbol, side, qty, status, reason, fill_price, fee, created_at "
        "FROM orders WHERE substr(created_at, 1, 10) = ? ORDER BY created_at",
        (as_of,),
    )
    positions = _read(path, "SELECT symbol, qty, avg_cost FROM positions ORDER BY symbol")
    cash_row = _read_one(path, "SELECT balance, updated_at FROM cash ORDER BY id DESC LIMIT 1")
    equity = _read(
        path,
        "SELECT trade_date, cash, positions_value, equity FROM paper_equity "
        "ORDER BY trade_date DESC LIMIT 2",
    )
    daily_change = None
    if len(equity) >= 2 and equity[1]["equity"]:
        try:
            daily_change = round(float(equity[0]["equity"]) / float(equity[1]["equity"]) - 1, 6)
        except (TypeError, ValueError, ZeroDivisionError):
            daily_change = None
    return {
        "orders": orders,
        "orders_count": len(orders),
        "fills_count": sum(1 for row in orders if row.get("status") == "filled"),
        "positions": positions,
        "positions_count": len(positions),
        "cash": cash_row,
        "equity_latest": equity[0] if equity else None,
        "equity_prev": equity[1] if len(equity) >= 2 else None,
        "daily_change": daily_change,
    }


def _factor_facts(path: Path) -> dict[str, Any]:
    dates = _read(
        path,
        "SELECT DISTINCT trade_date FROM factor_ic ORDER BY trade_date DESC LIMIT 20",
    )
    window = [str(row["trade_date"]) for row in dates]
    factors = _read(
        path,
        # 拒绝池行（rejected）不进日报因子区（add-rejected-factor-pool：既有读路径不可见）
        "SELECT id, expression, status FROM factors WHERE status != 'rejected' ORDER BY id",
    )
    per_factor: list[dict[str, Any]] = []
    if window:
        start = window[-1]
        rows = _read(
            path,
            "SELECT factor_id, AVG(rank_ic) AS mean_ic, COUNT(*) AS n "
            "FROM factor_ic WHERE trade_date >= ? GROUP BY factor_id ORDER BY factor_id",
            (start,),
        )
        by_id = {int(row["factor_id"]): row for row in rows}
        for factor in factors:
            stat = by_id.get(int(factor["id"]), {})
            per_factor.append(
                {
                    "factor_id": factor["id"],
                    "expression": factor["expression"],
                    "status": factor["status"],
                    "mean_ic": stat.get("mean_ic"),
                    "samples": stat.get("n", 0),
                }
            )
    return {
        "active_count": sum(1 for factor in factors if factor.get("status") == "active"),
        "ic_window_days": len(window),
        "latest_ic_date": window[0] if window else None,
        "per_factor": per_factor,
    }


def _job_failure_facts(path: Path, as_of: str) -> list[dict[str, Any]]:
    return _read(
        path,
        "SELECT job_id, status, error, started_at, finished_at FROM job_runs "
        "WHERE substr(started_at, 1, 10) = ? AND status IN ('failed', 'skipped-disabled') "
        "ORDER BY run_id DESC LIMIT 10",
        (as_of,),
    )


def collect_daily_facts(
    as_of: str,
    *,
    sessions_db_path: str | Path | None = None,
    screening_db_path: str | Path | None = None,
    execution_db_path: str | Path | None = None,
    factor_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """只读汇总当日事实；单个来源失败降级为 ``warnings``，不阻断整份汇报。"""
    sessions_db = _sessions_db(sessions_db_path)
    screening_db = _screening_db(screening_db_path)
    execution_db = _execution_db(execution_db_path)
    factor_db = _factor_db(factor_db_path)
    warnings: list[dict[str, str]] = []
    facts: dict[str, Any] = {
        "date": as_of,
        "generated_at": datetime.now().isoformat(),
        "warnings": warnings,
    }

    def _safe(name: str, fn: Any) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 单个来源失败不阻断整份汇报
            logger.warning("每日汇报事实采集失败 %s: %s", name, exc)
            warnings.append({"section": name, "error": f"{type(exc).__name__}: {exc}"})
            return None

    screening = _safe("screening", lambda: _screening_facts(screening_db, as_of))
    facts["screening"] = screening or {}
    if screening and screening.get("auto_analysis_runs"):
        session_ids = [
            str(row.get("session_id") or "")
            for row in screening["auto_analysis_runs"]
            if row.get("session_id")
        ]
        statuses = _safe("session_statuses", lambda: _session_statuses(sessions_db, session_ids))
        for row in screening["auto_analysis_runs"]:
            row["session_status"] = (statuses or {}).get(str(row.get("session_id") or ""))

    facts["predictions"] = _safe("predictions", lambda: _prediction_facts(sessions_db, as_of)) or {}
    facts["execution"] = _safe("execution", lambda: _execution_facts(execution_db, as_of)) or {}
    facts["factors"] = _safe("factors", lambda: _factor_facts(factor_db)) or {}
    facts["failures"] = _safe("failures", lambda: _job_failure_facts(sessions_db, as_of)) or []
    return facts


def _fmt(value: object, digits: int = 4) -> str:
    if value is None:
        return "无数据"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_fallback(facts: dict[str, Any], error: str) -> str:
    """LLM 失败时的确定性摘要：事实照录，顶部显式标注失败。"""
    as_of = facts.get("date", "无数据")
    screening = facts.get("screening") or {}
    batch = screening.get("batch") or {}
    predictions = facts.get("predictions") or {}
    execution = facts.get("execution") or {}
    factors = facts.get("factors") or {}
    failures = facts.get("failures") or []
    lines = [
        f"# 每日运营汇报 {as_of}（降级）",
        "",
        f"> ⚠️ LLM 生成失败：{error}。以下为系统事实的确定性摘要，未经 LLM 解读。",
        "",
        "## 今日结论",
        f"- 选股批次：{_fmt(batch.get('status'))}；精选池：{_fmt(batch.get('pool_size'))} 只。",
        f"- 今日新建观点：{_fmt(predictions.get('created_today'))} 条；"
        f"已结算：{_fmt(predictions.get('resolved_today'))} 条。",
        f"- 今日订单：{_fmt(execution.get('orders_count'))} 笔；"
        f"成交：{_fmt(execution.get('fills_count'))} 笔；"
        f"持仓：{_fmt(execution.get('positions_count'))} 只。",
        "",
        "## 选股与精选池",
        f"- 批次错误：{_fmt(batch.get('error'))}",
        f"- 池前 10：{', '.join(str(row.get('symbol')) for row in screening.get('pool_top', [])) or '无数据'}",
        f"- 自动分析运行：{_fmt(len(screening.get('auto_analysis_runs') or []))} 条。",
        "",
        "## 分析与决策",
        f"- 动作分布：{json.dumps(predictions.get('created_actions') or {}, ensure_ascii=False)}",
        f"- 结算状态：{json.dumps(predictions.get('resolved_status_counts') or {}, ensure_ascii=False)}",
        "",
        "## 交易与持仓",
        f"- 纸面账户权益：{_fmt(execution.get('equity_latest'))}",
        f"- 当日变动：{_fmt(execution.get('daily_change'))}",
        "",
        "## 风险与异常",
    ]
    if failures:
        for row in failures:
            lines.append(f"- {row.get('job_id')}：{row.get('status')}；{_fmt(row.get('error'))}")
    else:
        lines.append("- 无失败/跳过任务记录。")
    warnings = facts.get("warnings") or []
    for warning in warnings:
        lines.append(f"- 数据缺口（{warning.get('section')}）：{warning.get('error')}")
    lines.extend(
        [
            "",
            "## 明日关注",
            f"- 因子 IC 窗口：{_fmt(factors.get('ic_window_days'))} 个交易日；"
            f"最新日期：{_fmt(factors.get('latest_ic_date'))}。",
            "- LLM 汇报失败需人工复核；次日任务会自动重试生成。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_report_file(as_of: str, markdown: str) -> Path:
    reports_dir = Path(os.getenv("REPORTS_DIR", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"daily-brief-{as_of}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def init_daily_briefs(db_path: str | Path | None = None) -> None:
    path = _sessions_db(db_path)
    with _connect_write(path) as conn:
        conn.execute(_BRIEF_SCHEMA)
        conn.commit()


def upsert_daily_brief(
    brief_date: str,
    status: str,
    markdown: str,
    model: str | None = None,
    prompt_version: str | None = None,
    error: str | None = None,
    *,
    db_path: str | Path | None = None,
) -> None:
    init_daily_briefs(db_path)
    now = datetime.now().isoformat()
    with _connect_write(_sessions_db(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO daily_briefs
                (brief_date, status, markdown, model, prompt_version, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(brief_date) DO UPDATE SET
                status = excluded.status,
                markdown = excluded.markdown,
                model = excluded.model,
                prompt_version = excluded.prompt_version,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (brief_date, status, markdown, model, prompt_version, error, now, now),
        )
        conn.commit()


def get_daily_brief(
    brief_date: str | None = None, *, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    init_daily_briefs(db_path)
    path = _sessions_db(db_path)
    if brief_date:
        return _read_one(
            path,
            "SELECT brief_date, status, markdown, model, prompt_version, error, "
            "created_at, updated_at FROM daily_briefs WHERE brief_date = ?",
            (brief_date,),
        )
    return _read_one(
        path,
        "SELECT brief_date, status, markdown, model, prompt_version, error, "
        "created_at, updated_at FROM daily_briefs ORDER BY brief_date DESC LIMIT 1",
    )


def list_daily_briefs(
    *, limit: int = 30, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    init_daily_briefs(db_path)
    return _read(
        _sessions_db(db_path),
        "SELECT brief_date, status, markdown, model, prompt_version, error, "
        "created_at, updated_at FROM daily_briefs ORDER BY brief_date DESC LIMIT ?",
        (max(1, min(365, int(limit))),),
    )


def run_daily_brief(
    as_of: str | None = None,
    *,
    sessions_db_path: str | Path | None = None,
    screening_db_path: str | Path | None = None,
    execution_db_path: str | Path | None = None,
    factor_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """生成当日汇报；成功返回 ok 摘要，LLM 失败写降级摘要后抛错（任务记 failed）。"""
    day = as_of or datetime.now().strftime("%Y-%m-%d")
    facts = collect_daily_facts(
        day,
        sessions_db_path=sessions_db_path,
        screening_db_path=screening_db_path,
        execution_db_path=execution_db_path,
        factor_db_path=factor_db_path,
    )
    info = load_prompt_with_meta("daily_brief")
    prompt = info.template.replace(
        "{{daily_facts}}", json.dumps(facts, ensure_ascii=False, indent=2)
    ).replace("{{brief_date}}", day)
    try:
        raw, meta = complete_text(
            [{"role": "user", "content": prompt}],
            purpose="daily_brief",
            max_tokens=4000,
            temperature=0.3,
            trace={"name": "daily_brief", "metadata": {"brief_date": day}},
        )
        markdown = str(raw or "").strip()
        if not markdown:
            raise RuntimeError("empty_llm_output")
    except Exception as exc:  # noqa: BLE001 - 失败必须降级留痕并让任务失败
        logger.exception("每日汇报 LLM 生成失败（%s）", day)
        error = f"{type(exc).__name__}: {exc}"
        fallback = render_fallback(facts, error)
        upsert_daily_brief(
            day,
            "failed",
            fallback,
            model=None,
            prompt_version=str(info.prompt_version),
            error=error,
            db_path=sessions_db_path,
        )
        try:
            _write_report_file(day, fallback)
        except Exception:  # noqa: BLE001 - 文件失败不掩盖 LLM 失败
            logger.exception("每日汇报降级文件写入失败（%s）", day)
        raise RuntimeError(f"daily_brief_llm_failed: {type(exc).__name__}") from exc

    model = str(meta.get("model") or "") if isinstance(meta, dict) else ""
    upsert_daily_brief(
        day,
        "ok",
        markdown,
        model=model or None,
        prompt_version=str(info.prompt_version),
        error=None,
        db_path=sessions_db_path,
    )
    file_error = None
    try:
        path = _write_report_file(day, markdown)
    except Exception as exc:  # noqa: BLE001 - 文件是次要产物；DB 行才是真源
        logger.exception("每日汇报文件写入失败（%s）", day)
        path = None
        file_error = f"{type(exc).__name__}: {exc}"
    return {
        "status": "ok",
        "brief_date": day,
        "model": model,
        "chars": len(markdown),
        "path": str(path) if path else None,
        "file_error": file_error,
    }
