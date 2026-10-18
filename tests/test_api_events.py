"""事件观察清单 API 测试（delta add-event-driven-lane T5）。

断言面（spec「事件观察清单与深度分析接线」+「事件观察只读端点」）：
- GET 只读：全字段（预告要素/分诊理由/证据/公告日/状态/session_id）、source 过滤、
  空态 200、零状态写入；
- POST analyze：复用既有分析队列单例（同一并发通道）、关联 session、重复点击幂等
  且 MUST NOT 重复入队、未知 id 404。

零网络：PipelineRunner.start 与会话状态读取以可控桩替换（照 test_api_screening.py 先例）。
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from finance_agent import session_store
from finance_agent.api import app
from finance_agent.outcome.events.model import (
    forecast_key,
    insert_watchlist,
    list_watchlist,
    record_triage,
    upsert_forecasts,
)
from finance_agent.screening.queue import AnalyzeQueue

_NET = "归属于上市公司股东的净利润"


@pytest.fixture
def testing_client(monkeypatch):
    """TESTING=1 下 reload 出带 ``/api/test/seed`` 的客户端（仓库既有模式）。"""
    monkeypatch.setenv("TESTING", "1")
    import finance_agent.api as api_module

    importlib.reload(api_module)
    try:
        yield TestClient(api_module.app)
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)


@pytest.fixture(autouse=True)
def _events_db(tmp_path, monkeypatch):
    """事件库路径指向 tmp（env 调用期读取；不得污染开发库 data/events.db）。"""
    path = tmp_path / "events.db"
    monkeypatch.setenv("EVENTS_DB_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def _fresh_analyze_queue(monkeypatch):
    """逐用例重置 api 内队列单例（懒创建；避免跨用例残留）。"""
    import finance_agent.api as api_module

    monkeypatch.setattr(api_module, "_analyze_queue", None)


def _seed_item(
    db,
    *,
    code: str = "600519",
    announce: str = "2025-04-15",
    verdict: str = "outperform",
    source: str = "earnings",
    reason: str = "归母净利润预增 60%~90%",
    evidence: list[dict] | None = None,
) -> int:
    row = {
        "code": code,
        "name": f"公司{code}",
        "report_period": "2025-03-31",
        "source_record_id": f"{_NET}|{announce}",
        "forecast_type": "预增",
        "change_low": 60.0,
        "change_high": 90.0,
        "announce_date": announce,
        "available_at": announce,
        "source": "eastmoney",
    }
    upsert_forecasts([row], db_path=db)
    key = forecast_key(row)
    record_triage(
        key,
        verdict=verdict,
        reason=reason,
        evidence=evidence or [{"field": "change_low", "value": 60.0}],
        model="stub-model",
        prompt_fingerprint="event_triage@3",
        db_path=db,
    )
    item_id = insert_watchlist(key, source=source, db_path=db)
    assert item_id is not None
    return item_id


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


def _stub_sessions(monkeypatch) -> dict[str, str]:
    statuses: dict[str, str] = {}
    monkeypatch.setattr(
        session_store,
        "get_session",
        lambda session_id: {"status": statuses.get(session_id, "running")},
    )
    return statuses


class TestWatchlistEndpoint:
    def test_returns_full_items(self, _events_db) -> None:
        _seed_item(_events_db)
        resp = TestClient(app).get("/api/events/watchlist")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        item = items[0]
        # 预告要素 + 分诊理由/证据 + 公告日 + 状态/session
        assert item["code"] == "600519" and item["name"] == "公司600519"
        assert item["report_period"] == "2025-03-31"
        assert item["forecast_type"] == "预增"
        assert item["change_low"] == 60.0 and item["change_high"] == 90.0
        assert item["announce_date"] == "2025-04-15"
        assert item["verdict"] == "outperform"
        assert item["reason"] == "归母净利润预增 60%~90%"
        assert item["evidence"] == [{"field": "change_low", "value": 60.0}]
        assert item["status"] == "pending" and item["session_id"] is None
        assert item["source"] == "earnings"

    def test_empty_state_returns_200_empty_list(self, _events_db) -> None:
        resp = TestClient(app).get("/api/events/watchlist")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    def test_source_filter(self, _events_db) -> None:
        _seed_item(_events_db, code="600519", source="earnings")
        _seed_item(_events_db, code="000001", announce="2025-04-16", source="other")
        resp = TestClient(app).get("/api/events/watchlist?source=earnings")
        assert [item["code"] for item in resp.json()["items"]] == ["600519"]
        assert TestClient(app).get("/api/events/watchlist").json()["items"].__len__() == 2

    def test_endpoint_is_read_only(self, _events_db) -> None:
        """重复请求载荷一致且状态零改写（spec「端点零写入」）。"""
        _seed_item(_events_db)
        client = TestClient(app)
        first = client.get("/api/events/watchlist").json()
        before = json.dumps(list_watchlist(db_path=_events_db), ensure_ascii=False, sort_keys=True)
        second = client.get("/api/events/watchlist").json()
        after = json.dumps(list_watchlist(db_path=_events_db), ensure_ascii=False, sort_keys=True)
        assert first == second
        assert before == after

    def test_corrupt_evidence_is_null_with_warning(self, _events_db, caplog) -> None:
        """证据 JSON 损坏：如实返回 None（不伪装成 [] 或编造），并留 warning。"""
        import logging
        import sqlite3

        item_id = _seed_item(_events_db)
        conn = sqlite3.connect(_events_db)
        try:
            conn.execute("UPDATE event_triage SET evidence_json='{坏'")
            conn.commit()
        finally:
            conn.close()
        with caplog.at_level(logging.WARNING):
            item = TestClient(app).get("/api/events/watchlist").json()["items"][0]
        assert item["id"] == item_id
        assert item["evidence"] is None
        assert "证据" in caplog.text


class TestAnalyzeEndpoint:
    def test_starts_analysis_and_links_session(self, _events_db, monkeypatch) -> None:
        started = _stub_pipeline_start(monkeypatch)
        _stub_sessions(monkeypatch)
        item_id = _seed_item(_events_db)

        with TestClient(app) as client:
            resp = client.post(f"/api/events/watchlist/{item_id}/analyze")
        assert resp.status_code == 200
        body = resp.json()

        assert body["id"] == item_id
        assert body["session_id"]
        assert body["status"] == "analyzing"
        assert body["queue_state"] in ("running", "queued")
        assert body["already_started"] is False

        items = list_watchlist(db_path=_events_db)
        assert items[0]["status"] == "analyzing"
        assert items[0]["session_id"] == body["session_id"]

        # 会话真实建立（复用既有建会话段），入队即补位启动
        assert session_store.get_session(body["session_id"]) is not None
        assert started == [body["session_id"]]

    def test_repeated_click_is_idempotent_and_never_reenqueues(
        self, _events_db, monkeypatch
    ) -> None:
        started = _stub_pipeline_start(monkeypatch)
        _stub_sessions(monkeypatch)
        item_id = _seed_item(_events_db)

        with TestClient(app) as client:
            first = client.post(f"/api/events/watchlist/{item_id}/analyze").json()
            second = client.post(f"/api/events/watchlist/{item_id}/analyze")
        assert second.status_code == 200
        body = second.json()
        assert body["already_started"] is True
        assert body["session_id"] == first["session_id"]
        assert body["status"] == "analyzing"

        import finance_agent.api as api_module

        queue = api_module._get_analyze_queue()
        assert len(queue.snapshot()) == 1  # MUST NOT 重复入队
        assert len(started) == 1  # 且不重复启动

    def test_reuses_existing_queue_singleton(self, _events_db, monkeypatch) -> None:
        """复用既有分析队列单例（同并发通道）：预置队列条目保留，新条目追加其后。"""
        _stub_pipeline_start(monkeypatch)
        _stub_sessions(monkeypatch)
        import finance_agent.api as api_module

        queue = AnalyzeQueue(max_concurrency=2, poll_interval=0.01, starter=lambda s, sid: None)
        queue.seed([{"symbol": "000001", "session_id": "sess-prev", "state": "running"}])
        monkeypatch.setattr(api_module, "_analyze_queue", queue)

        item_id = _seed_item(_events_db)
        with TestClient(app) as client:
            body = client.post(f"/api/events/watchlist/{item_id}/analyze").json()

        snapshot = queue.snapshot()
        assert [entry["session_id"] for entry in snapshot][0] == "sess-prev"
        assert snapshot[1]["session_id"] == body["session_id"]
        assert queue.max_concurrency == 2  # 并发上限同源（不加新通道）

    def test_session_created_event_carries_source_event(self, _events_db, monkeypatch) -> None:
        """spec「来源标记 event」：``session_created`` 载荷带 ``source=event``。"""
        _stub_pipeline_start(monkeypatch)
        _stub_sessions(monkeypatch)
        item_id = _seed_item(_events_db)

        with TestClient(app) as client:
            body = client.post(f"/api/events/watchlist/{item_id}/analyze").json()

        events = [
            json.loads(row["event_json"])
            for row in session_store.list_session_events(body["session_id"])
        ]
        created = [event for event in events if event.get("type") == "session_created"]
        assert len(created) == 1
        assert created[0]["source"] == "event"

    def test_unknown_id_404(self, _events_db) -> None:
        resp = TestClient(app).post("/api/events/watchlist/999/analyze")
        assert resp.status_code == 404


class TestEventsSeed:
    """TESTING 造数：``events`` 块 + ``events_reset``（delta add-event-driven-lane）。

    守卫链键（api.py ``not ... and`` 链）：缺键会让 seed 请求被静默早退吞掉——
    本组用例同时是守卫链的回归（seed 后经只读端点核验真实落库）。
    """

    def _seed_payload(self, **extra) -> dict:
        return {
            "events": {
                "forecasts": [
                    {
                        "code": "600519",
                        "name": "贵州茅台",
                        "report_period": "2025-03-31",
                        "source_record_id": "归属于上市公司股东的净利润|2025-04-15",
                        "forecast_type": "预增",
                        "change_low": 60.0,
                        "change_high": 90.0,
                        "announce_date": "2025-04-15",
                    }
                ],
                "triage": [
                    {
                        "forecast_key": "600519|2025-03-31|归属于上市公司股东的净利润|2025-04-15",
                        "verdict": "outperform",
                        "reason": "预增 60%~90%",
                        "evidence": [{"field": "change_low", "value": 60.0}],
                        "model": "seed",
                        "prompt_fingerprint": "event_triage@1",
                    }
                ],
                "watchlist": [
                    {
                        "forecast_key": "600519|2025-03-31|归属于上市公司股东的净利润|2025-04-15",
                        "source": "earnings",
                    }
                ],
                "journal": [
                    {
                        "forecast_key": "600519|2025-03-31|归属于上市公司股东的净利润|2025-04-15",
                        "symbol": "600519.SH",
                        "entry_date": "2025-04-16",
                        "entry_price": 100.0,
                        "horizon_days": 5,
                        "status": "settled",
                        "settle_date": "2025-04-23",
                        "settle_price": 105.0,
                        "raw_return": 0.05,
                    }
                ],
            },
            **extra,
        }

    def test_seed_block_lands_rows_and_guard_chain_passes(self, _events_db, testing_client) -> None:
        client = testing_client
        resp = client.post("/api/test/seed", json=self._seed_payload())
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"  # 未被守卫链早退吞掉

        items = client.get("/api/events/watchlist").json()["items"]
        assert len(items) == 1
        assert items[0]["verdict"] == "outperform" and items[0]["source"] == "earnings"

        from finance_agent.outcome.events.model import list_settled_journal

        settled = list_settled_journal(db_path=_events_db)
        assert len(settled) == 1 and settled[0]["raw_return"] == 0.05

    def test_events_reset_clears_four_tables(self, _events_db, testing_client) -> None:
        client = testing_client
        client.post("/api/test/seed", json=self._seed_payload())
        resp = client.post("/api/test/seed", json={"events_reset": True})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert client.get("/api/events/watchlist").json() == {"items": []}
