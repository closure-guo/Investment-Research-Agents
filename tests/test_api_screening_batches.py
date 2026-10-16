"""选股批次历史单测（delta add-daily-ops-page T1；spec stock-screening delta）。

- ``store.list_batches``：``trade_date`` 倒序、字段齐备（状态/错误/起止时间/各层
  计数/池数）、空库 → 空列表、非法 ``limit`` fail loud；
- ``GET /api/screening/batches``：空库 200 空数组；一成功一失败两批次 → 日期倒序，
  成功行含各层计数与池数、失败行含错误信息逐字；``limit`` 钳制（下界 1 / 上界
  ``MAX_BATCH_HISTORY_LIMIT``）；只读端点（不暴露写操作）；
- TESTING 种子扩展（``screening.batches`` 历史两态）：一成功（含池行 → pool_size）
  一失败（含 error）经 seed 端点落库后端点可见。

库路径经 env 调用期读取（autouse fixture 指向 tmp），与既有
``tests/test_api_screening.py`` 同款隔离纪律。
"""

from __future__ import annotations

import importlib
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.screening.store import (
    MAX_BATCH_HISTORY_LIMIT,
    list_batches,
    record_screening_batch,
)

_OK_DATE = "2026-10-13"
_FAILED_DATE = "2026-10-14"
_HISTORY_FIELDS = {
    "trade_date",
    "status",
    "error",
    "started_at",
    "finished_at",
    "l1_passed",
    "l2_top",
    "l3_keep",
    "pool_size",
    # add-screening-empty-library-fallback：批次字段新增选择模式（factor / fallback）
    "selection_mode",
}


@pytest.fixture(autouse=True)
def _screening_db(tmp_path, monkeypatch):
    """screening 库路径指向 tmp（env 调用期读取；不得污染开发库 data/screening.db）。"""
    path = tmp_path / "screening.db"
    monkeypatch.setenv("SCREENING_DB_PATH", str(path))
    return path


def _pool_rows(count: int = 2) -> list[dict]:
    return [
        {
            "symbol": f"60000{index}",
            "composite_score": 2.0 - 0.1 * index,
            "rank": index,
            "consecutive_days": 3 + index,
            "l3_verdict": "keep",
            "l3_reason": f"无风险事件{index}",
            "factor_contribs": [],
        }
        for index in range(1, count + 1)
    ]


def _seed_ok(db_path, trade_date: str = _OK_DATE, pool_count: int = 2) -> None:
    record_screening_batch(
        trade_date,
        status="ok",
        started_at=f"{trade_date}T15:40:00",
        finished_at=f"{trade_date}T15:43:00",
        l1_passed=20,
        l2_top=9,
        l3_keep=2,
        pool_rows=_pool_rows(pool_count),
        db_path=db_path,
    )


def _seed_failed(db_path, trade_date: str = _FAILED_DATE) -> None:
    record_screening_batch(
        trade_date,
        status="failed",
        error="SnapshotNotFreshError: 快照最新数据日期为 2026-10-09",
        started_at=f"{trade_date}T15:40:00",
        finished_at=f"{trade_date}T15:40:05",
        db_path=db_path,
    )


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


# ── store.list_batches ─────────────────────────────────────────────────────


def test_list_batches_empty_db_returns_empty_list(_screening_db) -> None:
    """空库 → 空列表（建库幂等，不以错误冒充「无历史」）。"""
    assert list_batches(db_path=_screening_db) == []


def test_list_batches_returns_history_fields_newest_first(_screening_db) -> None:
    """两批次（一成功一失败）→ trade_date 倒序 + 字段齐备（失败行错误逐字）。"""
    _seed_ok(_screening_db)
    _seed_failed(_screening_db)

    rows = list_batches(db_path=_screening_db)

    assert [row["trade_date"] for row in rows] == [_FAILED_DATE, _OK_DATE]
    assert set(rows[0]) == _HISTORY_FIELDS
    failed, ok = rows
    # 失败批次：错误逐字；失败日零池行不变量
    assert failed["status"] == "failed"
    assert failed["error"] == "SnapshotNotFreshError: 快照最新数据日期为 2026-10-09"
    assert failed["pool_size"] == 0
    assert failed["l1_passed"] == 0
    # 成功批次：各层计数 + 池数（= 池行数）
    assert ok["status"] == "ok"
    assert ok["error"] is None
    assert (ok["l1_passed"], ok["l2_top"], ok["l3_keep"], ok["pool_size"]) == (20, 9, 2, 2)
    assert ok["started_at"] == f"{_OK_DATE}T15:40:00"
    assert ok["finished_at"] == f"{_OK_DATE}T15:43:00"


def test_list_batches_respects_limit_and_rejects_invalid(_screening_db) -> None:
    """``limit`` 截断取最新 N 条；非法值（0/负/布尔/非整数）fail loud。"""
    for offset in range(3):
        _seed_ok(
            _screening_db,
            trade_date=(date(2026, 10, 5) + timedelta(days=offset)).isoformat(),
        )

    rows = list_batches(limit=2, db_path=_screening_db)
    assert [row["trade_date"] for row in rows] == ["2026-10-07", "2026-10-06"]

    for bad in (0, -1, True, "2"):
        with pytest.raises(ValueError, match="limit"):
            list_batches(limit=bad, db_path=_screening_db)  # type: ignore[arg-type]


# ── GET /api/screening/batches ─────────────────────────────────────────────


def test_batches_endpoint_empty_db_returns_200_empty_list(_screening_db) -> None:
    """空库不报错：200 + 空数组。"""
    resp = TestClient(app).get("/api/screening/batches")

    assert resp.status_code == 200
    assert resp.json() == []


def test_batches_endpoint_returns_two_rows_newest_first(_screening_db) -> None:
    """一成功一失败 → 日期倒序两条；成功含各层计数与池数、失败含错误原文。"""
    _seed_ok(_screening_db)
    _seed_failed(_screening_db)

    body = TestClient(app).get("/api/screening/batches").json()

    assert [row["trade_date"] for row in body] == [_FAILED_DATE, _OK_DATE]
    assert set(body[0]) == _HISTORY_FIELDS
    assert body[0]["status"] == "failed"
    assert body[0]["error"] == "SnapshotNotFreshError: 快照最新数据日期为 2026-10-09"
    ok = body[1]
    assert (ok["l1_passed"], ok["l2_top"], ok["l3_keep"], ok["pool_size"]) == (20, 9, 2, 2)


def test_batches_endpoint_clamps_limit_lower_bound(_screening_db) -> None:
    """``limit`` 下界钳制：0/负数/缺省异常值 → 至少 1 条（不 422、不倾倒全量）。"""
    _seed_ok(_screening_db)
    _seed_failed(_screening_db)

    client = TestClient(app)
    assert len(client.get("/api/screening/batches", params={"limit": 0}).json()) == 1
    assert len(client.get("/api/screening/batches", params={"limit": -5}).json()) == 1
    assert len(client.get("/api/screening/batches", params={"limit": 2}).json()) == 2


def test_batches_endpoint_caps_limit_at_max(_screening_db) -> None:
    """``limit`` 上界钳制：超过 ``MAX_BATCH_HISTORY_LIMIT`` 只回上限条数（防一次倾倒全表）。"""
    for offset in range(MAX_BATCH_HISTORY_LIMIT + 5):
        _seed_ok(
            _screening_db,
            trade_date=(date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
            pool_count=0,
        )

    body = TestClient(app).get("/api/screening/batches", params={"limit": 9999}).json()

    assert len(body) == MAX_BATCH_HISTORY_LIMIT
    newest = (date(2026, 1, 1) + timedelta(days=MAX_BATCH_HISTORY_LIMIT + 4)).isoformat()
    assert body[0]["trade_date"] == newest  # 上限截断仍取最新端


def test_batches_endpoint_is_read_only(_screening_db) -> None:
    """只读端点：写方法不在（spec「SHALL NOT 暴露写操作」）。"""
    client = TestClient(app)
    for method in ("post", "put", "delete"):
        resp = getattr(client, method)("/api/screening/batches")
        assert resp.status_code == 405


# ── TESTING 种子扩展（screening.batches 历史两态） ─────────────────────────


def test_seed_batches_history_two_states(testing_client, _screening_db) -> None:
    """seed ``screening.batches``（一成功一失败）→ 历史端点可见（倒序 + 逐字字段）。

    历史行自带 ``pool`` 时 pool_size = 该行池行数（成功行「含池数」可断言）；
    失败行零池行是不变量（``record_screening_batch`` 单事务保证）。
    """
    resp = testing_client.post(
        "/api/test/seed",
        json={
            "screening": {
                "batches": [
                    {
                        "trade_date": _OK_DATE,
                        "status": "ok",
                        "started_at": f"{_OK_DATE}T15:40:00",
                        "finished_at": f"{_OK_DATE}T15:43:00",
                        "l1_passed": 20,
                        "l2_top": 9,
                        "l3_keep": 2,
                        "pool": _pool_rows(2),
                    },
                    {
                        "trade_date": _FAILED_DATE,
                        "status": "failed",
                        "error": "RuntimeError: LLM 网关整体不可用",
                        "started_at": f"{_FAILED_DATE}T15:40:00",
                        "finished_at": f"{_FAILED_DATE}T15:40:05",
                    },
                ]
            }
        },
    )
    assert resp.status_code == 200

    body = TestClient(app).get("/api/screening/batches").json()

    assert [row["trade_date"] for row in body] == [_FAILED_DATE, _OK_DATE]
    assert body[0]["error"] == "RuntimeError: LLM 网关整体不可用"
    assert (body[1]["l1_passed"], body[1]["l2_top"], body[1]["l3_keep"], body[1]["pool_size"]) == (
        20,
        9,
        2,
        2,
    )


def test_seed_batch_singular_wins_over_history_row(testing_client, _screening_db) -> None:
    """``batch`` 与 ``batches`` 撞同一天：单数（当前批次）胜出，既有语义不被历史行覆盖。"""
    resp = testing_client.post(
        "/api/test/seed",
        json={
            "screening": {
                "batch": {
                    "trade_date": _OK_DATE,
                    "status": "ok",
                    "started_at": f"{_OK_DATE}T15:40:00",
                    "finished_at": f"{_OK_DATE}T15:43:00",
                    "l1_passed": 20,
                    "l2_top": 9,
                    "l3_keep": 2,
                },
                "pool": _pool_rows(1),
                "batches": [
                    {
                        "trade_date": _OK_DATE,
                        "status": "failed",
                        "error": "历史行不应覆盖当前批次",
                        "started_at": f"{_OK_DATE}T09:00:00",
                        "finished_at": f"{_OK_DATE}T09:00:01",
                    }
                ],
            }
        },
    )
    assert resp.status_code == 200

    body = TestClient(app).get("/api/screening/batches").json()

    assert len(body) == 1
    assert body[0]["status"] == "ok"
    assert body[0]["pool_size"] == 1
