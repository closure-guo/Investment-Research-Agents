"""热库可用性探针（harden-sqlite-availability task 5，issue #515）。

契约（delta specs/session-persistence「Database Availability Probe In Health Check」）：
服务存活与库可用分开报——单个库打不开不得让整个容器无声不健康，必须给出库名与
短错误类。``/api/health`` 经 ``probe_databases`` 逐一开连 + 读 ``sqlite_master``（页 1；
``SELECT 1`` 不触库文件、目录/垃圾文件会假绿，design §2 的「开连+SELECT 1」据此
细化为读页 1）：

- 全 ok → ``{status: "ok"}``；
- 任一失败 → ``{"status": "unavailable", "error": <短错误类>}``；
- 探针受超时约束（默认 2s，design §2）：单库 I/O 卡死时报 ``TimeoutError`` 返回，
  不悬挂请求。卡死的探针线程随 executor ``shutdown(wait=False)`` 留存（不阻断
  响应；线程在 I/O 返回后自然结束）。

env 调用期读取（测试/E2E 注入隔离路径的前提；#515 迁卷后宿主 data/ 只是回滚副本，
探针始终在容器内对卷内库执行）。
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

PROBE_TIMEOUT_S = 2.0

_HOT_DB_ENV: tuple[tuple[str, str], ...] = (
    ("sessions", "SESSIONS_DB_PATH"),
    ("screening", "SCREENING_DB_PATH"),
    ("execution", "EXECUTION_DB_PATH"),
    ("events", "EVENTS_DB_PATH"),
    ("regime", "REGIME_DB_PATH"),
    ("research_reports", "RESEARCH_REPORTS_DB_PATH"),
    ("factor_library", "FACTOR_LIBRARY_DB_PATH"),
    ("market_snapshot", "SNAPSHOT_DB_PATH"),
)

ProbeResult = dict[str, dict[str, str]]


def hot_db_paths() -> dict[str, Path]:
    """热库名 → 路径（**调用期**读 env；缺省 data/*.db）。"""
    return {name: Path(os.getenv(env_key, f"data/{name}.db")) for name, env_key in _HOT_DB_ENV}


def _probe_one(path: Path) -> None:
    """单库连通性：打开 + 读 sqlite_master（页 1）。

    ``SELECT 1`` 是连接级语句、不触库文件——目录冒充库文件/垃圾文件在 Windows
    上 connect 都能成功，探针会假绿。读 ``sqlite_master`` 只碰页 1（大库同样廉价），
    文件非库/不可读即抛，异常上抛给调用方归类。
    """
    conn = sqlite3.connect(path, timeout=PROBE_TIMEOUT_S)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master")
    finally:
        conn.close()


def probe_databases(
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    paths: dict[str, Path] | None = None,
    probe_fn: Callable[[Path], None] | None = None,
) -> ProbeResult:
    """对全部热库逐一探测，返回 ``{库名: {"status": "ok"} | {"status": "unavailable", "error": 类名}}``。

    ``paths`` 注入库名→路径（缺省读 env）；``probe_fn`` 注入单库探测动作（测试用）。
    超时是**整体闸**（design §2：探针整体超时，默认 2s）——按 monotonic deadline
    分摊到逐库 ``result(remaining)``，慢库不叠加拖长总时长；超时/异常都归类为
    ``unavailable``，不令单库问题悬挂整体健康检查。
    """
    resolved = paths if paths is not None else hot_db_paths()
    check: Callable[[Path], None] = probe_fn if probe_fn is not None else _probe_one
    results: ProbeResult = {}
    if not resolved:
        return results
    pool = ThreadPoolExecutor(max_workers=len(resolved))
    try:
        futures = {name: pool.submit(check, path) for name, path in resolved.items()}
        deadline = time.monotonic() + timeout_s
        for name, future in futures.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                results[name] = {"status": "unavailable", "error": "TimeoutError"}
                continue
            try:
                future.result(timeout=remaining)
            except FuturesTimeoutError:
                results[name] = {"status": "unavailable", "error": "TimeoutError"}
            except Exception as exc:  # noqa: BLE001 - 错误归类即契约（短错误类）
                results[name] = {"status": "unavailable", "error": type(exc).__name__}
            else:
                results[name] = {"status": "ok"}
    finally:
        # 不等卡死线程：响应必须受超时约束；线程在 I/O 返回后自然结束
        pool.shutdown(wait=False, cancel_futures=True)
    return results
