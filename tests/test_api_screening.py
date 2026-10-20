"""选股 API 与批量分析队列单测（add-screening-pipeline T3；S3/S4/S6）。

- pool 端点（S4）：最近成功批次池（含 ``factor_contribs`` 与 ``consecutive_days``）；
  无成功批次 → ``batch_date=None`` + 空池；最近一次尝试失败 → ``stale=True`` 且仍
  展示最近成功批次（标注其日期）；
- status 端点（S4）：失败 → ``stale`` + 逐字文案「选股批次失败（最近成功: YYYY-MM-DD）」；
  读库异常 → 降级 200（**永不 500**）；
- analyze 端点（S3/S4）：池空 → 409；并发上限 2（其余 queued）、终态后自动补位；
- 队列（S3）：``snapshot()`` 四态；``seed`` 造数（E2E 徽标态）；
- seed 扩展（S6，TESTING=1）：``screening`` 造数（batch/pool/queue）+ ``screening_reset``
  清四表；库路径调用期读取（env 注入即生效 = 不落开发库）。

零网络：PipelineRunner.start 与 session 状态读取全部以可控桩替换。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finance_agent import session_store
from finance_agent.api import app
from finance_agent.data.snapshot import (
    latest_snapshot_date,
    set_meta_many,
    write_snapshot_rows,
)
from finance_agent.screening.queue import AnalyzeQueue
from finance_agent.screening.store import (
    list_screening_removals,
    list_selection_pool,
    record_screening_batch,
)

_AS_OF = "2026-10-13"
_FAILED_DATE = "2026-10-14"
# as_of 的前一交易日（NEW_EVENT 下界；见 pipeline._recent_event_bound）
_PRIOR_TRADE_DATE = "2026-10-12"


@pytest.fixture(autouse=True)
def _screening_db(tmp_path, monkeypatch):
    """screening / 快照两库路径均指向 tmp（env 调用期读取；任何用例不得污染开发库）。

    SNAPSHOT_DB_PATH 必须一并钉死（评审修正 1）：analyze 端点的时效性校验经
    ``latest_snapshot_date()`` 读 env 指向的快照库——不钉死时读 cwd 默认
    ``data/market_snapshot.db``，该开发库一旦有行，未显式覆盖的 analyze 用例会真正
    进入事件装配（WebSearch/litellm 网络调用）。显式覆盖的时效性用例不受影响
    （monkeypatch.setenv 后设者胜）。
    """
    path = tmp_path / "screening.db"
    monkeypatch.setenv("SCREENING_DB_PATH", str(path))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "market_snapshot.db"))
    return path


@pytest.fixture(autouse=True)
def _fresh_analyze_queue(monkeypatch):
    """逐用例重置 api 内队列单例（懒创建；避免跨用例残留）。"""
    import finance_agent.api as api_module

    monkeypatch.setattr(api_module, "_analyze_queue", None)


def _pool_rows(count: int = 5) -> list[dict]:
    return [
        {
            "symbol": f"60000{index}",
            "composite_score": 2.0 - 0.1 * index,
            "rank": index,
            "consecutive_days": 3 + index,
            "l3_verdict": "keep",
            "l3_reason": f"无风险事件{index}",
            "factor_contribs": [
                {"factor_id": 7, "zscore": 0.5, "weight": 0.8, "contribution": 0.4}
            ],
        }
        for index in range(1, count + 1)
    ]


@pytest.fixture
def pool_seed(_screening_db):
    """造 5 只标的的成功批次池（rank 1..5，consecutive_days 4..8）。"""
    record_screening_batch(
        _AS_OF,
        status="ok",
        started_at="2026-10-13T15:30:00",
        finished_at="2026-10-13T15:33:00",
        l1_passed=20,
        l2_top=10,
        l3_keep=5,
        pool_rows=_pool_rows(),
        db_path=_screening_db,
    )
    return _screening_db


def _seed_failed_batch(db_path, trade_date: str = _FAILED_DATE) -> None:
    record_screening_batch(
        trade_date,
        status="failed",
        error="RuntimeError: LLM 网关整体不可用",
        started_at=f"{trade_date}T15:30:00",
        finished_at=f"{trade_date}T15:30:05",
        db_path=db_path,
    )


# ── pool 端点（S4） ────────────────────────────────────────────────────────


def test_pool_endpoint_returns_latest_success_pool(pool_seed) -> None:
    body = TestClient(app).get("/api/screening/pool").json()

    assert body["batch_date"] == _AS_OF
    assert body["batch_status"] == "ok"
    assert body["stale"] is False
    assert [row["symbol"] for row in body["pool"]] == [f"60000{index}" for index in range(1, 6)]
    first = body["pool"][0]
    assert first["rank"] == 1 and first["consecutive_days"] == 4
    assert first["l3_verdict"] == "keep" and first["l3_reason"] == "无风险事件1"
    assert first["factor_contribs"][0]["factor_id"] == 7
    assert body["queue"] == []


def test_pool_endpoint_without_success_returns_empty(_screening_db) -> None:
    body = TestClient(app).get("/api/screening/pool").json()

    assert body["batch_date"] is None
    assert body["batch_status"] is None
    assert body["stale"] is False
    assert body["pool"] == []


def test_pool_endpoint_stale_after_failed_batch_keeps_last_success(pool_seed) -> None:
    """spec 场景：当日批次 failed → 页面仍展示最近成功批次并标注其日期 + stale 徽标。"""
    _seed_failed_batch(pool_seed)

    body = TestClient(app).get("/api/screening/pool").json()

    assert body["batch_status"] == "failed"
    assert body["stale"] is True
    assert body["batch_date"] == _AS_OF  # 最近成功批次日期（非失败日）
    assert len(body["pool"]) == 5


# ── status 端点（S4，永不 500） ────────────────────────────────────────────


def test_status_endpoint_fresh_after_success(pool_seed) -> None:
    body = TestClient(app).get("/api/screening/status").json()

    assert body == {
        "latest_success_date": _AS_OF,
        "failed_date": None,
        "message": None,
        "stale": False,
    }


def test_status_endpoint_reports_failure_with_message(pool_seed) -> None:
    _seed_failed_batch(pool_seed)

    body = TestClient(app).get("/api/screening/status").json()

    assert body["stale"] is True
    assert body["failed_date"] == _FAILED_DATE
    assert body["latest_success_date"] == _AS_OF
    assert body["message"] == "选股批次失败（最近成功: 2026-10-13）"


def test_status_endpoint_never_500_on_read_error(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr("finance_agent.api.latest_screening_batch", _boom)

    resp = TestClient(app).get("/api/screening/status")

    assert resp.status_code == 200
    assert resp.json() == {
        "latest_success_date": None,
        "failed_date": None,
        "message": None,
        "stale": False,
    }


# ── analyze 端点（S3/S4） ──────────────────────────────────────────────────


def test_analyze_endpoint_409_when_pool_empty(_screening_db) -> None:
    resp = TestClient(app).post("/api/screening/analyze", json={})

    assert resp.status_code == 409
    assert resp.json()["error"] == "screening_pool_empty"


def _stub_sessions(monkeypatch) -> dict[str, str]:
    """可控会话状态桩：默认 running，测试可改终态驱动补位（零网络）。"""
    statuses: dict[str, str] = {}
    monkeypatch.setattr(
        session_store,
        "get_session",
        lambda session_id: {"status": statuses.get(session_id, "running")},
    )
    return statuses


def _stub_pipeline_start(monkeypatch) -> list[str]:
    """PipelineRunner.start 桩：只记录 session_id，不真跑管线（零 LLM）。"""
    import finance_agent.api as api_module

    started: list[str] = []
    monkeypatch.setattr(
        api_module.PipelineRunner,
        "start",
        lambda session_id, event_source, snapshot, loop=None: started.append(session_id),
    )
    return started


def test_analyze_endpoint_caps_concurrency_at_two_and_queues_rest(pool_seed, monkeypatch) -> None:
    """并发上限 2：5 只标的全部建会话，但**只有补位者启动分析**（2 running + 3 queued）。"""
    started = _stub_pipeline_start(monkeypatch)
    _stub_sessions(monkeypatch)

    body = TestClient(app).post("/api/screening/analyze", json={}).json()

    assert [item["state"] for item in body["started"]] == ["running", "running"]
    assert [item["state"] for item in body["queued"]] == ["queued", "queued", "queued"]
    symbols = {item["symbol"] for item in body["started"] + body["queued"]}
    assert symbols == {f"60000{index}" for index in range(1, 6)}
    assert len(body["started"] + body["queued"]) == 5  # 每个标的独立 session
    session_ids = {item["session_id"] for item in body["started"] + body["queued"]}
    assert len(session_ids) == 5
    assert len(started) == 2  # 仅补位者真启动（并发约束的是真实在跑的分析）
    assert set(started) == {item["session_id"] for item in body["started"]}


def test_analyze_endpoint_promotes_next_when_session_completes(pool_seed, monkeypatch) -> None:
    """完成态补位：第一只 completed → 启动第三只，全程 running ≤ 2、启动数 ≤ 3。"""
    started = _stub_pipeline_start(monkeypatch)
    statuses = _stub_sessions(monkeypatch)

    with TestClient(app) as client:
        body = client.post("/api/screening/analyze", json={}).json()
        first_session = body["started"][0]["session_id"]
        statuses[first_session] = "completed"

        deadline = time.time() + 5.0
        while time.time() < deadline:
            snapshot = client.get("/api/screening/pool").json()["queue"]
            states = [item["state"] for item in snapshot]
            if states.count("done") == 1 and states.count("running") == 2 and len(started) == 3:
                break
            time.sleep(0.02)
        else:  # pragma: no cover - 超时诊断
            pytest.fail(f"队列未在超时内补位：{snapshot}，已启动 {started}")

        assert states.count("queued") == 2  # 5 = 1 done + 2 running + 2 queued
        assert len(started) == 3 and len(set(started)) == 3  # 补位启动且不重复启动
        queued_ids = {item["session_id"] for item in snapshot if item["state"] == "queued"}
        assert queued_ids.isdisjoint(set(started))  # 排队者尚未启动
        assert all(item["session_id"] in started or item["state"] == "queued" for item in snapshot)


def test_analyze_endpoint_queue_visible_in_pool_payload(pool_seed, monkeypatch) -> None:
    _stub_pipeline_start(monkeypatch)
    _stub_sessions(monkeypatch)
    queue = AnalyzeQueue(max_concurrency=2, poll_interval=0.01)
    import finance_agent.api as api_module

    monkeypatch.setattr(api_module, "_analyze_queue", queue)

    with TestClient(app) as client:
        client.post("/api/screening/analyze", json={})
        body = client.get("/api/screening/pool").json()

    assert [item["state"] for item in body["queue"]] == [
        "running",
        "running",
        "queued",
        "queued",
        "queued",
    ]
    assert [item["symbol"] for item in body["queue"]] == [f"60000{index}" for index in range(1, 6)]


# ── 队列（S3） ─────────────────────────────────────────────────────────────


def test_queue_rejects_non_positive_concurrency() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        AnalyzeQueue(max_concurrency=0)


async def _wait_states(queue: AnalyzeQueue, expected: list[str], timeout: float = 5.0) -> list[str]:
    deadline = asyncio.get_running_loop().time() + timeout
    states = [item["state"] for item in queue.snapshot()]
    while asyncio.get_running_loop().time() < deadline:
        states = [item["state"] for item in queue.snapshot()]
        if states == expected:
            break
        await asyncio.sleep(0.02)
    return states


async def test_queue_promotes_fifo_and_marks_terminal_states(monkeypatch) -> None:
    """终态判定：completed → done；failed/interrupted → failed；补位 FIFO。"""
    statuses = {"s1": "running", "s2": "running", "s3": "running"}
    monkeypatch.setattr(session_store, "get_session", lambda sid: {"status": statuses[sid]})
    queue = AnalyzeQueue(max_concurrency=2, poll_interval=0.01)
    for symbol, session_id in (("A", "s1"), ("B", "s2"), ("C", "s3")):
        queue.enqueue(symbol, session_id)

    assert [item["state"] for item in queue.snapshot()] == ["running", "running", "queued"]

    statuses["s1"] = "completed"
    assert await _wait_states(queue, ["done", "running", "running"]) == [
        "done",
        "running",
        "running",
    ]

    statuses["s2"] = "interrupted"
    assert await _wait_states(queue, ["done", "failed", "running"]) == [
        "done",
        "failed",
        "running",
    ]

    statuses["s3"] = "failed"
    assert await _wait_states(queue, ["done", "failed", "failed"]) == [
        "done",
        "failed",
        "failed",
    ]


async def test_queue_starter_launches_only_promoted_entries(monkeypatch) -> None:
    """补位即启动：``starter`` 收到 ``(symbol, session_id)`` 且只在补位时调用；
    启动失败 → 该条目 failed 并让位（不占并发位、不阻断后续补位）。"""
    statuses = {f"s{index}": "running" for index in range(1, 5)}
    monkeypatch.setattr(session_store, "get_session", lambda sid: {"status": statuses[sid]})
    launched: list[tuple[str, str]] = []

    def starter(symbol: str, session_id: str) -> None:
        if symbol == "C":
            raise RuntimeError("启动失败")
        launched.append((symbol, session_id))

    queue = AnalyzeQueue(max_concurrency=2, poll_interval=0.01, starter=starter)
    queue.enqueue("A", "s1")
    queue.enqueue("B", "s2")
    queue.enqueue("C", "s3")  # 排队（无空位）
    assert launched == [("A", "s1"), ("B", "s2")]

    queue.enqueue("D", "s4")
    statuses["s1"] = "completed"  # A 终态 → C 补位（启动失败）→ D 补位（成功）

    assert await _wait_states(queue, ["done", "running", "failed", "running"]) == [
        "done",
        "running",
        "failed",
        "running",
    ]
    assert launched == [("A", "s1"), ("B", "s2"), ("D", "s4")]


async def test_queue_seed_sets_display_states(monkeypatch) -> None:
    """seed 造数（E2E 徽标态）：逐条写入给定状态，不触发补位。"""
    queue = AnalyzeQueue(max_concurrency=2)
    added = queue.seed(
        [
            {"symbol": "600519", "session_id": "s1", "state": "done"},
            {"symbol": "000001", "session_id": "s2", "state": "queued"},
        ]
    )
    assert added == 2
    assert queue.snapshot() == [
        {"symbol": "600519", "session_id": "s1", "state": "done"},
        {"symbol": "000001", "session_id": "s2", "state": "queued"},
    ]
    with pytest.raises(ValueError, match="状态"):
        queue.seed([{"symbol": "300750", "session_id": "s3", "state": "bogus"}])


# ── seed 扩展（S6，TESTING=1） ─────────────────────────────────────────────


@pytest.fixture
def testing_client(monkeypatch):
    """TESTING=1 下 reload 出带 /api/test/seed 的客户端（仓库既有模式）。"""
    monkeypatch.setenv("TESTING", "1")
    import finance_agent.api as api_module

    importlib.reload(api_module)
    try:
        yield TestClient(api_module.app)
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)


def test_seed_placeholder_untouched_for_symbol_payload(testing_client) -> None:
    """既有 {symbol} 早退路径不受 screening 扩展影响（占位响应逐字不变）。"""
    resp = testing_client.post("/api/test/seed", json={"symbol": "300308"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "mode": "testing"}


def test_seed_screening_batch_and_pool_roundtrip(testing_client, _screening_db) -> None:
    """seed screening.batch/pool → 池端点可见（写的是 env 指向的库）。"""
    resp = testing_client.post(
        "/api/test/seed",
        json={
            "screening": {
                "batch": {
                    "trade_date": _AS_OF,
                    "status": "ok",
                    "started_at": "2026-10-13T15:30:00",
                    "finished_at": "2026-10-13T15:33:00",
                    "l1_passed": 9,
                    "l2_top": 5,
                    "l3_keep": 4,
                },
                "pool": _pool_rows(2),
            }
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "mode": "testing"}

    body = TestClient(app).get("/api/screening/pool").json()
    assert body["batch_date"] == _AS_OF and body["batch_status"] == "ok"
    assert [row["symbol"] for row in body["pool"]] == ["600001", "600002"]
    assert len(list_selection_pool(_AS_OF)) == 2  # 不带路径：env 指向的库


def test_seed_screening_failed_batch_for_banner(testing_client, _screening_db) -> None:
    """seed 造失败批次 → status 端点 stale + 文案（E2E 横幅造态）。"""
    testing_client.post(
        "/api/test/seed",
        json={
            "screening": {
                "batch": {
                    "trade_date": _FAILED_DATE,
                    "status": "failed",
                    "error": "SnapshotNotFreshError: 快照最新数据日期为 2026-10-13",
                    "started_at": "2026-10-14T15:30:00",
                    "finished_at": "2026-10-14T15:30:01",
                }
            }
        },
    )

    body = TestClient(app).get("/api/screening/status").json()
    assert body["stale"] is True
    assert body["failed_date"] == _FAILED_DATE
    assert body["message"] == "选股批次失败（最近成功: 无）"


def test_seed_screening_queue_and_reset(testing_client, pool_seed) -> None:
    """seed screening.queue 造徽标态；screening_reset 清四表（含 l2/triage）。"""
    testing_client.post(
        "/api/test/seed",
        json={
            "screening": {
                "queue": [
                    {"symbol": "600001", "session_id": "s1", "state": "running"},
                    {"symbol": "600002", "session_id": "s2", "state": "queued"},
                ]
            }
        },
    )
    body = TestClient(app).get("/api/screening/pool").json()
    assert [item["state"] for item in body["queue"]] == ["running", "queued"]

    assert len(list_selection_pool(_AS_OF)) == 5  # reset 前有池行
    resp = testing_client.post("/api/test/seed", json={"screening_reset": True})

    assert resp.status_code == 200
    assert list_selection_pool(_AS_OF) == []
    body = TestClient(app).get("/api/screening/pool").json()
    assert body["batch_date"] is None and body["pool"] == []


def test_screening_reset_discards_in_process_queue(testing_client, _screening_db) -> None:
    """screening_reset 同时丢弃进程内队列单例（评审修正）：seed/累积的徽标条目在库外
    （内存），只清库会让 reset 后的页面仍显示旧队列条目。"""
    testing_client.post(
        "/api/test/seed",
        json={"screening": {"queue": [{"symbol": "600001", "session_id": "s1", "state": "done"}]}},
    )
    assert [
        item["state"] for item in TestClient(app).get("/api/screening/pool").json()["queue"]
    ] == ["done"]

    resp = testing_client.post("/api/test/seed", json={"screening_reset": True})

    assert resp.status_code == 200
    assert TestClient(app).get("/api/screening/pool").json()["queue"] == []


# ── 分析前时效性校验接线（spec「信号时效性校验」：T2 校验接入 analyze 端点） ──


def test_snapshot_db_path_pinned_to_tmp_not_cwd_default(pool_seed, tmp_path) -> None:
    """回归（评审修正 1）：不显式覆盖的用例，快照库 = 逐用例 tmp 注入。

    不钉死时校验读 cwd 默认 ``data/market_snapshot.db``——该开发库一旦有行，本文件
    未显式覆盖的 analyze 用例会真正进入事件装配（WebSearch/litellm 网络调用）。
    """
    pinned = Path(os.environ["SNAPSHOT_DB_PATH"]).resolve()
    assert pinned.parent == tmp_path.resolve()
    assert pinned != Path("data/market_snapshot.db").resolve()
    assert latest_snapshot_date() is None  # tmp 库无行 → 校验按「无最新可得状态」跳过


def _install_snapshot(
    monkeypatch,
    tmp_path,
    *,
    as_of: str = _AS_OF,
    suspended: frozenset[str] = frozenset(),
    limit_locked: frozenset[str] = frozenset(),
) -> None:
    """造快照库（SNAPSHOT_DB_PATH → tmp）：池内 5 只符号逐行状态位；日历含前一交易日。"""
    path = tmp_path / "market_snapshot.db"
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(path))
    rows = [
        {
            "code": f"60000{index}",
            "name": f"合成{index}",
            "is_suspended": 1 if f"60000{index}" in suspended else 0,
            "limit_up_sealed": 1 if f"60000{index}" in limit_locked else 0,
            "limit_down_sealed": 0,
        }
        for index in range(1, 6)
    ]
    write_snapshot_rows(as_of, rows, db_path=path)
    set_meta_many(
        {"last_success_date": as_of, "trade_calendar": json.dumps([_PRIOR_TRADE_DATE, as_of])},
        db_path=path,
    )


def test_analyze_endpoint_removes_suspended_symbol_and_starts_others(
    pool_seed, monkeypatch, tmp_path
) -> None:
    """spec 场景「盘前停牌移除」：Z 停牌 → removed(SUSPENDED) 且不建会话，其余照常入队。"""
    _install_snapshot(monkeypatch, tmp_path, suspended=frozenset({"600001"}))
    monkeypatch.setattr("finance_agent.screening.freshness.fetch_key_events", lambda *args, **kwargs: [])
    started = _stub_pipeline_start(monkeypatch)
    _stub_sessions(monkeypatch)

    body = TestClient(app).post("/api/screening/analyze", json={}).json()

    assert body["removed"] == [{"symbol": "600001", "reason": "SUSPENDED", "detail": "停牌"}]
    assert [item["symbol"] for item in body["started"]] == ["600002", "600003"]
    assert [item["symbol"] for item in body["queued"]] == ["600004", "600005"]
    assert len(started) == 2  # 完好标的的并发上限语义不受校验影响
    # 被移除者不建会话（不产生孤儿 session）
    assert all("600001" not in item["display_name"] for item in session_store.list_sessions())
    # 移除原因审计行落库：基准日 = 快照日
    audit = list_screening_removals(db_path=pool_seed)
    assert [(row["trade_date"], row["symbol"], row["reason"], row["detail"]) for row in audit] == [
        (_AS_OF, "600001", "SUSPENDED", "停牌")
    ]
    assert audit[0]["removed_at"]  # 审计时间戳不空


def test_analyze_endpoint_flags_limit_locked_and_new_event(
    pool_seed, monkeypatch, tmp_path
) -> None:
    """LIMIT_LOCKED / NEW_EVENT 经端点透传（事件装配接线；桩零网络）。"""
    _install_snapshot(monkeypatch, tmp_path, limit_locked=frozenset({"600002"}))
    monkeypatch.setattr(
        "finance_agent.screening.freshness.fetch_key_events",
        lambda symbol, *args, **kwargs: (
            [{"date": _AS_OF, "title": "重大合同"}] if symbol == "600003" else []
        ),
    )
    _stub_pipeline_start(monkeypatch)
    _stub_sessions(monkeypatch)

    body = TestClient(app).post("/api/screening/analyze", json={}).json()

    assert body["removed"] == [
        {"symbol": "600002", "reason": "LIMIT_LOCKED", "detail": "一字涨停封板"},
        {"symbol": "600003", "reason": "NEW_EVENT", "detail": "新事件 2026-10-13：重大合同"},
    ]
    assert [item["symbol"] for item in body["started"]] == ["600001", "600004"]
    assert [item["symbol"] for item in body["queued"]] == ["600005"]
    audit = list_screening_removals(db_path=pool_seed)
    assert [(row["symbol"], row["reason"]) for row in audit] == [
        ("600002", "LIMIT_LOCKED"),
        ("600003", "NEW_EVENT"),
    ]


def test_analyze_endpoint_skips_freshness_without_snapshot_state(
    pool_seed, monkeypatch, tmp_path, caplog
) -> None:
    """无快照数据（无「最新可得状态」可核对）→ 不臆造移除、不触达事件装配；
    跳过记 **WARNING**（spec 重校验被跳过必须可观测，非静默 info）。"""
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "never-written.db"))

    def _must_not_fetch(*args, **kwargs):
        raise AssertionError("无快照状态时不得触达事件装配")

    monkeypatch.setattr("finance_agent.screening.freshness.fetch_key_events", _must_not_fetch)
    _stub_pipeline_start(monkeypatch)
    _stub_sessions(monkeypatch)

    with caplog.at_level(logging.INFO, logger="finance_agent.screening.freshness"):
        body = TestClient(app).post("/api/screening/analyze", json={}).json()

    assert body["removed"] == []
    assert len(body["started"] + body["queued"]) == 5
    assert list_screening_removals(db_path=pool_seed) == []
    skips = [record for record in caplog.records if "时效性校验跳过" in record.getMessage()]
    assert skips and all(record.levelno >= logging.WARNING for record in skips)


def test_analyze_endpoint_skips_and_warns_when_snapshot_older_than_batch(
    pool_seed, monkeypatch, tmp_path, caplog
) -> None:
    """跳过路径 2：快照最新日期早于池批次日（无更新状态可核对）→ 不移除 + WARNING。"""
    path = tmp_path / "older_snapshot.db"
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(path))
    write_snapshot_rows(
        _PRIOR_TRADE_DATE,
        [
            {
                "code": f"60000{index}",
                "name": f"合成{index}",
                "is_suspended": 0,
                "limit_up_sealed": 0,
                "limit_down_sealed": 0,
            }
            for index in range(1, 6)
        ],
        db_path=path,
    )

    def _must_not_fetch(*args, **kwargs):
        raise AssertionError("快照无更新状态时不得触达事件装配")

    monkeypatch.setattr("finance_agent.screening.freshness.fetch_key_events", _must_not_fetch)
    _stub_pipeline_start(monkeypatch)
    _stub_sessions(monkeypatch)

    with caplog.at_level(logging.INFO, logger="finance_agent.screening.freshness"):
        body = TestClient(app).post("/api/screening/analyze", json={}).json()

    assert body["removed"] == []
    assert len(body["started"] + body["queued"]) == 5
    skips = [record for record in caplog.records if "时效性校验跳过" in record.getMessage()]
    assert skips and all(record.levelno >= logging.WARNING for record in skips)


def test_analyze_endpoint_reads_snapshot_through_env_path(pool_seed, monkeypatch, tmp_path) -> None:
    """库路径调用期读 SNAPSHOT_DB_PATH（导入期冻结会让 E2E/env 注入失效）。"""
    _install_snapshot(monkeypatch, tmp_path, suspended=frozenset({"600005"}))
    monkeypatch.setattr("finance_agent.screening.freshness.fetch_key_events", lambda *args, **kwargs: [])
    _stub_pipeline_start(monkeypatch)
    _stub_sessions(monkeypatch)

    body = TestClient(app).post("/api/screening/analyze", json={}).json()

    assert [item["symbol"] for item in body["removed"]] == ["600005"]


# ── 降级披露（add-screening-empty-library-fallback T4） ─────────────────────


def _seed_fallback_batch(db_path) -> None:
    record_screening_batch(
        _AS_OF,
        status="ok",
        started_at="2026-10-13T15:30:00",
        finished_at="2026-10-13T15:33:00",
        l1_passed=20,
        l2_top=10,
        l3_keep=3,
        pool_rows=_pool_rows(3),
        selection_mode="fallback",
        db_path=db_path,
    )


def test_pool_payload_discloses_fallback_mode(_screening_db) -> None:
    """降级批次：池载荷含 selection_mode=fallback 与「非因子信号」披露文案。"""
    _seed_fallback_batch(_screening_db)
    body = TestClient(app).get("/api/screening/pool").json()

    assert body["selection_mode"] == "fallback"
    assert "非因子信号" in body["notice"]


def test_pool_payload_factor_mode_has_no_notice(pool_seed) -> None:
    body = TestClient(app).get("/api/screening/pool").json()

    assert body["selection_mode"] == "factor"
    assert body["notice"] is None


def test_batches_endpoint_includes_selection_mode(_screening_db) -> None:
    _seed_fallback_batch(_screening_db)
    rows = TestClient(app).get("/api/screening/batches").json()

    assert rows and rows[0]["selection_mode"] == "fallback"


def test_seed_screening_accepts_selection_mode(testing_client, _screening_db) -> None:
    """E2E 造数通道支持 selection_mode（页面降级横幅造态用）。"""
    resp = testing_client.post(
        "/api/test/seed",
        json={
            "screening": {
                "batch": {
                    "trade_date": _AS_OF,
                    "status": "ok",
                    "selection_mode": "fallback",
                }
            }
        },
    )

    assert resp.status_code == 200
    body = TestClient(app).get("/api/screening/pool").json()
    assert body["selection_mode"] == "fallback"
    assert "非因子信号" in body["notice"]
