"""add-track-record Task 4:track-record 只读 API 端点测试。"""

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.execution.store import list_paper_equity, upsert_paper_equity
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
    list_equity_curve,
    update_prediction_status,
    upsert_equity_point,
    upsert_index_closes,
    upsert_metrics_daily,
)

BASE = {
    "source_type": "live",
    "symbol": "600519.SH",
    "symbol_name": "贵州茅台",
    "direction": "long",
    "entry_price": 100.0,
    # 头条口径窗口（§1.9①：默认 T+20）；252 存量行由专门用例覆盖
    "horizon_days": 20,
    "confidence": 0.8,
    "rationale_snapshot": {"action": "buy"},
    "created_at": "2026-09-01T10:00:00",
}


def _use_db(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    init_track_record_tables(db)
    monkeypatch.setattr("finance_agent.outcome.track_record.model._default_db_path", lambda: db)
    return db


def _insert(db, **overrides):
    rec = dict(BASE)
    rec.update(overrides)
    return insert_prediction(rec, db_path=db)


def test_overview_empty(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    resp = TestClient(app).get("/api/v1/track-record/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0 and data["win_rate"] is None
    assert data["as_of"] and data["disclaimer"]
    assert data["insufficient_sample"] is True


def test_overview_with_settled(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    for i in range(3):
        pid = _insert(db, symbol=f"{i}.SH", created_at=f"2026-09-0{i + 1}T10:00:00")
        update_prediction_status(
            pid,
            {
                "status": "resolved_win" if i < 2 else "resolved_loss",
                "raw_return": 0.1,
                "excess_return": 0.05,
            },
            db_path=db,
        )
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["total"] == 3 and data["settled"] == 3
    # 显著性门槛:settled < 10 → 不展示胜率
    assert data["win_rate"] is None and data["insufficient_sample"] is True


def test_overview_win_rate_after_10_settled(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    for i in range(10):
        pid = _insert(db, symbol=f"{i}.SH", created_at=f"2026-09-{i + 1:02d}T10:00:00")
        update_prediction_status(
            pid,
            {
                "status": "resolved_win" if i < 7 else "resolved_loss",
                "raw_return": 0.1,
                "excess_return": 0.05,
            },
            db_path=db,
        )
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["settled"] == 10 and data["insufficient_sample"] is False
    assert data["win_rate"] == 0.7


def test_overview_source_filter(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    _insert(db, source_type="live")
    _insert(db, source_type="backtest")
    live = TestClient(app).get("/api/v1/track-record/overview", params={"source": "live"}).json()
    assert live["total"] == 1


# ── delta update-decision-settlement-contract Task 5：overview 口径字段（additive）──


def _avoid(db, pid, avoidance_status):
    update_prediction_status(
        pid,
        {"status": "avoidance", "avoidance_status": avoidance_status},
        db_path=db,
    )


def test_overview_caliber_fields_and_avoidance_threshold(monkeypatch, tmp_path):
    """overview 追加 avoidance / caliber_horizon / legacy_settled；回避样本 <10 → 正确率置 null。"""
    db = _use_db(monkeypatch, tmp_path)
    for i in range(6):
        _avoid(db, _insert(db, symbol=f"aw{i}.SH", direction="neutral"), "avoidance_win")
    for i in range(3):
        _avoid(db, _insert(db, symbol=f"al{i}.SH", direction="neutral"), "avoidance_loss")
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["caliber_horizon"] == 20
    assert data["avoidance"]["avoidance_win"] == 6
    assert data["avoidance"]["avoidance_loss"] == 3
    assert data["avoidance"]["settled"] == 9
    assert data["avoidance"]["avoidance_rate"] is None  # settled<10 不展示（与胜率同门槛）
    # neutral 回避判定不进 long/short 主指标
    assert data["settled"] == 0 and data["win_rate"] is None
    assert data["legacy_settled"] == 0


def test_overview_avoidance_rate_shown_at_threshold(monkeypatch, tmp_path):
    """回避样本 >=10 → 独立字段展示正确率，且不动 long/short 胜率。"""
    db = _use_db(monkeypatch, tmp_path)
    for i in range(7):
        _avoid(db, _insert(db, symbol=f"w{i}.SH", direction="neutral"), "avoidance_win")
    for i in range(3):
        _avoid(db, _insert(db, symbol=f"l{i}.SH", direction="neutral"), "avoidance_loss")
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["avoidance"]["settled"] == 10
    assert data["avoidance"]["avoidance_rate"] == 0.7
    assert data["settled"] == 0  # 不混入胜率(分母仍为 0)


def test_overview_legacy_settled_discloses_cross_caliber(monkeypatch, tmp_path):
    """头条口径限 T+20；252 存量已结算行经 legacy_settled 披露，不混入 headline settled。"""
    db = _use_db(monkeypatch, tmp_path)
    update_prediction_status(
        _insert(db, symbol="new.SH", direction="long", horizon_days=20),
        {"status": "resolved_win", "excess_return": 0.05},
        db_path=db,
    )
    update_prediction_status(
        _insert(db, symbol="old.SH", direction="long", horizon_days=252),
        {"status": "resolved_win", "excess_return": 0.05},
        db_path=db,
    )
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["caliber_horizon"] == 20
    assert data["total"] == 1 and data["settled"] == 1  # 头条:仅 T+20
    assert data["legacy_settled"] == 1  # 存量跨口径计数披露


def test_predictions_list_default_all_statuses(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    _insert(db)
    pid = _insert(db, symbol="300308.SZ", created_at="2026-09-02T10:00:00")
    update_prediction_status(pid, {"status": "resolved_loss"}, db_path=db)
    items = TestClient(app).get("/api/v1/track-record/predictions").json()["predictions"]
    assert len(items) == 2  # 默认含 loss


def test_predictions_filter_and_page(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    for i in range(5):
        _insert(db, symbol=f"6{i}.SH", created_at=f"2026-09-0{i + 1}T10:00:00")
    c = TestClient(app)
    assert (
        len(
            c.get("/api/v1/track-record/predictions", params={"status": "open"}).json()[
                "predictions"
            ]
        )
        == 5
    )
    page1 = c.get("/api/v1/track-record/predictions", params={"page": 1, "page_size": 2}).json()
    assert len(page1["predictions"]) == 2 and page1["total"] == 5


# ── add-track-record-stage-b：组合指标块 + 净值曲线端点 ──


def test_overview_portfolio_block_empty(monkeypatch, tmp_path):
    """无指标快照时 portfolio.available=false 且各字段 null，不报错。"""
    _use_db(monkeypatch, tmp_path)
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    portfolio = data["portfolio"]
    assert portfolio["available"] is False
    assert portfolio["annual_return"] is None
    assert portfolio["risk_score"] is None
    assert portfolio["as_of"] is None


def test_overview_portfolio_block_with_snapshot(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    upsert_metrics_daily(
        "2026-09-04",
        {
            "sample_size": 3,
            "settled": 2,
            "win_rate": 0.5,
            "annual_return": 0.12,
            "volatility": 0.2,
            "sharpe": 0.5,
            "max_drawdown": 0.05,
            "risk_score": 5,
            "risk_label": "中",
        },
        db_path=db,
    )
    upsert_equity_point("2026-09-04", agent_nav=1.0, benchmark_nav=1.0, db_path=db)
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    p = data["portfolio"]
    assert p["available"] is True
    assert p["annual_return"] == 0.12
    assert p["risk_score"] == 5
    assert p["risk_label"] == "中"
    assert p["as_of"] == "2026-09-04"


def test_overview_portfolio_as_of_is_data_date(monkeypatch, tmp_path):
    """incident 032 伴生发现：快照写入日 ≠ 数据日期时 as_of 取后者（诚实性）。

    快照 metric_date=2026-09-28，但净值数据停在 2026-09-24（marking 断更）→
    as_of SHALL 为 2026-09-24，SHALL NOT 冒称 09-28。
    """
    db = _use_db(monkeypatch, tmp_path)
    upsert_metrics_daily(
        "2026-09-28",
        {"sample_size": 1, "settled": 0, "risk_score": 5, "risk_label": "中"},
        db_path=db,
    )
    upsert_equity_point("2026-09-24", agent_nav=0.99, benchmark_nav=0.97, db_path=db)
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    p = data["portfolio"]
    assert p["available"] is True
    assert p["as_of"] == "2026-09-24"


def test_overview_portfolio_metrics_without_curve_is_unavailable(monkeypatch, tmp_path):
    """只有指标快照、无任何净值数据 → available=false（无净值不伪称有组合）。"""
    db = _use_db(monkeypatch, tmp_path)
    upsert_metrics_daily(
        "2026-09-28",
        {"sample_size": 1, "settled": 0, "risk_score": 5, "risk_label": "中"},
        db_path=db,
    )
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["portfolio"]["available"] is False
    assert data["portfolio"]["as_of"] is None


def test_equity_curve_empty(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    data = TestClient(app).get("/api/v1/track-record/equity-curve").json()
    assert data["points"] == []
    assert data["disclaimer"]


def test_equity_curve_returns_points(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    upsert_equity_point("2026-09-01", 1.0, 1.0, 0.0, 1, db_path=db)
    upsert_equity_point("2026-09-02", 1.01, 1.005, 0.01, 1, db_path=db)
    data = TestClient(app).get("/api/v1/track-record/equity-curve").json()
    assert [p["date"] for p in data["points"]] == ["2026-09-01", "2026-09-02"]
    assert data["points"][-1]["agent_nav"] == 1.01


# ── add-track-record-sort-filter：排序/关键字/时间段/过滤后 total ──


def test_predictions_sort_api(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    _insert(db, symbol="a.SH", entry_price=100.0, created_at="2026-09-01T10:00:00")
    _insert(db, symbol="b.SH", entry_price=100.0, created_at="2026-09-02T10:00:00")
    c = TestClient(app)
    items = c.get(
        "/api/v1/track-record/predictions", params={"sort_by": "created_at", "sort_dir": "asc"}
    ).json()["predictions"]
    assert [r["symbol"] for r in items] == ["a.SH", "b.SH"]
    # 非法 sort_by 回退默认 created_at DESC
    items2 = c.get("/api/v1/track-record/predictions", params={"sort_by": "bogus"}).json()[
        "predictions"
    ]
    assert items2[0]["symbol"] == "b.SH"


def test_predictions_keyword_api(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    _insert(db, symbol="600519.SH", symbol_name="贵州茅台", direction="long")
    _insert(db, symbol="300308.SZ", symbol_name="中际旭创", direction="short")
    c = TestClient(app)
    assert (
        len(
            c.get("/api/v1/track-record/predictions", params={"keyword": "茅台"}).json()[
                "predictions"
            ]
        )
        == 1
    )
    assert (
        len(
            c.get("/api/v1/track-record/predictions", params={"keyword": "看空"}).json()[
                "predictions"
            ]
        )
        == 1
    )


def test_predictions_date_range_api(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    _insert(db, symbol="a.SH", created_at="2026-09-01T10:00:00")
    _insert(db, symbol="b.SH", created_at="2026-09-30T10:00:00")
    _insert(db, symbol="c.SH", created_at="2026-10-02T10:00:00")
    data = (
        TestClient(app)
        .get(
            "/api/v1/track-record/predictions",
            params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
        )
        .json()
    )
    assert sorted(r["symbol"] for r in data["predictions"]) == ["a.SH", "b.SH"]
    assert data["total"] == 2  # total 反映过滤后子集


def test_predictions_filtered_total(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    for i in range(6):
        _insert(
            db,
            symbol=f"{i}.SH",
            symbol_name="茅台" if i < 2 else "平安",
            created_at=f"2026-09-0{i + 1}T10:00:00",
        )
    data = (
        TestClient(app)
        .get("/api/v1/track-record/predictions", params={"keyword": "茅台", "page_size": 1})
        .json()
    )
    assert len(data["predictions"]) == 1 and data["total"] == 2


# ── add-index-performance-compare Task 6：index-compare 读数端点 ──


def test_index_compare_span_validation(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    resp = TestClient(app).get("/api/v1/track-record/index-compare?span=2y")
    assert resp.status_code == 422


def test_index_compare_empty(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    resp = TestClient(app).get("/api/v1/track-record/index-compare")
    assert resp.status_code == 200
    data = resp.json()
    assert data["window"]["start"] is None
    assert data["agent_return"] is None
    assert len(data["indices"]) == 5
    assert all(i["beat"] is None for i in data["indices"])
    assert data["as_of"] and data["disclaimer"]


def test_index_compare_returns(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    upsert_equity_point("2026-09-28", 1.0, benchmark_nav=1.0, db_path=db)
    upsert_equity_point("2026-10-09", 1.05, benchmark_nav=0.99, db_path=db)
    upsert_index_closes(
        [("000300", "2026-09-26", 4000.0), ("000300", "2026-10-09", 4040.0)], db_path=db
    )
    resp = TestClient(app).get("/api/v1/track-record/index-compare?span=all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_return"] == pytest.approx(0.05)
    by = {i["code"]: i for i in data["indices"]}
    assert by["000300"]["beat"] is True


# ── add-index-performance-compare Task 7：test/seed 的 track_record 造数块 ──


# ── add-portfolio-beta-alpha Task 3：overview 透传 β/α + 全链集成 ──


def test_overview_portfolio_beta_alpha(monkeypatch, tmp_path):
    """全链:daily_marks(β=0.8 线性)→ persist_metrics_snapshot → overview 透传。"""
    from finance_agent.outcome.track_record.marking import persist_metrics_snapshot
    from finance_agent.outcome.track_record.model import insert_daily_mark

    db = _use_db(monkeypatch, tmp_path)
    pid = _insert(db)
    import random

    rng = random.Random(7)  # noqa: S311 — 可复现测试 fixture，非加密用途
    bench, cum = 4000.0, 0.0
    dates, closes = [], []
    for i in range(1, 26):
        rb = rng.uniform(-0.02, 0.02)
        bench *= 1 + rb
        cum += 0.8 * rb
        d = f"2026-09-{i:02d}"
        dates.append(d)
        closes.append(bench)
        insert_daily_mark(
            pid, d, mark_price=10.0, cum_return=cum, benchmark_price=bench, db_path=db
        )
    # 日历口径封闭性（update-track-record-data-integrity）：persist 缺省会自行
    # 拉网源基准（东财→新浪回退），测试必须离线确定性——传合成基准日 K 走日历路径
    import pandas as pd

    bench_df = pd.DataFrame({"日期": dates, "收盘": closes})
    persist_metrics_snapshot(db_path=db, benchmark=bench_df)
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["portfolio"]["beta"] == pytest.approx(0.8, abs=1e-6)
    assert data["portfolio"]["jensen_alpha"] == pytest.approx(-0.004, abs=1e-6)


def test_overview_portfolio_beta_alpha_null_when_insufficient(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["portfolio"]["beta"] is None and data["portfolio"]["jensen_alpha"] is None


def test_seed_track_record_block(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    # /api/test/seed 仅在 TESTING=1 的导入期注册（api.py `if TESTING:`）；单测进程
    # 与 CI 均未设该环境变量，故按仓库既有模式（test_testing_mode.client_testing）
    # reload api 模块使路由挂载，结束后恢复默认模块态避免跨用例污染。
    monkeypatch.setenv("TESTING", "1")
    import importlib

    import finance_agent.api as api_module
    import finance_agent.session_store as session_store_module

    # 会话库指向独立 tmp 库（同 test_testing_mode 模式），保证下方 /api/sessions
    # HTTP 断言确定性，不读写真实 data/sessions.db
    monkeypatch.setattr(session_store_module, "_DB_PATH", tmp_path / "sessions.db")
    session_store_module.init_db()

    importlib.reload(api_module)
    try:
        client = TestClient(api_module.app)
        resp = client.post(
            "/api/test/seed",
            json={
                "track_record": {
                    "equity_curve": [
                        {"curve_date": "2026-09-28", "agent_nav": 1.0, "benchmark_nav": 1.0},
                        {"curve_date": "2026-10-09", "agent_nav": 1.05, "benchmark_nav": 0.99},
                    ],
                    "index_closes": [
                        {"index_code": "000300", "trade_date": "2026-09-28", "close": 4000.0},
                        {"index_code": "000300", "trade_date": "2026-10-09", "close": 4040.0},
                    ],
                }
            },
        )
        assert resp.status_code == 200
        # track_record-only 造数返回占位响应（review fix）：不返回 session_id
        assert resp.json() == {"status": "ok", "mode": "testing"}
        # review fix：seed 是纯造数，track_record-only 请求不得创建会话（不污染 E2E 会话侧栏）
        assert client.get("/api/sessions").json()["sessions"] == []
        data = client.get("/api/v1/track-record/index-compare?span=all").json()
        assert data["agent_return"] == pytest.approx(0.05)
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)


# ── add-portfolio-beta-alpha Task 5：test/seed 的 metrics_snapshot 造数 ──


def test_seed_metrics_snapshot_block(monkeypatch, tmp_path):
    """seed track_record.metrics_snapshot → 当日 upsert → overview 透传 β/α。"""
    _use_db(monkeypatch, tmp_path)
    # reload 模式沿 test_seed_track_record_block（Testing=1 + importlib.reload + finally 恢复）
    monkeypatch.setenv("TESTING", "1")
    import importlib

    import finance_agent.api as api_module
    import finance_agent.session_store as session_store_module

    monkeypatch.setattr(session_store_module, "_DB_PATH", tmp_path / "sessions.db")
    session_store_module.init_db()

    importlib.reload(api_module)
    try:
        client = TestClient(api_module.app)
        resp = client.post(
            "/api/test/seed",
            json={"track_record": {"metrics_snapshot": {"beta": 0.85, "jensen_alpha": 0.031}}},
        )
        assert resp.status_code == 200
        # track_record-only 造数返回占位响应，不创建会话
        assert resp.json() == {"status": "ok", "mode": "testing"}
        data = client.get("/api/v1/track-record/overview").json()
        assert data["portfolio"]["beta"] == pytest.approx(0.85)
        assert data["portfolio"]["jensen_alpha"] == pytest.approx(0.031)
        # metrics-only 造数不得创建会话(双保险,闭环约束:seed=数据准备)
        assert client.get("/api/sessions").json()["sessions"] == []
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)


# ── add-prediction-pool-integrity Task 8：test/seed 的 predictions 造数 ──


def test_seed_predictions_block(monkeypatch, tmp_path):
    """seed track_record.predictions → 逐行 insert + 非 open 置终态 → 落库可查。

    duplicate 徽标 E2E（track-record 专属套件）的造数通道：open 行原样插入；
    非 open 状态（duplicate_of_day）经 update_prediction_status 置终态——与生产
    dedup 判定同一条状态变更路径。duplicate 行不进已结算分母（settled=win+loss）。
    """
    _use_db(monkeypatch, tmp_path)
    # reload 模式沿 test_seed_track_record_block（Testing=1 + importlib.reload + finally 恢复）
    monkeypatch.setenv("TESTING", "1")
    import importlib

    import finance_agent.api as api_module
    import finance_agent.session_store as session_store_module

    monkeypatch.setattr(session_store_module, "_DB_PATH", tmp_path / "sessions.db")
    session_store_module.init_db()

    importlib.reload(api_module)
    try:
        client = TestClient(api_module.app)
        resp = client.post(
            "/api/test/seed",
            json={
                "track_record": {
                    "predictions": [
                        {
                            "symbol": "600519.SH",
                            "direction": "neutral",
                            "created_at": "2026-10-09T10:00:00",
                        },
                        {
                            "symbol": "600519.SH",
                            "direction": "neutral",
                            "created_at": "2026-10-09T11:00:00",
                            "status": "duplicate_of_day",
                            "resolution_rule": "duplicate_of_day",
                        },
                    ]
                }
            },
        )
        assert resp.status_code == 200
        # track_record-only 造数返回占位响应，不创建会话
        assert resp.json() == {"status": "ok", "mode": "testing"}
        assert client.get("/api/sessions").json()["sessions"] == []
        # 落库可查：两条观点，一条 open、一条 duplicate_of_day 终态
        from finance_agent.outcome.track_record.model import list_predictions

        rows = list_predictions(sort_by="created_at", sort_dir="asc")
        assert len(rows) == 2
        assert rows[0]["status"] == "open"
        assert rows[0]["created_at"].startswith("2026-10-09T10:00:00")
        dup = rows[1]
        assert dup["status"] == "duplicate_of_day"
        assert dup["resolution_rule"] == "duplicate_of_day"
        assert dup["created_at"].startswith("2026-10-09T11:00:00")
        # duplicate 不进已结算分母：总览 total=2（含 duplicate）、settled=0
        stats = client.get("/api/v1/track-record/overview").json()
        assert stats["total"] == 2 and stats["settled"] == 0
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)


# ── add-current-stance-view Task 2：当前立场只读端点 ──


def test_current_endpoint_latest_open_per_symbol(monkeypatch, tmp_path):
    """立场视图:每股仅最新 open;dup/已结算不进入;as_of+disclaimer 必带。"""
    db = _use_db(monkeypatch, tmp_path)
    _insert(db, symbol="601058.SH", direction="neutral", created_at="2026-10-05T10:00:00")
    _insert(db, symbol="601058.SH", direction="neutral", created_at="2026-10-06T10:00:00")
    dup = _insert(db, symbol="300033.SZ", created_at="2026-10-06T11:00:00")
    update_prediction_status(
        dup, {"status": "duplicate_of_day", "resolution_rule": "duplicate_of_day"}, db_path=db
    )
    resp = TestClient(app).get("/api/v1/track-record/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["current"][0]["symbol"] == "601058.SH"
    assert data["current"][0]["created_at"] == "2026-10-06T10:00:00"
    assert data["current"][0]["source_type"] == "live"
    assert data["as_of"] and data["disclaimer"]


def test_current_endpoint_source_filter(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    _insert(db, symbol="600015.SH", source_type="live", created_at="2026-10-05T10:00:00")
    _insert(db, symbol="600016.SH", source_type="backtest", created_at="2026-10-05T11:00:00")
    resp = TestClient(app).get("/api/v1/track-record/current?source=live")
    assert resp.status_code == 200
    assert [r["symbol"] for r in resp.json()["current"]] == ["600015.SH"]


def test_overview_legacy_open_counts_non_headline_open(monkeypatch, tmp_path):
    """legacy_open=不在头条口径内的 open 计数（与 legacy_settled 对称的差值法）。"""
    db = _use_db(monkeypatch, tmp_path)
    _insert(db, symbol="600015.SH", created_at="2026-10-01T10:00:00")  # T+20 open（头条）
    _insert(db, symbol="600016.SH", horizon_days=252, created_at="2026-08-01T10:00:00")
    _insert(db, symbol="600017.SH", horizon_days=252, created_at="2026-08-02T10:00:00")
    resp = TestClient(app).get("/api/v1/track-record/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["legacy_open"] == 2
    assert data["legacy_settled"] == 0


# ── update-track-record-settle-price-display Task 1：seed 终态行透传结算字段 ──


def test_seed_predictions_settle_fields_passthrough(monkeypatch, tmp_path):
    """seed 终态行带 settle_entry_price/exit_price/raw_return/excess_return 时透传落库。

    结算口径展示 E2E（track-record-settle-price-display.spec.ts）的造数前提：
    已结算行须带 hfq 口径的结算入场价与结算价，才可断言「同口径可比」展示。
    透传走 update_prediction_status（_MUTABLE_FIELDS 含四字段）——与生产判定
    写入同一条路径。
    """
    _use_db(monkeypatch, tmp_path)
    monkeypatch.setenv("TESTING", "1")
    import importlib

    import finance_agent.api as api_module
    import finance_agent.session_store as session_store_module

    monkeypatch.setattr(session_store_module, "_DB_PATH", tmp_path / "sessions.db")
    session_store_module.init_db()

    importlib.reload(api_module)
    try:
        client = TestClient(api_module.app)
        resp = client.post(
            "/api/test/seed",
            json={
                "track_record": {
                    "predictions": [
                        {
                            "symbol": "601818.SH",
                            "direction": "short",
                            "created_at": "2026-10-09T18:00:00",
                            "status": "resolved_loss",
                            "resolution_rule": "horizon",
                            "settle_entry_price": 6.39,
                            "exit_price": 6.5,
                            "raw_return": -0.0172,
                            "excess_return": -0.0201,
                        },
                    ]
                }
            },
        )
        assert resp.status_code == 200
        from finance_agent.outcome.track_record.model import list_predictions

        rows = list_predictions(sort_by="created_at", sort_dir="asc")
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "resolved_loss"
        assert row["settle_entry_price"] == 6.39
        assert row["exit_price"] == 6.5
        assert row["raw_return"] == pytest.approx(-0.0172)
        assert row["excess_return"] == pytest.approx(-0.0201)
        # 列表 API 原样透出（前端渲染数据源）
        api_rows = client.get("/api/v1/track-record/predictions").json()["predictions"]
        assert api_rows[0]["settle_entry_price"] == 6.39
        assert api_rows[0]["exit_price"] == 6.5
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)


# ── add-paper-broker Task 2：overview paper 块（虚拟 vs 仿真双曲线）──


def _use_execution_db(monkeypatch, tmp_path):
    """execution 库指向独立 tmp（store 调用期读 EXECUTION_DB_PATH）。"""
    db = tmp_path / "execution.db"
    monkeypatch.setenv("EXECUTION_DB_PATH", str(db))
    return db


def test_overview_paper_block_aligns_common_dates(monkeypatch, tmp_path):
    """spec 场景：虚拟 1.043 / 仿真 1.031 → loss_pct -1.2；只保留共同日期。

    基期两线重合（paper 序列按共同基期缩放到虚拟净值同一标尺）；无对冲日期的
    行（曲线独有 10-09 / paper 独有 10-13）一律排除。
    """
    curve_db = _use_db(monkeypatch, tmp_path)
    exec_db = _use_execution_db(monkeypatch, tmp_path)
    upsert_equity_point("2026-09-01", 1.0, benchmark_nav=1.0, db_path=curve_db)
    upsert_equity_point("2026-09-30", 1.043, benchmark_nav=1.01, db_path=curve_db)
    upsert_equity_point("2026-10-09", 1.05, benchmark_nav=1.02, db_path=curve_db)  # 无 paper → 排除
    upsert_paper_equity("2026-09-01", 1_000_000.0, 0.0, 1_000_000.0, db_path=exec_db)
    upsert_paper_equity("2026-09-30", 998_000.0, 33_000.0, 1_031_000.0, db_path=exec_db)
    upsert_paper_equity(
        "2026-10-13", 1_000_000.0, 0.0, 1_000_000.0, db_path=exec_db
    )  # 无曲线 → 排除

    paper = TestClient(app).get("/api/v1/track-record/overview").json()["paper"]

    assert [p["date"] for p in paper["series"]] == ["2026-09-01", "2026-09-30"]
    assert paper["series"][0]["virtual_equity"] == 1.0
    assert paper["series"][0]["paper_equity"] == 1.0  # 基期两线重合
    assert paper["series"][1]["virtual_equity"] == 1.043
    assert paper["series"][1]["paper_equity"] == pytest.approx(1.031)  # 1,031,000/1,000,000
    assert paper["loss_pct"] == -1.2  # (1.031/1.043 − 1) × 100 = −1.1505 → 保留 1 位
    assert paper["base_date"] == "2026-09-01"
    assert paper["note"] == "两条曲线同一批决策同一基准期；差值为执行损耗"


def test_overview_paper_null_without_paper_data(monkeypatch, tmp_path):
    """无 paper_equity 行 → paper: null（前端隐藏系列）。"""
    curve_db = _use_db(monkeypatch, tmp_path)
    _use_execution_db(monkeypatch, tmp_path)
    upsert_equity_point("2026-09-30", 1.043, benchmark_nav=1.01, db_path=curve_db)
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["paper"] is None


def test_overview_paper_null_without_common_dates(monkeypatch, tmp_path):
    """两表都有数据但日期无交集 → 无可对齐基期 → paper: null。"""
    curve_db = _use_db(monkeypatch, tmp_path)
    exec_db = _use_execution_db(monkeypatch, tmp_path)
    upsert_equity_point("2026-09-30", 1.043, benchmark_nav=1.01, db_path=curve_db)
    upsert_paper_equity("2026-10-08", 1_000_000.0, 0.0, 1_000_000.0, db_path=exec_db)
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["paper"] is None


def test_overview_paper_excludes_future_rows(monkeypatch, tmp_path):
    """防前视：as_of（今天）之后的行不进对齐（虚拟与 paper 两序列同禁）。"""
    curve_db = _use_db(monkeypatch, tmp_path)
    exec_db = _use_execution_db(monkeypatch, tmp_path)
    upsert_equity_point("2026-09-01", 1.0, benchmark_nav=1.0, db_path=curve_db)
    upsert_equity_point("2099-01-01", 1.5, benchmark_nav=1.1, db_path=curve_db)
    upsert_paper_equity("2026-09-01", 1_000_000.0, 0.0, 1_000_000.0, db_path=exec_db)
    upsert_paper_equity("2099-01-01", 900_000.0, 0.0, 900_000.0, db_path=exec_db)
    paper = TestClient(app).get("/api/v1/track-record/overview").json()["paper"]
    assert [p["date"] for p in paper["series"]] == ["2026-09-01"]
    assert paper["loss_pct"] == 0.0


# ── add-paper-broker Task 2：test/seed 的 paper_equity 造数 ──


def test_seed_paper_equity_block(monkeypatch, tmp_path):
    """seed track_record.paper_equity → 落 execution 库（E2E 双曲线造数通道）。"""
    _use_db(monkeypatch, tmp_path)
    exec_db = _use_execution_db(monkeypatch, tmp_path)
    # reload 模式沿 test_seed_track_record_block（TESTING=1 + importlib.reload + finally 恢复）
    monkeypatch.setenv("TESTING", "1")
    import importlib

    import finance_agent.api as api_module
    import finance_agent.session_store as session_store_module

    monkeypatch.setattr(session_store_module, "_DB_PATH", tmp_path / "sessions.db")
    session_store_module.init_db()

    importlib.reload(api_module)
    try:
        client = TestClient(api_module.app)
        resp = client.post(
            "/api/test/seed",
            json={
                "track_record": {
                    "paper_equity": [
                        {
                            "trade_date": "2026-09-01",
                            "cash": 1_000_000.0,
                            "positions_value": 0.0,
                            "equity": 1_000_000.0,
                        },
                        {
                            "trade_date": "2026-09-30",
                            "cash": 998_000.0,
                            "positions_value": 33_000.0,
                            "equity": 1_031_000.0,
                        },
                    ]
                }
            },
        )
        assert resp.status_code == 200
        # track_record-only 造数返回占位响应（不创建会话）
        assert resp.json() == {"status": "ok", "mode": "testing"}
        assert client.get("/api/sessions").json()["sessions"] == []
        rows = list_paper_equity(db_path=exec_db)
        assert [r["trade_date"] for r in rows] == ["2026-09-01", "2026-09-30"]
        assert rows[1]["equity"] == 1_031_000.0
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)


def test_seed_track_record_reset_clears_dual_curve_tables(monkeypatch, tmp_path):
    """add-paper-broker T4：track_record_reset 清 equity_curve + paper_equity 两表。

    双曲线 E2E 每例前后复位用；predictions 等其它造数面不受影响（复位边界=
    双曲线造数面）。
    """
    curve_db = _use_db(monkeypatch, tmp_path)
    exec_db = _use_execution_db(monkeypatch, tmp_path)
    upsert_equity_point("2026-09-01", 1.0, benchmark_nav=1.0, db_path=curve_db)
    upsert_equity_point("2026-09-30", 1.043, benchmark_nav=1.01, db_path=curve_db)
    upsert_paper_equity("2026-09-01", 1_000_000.0, 0.0, 1_000_000.0, db_path=exec_db)
    upsert_paper_equity("2026-09-30", 998_000.0, 33_000.0, 1_031_000.0, db_path=exec_db)
    pred_id = _insert(curve_db)

    monkeypatch.setenv("TESTING", "1")
    import importlib

    import finance_agent.api as api_module
    import finance_agent.session_store as session_store_module

    monkeypatch.setattr(session_store_module, "_DB_PATH", tmp_path / "sessions.db")
    session_store_module.init_db()

    importlib.reload(api_module)
    try:
        client = TestClient(api_module.app)
        resp = client.post("/api/test/seed", json={"track_record_reset": True})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "mode": "testing"}
        assert list_equity_curve(db_path=curve_db) == []
        assert list_paper_equity(db_path=exec_db) == []
        # 复位边界：predictions 行原样保留
        preds = client.get("/api/v1/track-record/predictions").json()
        assert [p["prediction_id"] for p in preds["predictions"]] == [pred_id]
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)
