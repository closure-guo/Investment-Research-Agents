"""交易执行层落库（add-signal-portfolio-risk-gate G2 + add-paper-broker B1/B2）。

连接纪律与 ``data/snapshot.py`` / ``outcome/store.py`` 同款：WAL + busy_timeout 短连接，
DB 路径调用期读取 env ``EXECUTION_DB_PATH``（默认 ``data/execution.db``），测试经
``db_path`` 显式注入。

- ``signals``：Signal 契约落库表，PK=signal_id（确定性 id → 同日同标的重跑幂等
  upsert，不翻倍）；
- ``hysteresis_events``：滞回判定记录（applied 非空的判定逐条落库，供审计——
  delta spec decision-hysteresis「滞回判定记录落库」）；``UNIQUE(signal_id,
  detail_json)`` + ``ON CONFLICT DO NOTHING``（评审修正）→ 判定内容不变的重跑
  幂等，不产生重复审计行。
- ``orders``/``positions``/``cash``（add-paper-broker B1）：paper 账户落库；
  复合读写（订单+持仓+资金）经 ``execution_conn`` 同事务提交；
  ``signal_ids_with_orders``（已下单信号集合，只读）与 ``record_order_rejection``
  （门禁拒绝留痕）为 add-execution-chain-driver 的驱动器配套助手；
  ``delete_all_broker_state``（add-broker-console：TESTING 造数复位清三表）。
- ``paper_equity``（B2）：每日结算净值曲线（trade_date PK，幂等 upsert）。

模块级只依赖 stdlib：``Signal``/``OrderRecord`` 类型经 TYPE_CHECKING / 函数内
import 引入，避免 ``signals.py``（转换入口）/ ``brokers/``（执行末端）与
``store.py``（落库）的模块级循环依赖。
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from finance_agent.execution.brokers.base import OrderRecord
    from finance_agent.execution.risk_gate import OrderIntent
    from finance_agent.execution.signals import Signal

# 两表 DDL 集中定义（幂等 CREATE IF NOT EXISTS）。
EXECUTION_DDL = """
CREATE TABLE IF NOT EXISTS signals (
  signal_id    TEXT PRIMARY KEY,
  symbol       TEXT NOT NULL,
  direction    TEXT NOT NULL,
  strength     REAL NOT NULL,
  horizon_days INTEGER NOT NULL,
  source       TEXT NOT NULL,
  decision_id  TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_created
  ON signals (symbol, created_at);
CREATE TABLE IF NOT EXISTS hysteresis_events (
  event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id   TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
-- 幂等去重（评审修正）：同 signal_id + 同 detail_json 视为同一条判定记录。
-- 先清理历史重跑可能留下的重复行（保留最小 event_id），再建唯一索引供
-- INSERT ... ON CONFLICT DO NOTHING 使用；无重复的库上该 DELETE 是廉价 no-op。
DELETE FROM hysteresis_events
 WHERE event_id NOT IN (
   SELECT MIN(event_id) FROM hysteresis_events GROUP BY signal_id, detail_json
 );
CREATE UNIQUE INDEX IF NOT EXISTS idx_hysteresis_events_signal_detail
  ON hysteresis_events (signal_id, detail_json);
CREATE INDEX IF NOT EXISTS idx_hysteresis_events_signal
  ON hysteresis_events (signal_id);

-- add-paper-broker（B1/B2）：paper 账户三表 + 每日结算净值表（与 signals 同库
-- 不同表）。列名与计划 B1/B2 逐字一致。
CREATE TABLE IF NOT EXISTS orders (
  order_id    TEXT PRIMARY KEY,
  symbol      TEXT NOT NULL,
  side        TEXT NOT NULL,
  qty         INTEGER NOT NULL,
  status      TEXT NOT NULL,
  reason      TEXT,
  fill_price  REAL,
  fee         REAL NOT NULL DEFAULT 0,
  decision_id TEXT NOT NULL,
  signal_id   TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  attempts    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders (symbol);
CREATE TABLE IF NOT EXISTS positions (
  symbol     TEXT PRIMARY KEY,
  qty        INTEGER NOT NULL,
  avg_cost   REAL NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  balance    REAL NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_equity (
  trade_date      TEXT PRIMARY KEY,
  cash            REAL NOT NULL,
  positions_value REAL NOT NULL,
  equity          REAL NOT NULL
);
"""

_SIGNAL_COLUMNS = (
    "signal_id",
    "symbol",
    "direction",
    "strength",
    "horizon_days",
    "source",
    "decision_id",
    "created_at",
)

_SELECT_SIGNALS = (
    "SELECT signal_id, symbol, direction, strength, horizon_days, source, decision_id,"
    " created_at FROM signals"
)
_ORDER_SIGNALS = " ORDER BY created_at ASC, symbol ASC"


def _default_db_path() -> Path:
    """默认执行库路径；调用期读取 env（测试/SOP 经 EXECUTION_DB_PATH 覆盖）。"""
    return Path(os.getenv("EXECUTION_DB_PATH", "data/execution.db"))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    """短连接（snapshot 同款纪律）：WAL + busy_timeout，row_factory=Row。"""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_execution_db(db_path: str | Path | None = None) -> Path:
    """幂等建 signals + hysteresis_events 两表与索引，返回实际使用的库路径。"""
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(EXECUTION_DDL)
        conn.commit()
    finally:
        conn.close()
    return path


def record_signal(signal: Signal, *, db_path: str | Path | None = None) -> None:
    """Signal 落库（幂等 upsert by signal_id；同日重跑覆盖不翻倍）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO signals
                 (signal_id, symbol, direction, strength, horizon_days, source,
                  decision_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(signal_id) DO UPDATE SET
                 symbol=excluded.symbol,
                 direction=excluded.direction,
                 strength=excluded.strength,
                 horizon_days=excluded.horizon_days,
                 source=excluded.source,
                 decision_id=excluded.decision_id,
                 created_at=excluded.created_at""",
            (
                signal.signal_id,
                signal.symbol,
                signal.direction,
                signal.strength,
                signal.horizon_days,
                signal.source,
                signal.decision_id,
                signal.created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_signals(
    *,
    trade_date: str | None = None,
    symbol: str | None = None,
    db_path: str | Path | None = None,
) -> list[Signal]:
    """按交易日（created_at 前 10 位）/标的过滤，返回 created_at、symbol 升序的 Signal。"""
    from finance_agent.execution.signals import Signal

    init_execution_db(db_path)
    if trade_date is not None and symbol is not None:
        sql = _SELECT_SIGNALS + " WHERE substr(created_at, 1, 10) = ? AND symbol = ?"
        params: tuple[Any, ...] = (str(trade_date), str(symbol))
    elif trade_date is not None:
        sql = _SELECT_SIGNALS + " WHERE substr(created_at, 1, 10) = ?"
        params = (str(trade_date),)
    elif symbol is not None:
        sql = _SELECT_SIGNALS + " WHERE symbol = ?"
        params = (str(symbol),)
    else:
        sql = _SELECT_SIGNALS
        params = ()
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql + _ORDER_SIGNALS, params).fetchall()
    finally:
        conn.close()
    return [
        Signal(
            signal_id=str(row["signal_id"]),
            symbol=str(row["symbol"]),
            direction=str(row["direction"]),  # type: ignore[arg-type]  # 写入侧已过枚举校验
            strength=float(row["strength"]),
            horizon_days=int(row["horizon_days"]),
            source=str(row["source"]),
            decision_id=str(row["decision_id"]),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def record_hysteresis_event(
    signal_id: str,
    detail_json: str,
    created_at: str,
    *,
    db_path: str | Path | None = None,
) -> None:
    """滞回判定记录落库（幂等：同 signal_id + 同 detail_json 重复写入 → no-op）。

    detail_json 为 JSON 字符串，序列化归调用方。判定内容不变的 rerun（同日重跑）
    不得把事件表写成重复行；判定输入变化（detail 不同）仍各留一条审计记录。
    """
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO hysteresis_events (signal_id, detail_json, created_at)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(signal_id, detail_json) DO NOTHING",
            (str(signal_id), str(detail_json), str(created_at)),
        )
        conn.commit()
    finally:
        conn.close()


def list_hysteresis_events(
    *, signal_id: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """按 signal_id 过滤的判定记录（event_id 升序；detail_json 保持原始字符串）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        if signal_id is None:
            rows = conn.execute(
                "SELECT event_id, signal_id, detail_json, created_at FROM hysteresis_events"
                " ORDER BY event_id ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT event_id, signal_id, detail_json, created_at FROM hysteresis_events"
                " WHERE signal_id = ? ORDER BY event_id ASC",
                (str(signal_id),),
            ).fetchall()
    finally:
        conn.close()
    return [
        {
            "event_id": int(row["event_id"]),
            "signal_id": str(row["signal_id"]),
            "detail_json": str(row["detail_json"]),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


# ── add-paper-broker：paper 账户落库（orders/positions/cash/paper_equity）──


@contextmanager
def execution_conn(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """事务性短连接（复合读写的原子边界）。

    paper 下单/重试/结算要跨订单+持仓+资金三表读改写，必须同事务提交；调用方在
    同一 connection 上完成全部操作，正常退出统一 commit，异常回滚。连接纪律与
    ``_connect`` 同款（WAL + busy_timeout）。
    """
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_account(conn: sqlite3.Connection, initial_cash: float, updated_at: str) -> None:
    """惰性开户：cash(id=1) 不存在时以初始资金建行（幂等，已有余额不覆盖）。"""
    conn.execute(
        "INSERT INTO cash (id, balance, updated_at) VALUES (1, ?, ?) ON CONFLICT(id) DO NOTHING",
        (float(initial_cash), str(updated_at)),
    )


def read_cash(conn: sqlite3.Connection) -> float:
    """读账户余额（要求同一事务内先 ``ensure_account``）。"""
    row = conn.execute("SELECT balance FROM cash WHERE id = 1").fetchone()
    if row is None:
        raise RuntimeError("paper 账户未初始化（同一事务内先 ensure_account）")
    return float(row["balance"])


def write_cash(conn: sqlite3.Connection, balance: float, updated_at: str) -> None:
    """写账户余额（upsert；正常路径先经 ensure_account 建行）。"""
    conn.execute(
        "INSERT INTO cash (id, balance, updated_at) VALUES (1, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET balance=excluded.balance,"
        " updated_at=excluded.updated_at",
        (float(balance), str(updated_at)),
    )


def read_position(conn: sqlite3.Connection, symbol: str) -> dict[str, Any] | None:
    """读单票持仓（无持仓 → None）。"""
    row = conn.execute(
        "SELECT symbol, qty, avg_cost, updated_at FROM positions WHERE symbol = ?",
        (str(symbol),),
    ).fetchone()
    return dict(row) if row is not None else None


def write_position(
    conn: sqlite3.Connection, symbol: str, qty: int, avg_cost: float, updated_at: str
) -> None:
    """写单票持仓（upsert：数量 + 加权成本价 + 更新时间）。"""
    conn.execute(
        "INSERT INTO positions (symbol, qty, avg_cost, updated_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg_cost=excluded.avg_cost,"
        " updated_at=excluded.updated_at",
        (str(symbol), int(qty), float(avg_cost), str(updated_at)),
    )


def delete_position(conn: sqlite3.Connection, symbol: str) -> None:
    """清仓（数量归零的行不保留，避免查询侧再过滤）。"""
    conn.execute("DELETE FROM positions WHERE symbol = ?", (str(symbol),))


def list_positions(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """全部持仓（symbol 升序；只读快照）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        return read_positions(conn)
    finally:
        conn.close()


def read_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """全部持仓（事务内读取；symbol 升序）。"""
    rows = conn.execute(
        "SELECT symbol, qty, avg_cost, updated_at FROM positions ORDER BY symbol ASC"
    ).fetchall()
    return [dict(row) for row in rows]


_ORDER_COLUMNS = (
    "order_id",
    "symbol",
    "side",
    "qty",
    "status",
    "reason",
    "fill_price",
    "fee",
    "decision_id",
    "signal_id",
    "created_at",
    "attempts",
)

_SELECT_ORDERS = f"SELECT {', '.join(_ORDER_COLUMNS)} FROM orders"  # noqa: S608 - 拼接项为模块常量列名


def insert_order(conn: sqlite3.Connection, record: OrderRecord) -> None:
    """订单落库（PK=order_id；重复插入是编程错误，由调用方先读后写保幂等）。"""
    conn.execute(
        "INSERT INTO orders (order_id, symbol, side, qty, status, reason, fill_price, fee,"
        " decision_id, signal_id, created_at, attempts)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.order_id,
            record.symbol,
            record.side,
            int(record.qty),
            record.status,
            record.reason,
            record.fill_price,
            float(record.fee),
            record.decision_id,
            record.signal_id,
            record.created_at,
            int(record.attempts),
        ),
    )


def read_order(conn: sqlite3.Connection, order_id: str) -> dict[str, Any] | None:
    """读单笔订单（不存在 → None）。"""
    row = conn.execute(_SELECT_ORDERS + " WHERE order_id = ?", (str(order_id),)).fetchone()
    return dict(row) if row is not None else None


def update_order(
    conn: sqlite3.Connection,
    order_id: str,
    *,
    status: str,
    reason: str | None,
    fill_price: float | None,
    fee: float,
    attempts: int,
) -> None:
    """订单状态机更新（重试/成交/撤销共用；键不变量 order_id 不变）。"""
    conn.execute(
        "UPDATE orders SET status=?, reason=?, fill_price=?, fee=?, attempts=? WHERE order_id=?",
        (str(status), reason, fill_price, float(fee), int(attempts), str(order_id)),
    )


def list_orders(
    *, status: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """订单列表（created_at、order_id 升序；可选状态过滤）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        if status is None:
            rows = conn.execute(
                _SELECT_ORDERS + " ORDER BY created_at ASC, order_id ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                _SELECT_ORDERS + " WHERE status = ? ORDER BY created_at ASC, order_id ASC",
                (str(status),),
            ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def list_pending_orders(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """挂单列表（事务内读取；created_at、order_id 升序 → 重试顺序确定）。"""
    rows = conn.execute(
        _SELECT_ORDERS + " WHERE status = 'pending' ORDER BY created_at ASC, order_id ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def signal_ids_with_orders(*, db_path: str | Path | None = None) -> set[str]:
    """已有订单的 signal_id 集合（add-execution-chain-driver：待执行信号判定，只读）。

    含全部状态（filled/pending/rejected/expired）：被拒订单是该信号的终态留痕，
    驱动器重跑不得再为该信号生成订单（幂等锚点之一，另一锚点见 order_id 派生）。
    """
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT DISTINCT signal_id FROM orders").fetchall()
    finally:
        conn.close()
    return {str(row["signal_id"]) for row in rows}


def record_order_rejection(
    order_id: str,
    intent: OrderIntent,
    reason: str,
    created_at: str,
    *,
    db_path: str | Path | None = None,
) -> None:
    """门禁拒绝留痕（add-execution-chain-driver）：orders 行 ``rejected`` + 原因码。

    被拒订单 MUST NOT 静默丢弃（delta spec broker-execution）：以结构化原因码落库
    供台账/门禁日志回查，``decision_id``/``signal_id`` 照填、成交价与费用留空/零。
    ``order_id`` 由调用方经 ``PaperBroker.order_id_for(intent)`` 同源派生；本函数
    幂等——同 order_id 已有行（成交/挂单/既有拒绝）直接返回，不覆盖不重复插入。
    """
    # 函数内 import：模块级只依赖 stdlib 的连接纪律（同 list_signals 引入 Signal 先例）
    from finance_agent.execution.brokers.base import STATUS_REJECTED, OrderRecord

    with execution_conn(db_path) as conn:
        if read_order(conn, order_id) is not None:
            return
        insert_order(
            conn,
            OrderRecord(
                order_id=str(order_id),
                symbol=str(intent.symbol),
                side=str(intent.side),
                qty=int(intent.qty),
                status=STATUS_REJECTED,
                reason=str(reason),
                fill_price=None,
                fee=0.0,
                decision_id=str(intent.decision_id),
                signal_id=str(intent.signal_id),
                created_at=str(created_at),
                attempts=0,
            ),
        )


_PAPER_EQUITY_SQL = (
    "INSERT INTO paper_equity (trade_date, cash, positions_value, equity)"
    " VALUES (?, ?, ?, ?)"
    " ON CONFLICT(trade_date) DO UPDATE SET cash=excluded.cash,"
    " positions_value=excluded.positions_value, equity=excluded.equity"
)


def write_paper_equity(
    conn: sqlite3.Connection,
    trade_date: str,
    cash: float,
    positions_value: float,
    equity: float,
) -> None:
    """每日 paper 净值点写入（事务内；trade_date 幂等 upsert）。"""
    conn.execute(
        _PAPER_EQUITY_SQL,
        (str(trade_date), float(cash), float(positions_value), float(equity)),
    )


def upsert_paper_equity(
    trade_date: str,
    cash: float,
    positions_value: float,
    equity: float,
    *,
    db_path: str | Path | None = None,
) -> None:
    """每日 paper 净值点落库（trade_date PK；重跑覆盖不翻倍）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        write_paper_equity(conn, trade_date, cash, positions_value, equity)
        conn.commit()
    finally:
        conn.close()


def list_paper_equity(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """paper 净值曲线（trade_date 升序）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, cash, positions_value, equity FROM paper_equity"
            " ORDER BY trade_date ASC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def delete_all_paper_equity(db_path: str | Path | None = None) -> int:
    """清空 paper_equity（add-paper-broker：TESTING seed 造数清理，供 E2E 复位）。

    仅清本表；orders/positions/cash 账户三表不涉（那是 paper 交易账本，
    造数复位不应抹掉账户状态）。返回删除行数。
    """
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute("DELETE FROM paper_equity")
        conn.commit()
        return int(cursor.rowcount)
    finally:
        conn.close()


def delete_all_broker_state(db_path: str | Path | None = None) -> dict[str, int]:
    """清空 paper 账户三表（orders/positions/cash；add-broker-console 造数复位）。

    仅清账户账本三表；``paper_equity`` 净值曲线（战绩页造数面）不涉——两者复位
    边界独立（同 ``delete_all_paper_equity`` 的反向纪律）。返回各表删除行数。
    """
    init_execution_db(db_path)
    with execution_conn(db_path) as conn:
        return {
            "orders": int(conn.execute("DELETE FROM orders").rowcount),
            "positions": int(conn.execute("DELETE FROM positions").rowcount),
            "cash": int(conn.execute("DELETE FROM cash").rowcount),
        }
