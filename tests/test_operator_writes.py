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


# ── ingest_research_report（add-research-report-ingest）───────────────────────


def _fake_ingest(result, calls):
    def fake(**kwargs):
        calls.append(kwargs)
        return dict(result)

    return fake


@pytest.mark.asyncio
async def test_ingest_preview_does_not_execute(monkeypatch):
    calls = []
    monkeypatch.setattr(
        writes_mod,
        "_ingest_document",
        _fake_ingest({"status": "ingested", "doc_key": "d1"}, calls),
    )
    tool = _tools(message="把这份研报入库")["ingest_research_report"]
    out = json.loads(await tool(text="研报正文" * 10))
    assert out["status"] == "confirmation_required"
    assert "字" in out["preview"]
    assert out["token"]
    assert calls == []


@pytest.mark.asyncio
async def test_ingest_executes_after_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(
        writes_mod,
        "_ingest_document",
        _fake_ingest(
            {
                "status": "ingested",
                "doc_key": "d1",
                "char_count": 40,
                "report_key": "rk1",
                "extraction_error": None,
            },
            calls,
        ),
    )
    text = "研报正文" * 10
    preview = json.loads(
        await _tools(message="把这份研报入库")["ingest_research_report"](text=text)
    )
    assert preview["status"] == "confirmation_required"
    execute = _tools(message="确认执行")["ingest_research_report"]
    out = json.loads(await execute(text=text, confirm=True))
    assert out["ok"] is True
    assert out["status"] == "ingested"
    assert out["doc_key"] == "d1"
    assert len(calls) == 1
    assert calls[0]["extract"] is True


@pytest.mark.asyncio
async def test_ingest_text_change_between_calls_rejected(monkeypatch):
    calls = []
    monkeypatch.setattr(
        writes_mod,
        "_ingest_document",
        _fake_ingest({"status": "ingested", "doc_key": "d1"}, calls),
    )
    preview = json.loads(
        await _tools(message="把这份研报入库")["ingest_research_report"](text="第一版正文")
    )
    assert preview["status"] == "confirmation_required"
    execute = _tools(message="确认执行")["ingest_research_report"]
    out = json.loads(await execute(text="被改动过的正文", confirm=True))
    assert out["ok"] is False
    assert calls == []


@pytest.mark.asyncio
async def test_ingest_duplicate_skipped_receipt(monkeypatch):
    calls = []
    monkeypatch.setattr(
        writes_mod,
        "_ingest_document",
        _fake_ingest({"status": "skipped", "doc_key": "d1"}, calls),
    )
    text = "重复正文"
    await _tools(message="投喂")["ingest_research_report"](text=text)
    out = json.loads(
        await _tools(message="确认执行")["ingest_research_report"](text=text, confirm=True)
    )
    assert out["ok"] is True
    assert out["status"] == "skipped"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ingest_empty_text_rejected(monkeypatch):
    calls = []
    monkeypatch.setattr(
        writes_mod,
        "_ingest_document",
        _fake_ingest({"status": "ingested", "doc_key": "d1"}, calls),
    )
    tool = _tools()["ingest_research_report"]
    out = json.loads(await tool(text="   \n  "))
    assert out["ok"] is False
    assert "正文" in out["error"]
    assert calls == []


def test_ingest_no_url_param():
    import inspect

    params = inspect.signature(
        make_write_tools.__wrapped__
        if hasattr(make_write_tools, "__wrapped__")
        else make_write_tools
    ).parameters
    # url 抓取被契约禁止：工具签名不含 url 形参

    tool = _tools()["ingest_research_report"]
    assert "url" not in inspect.signature(tool).parameters
    assert params is not None
