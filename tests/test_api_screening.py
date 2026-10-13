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
import time

import pytest
from fastapi.testclient import TestClient

from finance_agent import session_store
from finance_agent.api import app
from finance_agent.screening.queue import AnalyzeQueue
from finance_agent.screening.store import (
    list_selection_pool,
    record_screening_batch,
)

_AS_OF = "2026-10-13"
_FAILED_DATE = "2026-10-14"


@pytest.fixture(autouse=True)
def _screening_db(tmp_path, monkeypatch):
    """screening 库指向 tmp（env 调用期读取；任何用例不得污染开发库）。"""
    path = tmp_path / "screening.db"
    monkeypatch.setenv("SCREENING_DB_PATH", str(path))
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
