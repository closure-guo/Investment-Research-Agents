"""add-track-record Task 4:track-record 只读 API 端点测试。"""

from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
    update_prediction_status,
    upsert_equity_point,
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
