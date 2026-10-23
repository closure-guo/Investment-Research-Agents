"""受控写操作工具（add-quick-mode-operator P3）。"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import finance_agent.operator.writes as writes_mod
from finance_agent.operator.confirmation import _reset_for_tests
from finance_agent.operator.writes import make_write_tools


@pytest.fixture(autouse=True)
def _clean():
    _reset_for_tests()
    yield
    _reset_for_tests()


def _tools(session="s1", message=""):
    return make_write_tools(session_id=session, user_message=message)


@pytest.mark.asyncio
async def test_factor_preview_does_not_execute(monkeypatch):
    calls = []

    async def fake_control(desired):
        calls.append(desired)
        return {
            "control": {
                "desired_running": desired,
                "pending": True,
                "honor": "live",
                "honor_eta_seconds": 0,
            }
        }

    monkeypatch.setattr(writes_mod, "_factor_control", fake_control)
    tool = _tools(message="停止因子研发")["set_factor_research_running"]

    out = json.loads(await tool(False))
    assert out["status"] == "confirmation_required"
    assert out["token"]
    assert calls == []


@pytest.mark.asyncio
async def test_factor_requires_user_confirmation(monkeypatch):
    calls = []

    async def fake_control(desired):
        calls.append(desired)
        return {
            "control": {
                "desired_running": desired,
                "pending": True,
                "honor": "live",
                "honor_eta_seconds": 0,
            }
        }

    monkeypatch.setattr(writes_mod, "_factor_control", fake_control)
    tool = _tools(message="停止因子研发")["set_factor_research_running"]

    await tool(False)
    out = json.loads(await tool(False, confirm=True))
    assert out["ok"] is False
    assert calls == []


@pytest.mark.asyncio
async def test_factor_executes_after_confirmation(monkeypatch):
    calls = []

    async def fake_control(desired):
        calls.append(desired)
        return {
            "control": {
                "desired_running": desired,
                "pending": True,
                "honor": "live",
                "honor_eta_seconds": 0,
            }
        }

    monkeypatch.setattr(writes_mod, "_factor_control", fake_control)
    tool = _tools(message="确认执行")["set_factor_research_running"]

    preview = json.loads(await tool(False))
    out = json.loads(await tool(False, confirm=True, token=preview["token"]))
    assert out["ok"] is True
    assert out["desired_running"] is False
    assert out["honor"] == "live"
    assert calls == [False]


@pytest.mark.asyncio
async def test_confirmation_survives_new_agent_run(monkeypatch):
    """预览与确认分属两个 Agent run：服务端按 session+action+params 匹配。"""
    calls = []

    async def fake_control(desired):
        calls.append(desired)
        return {
            "control": {
                "desired_running": desired,
                "pending": True,
                "honor": "live",
                "honor_eta_seconds": 0,
            }
        }

    monkeypatch.setattr(writes_mod, "_factor_control", fake_control)

    first_run = make_write_tools(session_id="s1", user_message="停止因子研发")
    preview = json.loads(await first_run["set_factor_research_running"](False))
    assert preview["status"] == "confirmation_required"

    second_run = make_write_tools(session_id="s1", user_message="确认执行")
    out = json.loads(await second_run["set_factor_research_running"](False, confirm=True))
    assert out["ok"] is True
    assert calls == [False]


@pytest.mark.asyncio
async def test_factor_control_unavailable_is_surfaced(monkeypatch):
    async def fake_control(desired):
        raise HTTPException(status_code=409, detail="control_unavailable")

    monkeypatch.setattr(writes_mod, "_factor_control", fake_control)
    tool = _tools(message="确认执行")["set_factor_research_running"]

    await tool(True)
    out = json.loads(await tool(True, confirm=True))
    assert out["ok"] is False
    assert "control_unavailable" in out["error"]


@pytest.mark.asyncio
async def test_ops_unknown_job_rejected():
    tool = _tools()["run_ops_job"]
    out = json.loads(await tool("not_a_job"))
    assert out["ok"] is False
    assert "未知" in out["error"]


@pytest.mark.asyncio
async def test_ops_job_preview_then_execute(monkeypatch):
    calls = []

    async def fake_run(job_id):
        calls.append(job_id)
        return {"run_id": 7, "status": "ok", "summary": {"ok": True}, "error": None}

    monkeypatch.setattr(writes_mod, "_run_job", fake_run)
    tool = _tools(message="确认执行")["run_ops_job"]

    preview = json.loads(await tool("factor_harvest_daily"))
    assert preview["status"] == "confirmation_required"
    out = json.loads(await tool("factor_harvest_daily", confirm=True, token=preview["token"]))
    assert out["ok"] is True
    assert out["status"] == "ok"
    assert out["run_id"] == 7
    assert calls == ["factor_harvest_daily"]


@pytest.mark.asyncio
async def test_memory_unknown_clause(monkeypatch):
    async def fake_list():
        return []

    monkeypatch.setattr(writes_mod, "_list_clauses", fake_list)
    tool = _tools(message="确认执行")["review_memory_clause"]
    out = json.loads(await tool("missing", "confirm"))
    assert out["ok"] is False
    assert "未知条款" in out["error"]


@pytest.mark.asyncio
async def test_memory_confirm_preview_and_execute(monkeypatch):
    calls = []

    async def fake_list():
        return [{"clause_id": "c1", "text": "不要追高", "status": "pending"}]

    async def fake_set(clause_id, action):
        calls.append((clause_id, action))
        return True

    monkeypatch.setattr(writes_mod, "_list_clauses", fake_list)
    monkeypatch.setattr(writes_mod, "_set_clause", fake_set)
    tool = _tools(message="确认执行")["review_memory_clause"]

    preview = json.loads(await tool("c1", "confirm"))
    assert "不要追高" in preview["preview"]
    out = json.loads(await tool("c1", "confirm", confirm=True, token=preview["token"]))
    assert out["ok"] is True
    assert out["status"] == "confirmed"
    assert calls == [("c1", "confirm")]


@pytest.mark.asyncio
async def test_pool_empty_rejected(monkeypatch):
    async def fake_pool():
        return {"pool": []}

    monkeypatch.setattr(writes_mod, "_pool_payload", fake_pool)
    tool = _tools()["start_pool_analysis"]
    out = json.loads(await tool())
    assert out["ok"] is False
    assert "精选池为空" in out["error"]


@pytest.mark.asyncio
async def test_pool_analysis_after_confirmation(monkeypatch):
    calls = []

    async def fake_pool():
        return {"pool": [{"symbol": "600519"}]}

    async def fake_analyze():
        calls.append(True)
        return {"started": [{"symbol": "600519"}], "queued": [], "removed": []}

    monkeypatch.setattr(writes_mod, "_pool_payload", fake_pool)
    monkeypatch.setattr(writes_mod, "_pool_analyze", fake_analyze)
    tool = _tools(message="确认执行")["start_pool_analysis"]

    preview = json.loads(await tool())
    assert preview["status"] == "confirmation_required"
    out = json.loads(await tool(confirm=True, token=preview["token"]))
    assert out["ok"] is True
    assert out["pool_size"] == 1
    assert calls == [True]
