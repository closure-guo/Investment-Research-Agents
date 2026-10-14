"""语义记忆确认 API 单测（delta add-memory-confirmation T1）。

spec decision-memory「确认操作入口」：
- ``GET /api/memory/clauses?status=pending|confirmed|rejected``（默认 pending）：
  三态过滤，字段含 id / 正文 / 来源 / 提炼时间 / 状态；未知 status → 400（不静默全量返回）；
- ``POST /api/memory/clauses/{id}/confirm|reject``：确认/驳回生效且幂等（重复操作返回
  当前状态而非报错）；未知 id → 404（MUST NOT 静默成功）；
- **未确认不注入回归**：API 只经 semantic 既有门禁读写——确认前条款不在
  ``list_confirmed_clauses``（注入唯一数据源）中，确认后即刻在其中（全路径门禁
  由 tests/memory/test_semantic.py 既有用例覆盖，本文件零修改既有测试语义）；
- TESTING seed：``memory`` 造数（条款 seed，走真实落库路径）+ ``memory_reset``
  清条款表，供 E2E/隔离库使用。

零网络：全部走 TestClient + tmp 库（SESSIONS_DB_PATH 逐例注入；memory 模块
调用期读 env，不落开发库）。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.memory.semantic import (
    list_clauses,
    list_confirmed_clauses,
    store_candidate_clauses,
)

_CLAUSES = "/api/memory/clauses"


@pytest.fixture(autouse=True)
def _memory_db(tmp_path, monkeypatch):
    """语义记忆库路径指向 tmp（语义模块 ``_db_path`` 调用期读 SESSIONS_DB_PATH）。

    与记忆同库的会话/战绩库不受影响：本文件端点只触 memory 模块的读路径。
    """
    path = tmp_path / "sessions.db"
    monkeypatch.setenv("SESSIONS_DB_PATH", str(path))
    return path


def _seed_clauses(
    *, pending: int = 0, confirmed: int = 0, rejected: int = 0, db_path=None
) -> dict[str, list[str]]:
    """按状态造条款：pending 直接 store，confirmed/rejected 经既有状态机置态。"""
    from finance_agent.memory.semantic import confirm_clause, reject_clause

    out: dict[str, list[str]] = {"pending": [], "confirmed": [], "rejected": []}
    for status, count in (("pending", pending), ("confirmed", confirmed), ("rejected", rejected)):
        ids = store_candidate_clauses(
            [f"{status}条款{index}" for index in range(count)], db_path=db_path
        )
        for clause_id in ids:
            if status == "confirmed":
                assert confirm_clause(clause_id, db_path=db_path) is True
            elif status == "rejected":
                assert reject_clause(clause_id, db_path=db_path) is True
        out[status] = ids
    return out


# ── 列表端点：三态过滤 + 字段齐备 ──────────────────────────────────────────


def test_list_defaults_to_pending_with_full_fields(_memory_db) -> None:
    """默认 status=pending；字段含 id / 正文 / 来源 / 提炼时间 / 状态。"""
    ids = _seed_clauses(pending=1, confirmed=1, rejected=1)

    body = TestClient(app).get(_CLAUSES).json()

    assert body["status"] == "pending"
    assert [row["id"] for row in body["clauses"]] == ids["pending"]
    row = body["clauses"][0]
    assert row["text"] == "pending条款0"
    assert row["status"] == "pending"
    assert row["source"] == "monthly_batch"
    assert row["created_at"]  # 提炼时间（落库时刻）


def test_list_filters_three_statuses(_memory_db) -> None:
    """spec 场景：3 pending + 2 confirmed + 1 rejected → 各状态下只回本态。"""
    ids = _seed_clauses(pending=3, confirmed=2, rejected=1)

    for status in ("pending", "confirmed", "rejected"):
        body = TestClient(app).get(_CLAUSES, params={"status": status}).json()
        assert body["status"] == status
        assert [row["id"] for row in body["clauses"]] == ids[status]
        assert {row["status"] for row in body["clauses"]} == {status}


def test_list_unknown_status_rejected(_memory_db) -> None:
    """未知 status → 400（MUST NOT 静默按默认/全量返回，掩盖调用方错误）。"""
    _seed_clauses(pending=1)

    resp = TestClient(app).get(_CLAUSES, params={"status": "archived"})

    assert resp.status_code == 400
    assert "archived" in resp.json()["detail"]


def test_list_empty_db_returns_empty_clauses(_memory_db) -> None:
    """空库（无表）→ 200 + 空列表（fail-open 与既有读路径一致，非 500）。"""
    body = TestClient(app).get(_CLAUSES).json()

    assert body == {"status": "pending", "clauses": []}


# ── 确认 / 驳回：生效、幂等、未知 id 404 ────────────────────────────────────


def test_confirm_moves_clause_into_confirmed_list(_memory_db) -> None:
    """spec 场景：确认后状态转 confirmed，且出现在已确认集合（注入集合）。"""
    ids = _seed_clauses(pending=1)

    resp = TestClient(app).post(f"{_CLAUSES}/{ids['pending'][0]}/confirm")

    assert resp.status_code == 200
    assert resp.json() == {"id": ids["pending"][0], "status": "confirmed"}
    confirmed = TestClient(app).get(_CLAUSES, params={"status": "confirmed"}).json()
    assert [row["id"] for row in confirmed["clauses"]] == ids["pending"]
    assert TestClient(app).get(_CLAUSES).json()["clauses"] == []


def test_reject_moves_clause_into_rejected_list(_memory_db) -> None:
    ids = _seed_clauses(pending=1)

    resp = TestClient(app).post(f"{_CLAUSES}/{ids['pending'][0]}/reject")

    assert resp.status_code == 200
    assert resp.json() == {"id": ids["pending"][0], "status": "rejected"}
    body = TestClient(app).get(_CLAUSES, params={"status": "rejected"}).json()
    assert [row["id"] for row in body["clauses"]] == ids["pending"]


def test_confirm_and_reject_are_idempotent(_memory_db) -> None:
    """幂等：重复操作返回当前状态而非报错（spec 场景「再次确认幂等返回 confirmed」）。"""
    ids = _seed_clauses(pending=2)
    client = TestClient(app)

    assert client.post(f"{_CLAUSES}/{ids['pending'][0]}/confirm").status_code == 200
    again = client.post(f"{_CLAUSES}/{ids['pending'][0]}/confirm")
    assert again.status_code == 200
    assert again.json() == {"id": ids["pending"][0], "status": "confirmed"}

    assert client.post(f"{_CLAUSES}/{ids['pending'][1]}/reject").status_code == 200
    again_reject = client.post(f"{_CLAUSES}/{ids['pending'][1]}/reject")
    assert again_reject.status_code == 200
    assert again_reject.json() == {"id": ids["pending"][1], "status": "rejected"}


def test_unknown_clause_id_returns_404(_memory_db) -> None:
    """spec 场景：未知条款 → 404（MUST NOT 静默成功）。"""
    _seed_clauses(pending=1)
    client = TestClient(app)

    confirm_resp = client.post(f"{_CLAUSES}/c_不存在/confirm")
    reject_resp = client.post(f"{_CLAUSES}/c_不存在/reject")

    assert confirm_resp.status_code == 404
    assert reject_resp.status_code == 404
    # 未知 id 不产生任何副作用（库内仍是原样 pending）
    assert len(list_clauses("pending")) == 1


def test_unknown_clause_id_404_on_empty_db(_memory_db) -> None:
    """空库（无表）同样 404：确认/驳回是写语义，不得静默成功。"""
    resp = TestClient(app).post(f"{_CLAUSES}/c_不存在/confirm")

    assert resp.status_code == 404


# ── 未确认不注入回归保留（既有门禁语义的 API 侧守卫） ─────────────────────


def test_unconfirmed_clause_not_in_injection_source(_memory_db) -> None:
    """未确认条款不在 ``list_confirmed_clauses``（注入唯一数据源）；确认后即刻进入。

    全 prompt 组装路径的零命中门禁由 tests/memory/test_semantic.py 既有用例把守
    （本 change 零修改）；本用例守住 API 不旁路：pending 永不入注入集合。
    """
    ids = _seed_clauses(pending=1)
    clause_id = ids["pending"][0]

    assert list_confirmed_clauses() == []  # 未确认：不在注入集合
    TestClient(app).post(f"{_CLAUSES}/{clause_id}/confirm")
    assert [row["clause_id"] for row in list_confirmed_clauses()] == [clause_id]


# ── TESTING seed 扩展（memory 造数 + memory_reset） ────────────────────────


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


def test_seed_placeholder_untouched_for_memory_free_payload(testing_client) -> None:
    """既有 {symbol} 早退路径不受 memory 扩展影响（占位响应逐字不变）。"""
    resp = testing_client.post("/api/test/seed", json={"symbol": "300308"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "mode": "testing"}


def test_seed_memory_clauses_roundtrip(testing_client, _memory_db) -> None:
    """seed memory.clauses 按状态造条款（走真实落库 + 状态机），端点可见。"""
    resp = testing_client.post(
        "/api/test/seed",
        json={
            "memory": {
                "clauses": [
                    {"text": "待确认条款", "status": "pending"},
                    {"text": "已确认条款", "status": "confirmed"},
                    {"text": "已驳回条款", "status": "rejected"},
                ]
            }
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "mode": "testing"}

    client = TestClient(app)
    for status, text in (
        ("pending", "待确认条款"),
        ("confirmed", "已确认条款"),
        ("rejected", "已驳回条款"),
    ):
        body = client.get(_CLAUSES, params={"status": status}).json()
        assert [row["text"] for row in body["clauses"]] == [text]


def test_seed_memory_reset_clears_clauses(testing_client, _memory_db) -> None:
    """memory_reset 清条款表（E2E 每例前后复位；不影响其它库）。"""
    _seed_clauses(pending=2, confirmed=1)

    resp = testing_client.post("/api/test/seed", json={"memory_reset": True})

    assert resp.status_code == 200
    assert list_clauses() == []
    assert TestClient(app).get(_CLAUSES).json() == {"status": "pending", "clauses": []}


def test_seed_memory_reset_then_seed_is_deterministic(testing_client, _memory_db) -> None:
    """reset 后重 seed 同文本条款不因去重被吞（E2E 串行套件的复位前提）。"""
    payload = {"memory": {"clauses": [{"text": "同文本条款", "status": "pending"}]}}
    testing_client.post("/api/test/seed", json=payload)
    testing_client.post("/api/test/seed", json={"memory_reset": True})
    testing_client.post("/api/test/seed", json=payload)

    body = TestClient(app).get(_CLAUSES).json()
    assert [row["text"] for row in body["clauses"]] == ["同文本条款"]
