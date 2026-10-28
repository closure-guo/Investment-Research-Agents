"""因子库 SQLite 存储层（add-factor-library-and-monitor Task 2）。

三表（Global Constraints 逐字）：``factors``（因子主表 + 状态机）、``factor_ic``
（逐日 RankIC，主键 ``(factor_id, trade_date)`` 幂等 upsert）、``factor_events``
（状态/审计事件，detail 为 JSON 文本）。连接纪律与 ``data/snapshot.py`` 同款
（WAL + busy_timeout 短连接、DB 路径调用期读 env、测试经 ``db_path`` 显式注入）。

状态机（逐字）与合法迁移：``candidate → active``；``active → degraded``；
``active → retired``；``degraded → retired``；``degraded → active``（复核恢复）；
``retired → ∅``（无出边）。非法迁移 / 非法初始状态 / 目标因子不存在 →
``FactorStateError``。**状态合法性由本模块代码单一来源把关**（DB 不加 CHECK 约束，
避免两处枚举漂移，迁移错误一律以 ``FactorStateError`` 显式暴露）。

关键设计决策：

- **E7 结构层查重**：``find_structural_duplicate`` 对库内 ``candidate``/``active``/
  ``degraded`` 全部因子做 ``is_subtree_isomorphic``（规范化后比较，数字只按「有」
  匹配）；**``retired`` 不参与**（允许重提）。查重是入库前独立步骤（先于五道闸），
  由调用方（``validate.admit_candidates``）执行；``add_factor`` 自身只校验初始状态。
- **E8 分桶保种**：``apply_bucket_quota`` 对**单个 category 桶内 active** 按
  ``pool_increment`` 降序保留 top ``k``（``BUCKET_TOP_K``），并列取 ``created_at``
  早者优先（再并列取 id 小者）；``pool_increment`` 为 NULL（尚未评估）按最低优先级；
  超出者 ``active → retired`` 并落事件（detail 含 ``reason="BUCKET_QUOTA"`` /
  ``category`` / ``quota`` / ``pool_increment``）。其他桶一动不动。

格式契约（与调用方之间）：``ast_json`` 为 ``dsl.serialize(parse(expression))`` 产出的
**规范表达式字符串**；查重时以 ``parse(..., enforce_complexity=False)`` 回读
（复杂度硬约束已在提案路径执行，库层不重复裁决）。库内行 ``ast_json`` 为 NULL 时
回退用 ``expression`` 解析比较。

``record_ic`` / ``set_pool_increment`` 是**指标写入**，不改变状态、不触碰
``updated_at``、不落事件（审计只覆盖状态机迁移）；状态变化唯一入口是
``set_status``（``add_factor`` 落 ``created`` 事件）。
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from finance_agent.factors.constraints import BUCKET_TOP_K
from finance_agent.factors.dsl import is_subtree_isomorphic, parse, serialize

# 状态机（逐字；Global Constraints）
FACTOR_STATES: tuple[str, ...] = ("candidate", "active", "degraded", "retired", "rejected")
# 允许作为 add_factor 初始状态的三态（degraded/retired 只能经监控/保种到达；
# rejected 只能经准入拒收路径 add_factor 到达）
_INITIAL_STATES: frozenset[str] = frozenset({"candidate", "active", "rejected"})
# 合法迁移表：retired / rejected → ∅（空集，均为终态；rejected 行永不迁移）
_TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"active"}),
    "active": frozenset({"degraded", "retired"}),
    "degraded": frozenset({"active", "retired"}),
    "retired": frozenset(),
    "rejected": frozenset(),
}
# 结构查重参与状态（retired 允许重提；rejected 拒绝池行不参与——重提不受阻）
_DEDUP_STATES: tuple[str, ...] = ("candidate", "active", "degraded")

# 事件类型（逐字）
EVENT_CREATED = "created"
EVENT_STATUS_CHANGE = "status_change"
EVENT_RE_REJECTED = "re_rejected"

# 三表 DDL（幂等 CREATE IF NOT EXISTS；列名与 Global Constraints 逐字一致）
FACTOR_LIBRARY_DDL = """
CREATE TABLE IF NOT EXISTS factors (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  expression      TEXT NOT NULL,
  hypothesis      TEXT NOT NULL,
  category        TEXT NOT NULL,
  status          TEXT NOT NULL,
  ast_json        TEXT,
  source          TEXT NOT NULL DEFAULT 'proposal',
  alignment_score REAL,
  orientation     INTEGER,
  pool_increment  REAL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_factors_status ON factors (status);
CREATE INDEX IF NOT EXISTS idx_factors_category_status ON factors (category, status);
CREATE TABLE IF NOT EXISTS factor_ic (
  factor_id  INTEGER NOT NULL,
  trade_date TEXT NOT NULL,
  rank_ic    REAL NOT NULL,
  PRIMARY KEY (factor_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_factor_ic_factor_date ON factor_ic (factor_id, trade_date);
CREATE TABLE IF NOT EXISTS factor_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  factor_id   INTEGER NOT NULL,
  event       TEXT NOT NULL,
  from_status TEXT,
  to_status   TEXT,
  detail      TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_factor_events_factor ON factor_events (factor_id);
CREATE TABLE IF NOT EXISTS factor_admission_ledger (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id      TEXT NOT NULL,
  source        TEXT NOT NULL,
  expression    TEXT NOT NULL,
  p_value       REAL,
  p_adj         REAL,
  q             REAL,
  n_hypotheses  INTEGER,
  admitted      INTEGER NOT NULL,
  factor_id     INTEGER,
  rejected_gate TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_factor_ledger_created ON factor_admission_ledger (created_at);
CREATE INDEX IF NOT EXISTS idx_factor_ledger_p ON factor_admission_ledger (p_value);
CREATE TABLE IF NOT EXISTS factor_diagnostics (
  factor_id      INTEGER PRIMARY KEY,
  as_of          TEXT NOT NULL,
  style_exposure TEXT,
  monotonicity   TEXT,
  computed_at    TEXT NOT NULL
);
"""


class FactorStateError(ValueError):
    """状态机错误：非法状态迁移 / 非法初始状态 / 目标因子不存在。

    ``ValueError`` 子类（调用方按 ``ValueError`` 兜底捕获亦成立）；消息说明
    具体违法点（形如 ``非法状态迁移 'candidate' → 'degraded'``）。
    """


# ── 连接纪律（与 data/snapshot.py 同款） ────────────────────────────────────


def _default_db_path() -> Path:
    """默认因子库路径；调用期读取 env（测试/SOP 经 FACTOR_LIBRARY_DB_PATH 覆盖）。"""
    return Path(os.getenv("FACTOR_LIBRARY_DB_PATH", "data/factor_library.db"))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    """短连接：WAL + busy_timeout，row_factory=Row（snapshot.py 同款纪律）。"""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_library(db_path: str | Path | None = None) -> Path:
    """幂等建 3 表 + 索引（含存量库 orientation 列迁移），返回实际使用的库路径。"""
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(FACTOR_LIBRARY_DDL)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(factors)")}
        if "orientation" not in existing:
            conn.execute("ALTER TABLE factors ADD COLUMN orientation INTEGER")
        conn.commit()
    finally:
        conn.close()
    return path


def _now_iso() -> str:
    """事件/行时间戳（本地时区 ISO 文本，含微秒——并列由 id 兜底）。"""
    return datetime.now().isoformat()


def _parse_trade_date(value: str) -> str:
    """交易日严格规范 ``YYYY-MM-DD``（字典序比较 / 序列表依赖规范形态）。"""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非规范交易日: {value!r}，须为 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"非规范交易日: {value!r}，须为 YYYY-MM-DD")
    return value


def _finite_float(value: Any, field: str) -> float:
    """数值入参 → float；None/NaN/±inf/不可解析 → ``ValueError``（不落坏值）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是有限数值: {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{field} 必须是有限数值: {value!r}")
    return number


def _require_factor(conn: sqlite3.Connection, factor_id: int) -> sqlite3.Row:
    """按 id 取因子行；不存在 → ``FactorStateError``（fail loud）。"""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM factors WHERE id = ?", (factor_id,)
    ).fetchone()
    if row is None:
        raise FactorStateError(f"因子不存在: {factor_id}")
    return row


def _insert_event(
    conn: sqlite3.Connection,
    factor_id: int,
    event: str,
    from_status: str | None,
    to_status: str | None,
    detail: dict[str, Any] | None,
    created_at: str,
) -> None:
    """落一条事件（detail → JSON 文本；None 存 NULL）。"""
    conn.execute(
        "INSERT INTO factor_events (factor_id, event, from_status, to_status, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            factor_id,
            event,
            from_status,
            to_status,
            json.dumps(detail, ensure_ascii=False) if detail is not None else None,
            created_at,
        ),
    )


# ── 写入：新增 / 状态迁移 ───────────────────────────────────────────────────


def add_factor(
    *,
    expression: str,
    hypothesis: str,
    category: str,
    status: str = "candidate",
    ast_json: str | None = None,
    source: str = "proposal",
    alignment_score: float | None = None,
    event_detail: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> int:
    """新增因子行，返回 ``factors.id``；落 ``created`` 事件（``event_detail`` 并入其 detail）。

    仅校验**初始状态合法性**（``candidate``/``active``/``rejected``；其余 → ``FactorStateError``）
    ——结构查重与五道闸由调用方（``validate.admit_candidates``）在入库前完成，
    本函数不做表达式解析、不复核类别词表（库层保持通用存储语义）。
    """
    if status not in _INITIAL_STATES:
        raise FactorStateError(
            f"非法初始状态 {status!r}；允许: {sorted(_INITIAL_STATES)}"
            "（degraded/retired 只能经状态迁移到达）"
        )
    init_library(db_path)
    now = _now_iso()
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO factors (expression, hypothesis, category, status, ast_json, source, "
            "alignment_score, pool_increment, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                expression,
                hypothesis,
                category,
                status,
                ast_json,
                source,
                alignment_score,
                now,
                now,
            ),
        )
        factor_id = int(cursor.lastrowid or 0)
        _insert_event(conn, factor_id, EVENT_CREATED, None, status, event_detail, now)
        conn.commit()
        return factor_id
    finally:
        conn.close()


def set_status(
    factor_id: int,
    status: str,
    *,
    detail: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> None:
    """状态迁移（唯一状态写入口）：校验合法迁移 + 落 ``status_change`` 事件。

    ``detail`` 为随事件持久化的 JSON 字典（降级事件带滚动 IC/ICIR 快照、保种事件带
    ``reason="BUCKET_QUOTA"`` 等）。非法迁移 / 未知目标状态 / 因子不存在 →
    ``FactorStateError``，且**不改状态、不落事件**（同事务回滚语义：先校验后写）。
    """
    init_library(db_path)
    conn = _connect(db_path)
    try:
        row = _require_factor(conn, factor_id)
        current = str(row["status"])
        allowed = _TRANSITIONS.get(current, frozenset())
        if status not in allowed:
            raise FactorStateError(f"非法状态迁移 {current!r} → {status!r}")
        now = _now_iso()
        conn.execute(
            "UPDATE factors SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, factor_id),
        )
        _insert_event(conn, factor_id, EVENT_STATUS_CHANGE, current, status, detail, now)
        conn.commit()
    finally:
        conn.close()


# ── 结构层查重（E7） ───────────────────────────────────────────────────────


def find_structural_duplicate(ast_json: str, *, db_path: str | Path | None = None) -> int | None:
    """库内（candidate/active/degraded）同构因子 id；无 → None。

    ``ast_json`` 为规范表达式字符串（``dsl.serialize`` 产出；原始表达式亦可，
    两者 ``parse`` 等价）；比较在 ``is_subtree_isomorphic``（规范化）下进行，
    数字取值忽略。库内行 ``ast_json`` 为 NULL 时回退用 ``expression``。
    解析失败 → ``DSLParseError``（fail loud，不当作无重复）。

    快路径（issue #464）：库内行的 ``ast_json`` 按契约即 ``serialize(parse(...))``
    规范串——**同串必同构**，规范串精确命中直接返回，免去逐行 parse+同构比对；
    未命中（含数字变体/交换律变体/``ast_json`` NULL 或非规范的旧数据行）才走
    全量同构扫描。语义不变，仅免掉命中场景的 O(库大小) 次解析。
    """
    init_library(db_path)
    candidate = parse(ast_json, enforce_complexity=False)
    canonical = serialize(candidate)
    placeholders = ", ".join("?" * len(_DEDUP_STATES))
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            # placeholders 仅为 "?" 序列；状态值全参数化
            "SELECT id, expression, ast_json FROM factors "  # noqa: S608
            f"WHERE status IN ({placeholders}) ORDER BY id",
            list(_DEDUP_STATES),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        stored = row["ast_json"] or row["expression"]
        if stored == canonical:  # 规范串精确命中 → 必同构（serialize 为规范形）
            return int(row["id"])
    for row in rows:
        stored = row["ast_json"] or row["expression"]
        if is_subtree_isomorphic(candidate, parse(stored, enforce_complexity=False)):
            return int(row["id"])
    return None


def find_rejected_duplicate(ast_json: str, *, db_path: str | Path | None = None) -> int | None:
    """拒绝池（``status='rejected'``）内同构因子 id；无 → ``None``。

    与 ``find_structural_duplicate`` 同款比对逻辑（规范串精确命中快路径 +
    全量同构扫描），仅范围限定在拒绝池行——供 ``record_rejected_factor``
    的 upsert 幂等语义使用，与闸0 查重（``_DEDUP_STATES``）互不影响。
    解析失败 → ``DSLParseError``（fail loud，不当作无重复）。
    """
    init_library(db_path)
    candidate = parse(ast_json, enforce_complexity=False)
    canonical = serialize(candidate)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, expression, ast_json FROM factors WHERE status = 'rejected' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        stored = row["ast_json"] or row["expression"]
        if stored == canonical:  # 规范串精确命中 → 必同构
            return int(row["id"])
    for row in rows:
        stored = row["ast_json"] or row["expression"]
        if is_subtree_isomorphic(candidate, parse(stored, enforce_complexity=False)):
            return int(row["id"])
    return None


def record_rejected_factor(
    *,
    expression: str,
    hypothesis: str,
    category: str,
    ast_json: str | None,
    source: str,
    gate: int | None,
    reason: str | None,
    detail: dict[str, Any] | None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """拒绝池写入（upsert 幂等）：同构已有 ``rejected`` 行则更新并追加事件，否则新建。

    - 新建：``add_factor(status="rejected")``，created 事件 detail 携带
      ``gate`` / ``reason`` 与判定读数（``detail``）。
    - 更新：刷新既有行 ``updated_at``，追加 ``re_rejected`` 事件（历史裁决经事件表保留，
      行身份不重复——IC 序列不因重提被切碎）。
    - ``ast_json`` 缺失 → 由 ``expression`` 现算规范串（回填旧数据的回退路径，与
      ``find_structural_duplicate`` 同款）。

    返回 ``{"factor_id": int, "action": "created" | "updated"}``。
    """
    node = parse(ast_json or expression, enforce_complexity=False)
    canonical = serialize(node)
    rejection_detail: dict[str, Any] = {
        "gate": gate,
        "reason": reason,
        **(dict(detail) if detail else {}),
    }
    duplicate = find_rejected_duplicate(canonical, db_path=db_path)
    now = _now_iso()
    if duplicate is not None:
        conn = _connect(db_path)
        try:
            conn.execute("UPDATE factors SET updated_at = ? WHERE id = ?", (now, duplicate))
            _insert_event(
                conn,
                duplicate,
                EVENT_RE_REJECTED,
                "rejected",
                "rejected",
                rejection_detail,
                now,
            )
            conn.commit()
        finally:
            conn.close()
        return {"factor_id": duplicate, "action": "updated"}
    factor_id = add_factor(
        expression=expression,
        hypothesis=hypothesis,
        category=category,
        status="rejected",
        ast_json=canonical,
        source=source,
        event_detail=rejection_detail,
        db_path=db_path,
    )
    return {"factor_id": factor_id, "action": "created"}


# ── factor_ic：幂等 upsert 与序列读取 ──────────────────────────────────────


def record_ic(
    factor_id: int,
    trade_date: str,
    rank_ic: float,
    db_path: str | Path | None = None,
) -> None:
    """写入某因子某日 RankIC；同 ``(factor_id, trade_date)`` **幂等**覆盖（upsert）。

    ``trade_date`` 须规范 ``YYYY-MM-DD``、``rank_ic`` 须有限、因子须存在
    （fail loud，防坏值/悬挂行污染 IC 序列排序）。不落事件、不动 ``updated_at``
    （这是指标刷新，不是状态变化）。
    """
    normalized_date = _parse_trade_date(trade_date)
    value = _finite_float(rank_ic, "rank_ic")
    init_library(db_path)
    conn = _connect(db_path)
    try:
        _require_factor(conn, factor_id)
        conn.execute(
            "INSERT INTO factor_ic (factor_id, trade_date, rank_ic) VALUES (?, ?, ?) "
            "ON CONFLICT(factor_id, trade_date) DO UPDATE SET rank_ic = excluded.rank_ic",
            (factor_id, normalized_date, value),
        )
        conn.commit()
    finally:
        conn.close()


def list_ic_series(
    factor_id: int, *, limit: int | None = None, db_path: str | Path | None = None
) -> list[tuple[str, float]]:
    """因子 IC 序列 ``[(trade_date, rank_ic)...]``，**按日期升序**。

    ``limit`` 给定时返回**最近 N 个交易日**（仍为升序；衰减监控取最近
    ``DECAY_WINDOW`` 日即用此参数）。``limit`` 非正 → ``ValueError``；无记录 → ``[]``。
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit 必须是正整数或 None: {limit!r}")
    init_library(db_path)
    conn = _connect(db_path)
    try:
        if limit is None:
            rows = conn.execute(
                "SELECT trade_date, rank_ic FROM factor_ic WHERE factor_id = ? ORDER BY trade_date",
                (factor_id,),
            ).fetchall()
            return [(str(row[0]), float(row[1])) for row in rows]
        rows = conn.execute(
            "SELECT trade_date, rank_ic FROM factor_ic WHERE factor_id = ? "
            "ORDER BY trade_date DESC LIMIT ?",
            (factor_id, limit),
        ).fetchall()
        return [(str(row[0]), float(row[1])) for row in reversed(rows)]
    finally:
        conn.close()


# ── 查询 ───────────────────────────────────────────────────────────────────


def list_factors(
    *,
    status: str | None = None,
    category: str | None = None,
    include_rejected: bool = False,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """因子行列表（按 id 升序）；可选 status/category 过滤。

    ``include_rejected=False``（缺省）且 ``status`` 未显式给定时排除 ``rejected``
    拒绝池行——拒绝池对既有读路径不可见（既有调用方语义不变）。``status``
    显式给定时按 status 精确返回（拒绝池跟踪走 ``status="rejected"``）；
    ``include_rejected=True`` 用于按 id 诊断类读取。
    """
    init_library(db_path)
    sql = "SELECT * FROM factors"
    conditions: list[str] = []
    params: list[Any] = []
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    elif not include_rejected:
        conditions.append("status != 'rejected'")
    if category is not None:
        conditions.append("category = ?")
        params.append(category)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY id"
    conn = _connect(db_path)
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def list_active_factors(
    *, category: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """在册（``status='active'``）因子；当日打分消费方据此过滤（降级因子自然剔除）。"""
    return list_factors(status="active", category=category, db_path=db_path)


def reset_factor_library(db_path: str | Path | None = None) -> dict[str, int]:
    """清空因子库三表（TESTING 造数复位专用，非业务路径）；返回各表删除行数。

    仅 delta add-factor-library-page 的 `/api/test/seed` ``factors_reset`` 调用：
    E2E 每例前后复位因子面（库路径随 ``FACTOR_LIBRARY_DB_PATH`` 指向独立测试库）。
    业务写路径只经五道闸与衰减监控，无生产调用方。

    为何放在本模块：表名只允许出现在 library.py（api.py 自行拼 SQL 会复制 schema
    知识；与 screening.store / memory.semantic 的 reset 函数同款纪律）。
    """
    init_library(db_path)
    conn = _connect(db_path)
    removed: dict[str, int] = {}
    try:
        # 先子表后主表（无外键，仅语义顺序：事件/IC/读数属于因子）
        for table in (
            "factor_diagnostics",
            "factor_admission_ledger",
            "factor_events",
            "factor_ic",
            "factors",
        ):
            cursor = conn.execute(f"DELETE FROM {table}")  # noqa: S608 -- 表名来自模块常量
            removed[table] = int(cursor.rowcount)
        conn.commit()
        return removed
    finally:
        conn.close()


# ── 跨批次候选检验账本（add-fdr-candidate-ledger / issue #355 使能层） ──

_LEDGER_COLUMNS = (
    "batch_id",
    "source",
    "expression",
    "p_value",
    "p_adj",
    "q",
    "n_hypotheses",
    "admitted",
    "factor_id",
    "rejected_gate",
)


def record_admission_ledger(
    entries: list[dict[str, Any]], *, db_path: str | Path | None = None
) -> int:
    """批量写入候选检验账本（单事务；返回行数）。空列表 → 0（零写入）。

    每 entry 键 = ``_LEDGER_COLUMNS``；``admitted`` 为布尔（存 0/1）；未达闸③的
    候选 ``p_value``/``p_adj``/``q``/``n_hypotheses`` 为 None（不计入跨批家族）。
    """
    if not entries:
        return 0
    init_library(db_path)
    conn = _connect(db_path)
    try:
        now = _now_iso()
        rows = [
            (
                str(e.get("batch_id") or ""),
                str(e.get("source") or ""),
                str(e.get("expression") or ""),
                e.get("p_value"),
                e.get("p_adj"),
                e.get("q"),
                e.get("n_hypotheses"),
                1 if e.get("admitted") else 0,
                e.get("factor_id"),
                e.get("rejected_gate"),
                now,
            )
            for e in entries
        ]
        columns = ", ".join(_LEDGER_COLUMNS)
        placeholders = ", ".join("?" for _ in _LEDGER_COLUMNS)
        conn.executemany(
            f"INSERT INTO factor_admission_ledger ({columns}, created_at) "  # noqa: S608 - 列名来自模块常量
            f"VALUES ({placeholders}, ?)",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def list_ledger_entries(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """账本全量（按写入序 id 升序）；``admitted`` 还原为布尔、空库返回 []。"""
    init_library(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM factor_admission_ledger ORDER BY id").fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["admitted"] = bool(item.get("admitted"))
        out.append(item)
    return out


def ledger_summary(db_path: str | Path | None = None) -> dict[str, Any]:
    """库级读数：总行数 / 已验证数（p 非空 = 跨批家族面）/ 已入库数 / 分批明细。

    分批按写入序（首行出现序）稳定输出；读数不改变任何准入结论（执行层待裁决，
    issue #355 方向 1/2/3）。
    """
    entries = list_ledger_entries(db_path)
    batches: dict[str, dict[str, Any]] = {}
    for e in entries:
        bucket = batches.setdefault(
            str(e["batch_id"]),
            {
                "batch_id": e["batch_id"],
                "source": e["source"],
                "size": 0,
                "tested": 0,
                "admitted": 0,
            },
        )
        bucket["size"] += 1
        if e["p_value"] is not None:
            bucket["tested"] += 1
        if e["admitted"]:
            bucket["admitted"] += 1
    return {
        "total": len(entries),
        "tested": sum(1 for e in entries if e["p_value"] is not None),
        "admitted": sum(1 for e in entries if e["admitted"]),
        "batches": list(batches.values()),
    }


def list_events(
    factor_id: int | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """事件审计列表（按写入序 id 升序）；``factor_id`` 给定时只返回该因子事件。

    每行 dict：``id`` / ``factor_id`` / ``event`` / ``from_status`` / ``to_status`` /
    ``detail``（JSON 解析回 dict，NULL → None）/ ``created_at``。
    """
    init_library(db_path)
    sql = "SELECT * FROM factor_events"
    params: list[Any] = []
    if factor_id is not None:
        sql += " WHERE factor_id = ?"
        params.append(factor_id)
    sql += " ORDER BY id"
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    events: list[dict[str, Any]] = []
    for row in rows:
        event = dict(row)
        raw_detail = event.get("detail")
        event["detail"] = json.loads(raw_detail) if raw_detail else None
        events.append(event)
    return events


# ── 池级增量指标 / 分桶保种（E8） ───────────────────────────────────────────


def set_pool_increment(factor_id: int, value: float, db_path: str | Path | None = None) -> None:
    """写入池级增量（闸④的 IR 增量）；非有限值 / 因子不存在 → 异常。"""
    increment = _finite_float(value, "pool_increment")
    init_library(db_path)
    conn = _connect(db_path)
    try:
        _require_factor(conn, factor_id)
        conn.execute("UPDATE factors SET pool_increment = ? WHERE id = ?", (increment, factor_id))
        conn.commit()
    finally:
        conn.close()


def set_orientation(factor_id: int, value: int, db_path: str | Path | None = None) -> None:
    """入池定向符号写入（-1/0/1；0=不可定）。越界 ValueError——列语义由监控判据消费。"""
    if value not in (-1, 0, 1):
        raise ValueError(f"orientation 限 {{-1, 0, 1}}，收到 {value!r}")
    init_library(db_path)
    conn = _connect(db_path)
    try:
        _require_factor(conn, factor_id)
        conn.execute("UPDATE factors SET orientation = ? WHERE id = ?", (value, factor_id))
        conn.commit()
    finally:
        conn.close()


def apply_bucket_quota(
    category: str, *, k: int = BUCKET_TOP_K, db_path: str | Path | None = None
) -> list[int]:
    """单桶保种：桶内 active 保留 ``pool_increment`` top ``k``，超出者退役。

    排序键：``pool_increment`` 降序 → ``created_at`` 升序（早者优先）→ id 升序；
    ``pool_increment`` 为 NULL 视为最低优先级（未评估者先让位）。仅动本 category
    的 **active** 行（candidate/degraded/retired 不占配额），其他桶不动。返回被退役
    的因子 id 列表（按淘汰顺序 = 排序序）；未超容 → ``[]``。``k < 0`` → ``ValueError``。
    """
    if k < 0:
        raise ValueError(f"k 必须是非负整数: {k!r}")
    init_library(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, pool_increment, created_at FROM factors "
            "WHERE category = ? AND status = 'active'",
            (category,),
        ).fetchall()
    finally:
        conn.close()
    ordered = sorted(rows, key=_quota_sort_key)
    retired: list[int] = []
    for position, row in enumerate(ordered[k:], start=k + 1):
        factor_id = int(row["id"])
        set_status(
            factor_id,
            "retired",
            detail={
                "reason": "BUCKET_QUOTA",
                "category": category,
                "quota": k,
                "pool_increment": (
                    float(row["pool_increment"]) if row["pool_increment"] is not None else None
                ),
                "rank": position,
            },
            db_path=db_path,
        )
        retired.append(factor_id)
    return retired


def _quota_sort_key(row: sqlite3.Row) -> tuple[float, str, int]:
    """保种排序键：增量降序（NULL → +inf 最低优先）→ created_at 升序 → id 升序。"""
    increment = row["pool_increment"]
    rank_value = -float(increment) if increment is not None else math.inf
    return (rank_value, str(row["created_at"]), int(row["id"]))


# ── 诊断读数存储（add-factor-diagnostics-batch）─────────────────────────────
# 每因子一行；日批（factors.diagnostics）生产、详情接口只读消费。


def set_factor_diagnostics(
    factor_id: int,
    as_of: str,
    *,
    style_exposure: dict[str, Any] | None,
    monotonicity: dict[str, Any] | None,
    db_path: str | Path | None = None,
) -> None:
    """Upsert 单因子诊断读数（日批幂等写：同因子覆盖）。

    ``style_exposure`` / ``monotonicity`` 为 JSON 友好载荷（``style_exposure_payload``
    输出 / ``check_monotonicity`` detail 扩展，非有限值由生产侧置 None）；
    ``None`` 存 NULL。
    """
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO factor_diagnostics (factor_id, as_of, style_exposure, monotonicity, computed_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(factor_id) DO UPDATE SET as_of=excluded.as_of,"
            " style_exposure=excluded.style_exposure, monotonicity=excluded.monotonicity,"
            " computed_at=excluded.computed_at",
            (
                int(factor_id),
                str(as_of),
                json.dumps(style_exposure, ensure_ascii=False)
                if style_exposure is not None
                else None,
                json.dumps(monotonicity, ensure_ascii=False) if monotonicity is not None else None,
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_factor_diagnostics(
    factor_id: int, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    """读单因子诊断读数；无记录 → ``None``（详情接口据此置 null，不虚构）。"""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT as_of, style_exposure, monotonicity, computed_at"
            " FROM factor_diagnostics WHERE factor_id = ?",
            (int(factor_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "as_of": str(row["as_of"]),
        "style_exposure": json.loads(row["style_exposure"]) if row["style_exposure"] else None,
        "monotonicity": json.loads(row["monotonicity"]) if row["monotonicity"] else None,
        "computed_at": str(row["computed_at"]),
    }
