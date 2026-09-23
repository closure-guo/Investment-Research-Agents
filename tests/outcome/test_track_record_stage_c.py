"""add-track-record-stage-c：版本分段/完整性校验/校准/切片 测试。"""

from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.outcome.track_record.calibration import calibration_table, outcome_value
from finance_agent.outcome.track_record.model import (
    append_audit,
    compute_snapshot_hash,
    get_active_agent,
    get_prediction,
    init_track_record_tables,
    insert_daily_mark,
    insert_prediction,
    integrity_check,
    list_agents,
    list_audit,
    register_agent,
    update_prediction_status,
)
from finance_agent.outcome.track_record.segments import (
    market_env_signal,
    segment_all,
    segment_by_holding,
    segment_by_industry,
    segment_by_market_cap,
    segment_by_market_environment,
)

BASE = {
    "source_type": "live",
    "symbol": "600519.SH",
    "symbol_name": "贵州茅台",
    "direction": "long",
    "entry_price": 100.0,
    "horizon_days": 252,
    "confidence": 0.85,
    "rationale_snapshot": {"markdown": "原文", "decision": {"action": "buy"}},
    "created_at": "2026-06-01T10:00:00",
}


def _mkdb(tmp_path):
    db = tmp_path / "t.db"
    init_track_record_tables(db)
    return db


def _insert(db, **overrides):
    rec = dict(BASE)
    rec.update(overrides)
    return insert_prediction(rec, db_path=db)


def _resolve(db, pid, status="resolved_win", source="system", **extra):
    update_prediction_status(
        pid,
        {
            "status": status,
            "raw_return": 0.1,
            "excess_return": 0.05,
            "resolution_rule": "expiry",
            **extra,
        },
        db_path=db,
        source=source,
    )


class TestSnapshotHash:
    def test_stable_across_key_order(self):
        a = compute_snapshot_hash({"b": 1, "a": {"x": 2}})
        b = compute_snapshot_hash({"a": {"x": 2}, "b": 1})
        assert a == b

    def test_differs_on_change(self):
        assert compute_snapshot_hash({"a": 1}) != compute_snapshot_hash({"a": 2})


class TestAgents:
    def test_register_and_retire_flow(self, tmp_path):
        db = _mkdb(tmp_path)
        first = register_agent("glm-4.5", db_path=db)
        assert first["version_seq"] == 1
        assert get_active_agent(db_path=db)["agent_id"] == first["agent_id"]
        second = register_agent("glm-4.6", note="升级", db_path=db)
        assert second["version_seq"] == 2
        assert get_active_agent(db_path=db)["agent_id"] == second["agent_id"]
        agents = list_agents(db_path=db)
        assert agents[0]["retired_at"] is not None  # 旧版本已封存

    def test_insert_prediction_defaults_to_active_version(self, tmp_path):
        db = _mkdb(tmp_path)
        register_agent("v1", db_path=db)
        pid = _insert(db)
        p = get_prediction(pid, db_path=db)
        assert p["version_seq"] == 1
        assert p["snapshot_hash"] == compute_snapshot_hash(BASE["rationale_snapshot"])


class TestAudit:
    def test_status_change_logged(self, tmp_path):
        db = _mkdb(tmp_path)
        pid = _insert(db)
        _resolve(db, pid, status="resolved_loss", source="unit")
        entries = list_audit(pid, db_path=db)
        assert len(entries) == 1
        assert entries[0]["old_status"] == "open"
        assert entries[0]["new_status"] == "resolved_loss"
        assert entries[0]["source"] == "unit"

    def test_no_audit_when_status_unchanged(self, tmp_path):
        db = _mkdb(tmp_path)
        pid = _insert(db)
        _resolve(db, pid, status="resolved_win")
        update_prediction_status(pid, {"raw_return": 0.2}, db_path=db)  # 仅改非状态字段
        assert len(list_audit(pid, db_path=db)) == 1

    def test_append_audit_explicit(self, tmp_path):
        db = _mkdb(tmp_path)
        pid = _insert(db)
        append_audit(
            pid, "integrity_mismatch", detail="hash 不一致", source="integrity-check", db_path=db
        )
        assert list_audit(pid, db_path=db)[0]["action"] == "integrity_mismatch"


class TestIntegrity:
    def test_clean_no_mismatch(self, tmp_path):
        db = _mkdb(tmp_path)
        _insert(db)
        result = integrity_check(db_path=db)
        assert result["checked"] == 1
        assert result["mismatch_count"] == 0

    def test_tampered_detected_and_audited(self, tmp_path):
        db = _mkdb(tmp_path)
        pid = _insert(db)
        import sqlite3

        conn = sqlite3.connect(db)
        conn.execute(
            "UPDATE predictions SET rationale_snapshot=? WHERE prediction_id=?",
            ('{"markdown": "被篡改"}', pid),
        )
        conn.commit()
        conn.close()
        result = integrity_check(db_path=db)
        assert result["mismatch_count"] == 1
        assert result["mismatches"][0]["prediction_id"] == pid
        assert list_audit(pid, db_path=db)[-1]["action"] == "integrity_mismatch"


def _calib_preds():
    return [
        {"confidence": 0.55, "status": "resolved_win"},
        {"confidence": 0.55, "status": "resolved_loss"},
        {"confidence": 0.75, "status": "resolved_win"},
        {"confidence": 0.75, "status": "resolved_neutral"},
        {"confidence": 0.95, "status": "resolved_loss"},
        {"confidence": 0.85, "status": "open"},  # 未结算不进桶
        {"confidence": None, "status": "resolved_win"},  # 无置信度剔除
    ]


class TestCalibration:
    def test_buckets_and_hit_rate(self):
        result = calibration_table(_calib_preds())
        assert result.sample_size == 5
        by_bucket = {b["bucket"]: b for b in result.buckets}
        assert by_bucket["[0.5,0.6)"]["n"] == 2
        assert by_bucket["[0.5,0.6)"]["hit_rate"] == 0.5
        assert by_bucket["[0.7,0.8)"]["hit_rate"] == 0.75  # (1+0.5)/2
        assert result.brier is not None

    def test_neutral_excluded_when_none(self):
        result = calibration_table(_calib_preds(), neutral_prob=None)
        assert result.sample_size == 4  # neutral 剔除

    def test_brier_formula(self):
        # ((0.8-1)^2 + (0.2-0)^2)/2 = 0.04
        preds = [
            {"confidence": 0.8, "status": "resolved_win"},
            {"confidence": 0.2, "status": "resolved_loss"},
        ]
        result = calibration_table(preds)
        assert result.brier == 0.04

    # ── Δ2 Task 5：回避终态按 avoidance_status 映射（原 status='avoidance' 落 None）──

    def test_outcome_value_avoidance_mapping(self):
        assert outcome_value({"status": "avoidance", "avoidance_status": "avoidance_win"}) == 1.0
        assert outcome_value({"status": "avoidance", "avoidance_status": "avoidance_loss"}) == 0.0
        assert (
            outcome_value({"status": "avoidance", "avoidance_status": "avoidance_neutral"}) == 0.5
        )

    def test_outcome_value_avoidance_undecided_skipped(self):
        # avoidance_status 缺失/为空 → 不参与（原状态机未判定，非结果）
        assert outcome_value({"status": "avoidance", "avoidance_status": None}) is None
        assert outcome_value({"status": "avoidance"}) is None

    def test_outcome_value_other_statuses_unchanged(self):
        assert outcome_value({"status": "resolved_win"}) == 1.0
        assert outcome_value({"status": "resolved_loss"}) == 0.0
        assert outcome_value({"status": "resolved_neutral"}) == 0.5
        assert outcome_value({"status": "open"}) is None
        assert outcome_value({"status": "unresolvable"}) is None

    def test_calibration_includes_avoidance_rows(self):
        preds = [
            {"confidence": 0.8, "status": "avoidance", "avoidance_status": "avoidance_win"},
            {"confidence": 0.8, "status": "avoidance", "avoidance_status": "avoidance_neutral"},
            {"confidence": 0.8, "status": "avoidance", "avoidance_status": None},  # 跳过
        ]
        result = calibration_table(preds)
        assert result.sample_size == 2
        by_bucket = {b["bucket"]: b for b in result.buckets}
        assert by_bucket["[0.8,0.9)"]["hit_rate"] == 0.75  # (1.0 + 0.5)/2


def _seg_preds():
    # direction="long"：切片胜率/平均超额人口限 long/short 三态（§1.8①），
    # 缺 direction 会让本文件的分桶基线只覆盖样本数、不覆盖 win_rate/avg_excess。
    return [
        {
            "prediction_id": "p1",
            "symbol": "600519.SH",
            "direction": "long",
            "created_at": "2026-06-01T00:00:00",
            "resolved_at": "2026-06-10",
            "status": "resolved_win",
            "excess_return": 0.1,
        },
        {
            "prediction_id": "p2",
            "symbol": "600519.SH",
            "direction": "long",
            "created_at": "2026-06-01T00:00:00",
            "resolved_at": "2026-06-02",
            "status": "resolved_loss",
            "excess_return": -0.05,
        },
        {
            "prediction_id": "p3",
            "symbol": "999999.SH",  # 未知行业
            "direction": "long",
            "created_at": "2026-06-01T00:00:00",
            "resolved_at": "2026-07-01",
            "status": "resolved_win",
            "excess_return": 0.03,
        },
    ]


class TestSegments:
    def test_holding_period_buckets(self):
        dim = segment_by_holding(_seg_preds())
        by = {b.name: b for b in dim.buckets}
        # p1: 06-10 - 06-01 = 9 天 → 6-20 天; p2: 1 天 → 0-5 天; p3: 30 天 → 21-60 天
        assert by["0-5天"].sample_size == 1
        assert by["6-20天"].sample_size == 1
        assert by["21-60天"].sample_size == 1
        assert by["61天+"].sample_size == 0

    def test_industry_map_and_unknown(self):
        dim = segment_by_industry(_seg_preds())
        by = {b.name: b for b in dim.buckets}
        assert by["白酒"].sample_size == 2
        assert by["未知"].sample_size == 1

    def test_market_cap_buckets(self):
        dim = segment_by_market_cap(
            _seg_preds(), market_caps={"p1": 50.0, "p2": 300.0, "p3": 800.0}
        )
        by = {b.name: b for b in dim.buckets}
        assert by["<100亿"].sample_size == 1
        assert by["100-500亿"].sample_size == 1
        assert by["500亿+"].sample_size == 1
        assert by["未知"].sample_size == 0

    def test_market_environment(self):
        dim = segment_by_market_environment(_seg_preds(), market_envs={"p1": "bull", "p2": "bear"})
        by = {b.name: b for b in dim.buckets}
        assert by["牛"].sample_size == 1
        assert by["熊"].sample_size == 1
        assert by["未知"].sample_size == 1

    def test_win_rate_excludes_neutral(self):
        dim = segment_by_market_environment(_seg_preds(), market_envs={})
        by = {b.name: b for b in dim.buckets}
        assert by["未知"].sample_size == 3

    # ── Δ2 Task 5：切片口径对齐 §1.8①（胜率限 long/short；avoidance 不进胜率/超额）──

    def test_win_rate_requires_long_short_and_excludes_avoidance(self):
        preds = [
            {
                "prediction_id": "p1",
                "symbol": "600519.SH",
                "direction": "long",
                "status": "resolved_win",
                "excess_return": 0.1,
            },
            {
                "prediction_id": "p2",
                "symbol": "600519.SH",
                "direction": "short",
                "status": "resolved_loss",
                "excess_return": -0.05,
            },
            {
                "prediction_id": "p3",
                "symbol": "600519.SH",
                "direction": "long",
                "status": "resolved_neutral",
                "excess_return": 0.0,
            },
            # 存量遗留：neutral 方向却落 resolved_win → 不进胜率也不进超额
            {
                "prediction_id": "p4",
                "symbol": "600519.SH",
                "direction": "neutral",
                "status": "resolved_win",
                "excess_return": 0.5,
            },
            # 回避终态：有 excess_return 但不属主指标人口
            {
                "prediction_id": "p5",
                "symbol": "600519.SH",
                "direction": "neutral",
                "status": "avoidance",
                "avoidance_status": "avoidance_win",
                "excess_return": -0.2,
            },
        ]
        dim = segment_by_industry(preds)
        bucket = {b.name: b for b in dim.buckets}["白酒"]
        assert bucket.sample_size == 5  # 计数/分桶分母保留全量
        assert bucket.win_rate == 0.5  # 1 win / (1 win + 1 loss)
        assert bucket.avg_excess == round((0.1 - 0.05 + 0.0) / 3, 4)  # long/short 三态
        assert dim.settled == 2

    def test_win_rate_none_when_no_long_short_decided(self):
        """无 long/short 已判定行 → 胜率 None（旧行为会把 neutral 方向 resolved_* 计入）。"""
        preds = [
            {
                "prediction_id": "n1",
                "symbol": "600519.SH",
                "direction": "neutral",
                "status": "resolved_win",
                "excess_return": 0.5,
            }
        ]
        dim = segment_by_industry(preds)
        bucket = {b.name: b for b in dim.buckets}["白酒"]
        assert bucket.win_rate is None and bucket.avg_excess is None
        assert bucket.sample_size == 1

    def test_market_env_signal(self):
        rising = list(range(300))  # 上涨 > MA250
        assert market_env_signal(rising) == "bull"
        flat = [100] * 260 + [80] * 10
        assert market_env_signal(flat) == "bear"
        assert market_env_signal([1, 2]) == "unknown"

    def test_segment_all_includes_four_dimensions(self):
        dims = segment_all(_seg_preds())
        assert [d.dimension for d in dims] == ["持有期", "行业", "市值", "市场环境"]


# ── API 层 ──


def _use_db(monkeypatch, tmp_path):
    db = _mkdb(tmp_path)
    monkeypatch.setattr("finance_agent.outcome.track_record.model._default_db_path", lambda: db)
    return db


def test_api_calibration(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    pid = _insert(db, confidence=0.8)
    _resolve(db, pid, status="resolved_win")
    data = TestClient(app).get("/api/v1/track-record/calibration").json()
    assert data["sample_size"] == 1
    assert data["brier"] is not None
    assert data["disclaimer"]


def test_api_segments(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    _insert(db)
    data = TestClient(app).get("/api/v1/track-record/segments").json()
    dims = {d["dimension"]: d for d in data["dimensions"]}
    assert set(dims) == {"持有期", "行业", "市值", "市场环境"}
    by = {b["name"]: b for b in dims["行业"]["buckets"]}
    assert by["白酒"]["sample_size"] == 1


def test_api_detail_404(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    resp = TestClient(app).get("/api/v1/track-record/predictions/nope")
    assert resp.status_code == 404


def test_api_detail_with_audit_and_marks(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    pid = _insert(db)
    _resolve(db, pid, status="resolved_win")
    insert_daily_mark(pid, "2026-06-02", 101.0, 0.01, -0.02, 3100.0, db_path=db)
    data = TestClient(app).get(f"/api/v1/track-record/predictions/{pid}").json()
    assert data["prediction"]["prediction_id"] == pid
    assert len(data["audit"]) == 1
    assert data["marks"][0]["mark_date"] == "2026-06-02"


def test_api_overview_version_param(monkeypatch, tmp_path):
    db = _use_db(monkeypatch, tmp_path)
    register_agent("v1", db_path=db)
    pid = _insert(db, horizon_days=20)  # 头条口径窗口（Δ2 Task 5：overview 限 T+20）
    _resolve(db, pid)
    register_agent("v2", db_path=db)
    # 默认取当前版本（v2）→ 无观点
    data = TestClient(app).get("/api/v1/track-record/overview").json()
    assert data["version_seq"] == 2
    assert data["total"] == 0
    # 指定 v1 → 1 条
    data1 = TestClient(app).get("/api/v1/track-record/overview", params={"version": 1}).json()
    assert data1["total"] == 1
    assert len(data1["versions"]) == 2
