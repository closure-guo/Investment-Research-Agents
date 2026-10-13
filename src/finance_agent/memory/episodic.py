"""情景记忆（add-decision-memory）：从已结算历史决策检索 Top-5 并组装注入块。

数据源 = outcome track-record 库（`predictions`，只读；连接纪律复用
``outcome.track_record.model._connect`` 的 WAL + busy_timeout 短连接）。
本模块**不建表、不写行、不触网**；缺库/缺表/查询异常一律 fail-open（返回空，
旁路铁律：历史查询不可用 ≠ 分析不可行）。

相关性口径（钉死）：同标的 > 同行业（申万三级行业成分股代码，由调用方给出），
同级内按决策日期降序（不使用收益幅度排序）。as_of 严格早于（防同日自污染）。

注入纪律：条目逐字含来源决策 ID 与日期；论据摘要截断 ≤200 字，缺字段空串不编造；
记忆块以 state 键 `decision_memory` 随上下文组装下发（无记忆 → 零块，向后兼容），
数值经既有 citation 直读路径校验（field_ref 前缀 ``decision_memory.episodic.<i>``）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 用途标签逐字（spec decision-memory）：检索/裁剪计入 Langfuse trace
PURPOSE_TAG_DECISION_MEMORY = "decision_memory"

# 已结算终态（含读数或终态判定）：open = 未结算；duplicate_of_day = 同日重复、
# 无结算读数且不计统计分母（model.py 既有口径）——二者均不进记忆。
SETTLED_STATUSES: tuple[str, ...] = (
    "resolved_win",
    "resolved_loss",
    "resolved_neutral",
    "avoidance",
    "unresolvable",
)

# 论据摘要来源字段优先级（rationale_snapshot 内）；缺字段 → 空串不编造
_RATIONALE_KEYS: tuple[str, ...] = ("fund_manager_decision_reasoning",)
RATIONALE_MAX_CHARS = 200

# Top-K 与预算：记忆块文本长度计入分析师上下文预算（chars 口径，超限先裁情景记忆，
# 语义条款完整保留——spec analyst-context-budget「预算不足裁剪」）。
DEFAULT_TOP_K = 5
MEMORY_BLOCK_BUDGET_CHARS = 1200

_MEMORY_HEADER = (
    "【历史决策记忆（decision_memory；来源=已结算历史决策，只读；引用数值时 field_ref "
    "用 decision_memory.episodic.<索引>.<字段>（如 decision_memory.episodic.0.raw_return），"
    "interpretation 只写结论与数值、不写来源 ID（来源 ID 含数字，会被 citation 回声校验"
    "读作数值而误判 internal_inconsistency）；MUST NOT 改写或臆测）】"
)
_SEMANTIC_HEADER = (
    "已确认经验条款（人工确认后生效；引用时 field_ref 用 decision_memory.semantic.<索引>.text）:"
)


def symbol_for(stock_code: str) -> str:
    """6 位代码 → 库内 symbol（与 ingest/recent_directions 同映射：6 开头 .SH，否则 .SZ）。"""
    code = str(stock_code or "").strip()
    if "." in code:
        return code
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


def code_of(symbol: str) -> str:
    """库内 symbol → 6 位代码（无后缀则原样）。"""
    return str(symbol or "").split(".")[0]


def _db_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def _connect_readonly(db_path: str | Path | None) -> sqlite3.Connection | None:
    """只读连接；库文件不存在 → None（不创建任何库文件）。"""
    path = _db_path(db_path)
    if not path.exists():
        return None
    from finance_agent.outcome.track_record.model import _connect

    try:
        return _connect(path)
    except Exception:  # noqa: BLE001 -- fail-open（连接失败 ≠ 无历史可查的结论）
        logger.warning("记忆库连接失败（fail-open 空记忆）: %s", path, exc_info=True)
        return None


def _rationale_summary(raw_snapshot: Any) -> str:
    """从 rationale_snapshot 取决策论据摘要：截断 ≤200 字；缺字段 → 空串（不编造）。"""
    snapshot: Any = raw_snapshot
    if isinstance(raw_snapshot, str):
        try:
            snapshot = json.loads(raw_snapshot)
        except ValueError:
            return ""
    if not isinstance(snapshot, dict):
        return ""
    for key in _RATIONALE_KEYS:
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:RATIONALE_MAX_CHARS]
    return ""


def _entry_from_row(row: sqlite3.Row) -> dict[str, Any]:
    created_at = str(row["created_at"] or "")
    raw_return = row["raw_return"]
    return {
        "decision_id": str(row["prediction_id"]),
        "date": created_at[:10],
        "symbol": code_of(str(row["symbol"])),
        "direction": str(row["direction"]),
        "settlement_status": str(row["status"]),
        "raw_return": None if raw_return is None else float(raw_return),
        "rationale_summary": _rationale_summary(row["rationale_snapshot"]),
    }


def _settled_rows(
    *,
    as_of: str | None,
    db_path: str | Path | None,
    symbols: Sequence[str] | None = None,
) -> list[sqlite3.Row]:
    """已结算行（as_of 之前；symbols 给定则限定）。缺库/缺表/异常 → []（fail-open）。"""
    conn = _connect_readonly(db_path)
    if conn is None:
        return []
    status_placeholders = ",".join("?" for _ in SETTLED_STATUSES)
    sql = f"SELECT * FROM predictions WHERE status IN ({status_placeholders})"  # noqa: S608
    params: list[Any] = list(SETTLED_STATUSES)
    if as_of:
        sql += " AND substr(created_at, 1, 10) < ?"
        params.append(str(as_of)[:10])
    if symbols is not None:
        symbol_list = [s for s in symbols if s]
        if not symbol_list:
            return []
        sql += f" AND symbol IN ({','.join('?' for _ in symbol_list)})"
        params.extend(symbol_list)
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError:
        logger.warning("记忆库查询失败（fail-open 空记忆）", exc_info=True)
        return []
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def retrieve_episodic(
    symbol: str,
    *,
    industry_codes: Sequence[str] = (),
    as_of: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """同标的/同行业已结算历史决策 Top-K（相关性 = 标的/行业归属 + 时效）。

    - industry_codes：同行业（申万三级行业成分）**股票代码**列表；生产路径由
      state 的行业成分（peer_financials/peer_codes，fetch 已抓取）派生；
    - 排序：同标的优先 → 同级按决策时间降序（时间同则按 id 降序，保序稳定）；
    - 入参 as_of 缺省 = 今日；无历史/库不可用 → []（fail-open）。
    """
    target = symbol_for(symbol)
    industry = {symbol_for(c) for c in industry_codes if str(c or "").strip()}
    industry.discard(target)
    k = max(1, min(int(top_k), 20))
    as_of_date = str(as_of)[:10] if as_of else date.today().isoformat()

    rows = _settled_rows(as_of=as_of_date, db_path=db_path, symbols=[target, *sorted(industry)])
    entries = [_entry_from_row(r) for r in rows]
    # 两级稳定排序（Python sort 稳定）：先按（日期, id）降序，再按相关性档升序——
    # 结果 = 同标的优先 → 同级内日期降序（日期并列按 id 降序，保序稳定）
    entries.sort(key=lambda e: (str(e["date"]), str(e["decision_id"])), reverse=True)
    entries.sort(key=lambda e: 0 if symbol_for(e["symbol"]) == target else 1)
    return entries[:k]


def settled_records(
    *, as_of: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """全部已结算记录的记忆条目形态（月批提炼入口的输入；只读 fail-open）。"""
    rows = _settled_rows(as_of=as_of, db_path=db_path)
    entries = [_entry_from_row(r) for r in rows]
    entries.sort(key=lambda e: (str(e["date"]), str(e["decision_id"])))
    return entries


# ── 注入块组装 ──────────────────────────────────────────────────────────────


def _render_return(raw_return: float | None) -> str:
    return "无结算读数" if raw_return is None else f"{raw_return:+.2%}"


def _episodic_lines(episodic: Sequence[dict[str, Any]]) -> list[str]:
    lines = []
    for entry in episodic:
        line = (
            f"[记忆] 来源: {entry.get('decision_id')} ({entry.get('date')}) "
            f"{entry.get('symbol')} {entry.get('direction')} "
            f"结算:{entry.get('settlement_status')} "
            f"收益:{_render_return(entry.get('raw_return'))}"
        )
        summary = str(entry.get("rationale_summary") or "")
        if summary:
            line += f" 论据:{summary}"
        lines.append(line)
    return lines


def _semantic_lines(semantic_clauses: Sequence[dict[str, Any]]) -> list[str]:
    return [
        f"[记忆] 来源: {clause.get('clause_id')} ({str(clause.get('confirmed_at') or '')[:10]}) "
        f"{clause.get('text')}"
        for clause in semantic_clauses
    ]


def build_memory_block(
    episodic: Sequence[dict[str, Any]], semantic_clauses: Sequence[dict[str, Any]]
) -> str:
    """渲染记忆块（markdown 风格纯文本）；两列表皆空 → 空串（无块 = 向后兼容）。

    逐条格式逐字（spec）：``[记忆] 来源: {decision_id} ({date}) …``。
    """
    episodic_lines = _episodic_lines(episodic)
    semantic_lines = _semantic_lines(semantic_clauses)
    if not episodic_lines and not semantic_lines:
        return ""
    sections = [_MEMORY_HEADER]
    if episodic_lines:
        sections.append("情景记忆（同标的/同行业已结算决策，相关性降序）:")
        sections.extend(episodic_lines)
    if semantic_lines:
        sections.append(_SEMANTIC_HEADER)
        sections.extend(semantic_lines)
    return "\n".join(sections)


def apply_memory_budget(
    episodic: Sequence[dict[str, Any]],
    semantic_clauses: Sequence[dict[str, Any]],
    *,
    budget_chars: int = MEMORY_BLOCK_BUDGET_CHARS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """预算裁剪（spec analyst-context-budget）：语义条款完整保留，情景记忆按相关性
    降序截断（先丢最低相关 = 列表尾部），返回（保留的情景记忆, 裁剪事件）。

    裁剪事件含 ``trimmed`` 计数与 ``dropped_ids``（召回的 trace 元数据消费）。
    """
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    # 语义条款不可裁：先量出语义段基线；情景逐条（相关性降序）试加，首次超限即停
    for entry in episodic:
        candidate = build_memory_block([*kept, entry], semantic_clauses)
        if len(candidate) <= budget_chars:
            kept.append(entry)
        else:
            dropped.append(str(entry.get("decision_id")))
    final_block = build_memory_block(kept, semantic_clauses)
    event = {
        "budget_chars": int(budget_chars),
        "used_chars": len(final_block),
        "trimmed": len(dropped),
        "dropped_ids": dropped,
        "over_budget": bool(final_block) and len(final_block) > int(budget_chars),
    }
    return kept, event


# ── state 注入与 trace ──────────────────────────────────────────────────────


def _trace_memory(metadata: dict[str, Any]) -> None:
    """检索/裁剪 span（Langfuse 未配置 → 直通零开销）；埋点失败不影响业务。"""
    from finance_agent.langfuse_tracing import get_langfuse

    client = get_langfuse()
    if client is None:
        return
    try:
        with (
            client.start_as_current_observation(
                as_type="span", name=PURPOSE_TAG_DECISION_MEMORY
            ) as span,
            contextlib.suppress(Exception),
        ):
            span.update(metadata=metadata)
    except Exception:  # noqa: BLE001 -- 纯可观测性，失败不阻断分析
        logger.debug("决策记忆 span 写入失败", exc_info=True)


def industry_codes_from_state(state: dict) -> list[str]:
    """同行业成分股代码（申万三级行业）：fetch 已抓取的同业表/对标股代码。

    生产来源优先级：peer_financials 同业表（行业成分市值 Top5 或无显式对标股时自动
    选取，见 nodes/fetch `_fetch_peers`）→ state `peer_codes`。不可得 → []（同行业档
    自然退化，仅检索同标的）。
    """
    codes: list[str] = []
    frame = state.get("peer_financials")
    if frame is not None and hasattr(frame, "columns"):
        for column in ("代码", "code"):
            if column in frame.columns:
                codes.extend(str(v) for v in frame[column].tolist())
                break
    codes.extend(str(c) for c in (state.get("peer_codes") or []))
    return [code for code in codes if code.strip()]


def render_memory_state(
    state: dict,
    *,
    db_path: str | Path | None = None,
    as_of: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    budget_chars: int = MEMORY_BLOCK_BUDGET_CHARS,
) -> dict[str, Any]:
    """组装 `decision_memory` state 键（与滞回键同风格：dict 随图通道下发）。

    返回 ``{"decision_memory": {episodic, semantic, as_of, trimmed, budget_chars,
    used_chars, dropped_ids}}``；无标的不检索（空结构，节点侧零块）。
    """
    from finance_agent.memory.semantic import list_confirmed_clauses

    as_of_date = str(as_of)[:10] if as_of else date.today().isoformat()
    stock_code = str(state.get("stock_code") or "").strip()
    semantic = list_confirmed_clauses(db_path=db_path)
    episodic: list[dict[str, Any]] = []
    if stock_code:
        episodic = retrieve_episodic(
            stock_code,
            industry_codes=industry_codes_from_state(state),
            as_of=as_of_date,
            top_k=top_k,
            db_path=db_path,
        )
    kept, event = apply_memory_budget(episodic, semantic, budget_chars=budget_chars)
    _trace_memory(
        {
            "purpose_tag": PURPOSE_TAG_DECISION_MEMORY,
            "as_of": as_of_date,
            "stock_code": stock_code,
            "top_k": int(top_k),
            "episodic_count": len(kept),
            "semantic_count": len(semantic),
            **event,
        }
    )
    return {
        "decision_memory": {
            "episodic": kept,
            "semantic": semantic,
            "as_of": as_of_date,
            "trimmed": event["trimmed"],
            "budget_chars": event["budget_chars"],
            "used_chars": event["used_chars"],
            "dropped_ids": event["dropped_ids"],
        }
    }


def memory_recall_entry(state: dict) -> dict:
    """生产图 analysts_entry：深度分析启动时检索记忆（无标的历史 → 空结构）。

    幂等短路：citation 重试回边再次进入本节点时，已有 `decision_memory` 键直接复用
    （不重复查库；同一次分析的记忆快照保持稳定）。
    """
    if "decision_memory" in state:
        return {}
    return render_memory_state(state)


# 图接线标记（tests/nodes/test_decision_memory_injection.py 断言 analysts_entry 身份）
memory_recall_entry.memory_recall_enabled = True  # type: ignore[attr-defined]


def memory_block_for_state(state: dict) -> str | None:
    """节点上下文消费：记忆块文本；无记忆键/空记忆 → None（零增量，向后兼容）。"""
    memory = state.get("decision_memory") or {}
    if not isinstance(memory, dict):
        return None
    block = build_memory_block(memory.get("episodic") or [], memory.get("semantic") or [])
    return block or None


def semantic_block_for_state(state: dict) -> str | None:
    """风控辩论层消费：仅已确认语义条款段；无条款 → None（情景记忆不进该层）。"""
    memory = state.get("decision_memory") or {}
    if not isinstance(memory, dict):
        return None
    clauses = memory.get("semantic") or []
    if not clauses:
        return None
    return build_memory_block([], clauses) or None


def append_memory_block(context: str, state: dict, *, semantic_only: bool = False) -> str:
    """上下文追加记忆块：有记忆才拼接（无记忆逐字返回原上下文 = 向后兼容）。

    注入先例同 ``derived_series``/因子库段：工具生成、随上下文下发、LLM 直接引用。
    """
    block = semantic_block_for_state(state) if semantic_only else memory_block_for_state(state)
    return f"{context}\n\n{block}" if block else context
