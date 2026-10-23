"""operator 派单服务：建 analysis 会话并启动管线，返回回执。"""

from __future__ import annotations

import pytest

import finance_agent.api as api_mod
from finance_agent import session_store


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    return tmp_path / "t.db"


@pytest.mark.asyncio
async def test_dispatch_creates_analysis_session_and_starts_pipeline(isolated_db, monkeypatch):
    started: list[str] = []

    def fake_start(cls, session_id, event_source, initial_snapshot, loop=None):
        started.append(session_id)

    monkeypatch.setattr(api_mod.PipelineRunner, "start", classmethod(fake_start))

    receipt = await api_mod.start_analysis_session_for_operator("600519", "贵州茅台")

    assert receipt["symbol"] == "600519"
    assert receipt["status"] == "running"
    assert receipt["display_name"] == "贵州茅台 深度分析"
    assert started == [receipt["session_id"]]

    session = session_store.get_session(receipt["session_id"])
    assert session["session_type"] == "analysis"
    assert session["status"] == "running"
    assert session["stock_code"] == "600519"
    assert session["chat_history"][0]["role"] == "user"
    assert "600519" in session["chat_history"][0]["content"]


@pytest.mark.asyncio
async def test_dispatch_propagates_llm_config(isolated_db, monkeypatch):
    captured: dict = {}

    def fake_start(cls, session_id, event_source, initial_snapshot, loop=None):
        captured["session_id"] = session_id

    monkeypatch.setattr(api_mod.PipelineRunner, "start", classmethod(fake_start))
    cfg = api_mod.LLMConfig(model="openai/gpt-4o", apiKey="sk-test")

    receipt = await api_mod.start_analysis_session_for_operator(
        "600519", "贵州茅台", api_key="sk-test", llm_config=cfg
    )

    assert captured["session_id"] == receipt["session_id"]


@pytest.mark.asyncio
async def test_dispatch_failure_marks_session_failed(isolated_db, monkeypatch):
    def fake_start(cls, session_id, event_source, initial_snapshot, loop=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(api_mod.PipelineRunner, "start", classmethod(fake_start))

    with pytest.raises(RuntimeError, match="boom"):
        await api_mod.start_analysis_session_for_operator("600519", "贵州茅台")

    sessions = session_store.list_sessions()
    target = [s for s in sessions if s.get("stock_code") == "600519"]
    assert target, "失败派单应留下可审计的会话记录"
    assert target[0]["status"] == "failed"
