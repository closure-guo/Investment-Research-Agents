"""语义记忆（add-decision-memory）：月批提炼候选经验条款 + 人工确认门禁。

存储复用 outcome track-record 库（同 `SESSIONS_DB_PATH`，本 change 文档化决策：
记忆源自该库，择一不建第二库）。状态机 ``pending → confirmed | rejected``；
**未确认条款 SHALL NOT 出现在任何 prompt**——注入侧只读 confirmed 列表，
prompt 组装路径无第二数据源（门禁测试：全组装路径扫描未确认文本零命中）。

本阶段（T1）只含：幂等 DDL、连接、confirmed 读取（注入块的语义段数据源）；
提炼/落库/确认（T2）随后补齐。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CLAUSE_STATUSES: tuple[str, ...] = ("pending", "confirmed", "rejected")

CLAUSES_DDL = """
CREATE TABLE IF NOT EXISTS memory_clauses (
  clause_id    TEXT PRIMARY KEY,
  text         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','confirmed','rejected')),
  source       TEXT NOT NULL DEFAULT 'monthly_batch',
  evidence     TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_clauses_status ON memory_clauses(status);
"""


def _now_iso() -> str:
    """行写入时刻（updated_at 专用；不复用确认日期——语义不同源）。"""
    return datetime.now(UTC).isoformat()


def _db_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_clauses'"
    ).fetchone()
    return row is not None


def init_clauses(db_path: str | Path | None = None) -> None:
    """幂等建表（写入路径专用；读路径不建表、缺表 → 空）。"""
    conn = _connect(db_path)
    try:
        conn.executescript(CLAUSES_DDL)
        conn.commit()
    finally:
        conn.close()


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    """连接纪律同 track-record 库（WAL + busy_timeout 短连接，调用期读默认路径）。"""
    from finance_agent.outcome.track_record.model import _connect as _track_record_connect

    return _track_record_connect(_db_path(db_path))


def _read_clauses(
    status: str | None = None, *, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """只读条款列表（缺库/缺表 → []，fail-open）；按创建时间升序（稳定）。"""
    path = _db_path(db_path)
    if not path.exists():
        return []
    try:
        conn = _connect(path)
    except Exception:  # noqa: BLE001 -- fail-open
        logger.warning("语义记忆库连接失败（fail-open 空条款）: %s", path, exc_info=True)
        return []
    try:
        if not _table_exists(conn):
            return []
        sql = "SELECT * FROM memory_clauses"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at ASC, rowid ASC"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        logger.warning("语义条款查询失败（fail-open 空条款）", exc_info=True)
        return []
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def list_confirmed_clauses(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """已确认条款（唯一注入源；未确认永不进入任何 prompt 组装路径）。"""
    return _read_clauses("confirmed", db_path=db_path)


def list_pending_clauses(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """待人工确认的候选条款（月批产出）。"""
    return _read_clauses("pending", db_path=db_path)


def list_clauses(
    status: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """全部/按状态条款（人工确认工作台消费；只读）。"""
    if status is not None and status not in CLAUSE_STATUSES:
        raise ValueError(f"未知条款状态: {status!r}")
    return _read_clauses(status, db_path=db_path)


# ── 月批提炼（LLM 可注入 stub；无 client → 规则式降级，零 LLM 可测）────────────

_DIRECTION_LABELS = {"long": "看多", "short": "看空", "neutral": "中性"}
_STATUS_LABELS = {
    "resolved_win": "命中",
    "resolved_loss": "未中",
    "resolved_neutral": "带内中性",
    "avoidance": "回避",
    "unresolvable": "不可判定",
}
_DIRECTION_ORDER = ("long", "short", "neutral")
_LLM_MAX_CLAUSES = 10

# #485：LLM 常把「样本量不足，无法提炼…」这类对任务的元陈述当作条款返回。候选定义是
# 「经验条款」（spec decision-memory），元陈述不是条款 → 出口过滤；过滤后一条不剩则
# 返回 None，调用方按既有降级路径回退规则式。只匹配「说提炼不出」的句式，不误杀
# 「样本不足时应降低仓位」这类真经验（测试钉死）。
_METADATA_CLAUSE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(无法|不能|难以|不足以|无从).{0,6}(提炼|总结|归纳|概括|生成|形成|给出).{0,12}"
        r"(条款|结论|经验|规律)"
    ),
    re.compile(r"(无|没有|缺少|缺乏).{0,6}(可提炼|可用|足够|充分).{0,8}(条款|样本|数据|结论|经验)"),
    re.compile(
        r"(样本量?|数据|信息|素材)(不足|不够|匮乏|缺失)[，,。；;]?\s*(无法|不能|难以|不足以|暂无法)"
    ),
)


def _is_metadata_statement(text: str) -> bool:
    """是否「无内容可提炼」的元陈述（不是可注入的经验条款）。"""
    return any(pattern.search(text) for pattern in _METADATA_CLAUSE_PATTERNS)


def _llm_extract(settled: list[dict[str, Any]], client: Any) -> list[str] | None:
    """LLM 提炼（可选路径）：client 可为 callable 或带 complete_text 的对象。

    任何失败（异常/空产出/只剩元陈述）→ None（调用方降级规则式提炼，月批不因
    端点故障或模型「无话可说」而中断）。
    """
    digest = "\n".join(
        f"{e.get('date')} {e.get('symbol')} {e.get('direction')} "
        f"{e.get('settlement_status')} raw_return={e.get('raw_return')}"
        for e in settled
    )
    prompt = (
        "以下是已结算历史决策清单，请提炼可复用的经验条款（每行一条，不要编号与解释；"
        "每条必须包含方向、样本数与占比或胜率等统计要素；只写有统计依据的结论；"
        "若不足以支撑任何条款，请只输出空行，不要输出解释或说明性文字）:\n"
        "已结算决策:\n" + digest
    )
    try:
        raw = client(prompt) if callable(client) else client.complete_text(prompt)
    except Exception:  # noqa: BLE001 -- 端点在环，失败降级规则式（旁路铁律）
        logger.warning("语义记忆 LLM 提炼失败（降级规则式提炼）", exc_info=True)
        return None
    if not isinstance(raw, str):
        return None
    lines = [line.strip(" \t-*#0123456789.、") for line in raw.splitlines()]
    texts = [
        line
        for line in lines
        if line and not line.startswith("```") and not _is_metadata_statement(line)
    ]
    return texts[:_LLM_MAX_CLAUSES] or None


def _rule_based_clauses(settled: list[dict[str, Any]], *, min_samples: int) -> list[dict[str, Any]]:
    """规则式降级提炼：按「方向 × 结算状态」分组统计生成模板条款（确定性排序）。"""
    by_direction: dict[str, list[dict[str, Any]]] = {}
    for entry in settled:
        by_direction.setdefault(str(entry.get("direction") or ""), []).append(entry)

    clauses: list[dict[str, Any]] = []
    for direction in _DIRECTION_ORDER:
        group = by_direction.get(direction)
        if not group:
            continue
        total = len(group)
        label = _DIRECTION_LABELS[direction]
        for status in (
            "resolved_win",
            "resolved_loss",
            "resolved_neutral",
            "avoidance",
            "unresolvable",
        ):
            count = sum(1 for e in group if e.get("settlement_status") == status)
            if count < min_samples:
                continue
            clauses.append(
                {
                    "text": f"{label}方向历史决策 {count} 条：结算{_STATUS_LABELS[status]}"
                    f"（占该方向 {count / total:.0%}）",
                    "evidence": {
                        "direction": direction,
                        "settlement_status": status,
                        "count": count,
                        "direction_total": total,
                    },
                }
            )
        wins = sum(1 for e in group if e.get("settlement_status") == "resolved_win")
        losses = sum(1 for e in group if e.get("settlement_status") == "resolved_loss")
        readings = wins + losses
        if readings >= min_samples:
            clauses.append(
                {
                    "text": f"{label}方向已结算读数 {readings} 条：胜率 {wins / readings:.0%}"
                    f"（命中 {wins} / 未中 {losses}）",
                    "evidence": {
                        "direction": direction,
                        "resolved_win": wins,
                        "resolved_loss": losses,
                        "readings": readings,
                    },
                }
            )
    return clauses


def extract_candidate_clauses(
    settled: list[dict[str, Any]], *, client: Any = None, min_samples: int = 2
) -> list[dict[str, Any]]:
    """月批提炼入口：已结算记录 → 候选经验条款（`{"text", "evidence"}` 列表）。

    - 注入 client（LLM 提炼 stub/真实端点）→ 优先 LLM 产出；
    - client 缺省/失败/空产出 → 规则式降级（零 LLM 可测）；
    - min_samples：单样本组不入候选（避免 n=1 噪声条款）。
    """
    if not settled:
        # 空素材一律零候选且**不调 LLM**：空输入下模型产出属编造
        # （add-monthly-batch-scheduling 实跑暴露——空列表 + 真实网关曾产出 1 条候选，
        # 违反该 delta 规范场景「无素材 MUST NOT 以编造条款冒充提炼产物」）。
        return []
    if client is not None:
        texts = _llm_extract(settled, client)
        if texts:
            return [{"text": text, "evidence": {"source": "llm"}} for text in texts]
    return _rule_based_clauses(settled, min_samples=min_samples)


# ── 落库与人工确认门禁 ──────────────────────────────────────────────────────


def _clause_item(raw: str | dict[str, Any], default_evidence: str | None) -> tuple[str, str | None]:
    """落库项归一：裸文本沿用调用方统一 evidence；dict 项带自己的出处。

    #486：月批两条入口此前只传正文，`extract_candidate_clauses` 算出的出处
    （LLM `{"source": "llm"}` / 规则式统计分组）在落库时丢失。dict 项的 evidence
    统一序列化为 JSON 文本（与前端 ``evidenceLines`` 的解析口径一致）。
    """
    if isinstance(raw, dict):
        text = str(raw.get("text") or "").strip()
        evidence: Any = raw.get("evidence", default_evidence)
        if evidence is not None and not isinstance(evidence, str):
            evidence = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        return text, evidence
    return str(raw or "").strip(), default_evidence


def store_candidate_clauses(
    items: Sequence[str | dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    source: str = "monthly_batch",
    evidence: str | None = None,
) -> list[str]:
    """候选条款落 pending（写入路径建表），返回新入库的 clause_id 列表。

    ``items`` 可为裸文本（沿用参数 ``evidence``）或 ``{"text", "evidence"}`` 字典
    （月批两条入口传字典，保留 LLM / 规则式出处）。

    去重口径：库内任意状态同文本已存在即跳过（月批全量重提炼不产生重复行；
    已驳回条款不会被下月重新塞回待确认队列——非目标：不做自动改写/遗忘）。
    """
    init_clauses(db_path)
    existing = {str(row.get("text")) for row in _read_clauses(None, db_path=db_path)}
    new_ids: list[str] = []
    now = _now_iso()
    conn = _connect(db_path)
    try:
        for raw in items:
            text, row_evidence = _clause_item(raw, evidence)
            if not text or text in existing:
                continue
            clause_id = f"c_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT INTO memory_clauses
                     (clause_id, text, status, source, evidence, created_at, updated_at)
                   VALUES (?,?,'pending',?,?,?,?)""",
                (clause_id, text, source, row_evidence, now, now),
            )
            existing.add(text)
            new_ids.append(clause_id)
        conn.commit()
    finally:
        conn.close()
    return new_ids


def _set_status(clause_id: str, status: str, *, db_path: str | Path | None) -> bool:
    """状态落库（confirmed 记 confirmed_at）；0 行命中 → False（id 不存在）。"""
    path = _db_path(db_path)
    if not path.exists():
        return False
    conn = _connect(path)
    try:
        if not _table_exists(conn):
            return False
        now = _now_iso()
        if status == "confirmed":
            cursor = conn.execute(
                """UPDATE memory_clauses SET status='confirmed', confirmed_at=?, updated_at=?
                   WHERE clause_id=?""",
                (now, now, clause_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE memory_clauses SET status=?, updated_at=? WHERE clause_id=?",
                (status, now, clause_id),
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def reset_clauses(db_path: str | Path | None = None) -> None:
    """清空条款表（TESTING 造数复位专用，非业务路径）。

    仅 delta add-memory-confirmation 的 `/api/test/seed` ``memory_reset`` 调用：
    E2E 每例前后复位条款面。业务确认/注入只经既有读路径与状态机，无生产调用方。

    为何放在本模块：表名 ``memory_clauses`` 只允许出现在 semantic.py
    （门禁测试「无第二读入口」逐文件扫描 src）；api.py 若自行拼 SQL 会让门禁变红。
    """
    init_clauses(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM memory_clauses")
        conn.commit()
    finally:
        conn.close()


def confirm_clause(clause_id: str, db_path: str | Path | None = None) -> bool:
    """人工确认：pending → confirmed（此后才允许注入 prompt）。"""
    return _set_status(clause_id, "confirmed", db_path=db_path)


def reject_clause(clause_id: str, db_path: str | Path | None = None) -> bool:
    """人工驳回：pending → rejected（永不注入）。"""
    return _set_status(clause_id, "rejected", db_path=db_path)


def run_monthly_extraction(
    *,
    db_path: str | Path | None = None,
    client: Any = None,
    as_of: str | None = None,
    min_samples: int = 2,
) -> list[str]:
    """月批入口（job 调用）：已结算记录 → 提炼 → pending 落库；返回新候选 clause_id。"""
    from finance_agent.memory.episodic import settled_records

    settled = settled_records(as_of=as_of, db_path=db_path)
    clauses = extract_candidate_clauses(settled, client=client, min_samples=min_samples)
    return store_candidate_clauses(clauses, db_path=db_path, source="monthly_batch")
