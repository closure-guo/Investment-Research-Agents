"""SQLite 热库连接 seam（C4：docs/architecture-debt/c4-sqlite-connector.md）。

把 #515「SQLite 连接纪律」从**抄写传播**收敛为**接口**：

- ``open_hot``：mkdir + Row factory + busy_timeout + ``check_same_thread=False``（可关）
  + 可选瞬态错误连接重试预算；六个 store 的 ``_connect`` 逐字拷贝全部收编到这里。
- ``init_wal``：WAL 只进 init 的**唯一实现**——连接路径不设 journal_mode（#515 纪律），
  各 store 的 init 函数通过它设置，替代 18 处复制的 ``PRAGMA journal_mode=WAL`` 注释行。
- ``connect_retry_budget``：瞬态错误连接重试的预算公式（clamp [30, 300]）唯一源，
  session_store 的预算制实现原语义搬入。

零行为迁移：timeout / busy_timeout / check_same_thread / 是否重试按调用方现值传参，
本模块不改变任何 store 的既有行为。
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

# 瞬态 SQLite 错误模式（delta add-event-delivery-resilience「SQLite 瞬态错误重试」；
# session_store 原实现语义搬入）：unable to open database file = bind mount 瞬断；
# locked = busy_timeout 覆盖不到的连接期锁；disk I/O error = 存储瞬态。
# 非瞬态错误不重试立即抛出。
TRANSIENT_PATTERNS: tuple[str, ...] = (
    "unable to open database file",
    "database is locked",
    "disk i/o error",
)

# 重试预算 clamp 边界与退避参数（session_store 原常量语义）
_RETRY_BUDGET_MIN_S = 30.0
_RETRY_BUDGET_MAX_S = 300.0
_RETRY_SLEEP_CAP_S = 2.0
_RETRY_MAX_ATTEMPTS = 500
_RETRY_BASE_SLEEP_S = 0.1


def connect_retry_budget(
    env_var: str = "SESSION_DB_CONNECT_RETRY_BUDGET_S", default_s: float = 120.0
) -> float:
    """瞬态错误连接重试预算（秒）：env 可覆盖，clamp 到 [30, 300]。

    session_store 原语义搬入（harden-sqlite-connection-discipline ④）：
    指数退避持续重试至累计等待达到预算，取代旧的固定小次数——
    重试窗口须盖过实际停摆（incident 053 的 9p 连接失败动辄分钟级）。
    """
    raw = os.getenv(env_var)
    try:
        value = float(raw) if raw is not None else default_s
    except ValueError:
        value = default_s
    return min(_RETRY_BUDGET_MAX_S, max(_RETRY_BUDGET_MIN_S, value))


def open_hot(
    db_path: str | Path | None,
    *,
    default: str | Path | None = None,
    timeout: float = 15.0,
    busy_timeout_ms: int = 15000,
    check_same_thread: bool = False,
    retry_budget_s: float | None = None,
) -> sqlite3.Connection:
    """打开一个热库连接（统一纪律的连接入口）。

    - ``db_path`` 为 None 时取 ``default``（各 store 自己的默认路径登记——
      库身份与共享决策留在 store 本地，这里只管连接纪律）；
    - 自动建父目录；
    - ``row_factory = sqlite3.Row``、``PRAGMA busy_timeout``；
    - ``check_same_thread=False`` 为默认（流式/多线程落库场景），可显式关；
    - ``retry_budget_s`` 给定时，瞬态错误按预算指数退避重试（见 ``connect_retry_budget``）；
      未给定则不重试（保持未迁移 store 的既有行为）。

    WAL **不在这里设置**——journal_mode 只进 init（``init_wal``，#515 纪律）。
    """
    path = Path(db_path) if db_path else Path(default) if default is not None else None
    if path is None:
        raise ValueError("open_hot 需要 db_path 或 default 之一")
    path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + retry_budget_s if retry_budget_s is not None else None
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            conn = sqlite3.connect(path, check_same_thread=check_same_thread, timeout=timeout)
            conn.row_factory = sqlite3.Row
            # 并发写等待锁而非立即抛 "database is locked"（高频落库场景必需）
            conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            return conn
        except sqlite3.OperationalError as exc:
            if retry_budget_s is None or not any(p in str(exc).lower() for p in TRANSIENT_PATTERNS):
                raise
            last_exc = exc
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0 or attempt == _RETRY_MAX_ATTEMPTS - 1:
                # 预算耗尽（或硬顶次数到）：重试放弃即抛，不做无意义的收尾等待
                raise last_exc from exc
            sleep_s = min(
                _RETRY_BASE_SLEEP_S * (2 ** (attempt + 1)),
                _RETRY_SLEEP_CAP_S,
                remaining if remaining is not None else _RETRY_SLEEP_CAP_S,
            )
            time.sleep(max(sleep_s, 0.0))
    raise last_exc  # type: ignore[misc]  # pragma: no cover - 硬顶保护，正常不可达


def init_wal(conn: sqlite3.Connection) -> None:
    """WAL 只进 init（#515 连接纪律）——各 store init 函数的唯一设置入口。"""
    conn.execute("PRAGMA journal_mode=WAL")
