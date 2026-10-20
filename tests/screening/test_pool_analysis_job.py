"""自动选股分析日批入口与主循环桥接测试（Task 3）。"""

from __future__ import annotations

import asyncio
import threading

from finance_agent import api
from finance_agent.outcome.ops.jobs import JOB_FUNCS, JOB_IDS, JOB_LABELS, SCHEDULES
from finance_agent.screening import pool_analysis


def test_registry_and_schedule():
    assert "auto_pool_analysis_daily" in JOB_IDS
    assert JOB_FUNCS["auto_pool_analysis_daily"] is pool_analysis.run_pool_analysis_daily
    assert JOB_LABELS["auto_pool_analysis_daily"] == "自动选股分析"
    assert SCHEDULES["auto_pool_analysis_daily"] == {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 22,
        "timezone": "Asia/Shanghai",
    }


def test_enqueue_returns_false_without_loop(monkeypatch):
    monkeypatch.setattr(api, "_MAIN_LOOP", None)
    assert api._enqueue_pool_analysis("600519", "sid") is False


def test_enqueue_dispatches_to_queue(monkeypatch):
    calls: list[tuple] = []

    class FakeLoop:
        def is_closed(self) -> bool:
            return False

        def call_soon_threadsafe(self, fn, *args) -> None:
            calls.append((fn, args))

    class FakeQueue:
        def enqueue(self, symbol: str, session_id: str) -> None:
            return None

    queue = FakeQueue()
    monkeypatch.setattr(api, "_MAIN_LOOP", FakeLoop())
    monkeypatch.setattr(api, "_get_analyze_queue", lambda: queue)
    assert api._enqueue_pool_analysis("600519", "sid") is True
    assert len(calls) == 1
    fn, args = calls[0]
    assert fn == queue.enqueue
    assert args == ("600519", "sid")


def test_submit_batch_runs_prepare_on_main_loop(monkeypatch):
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run() -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert ready.wait(2.0)
    try:
        monkeypatch.setattr(api, "_MAIN_LOOP", loop)
        monkeypatch.setattr(
            api,
            "prepare_pool_analysis_batch",
            lambda **kwargs: {"status": "ok", "max_symbols": kwargs.get("max_symbols")},
        )
        out = api.submit_pool_analysis_batch(max_symbols=3, timeout=5.0)
        assert out == {"status": "ok", "max_symbols": 3}
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2.0)
        loop.close()


def test_submit_batch_raises_without_loop(monkeypatch):
    monkeypatch.setattr(api, "_MAIN_LOOP", None)
    try:
        api.submit_pool_analysis_batch(timeout=1.0)
    except RuntimeError as exc:
        assert "主事件循环不可用" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")
