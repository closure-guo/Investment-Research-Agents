"""因子库只读 API 单测（delta add-factor-library-page T1.1/T1.2）。

spec factor-library「因子库只读查询」：

- ``GET /api/factors``：列表含 名称 / 表达式 / 状态（active|retired）/ 来源
  （builtin|llm）/ ICIR / 最近闸门结论 / 衰减标记；按 ICIR 降序固定排序；
  空库 → 200 + 空数组（MUST NOT 500 或以伪数据冒充）；
- ``GET /api/factors/{factor_id}``：详情含完整 DSL 表达式（含规范 ``ast_json``）、
  IC 序列（近 60 点升序）与事件历史（准入/降级/退役及时间）；未知 id → 404；
- **只读面**：接口只包装 ``factors.library`` 的 ``list_factors`` / ``list_events`` /
  ``list_ic_series``（零业务复制），不暴露任何写操作；
- TESTING seed（T1.2）：``factors`` 造数（逐条经既有落库 + 状态机走到目标态，
  与生产准入/退役路径同源）+ ``factors_reset`` 清三表，供 E2E 造数/复位。

零网络：TestClient + tmp 库（FACTOR_LIBRARY_DB_PATH 逐例注入；library 模块
调用期读 env，不落开发库 data/factor_library.db）。
"""

from __future__ import annotations

import importlib
import math

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.factors.library import (
    add_factor,
    list_events,
    list_factors,
    record_ic,
    set_status,
)

_FACTORS = "/api/factors"
# builtin 注册词表逐字（builtin.BUILTIN_FACTORS：momentum_20 的表达式）
_BUILTIN_EXPRESSION = "delta(close,20)"
_BUILTIN_NAME = "momentum_20"
_LLM_EXPRESSION = "ts_rank(close,20)"


@pytest.fixture(autouse=True)
def _factors_db(tmp_path, monkeypatch):
    """因子库路径指向 tmp（library._default_db_path 调用期读 env）。

    快照库路径同样注入 tmp（add-factor-style-exposure：详情端点的风格暴露披露会经
    ``_panel_from_snapshot`` 触碰 SNAPSHOT_DB_PATH——不隔离会打到生产快照库，
    空 tmp 库 → 该块诚实置 None，与前端「数据不足」空态一致）。
    """
    path = tmp_path / "factor_library.db"
    monkeypatch.setenv("FACTOR_LIBRARY_DB_PATH", str(path))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "snapshot.db"))
    return path


def _seed_library() -> tuple[int, int]:
    """造 spec 场景两因子：active+llm（有 IC）/ retired+builtin（衰减后退役）。

    状态迁移全部经 ``set_status``（唯一状态写入口），事件形态与生产一致：
    candidate（created）→ active（ADMITTED）→ degraded（DECAY）→ retired（BUCKET_QUOTA）。
    """
    llm_id = add_factor(
        expression=_LLM_EXPRESSION,
        hypothesis="近 20 日截面排名刻画价格相对强弱，排名抬升的标的短期收益分布偏正。",
        category="动量",
        status="candidate",
        source="llm",
    )
    set_status(llm_id, "active", detail={"reason": "ADMITTED"})
    record_ic(llm_id, "2026-10-08", 0.10)
    record_ic(llm_id, "2026-10-09", 0.12)

    builtin_id = add_factor(
        expression=_BUILTIN_EXPRESSION,
        hypothesis="过去 20 个交易日涨幅高的标的短期内倾向继续跑赢（Jegadeesh-Titman）。",
        category="builtin",
        status="candidate",
        source="builtin",
    )
    set_status(builtin_id, "active", detail={"reason": "ADMITTED"})
    set_status(builtin_id, "degraded", detail={"reason": "DECAY", "as_of": "2026-10-13"})
    set_status(builtin_id, "retired", detail={"reason": "BUCKET_QUOTA"})
    return llm_id, builtin_id


# ── 列表端点：字段齐备 + 排序 + 空库 ──────────────────────────────────────


def test_list_fields_complete(_factors_db) -> None:
    """spec 场景：两因子列表含 名称/表达式/状态/来源/ICIR/最近闸门结论/衰减标记。"""
    llm_id, builtin_id = _seed_library()

    resp = TestClient(app).get(_FACTORS)

    assert resp.status_code == 200
    rows = resp.json()["factors"]
    assert [row["id"] for row in rows] == [llm_id, builtin_id]  # ICIR 降序（有 IC 者在前）

    llm = rows[0]
    assert {
        "id",
        "name",
        "expression",
        "hypothesis",
        "category",
        "status",
        "source",
        "created_at",
        "updated_at",
        "ic_mean_60d",
        "icir_60d",
        "ic_points",
        "decayed",
        "last_event",
    } <= set(llm)
    assert llm["name"] is None  # LLM 提案无注册名：不臆造
    assert llm["expression"] == _LLM_EXPRESSION
    assert llm["status"] == "active"
    assert llm["source"] == "llm"
    assert llm["ic_points"] == 2
    assert llm["ic_mean_60d"] == pytest.approx(0.11)
    expected_icir = 0.11 / math.sqrt(((0.10 - 0.11) ** 2 + (0.12 - 0.11) ** 2) / 1)
    assert llm["icir_60d"] == pytest.approx(expected_icir)
    assert llm["decayed"] is False
    assert llm["last_event"] == {
        "event": "status_change",
        "from_status": "candidate",
        "to_status": "active",
        "reason": "ADMITTED",
        "created_at": llm["last_event"]["created_at"],
    }

    builtin = rows[1]
    assert builtin["name"] == _BUILTIN_NAME  # builtin 注册词表作品名（逐字）
    assert builtin["status"] == "retired"
    assert builtin["source"] == "builtin"
    assert builtin["ic_points"] == 0
    assert (
        builtin["icir_60d"] == 0.0
    )  # 无 IC 记录：recent_ic_stats 口径记 0（ic_points 供界面区分）
    assert builtin["decayed"] is True  # 衰减降级史（retired 后标记仍在）
    assert builtin["last_event"]["to_status"] == "retired"
    assert builtin["last_event"]["reason"] == "BUCKET_QUOTA"


def test_list_empty_db_returns_empty_array(_factors_db) -> None:
    """spec 场景：空库 → 200 + 空列表（MUST NOT 500 或伪数据冒充）。"""
    resp = TestClient(app).get(_FACTORS)

    assert resp.status_code == 200
    assert resp.json() == {"factors": []}


# ── 详情端点：完整表达式 / IC 序列 / 事件历史 / 未知 id 404 ────────────────


def test_detail_returns_expression_ic_series_and_events(_factors_db) -> None:
    """详情含完整 DSL 表达式、IC 序列（升序）与事件历史（含时间与事件类型）。"""
    llm_id, _ = _seed_library()

    resp = TestClient(app).get(f"{_FACTORS}/{llm_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["expression"] == _LLM_EXPRESSION
    assert body["status"] == "active"
    assert body["source"] == "llm"
    assert body["ic_series"] == [
        {"trade_date": "2026-10-08", "rank_ic": 0.10},
        {"trade_date": "2026-10-09", "rank_ic": 0.12},
    ]

    events = body["events"]
    assert [event["event"] for event in events] == ["created", "status_change"]
    assert events[0]["to_status"] == "candidate"
    assert events[0]["created_at"]  # 准入事件时间
    assert events[-1]["from_status"] == "candidate"
    assert events[-1]["to_status"] == "active"
    assert events[-1]["detail"]["reason"] == "ADMITTED"
    assert events[-1]["created_at"]  # 闸门结论事件时间


def test_detail_style_exposure_null_without_snapshot(_factors_db) -> None:
    """详情含 style_exposure 键；快照库为空 → 诚实 None（MUST NOT 用 0/虚构冒充）。"""
    llm_id, _retired_id = _seed_library()

    resp = TestClient(app).get(f"{_FACTORS}/{llm_id}")

    assert resp.status_code == 200
    assert resp.json()["style_exposure"] is None


def test_detail_exposes_ic_readiness_fields(_factors_db) -> None:
    """列表/详情披露 IC 就绪度（窗口 60、门槛 10、预计恢复打分日期）。"""
    from datetime import date

    llm_id, _ = _seed_library()

    body = TestClient(app).get(f"{_FACTORS}/{llm_id}").json()

    assert body["ic_window"] == 60
    assert body["min_samples_for_scoring"] == 10
    assert body["ic_points"] == 2
    expected = body["expected_factor_mode_date"]
    assert expected is not None
    assert date.fromisoformat(expected).weekday() < 5  # 工作日口径（跳过周末）

    listed = TestClient(app).get(_FACTORS).json()["factors"]
    row = next(item for item in listed if item["id"] == llm_id)
    assert row["ic_points"] == 2
    assert row["min_samples_for_scoring"] == 10
    assert row["expected_factor_mode_date"] == expected


def test_detail_style_exposure_skips_panel_when_ic_points_below_min(
    _factors_db, monkeypatch
) -> None:
    """IC 点数 < MIN_STYLE_SAMPLES → 直接 None，不得构建准入面板。

    风格暴露需要「因子 IC × 代理 IC」的 ≥30 个共同交易日；因子自身 IC 点数不足时
    结果必为 None。旧实现仍无条件构建 500 日 × 全市场面板（实测数分钟），把详情
    接口拖成超时——本用例把面板构造替换为断言，证明不足时零面板开销。
    """
    llm_id, _ = _seed_library()
    import finance_agent.factors.monthly as monthly

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("IC 点数不足时不得构建风格暴露面板")

    monkeypatch.setattr(monthly, "_panel_from_snapshot", _boom)

    resp = TestClient(app).get(f"{_FACTORS}/{llm_id}")

    assert resp.status_code == 200
    assert resp.json()["style_exposure"] is None


def test_detail_series_empty_for_factor_without_ic(_factors_db) -> None:
    """无 IC 记录 → 空序列（不编造占位点）。"""
    _, builtin_id = _seed_library()

    body = TestClient(app).get(f"{_FACTORS}/{builtin_id}").json()

    assert body["ic_series"] == []
    assert [event["to_status"] for event in body["events"]] == [
        "candidate",
        "active",
        "degraded",
        "retired",
    ]


def test_detail_unknown_id_returns_404(_factors_db) -> None:
    """未知 id → 404（不静默返回空详情）。"""
    _seed_library()

    resp = TestClient(app).get(f"{_FACTORS}/99999")

    assert resp.status_code == 404
    assert "99999" in resp.json()["detail"]


def test_detail_unknown_id_404_on_empty_db(_factors_db) -> None:
    """空库同样 404（无行可返回，不臆造）。"""
    resp = TestClient(app).get(f"{_FACTORS}/1")

    assert resp.status_code == 404


# ── TESTING seed 扩展（factors 造数 + factors_reset） ──────────────────────


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


def test_seed_placeholder_untouched_for_factor_free_payload(testing_client) -> None:
    """既有 {symbol} 早退路径不受 factors 扩展影响（占位响应逐字不变）。"""
    resp = testing_client.post("/api/test/seed", json={"symbol": "300308"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "mode": "testing"}


def test_seed_factors_roundtrip(testing_client, _factors_db) -> None:
    """seed `factors.factors`（active+llm / retired+builtin + 事件 + IC）→ 端点可见。"""
    resp = testing_client.post(
        "/api/test/seed",
        json={
            "factors": {
                "factors": [
                    {
                        "expression": _LLM_EXPRESSION,
                        "hypothesis": "截面排名动量：排名抬升的标的短期收益分布偏正。",
                        "category": "动量",
                        "status": "active",
                        "source": "llm",
                        "alignment_score": 0.7,
                        "ast_json": _LLM_EXPRESSION,
                        "ic": [["2026-10-09", 0.12], ["2026-10-08", 0.10]],
                    },
                    {
                        "expression": _BUILTIN_EXPRESSION,
                        "hypothesis": "20 日动量（Jegadeesh-Titman）。",
                        "category": "builtin",
                        "status": "retired",
                        "source": "builtin",
                        "pool_increment": 0.05,
                        "detail": {"reason": "BUCKET_QUOTA"},
                    },
                ]
            }
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "mode": "testing"}

    rows = TestClient(app).get(_FACTORS).json()["factors"]
    assert [(row["status"], row["source"]) for row in rows] == [
        ("active", "llm"),
        ("retired", "builtin"),
    ]
    llm_id = rows[0]["id"]
    builtin_id = rows[1]["id"]
    assert rows[0]["ic_points"] == 2
    assert rows[0]["last_event"]["reason"] == "ADMITTED"  # 状态机走到位（active 记 ADMITTED）
    assert rows[1]["last_event"]["reason"] == "BUCKET_QUOTA"

    detail = TestClient(app).get(f"{_FACTORS}/{builtin_id}").json()
    assert detail["pool_increment"] == pytest.approx(0.05)
    assert [event["to_status"] for event in detail["events"]] == ["candidate", "active", "retired"]
    assert TestClient(app).get(f"{_FACTORS}/{llm_id}").json()["ast_json"] == _LLM_EXPRESSION


def test_seed_factors_via_degraded_records_decay_history(testing_client, _factors_db) -> None:
    """seed ``via_degraded``：retired 先经 degraded(DECAY) → 衰减标记为真（E2E 造态）。"""
    resp = testing_client.post(
        "/api/test/seed",
        json={
            "factors": {
                "factors": [
                    {
                        "expression": _BUILTIN_EXPRESSION,
                        "status": "retired",
                        "source": "builtin",
                        "via_degraded": True,
                        "detail": {"reason": "BUCKET_QUOTA"},
                    }
                ]
            }
        },
    )

    assert resp.status_code == 200
    row = TestClient(app).get(_FACTORS).json()["factors"][0]
    assert row["status"] == "retired"
    assert row["decayed"] is True
    assert row["last_event"]["reason"] == "BUCKET_QUOTA"

    events = TestClient(app).get(f"{_FACTORS}/{row['id']}").json()["events"]
    assert [event["to_status"] for event in events] == [
        "candidate",
        "active",
        "degraded",
        "retired",
    ]
    assert events[2]["detail"] == {
        "reason": "DECAY"
    }  # 降级事件 detail 缺省 DECAY（可经 degraded_detail 覆盖）


def test_seed_factors_unknown_status_fails_loud(testing_client, _factors_db) -> None:
    """种子状态非法 → 显式失败（不静默落一条状态不符预期的行）。"""
    with pytest.raises(ValueError, match="非法种子状态"):
        testing_client.post(
            "/api/test/seed",
            json={"factors": {"factors": [{"expression": "delta(close,5)", "status": "bogus"}]}},
        )

    assert list_factors() == []


def test_seed_factors_reset_clears_library(testing_client, _factors_db) -> None:
    """factors_reset 清三表（E2E 每例前后复位；不影响其它库）。"""
    _seed_library()

    resp = testing_client.post("/api/test/seed", json={"factors_reset": True})

    assert resp.status_code == 200
    assert list_factors() == []
    assert list_events() == []
    assert TestClient(app).get(_FACTORS).json() == {"factors": []}


def test_seed_factors_reset_then_seed_is_deterministic(testing_client, _factors_db) -> None:
    """reset 后重 seed 同表达式不被结构规则吞（E2E 串行套件的复位前提）。"""
    payload = {
        "factors": {
            "factors": [{"expression": _LLM_EXPRESSION, "status": "active", "source": "llm"}]
        }
    }
    testing_client.post("/api/test/seed", json=payload)
    testing_client.post("/api/test/seed", json={"factors_reset": True})
    testing_client.post("/api/test/seed", json=payload)

    rows = TestClient(app).get(_FACTORS).json()["factors"]
    assert [row["expression"] for row in rows] == [_LLM_EXPRESSION]


def test_detail_monotonicity_null_without_snapshot(_factors_db) -> None:
    """详情含 monotonicity 键；快照库为空 → 诚实 None（MUST NOT 用 0/虚构冒充）。"""
    llm_id, _ = _seed_library()

    body = TestClient(app).get(f"{_FACTORS}/{llm_id}").json()

    assert "monotonicity" in body
    assert body["monotonicity"] is None


def test_detail_monotonicity_observed_with_panel(_factors_db, monkeypatch) -> None:
    """IC 点数过门槛 + 可注入面板 → monotonicity 为观测读数（含全部 detail 字段）。"""
    from datetime import date as _date
    from datetime import timedelta as _timedelta

    import numpy as np
    import pandas as pd

    import finance_agent.factors.monthly as monthly
    from finance_agent.factors.eval_engine import MarketPanel

    # 种子因子表达式 = "close"（直接取 close 帧，规避 ts_rank 热身期/常量截面干扰）
    factor_id = add_factor(
        expression="close",
        hypothesis="单调性观测读数用例",
        category="价量",
        status="candidate",
        source="llm",
    )
    set_status(factor_id, "active", detail={"reason": "ADMITTED"})
    for offset in range(30):  # 越过 MIN_STYLE_SAMPLES(30) 短路门槛
        record_ic(factor_id, (_date(2026, 1, 5) + _timedelta(days=offset)).isoformat(), 0.1)

    days, codes_n = 41, 40
    codes = [f"c{i:03d}" for i in range(codes_n)]
    dates = [(_date(2026, 1, 5) + _timedelta(days=o)).isoformat() for o in range(days)]
    # 收益截面随码序递增 + 逐日码相位抖动（IC 逐日非常量，风格暴露相关可定义）
    t_axis = np.arange(days)[:, None]
    i_axis = np.arange(codes_n)[None, :]
    daily = np.linspace(0.0005, 0.0025, codes_n)[None, :] * np.ones((days, 1))
    daily = daily + 0.0008 * np.sin(t_axis / 5.0 + i_axis / 7.0)
    close = 100.0 * np.cumprod(1.0 + daily, axis=0)

    def _frame(matrix: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(matrix, index=dates, columns=codes)

    panel = MarketPanel(
        fields={
            "open": _frame(close),
            "high": _frame(close),
            "low": _frame(close),
            "close": _frame(close),
            "volume": _frame(close),
            "amount": _frame(close),
        }
    )
    monkeypatch.setattr(monthly, "_panel_from_snapshot", lambda *_a, **_k: panel)

    body = TestClient(app).get(f"{_FACTORS}/{factor_id}").json()

    mono = body["monotonicity"]
    assert mono is not None
    assert {"passed", "spearman", "q10_q1", "decile_returns", "valid_days", "threshold"} <= set(
        mono
    )


# ── 拒绝池不可见性（add-rejected-factor-pool T6）──────────────────────────


def test_rejected_row_absent_from_list_but_detail_reachable(_factors_db) -> None:
    """列表不返回 rejected 行；详情按 id 仍可直查（只读诊断：IC 跟踪序列可观测）。"""
    from finance_agent.factors.library import record_rejected_factor

    rejected_id = record_rejected_factor(
        expression="volume",
        hypothesis="闸②拒收的量因子",
        category="价量",
        ast_json="volume",
        source="harvest",
        gate=2,
        reason="WEAK_ICIR",
        detail={},
    )["factor_id"]

    resp = TestClient(app).get(_FACTORS)
    assert resp.status_code == 200
    assert all(row["status"] != "rejected" for row in resp.json()["factors"])

    detail = TestClient(app).get(f"{_FACTORS}/{rejected_id}")
    assert detail.status_code == 200
    assert detail.json()["expression"] == "volume"
    assert detail.json()["events"][0]["detail"]["gate"] == 2
