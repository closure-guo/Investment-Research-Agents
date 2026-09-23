"""add-track-record Task 3:predictions 日批判定 job(settle_open_predictions)。

delta update-decision-settlement-contract Task 4:判定落库改用派生 settle_entry_price、
neutral 写 avoidance_status(不写 resolved_*)、long/short 上报 decision_* Score、
neutral 不走 superseded。
"""

import datetime
import logging

import pandas as pd

from finance_agent.outcome.track_record.job import settle_open_predictions
from finance_agent.outcome.track_record.model import (
    init_predictions,
    insert_prediction,
    list_predictions,
)


def _db(tmp_path):
    db = tmp_path / "t.db"
    init_predictions(db)
    return db


def _kline(prices, start="2026-09-02"):
    return pd.DataFrame(
        {
            "日期": [
                str(datetime.date.fromisoformat(start) + datetime.timedelta(days=i))
                for i in range(len(prices))
            ],
            "开盘": prices,
            "最高": prices,
            "最低": prices,
            "收盘": prices,
            "成交量": [1] * len(prices),
        }
    )


class _StubClient:
    def __init__(self, klines, benchmark=None):
        self.klines = klines
        self.benchmark = benchmark
        self.kline_adjusts: list[str] = []

    def fetch_kline(self, code, days=None, *, adjust="qfq"):
        self.kline_adjusts.append(adjust)
        return self.klines.get(code)

    def fetch_index_kline(self, code, days=None):
        return self.benchmark


class _FakeLangfuse:
    """收集 create_score 调用（先例 test_job.py 用 MagicMock，此处要按 trace_id 分流）。"""

    def __init__(self):
        self.calls: list[dict] = []

    def create_score(self, **kwargs):
        self.calls.append(kwargs)


def _flaky_langfuse(fail_name: str) -> _FakeLangfuse:
    """指定 name 的 create_score 抛错、其余正常记录（逐 Score 隔离用例共用）。"""
    langfuse = _FakeLangfuse()

    def flaky(**kwargs):
        if kwargs["name"] == fail_name:
            raise RuntimeError("trace expired")
        langfuse.calls.append(kwargs)

    langfuse.create_score = flaky  # type: ignore[method-assign]
    return langfuse


def _insert(
    db,
    symbol="600519.SH",
    entry=100.0,
    horizon=10,
    direction="long",
    created="2026-09-01T10:00:00",
    trace_id=None,
):
    return insert_prediction(
        {
            "source_type": "live",
            "symbol": symbol,
            "symbol_name": "贵州茅台",
            "direction": direction,
            "entry_price": entry,
            "horizon_days": horizon,
            "confidence": 0.8,
            "rationale_snapshot": {"action": "buy"},
            "created_at": created,
            "langfuse_trace_id": trace_id,
        },
        db_path=db,
    )


def test_settle_horizon_win(tmp_path):
    db = _db(tmp_path)
    pid = _insert(db)
    # 11 行:首行被派生入场吃掉,其后 10 行才够 horizon 10(delta:入场基准改为派生价)
    client = _StubClient(
        {"600519": _kline([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115])}
    )
    result = settle_open_predictions(client=client, db_path=db)
    assert result["settled"] == 1 and result["errors"] == 0
    row = list_predictions(db_path=db)[0]
    assert row["status"] == "resolved_win"
    assert row["prediction_id"] == pid


def test_settlement_job_requests_hfq(tmp_path):
    """结算路径取数必须显式后复权（hfq）：as-of 保真，与管线 qfq 输入口径分离。

    delta add-backtest-leakage-controls：结算/回测统一 hfq；管线（nodes/fetch）保持 qfq。
    """
    db = _db(tmp_path)
    _insert(db)
    client = _StubClient(
        {"600519": _kline([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115])}
    )
    settle_open_predictions(client=client, db_path=db)
    assert client.kline_adjusts  # 确有取数
    assert set(client.kline_adjusts) == {"hfq"}


def test_superseded_path_requests_hfq(tmp_path):
    """superseded 提前结算路径同样显式 hfq（与 horizon 路径同口径，不混用）。

    delta add-backtest-leakage-controls：若 superseded 路径漏传，记录集会出现 qfq 混入。
    """
    db = _db(tmp_path)
    _insert(db, direction="long", created="2026-09-01T10:00:00")
    _insert(db, direction="short", created="2026-09-03T10:00:00")  # 反向 → supersede 旧观点
    client = _StubClient({"600519": _kline([100, 99, 98, 97])})
    result = settle_open_predictions(client=client, db_path=db)
    assert result["superseded"] == 1
    assert client.kline_adjusts  # superseded 与 horizon 两路径均有取数
    assert set(client.kline_adjusts) == {"hfq"}


def test_settle_skips_not_enough_rows(tmp_path):
    db = _db(tmp_path)
    _insert(db)
    client = _StubClient({"600519": _kline([101, 102])})  # 2 行 < horizon 10
    result = settle_open_predictions(client=client, db_path=db)
    assert result["settled"] == 0 and result["skipped"] == 1
    assert list_predictions(db_path=db)[0]["status"] == "open"


def test_superseded_resolves_old(tmp_path):
    db = _db(tmp_path)
    old = _insert(db, direction="long", created="2026-09-01T10:00:00")
    _insert(db, direction="short", created="2026-09-03T10:00:00")  # 反向 → 旧观点被 supersede
    client = _StubClient({"600519": _kline([100, 99, 98, 97])})
    result = settle_open_predictions(client=client, db_path=db)
    assert result["superseded"] == 1
    rows = {r["prediction_id"]: r for r in list_predictions(db_path=db)}
    assert rows[old]["status"] in ("resolved_win", "resolved_loss", "resolved_neutral")
    assert rows[old]["resolution_rule"] == "superseded"


def test_stale_marks_unresolvable(tmp_path):
    db = _db(tmp_path)
    _insert(db, created="2026-09-01T10:00:00")
    # ticker 行情停在 9-01,基准已到 9-10(落后 > STALE_DAYS)
    client = _StubClient(
        {"600519": _kline([100], start="2026-09-01")},
        benchmark=_kline(
            [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110], start="2026-09-02"
        ),
    )
    result = settle_open_predictions(client=client, db_path=db)
    assert result["unresolvable"] == 1
    row = list_predictions(db_path=db)[0]
    assert row["status"] == "unresolvable"
    # 无结算日:写 NULL(原写 created_at[:10] 会让 segments 持仓天数分桶失真)
    assert row["resolved_at"] is None


# ── delta update-decision-settlement-contract Task 4 ──


def test_neutral_prediction_writes_avoidance_status(tmp_path):
    """neutral 观点到点判定 → 结果只入 avoidance_status,status 落终态 avoidance。

    终态(而非保持 open)是必须的:否则该行永远留在 open 池,日批无限重结算。
    """
    db = _db(tmp_path)
    pid = _insert(db, direction="neutral", horizon=20, entry=88.0)  # 参考价故意≠派生入场
    # 入场 09-01 收盘 11 → 20 个交易日后 09-21 收盘 9.9(-10%);基准持平 → 超额 = raw
    prices = [11.0] + [10.5] * 19 + [9.9]
    client = _StubClient(
        {"600519": _kline(prices, start="2026-09-01")},
        benchmark=_kline([4000.0] * 21, start="2026-09-01"),
    )
    langfuse = _FakeLangfuse()
    result = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert result["errors"] == 0
    row = list_predictions(db_path=db)[0]
    assert row["prediction_id"] == pid
    assert row["avoidance_status"] == "avoidance_win"  # 跑输 → 回避正确
    assert row["status"] == "avoidance"  # 终态:结果不入 status,状态不进 resolved_*
    assert row["status"] not in ("resolved_win", "resolved_loss", "resolved_neutral")
    assert list_predictions(status="open", db_path=db) == []  # 离开 open 池(不再重结算)
    assert row["settle_entry_price"] == 11.0  # 派生入场价落库
    assert row["resolved_at"] == "2026-09-21"  # 真实结算日
    assert langfuse.calls == []  # neutral 不上报 decision_* Score


def test_settled_rows_leave_open_pool_on_rerun(tmp_path):
    """幂等(重跑日批):已结算行不再进 open 池 → 不重复结算、不重复上报 Score。"""
    db = _db(tmp_path)
    _insert(db, trace_id="trace-long")
    client = _StubClient(
        {"600519": _kline([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115])}
    )
    langfuse = _FakeLangfuse()
    first = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert first["settled"] == 1 and first["scores_reported"] == 3
    second = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert second["settled"] == 0 and second["skipped"] == 0
    assert second["scores_reported"] == 0
    assert len(langfuse.calls) == 3  # 未重复上报
    assert list_predictions(db_path=db)[0]["status"] == "resolved_win"


def test_long_prediction_writes_settle_entry_price(tmp_path):
    """long 判定落库含派生 settle_entry_price(非参考价)与真实结算日 resolved_at。"""
    db = _db(tmp_path)
    _insert(db, entry=95.0)  # 参考价 95 ≠ 派生入场 100
    client = _StubClient(
        {
            "600519": _kline(
                [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115], start="2026-09-01"
            )
        }
    )
    result = settle_open_predictions(client=client, db_path=db)
    assert result["settled"] == 1 and result["errors"] == 0
    row = list_predictions(db_path=db)[0]
    assert row["status"] == "resolved_win"
    assert row["entry_price"] == 95.0  # 参考价不动(展示/盯市口径)
    assert row["settle_entry_price"] == 100.0  # 派生结算入场价
    assert row["resolved_at"] == "2026-09-11"  # 真实结算日,非 created_at[:10]
    assert abs(row["raw_return"] - 0.15) < 1e-9  # 基于派生入场价计算


def test_scores_reported_for_long_only(tmp_path):
    """long 结算上报 3 个 decision_* Score;neutral 回避判定上报 0 个。"""
    db = _db(tmp_path)
    _insert(db, symbol="600519.SH", trace_id="trace-long")
    _insert(db, symbol="000001.SZ", direction="neutral", horizon=20, trace_id="trace-neutral")
    client = _StubClient(
        {
            "600519": _kline([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115]),
            "000001": _kline([11.0] + [10.5] * 19 + [9.9], start="2026-09-01"),
        },
        benchmark=_kline([4000.0] * 21, start="2026-09-01"),
    )
    langfuse = _FakeLangfuse()
    result = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert result["errors"] == 0
    assert result["scores_reported"] == 3
    assert {c["name"] for c in langfuse.calls} == {
        "decision_hit",
        "decision_return",
        "decision_excess",
    }
    assert {c["trace_id"] for c in langfuse.calls} == {"trace-long"}  # neutral 不报
    by_name = {c["name"]: c for c in langfuse.calls}
    assert by_name["decision_hit"]["data_type"] == "BOOLEAN"
    assert by_name["decision_hit"]["value"] == 1.0
    assert abs(by_name["decision_return"]["value"] - 0.15) < 1e-9
    assert "settle_price=115" in by_name["decision_return"]["comment"]


def test_score_failure_does_not_block_settlement(tmp_path, caplog):
    """Score 上报异常仅 WARN:判定仍落库,不中断整批(旁路铁律)。"""
    db = _db(tmp_path)
    _insert(db, trace_id="trace-long")
    langfuse = _FakeLangfuse()

    def boom(**kwargs):
        raise RuntimeError("trace expired")

    langfuse.create_score = boom  # type: ignore[method-assign]
    client = _StubClient(
        {"600519": _kline([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115])}
    )
    with caplog.at_level(logging.WARNING):
        result = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert result["settled"] == 1 and result["scores_reported"] == 0
    assert list_predictions(db_path=db)[0]["status"] == "resolved_win"
    assert any("score 上报失败" in r.message for r in caplog.records)


def test_superseded_skips_neutral_old(tmp_path):
    """neutral 旧观点不走 superseded(回避判定以完整 horizon 窗口为准)。"""
    db = _db(tmp_path)
    old = _insert(db, direction="neutral", created="2026-09-01T10:00:00")
    _insert(db, direction="short", created="2026-09-03T10:00:00")
    client = _StubClient({"600519": _kline([100, 99, 98, 97])})
    result = settle_open_predictions(client=client, db_path=db)
    assert result["superseded"] == 0
    row = {r["prediction_id"]: r for r in list_predictions(db_path=db)}[old]
    assert row["status"] == "open"
    assert row["resolution_rule"] is None
    assert row["avoidance_status"] is None


def test_superseded_long_uses_derived_entry(tmp_path):
    """superseded 提前结算同样用派生入场价并落 settle_entry_price。"""
    db = _db(tmp_path)
    old = _insert(db, entry=95.0, direction="long", created="2026-09-01T10:00:00")
    _insert(db, direction="short", created="2026-09-03T10:00:00")  # 反向 → supersede 旧观点
    client = _StubClient({"600519": _kline([100, 99, 98, 97])})  # 09-02..09-05
    result = settle_open_predictions(client=client, db_path=db)
    assert result["superseded"] == 1
    row = {r["prediction_id"]: r for r in list_predictions(db_path=db)}[old]
    assert row["resolution_rule"] == "superseded"
    assert row["settle_entry_price"] == 100.0  # 派生入场 = 首行收盘
    assert row["resolved_at"] == "2026-09-05"  # 结算日 = 最新行情日
    assert row["status"] == "resolved_loss"  # 100 → 97


# ── Δ2T4 质量审查路由：Score 守卫补测 + superseded 上报 + 入场/时间戳对齐 ──


def test_scores_skipped_without_trace_id(tmp_path):
    """缺 langfuse_trace_id → 不上报 Score(trace 不可关联),判定仍落库。"""
    db = _db(tmp_path)
    _insert(db, trace_id=None)
    langfuse = _FakeLangfuse()
    client = _StubClient(
        {"600519": _kline([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115])}
    )
    result = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert result["settled"] == 1 and result["scores_reported"] == 0
    assert langfuse.calls == []
    assert list_predictions(db_path=db)[0]["status"] == "resolved_win"


def test_score_partial_failure_reports_others(tmp_path, caplog):
    """单个 Score 抛错不吞掉其余(逐 Score 隔离):decision_excess 失败仍计 2。"""
    db = _db(tmp_path)
    _insert(db, trace_id="trace-long")
    langfuse = _flaky_langfuse("decision_excess")
    client = _StubClient(
        {"600519": _kline([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115])}
    )
    with caplog.at_level(logging.WARNING):
        result = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert result["settled"] == 1 and result["scores_reported"] == 2
    assert {c["name"] for c in langfuse.calls} == {"decision_hit", "decision_return"}
    assert list_predictions(db_path=db)[0]["status"] == "resolved_win"
    assert any("decision_excess" in r.message for r in caplog.records)


def test_score_failure_does_not_abort_later_scores(tmp_path):
    """中间一个 Score 失败不影响其后 Score(整体放弃/提前 return 会红)。

    与上例互补:decision_excess 是末尾项,单独测它无法区分「跳过失败项」与
    「首个失败即整体放弃」,故此处让中间的 decision_return 失败。
    """
    db = _db(tmp_path)
    _insert(db, trace_id="trace-long")
    langfuse = _flaky_langfuse("decision_return")
    client = _StubClient(
        {"600519": _kline([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115])}
    )
    result = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert result["settled"] == 1 and result["scores_reported"] == 2
    assert {c["name"] for c in langfuse.calls} == {"decision_hit", "decision_excess"}


def test_langfuse_none_leg_skips_scores(tmp_path, monkeypatch, caplog):
    """不带 langfuse kwarg → 走内部 fallback 取 get_langfuse;其为 None 时不上报、不 WARN。

    None 腿必须早退:否则会拿 None 调 create_score,AttributeError 被兜底吞成 WARN。
    """
    db = _db(tmp_path)
    _insert(db, trace_id="trace-long")
    calls: list[str] = []

    def fake_get_langfuse():
        calls.append("called")
        return None

    monkeypatch.setattr("finance_agent.langfuse_tracing.get_langfuse", fake_get_langfuse)
    client = _StubClient(
        {"600519": _kline([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115])}
    )
    with caplog.at_level(logging.WARNING):
        result = settle_open_predictions(client=client, db_path=db)
    assert calls == ["called"]  # fallback 确实被执行
    assert result["settled"] == 1 and result["scores_reported"] == 0
    assert not any("score 上报失败" in r.message for r in caplog.records)


def test_superseded_reports_scores(tmp_path):
    """superseded 提前结算(状态转 resolved_*)同样上报 Score;excess 未知 → 只报 2 个。"""
    db = _db(tmp_path)
    old = _insert(db, direction="long", created="2026-09-01T10:00:00", trace_id="trace-old")
    _insert(db, direction="short", created="2026-09-03T10:00:00")  # 反向 → supersede
    langfuse = _FakeLangfuse()
    client = _StubClient({"600519": _kline([100, 99, 98, 97])})
    result = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert result["superseded"] == 1 and result["errors"] == 0
    assert result["scores_reported"] == 2  # 无同期基准区间 → 不报 decision_excess
    assert {c["name"] for c in langfuse.calls} == {"decision_hit", "decision_return"}
    assert {c["trace_id"] for c in langfuse.calls} == {"trace-old"}  # 用旧观点的 trace
    by_name = {c["name"]: c for c in langfuse.calls}
    assert by_name["decision_hit"]["data_type"] == "BOOLEAN"
    assert by_name["decision_hit"]["value"] == 0.0  # 100 → 97
    assert "settle_price=97" in by_name["decision_return"]["comment"]
    rows = {r["prediction_id"]: r for r in list_predictions(db_path=db)}
    assert rows[old]["status"] == "resolved_loss"


# ── Δ2 Task 5：superseded 与 horizon 路径共用 ±2% 中性带（原按 raw 符号三分）──


def test_superseded_within_neutral_band_is_resolved_neutral(tmp_path):
    """|raw| < 2% → resolved_neutral（不按符号三分；与 horizon 路径同口径）。"""
    db = _db(tmp_path)
    old = _insert(db, direction="long", created="2026-09-01T10:00:00")
    _insert(db, direction="short", created="2026-09-03T10:00:00")  # 反向 → supersede
    client = _StubClient({"600519": _kline([100, 101])})  # 100 → 101 = +1% 带内
    result = settle_open_predictions(client=client, db_path=db)
    assert result["superseded"] == 1 and result["errors"] == 0
    row = {r["prediction_id"]: r for r in list_predictions(db_path=db)}[old]
    assert row["status"] == "resolved_neutral"
    assert row["resolution_rule"] == "superseded"


def test_superseded_beyond_band_still_win(tmp_path):
    """|raw| >= 2% 仍按方向判 win/loss（+3% → resolved_win）。"""
    db = _db(tmp_path)
    old = _insert(db, direction="long", created="2026-09-01T10:00:00")
    _insert(db, direction="short", created="2026-09-03T10:00:00")
    client = _StubClient({"600519": _kline([100, 103])})  # +3% 超带
    result = settle_open_predictions(client=client, db_path=db)
    assert result["superseded"] == 1 and result["errors"] == 0
    row = {r["prediction_id"]: r for r in list_predictions(db_path=db)}[old]
    assert row["status"] == "resolved_win"


def test_superseded_within_band_for_short_is_neutral(tmp_path):
    """short 方向对称：raw（已按 short 取负）落在带内 → resolved_neutral。"""
    db = _db(tmp_path)
    old = _insert(db, direction="short", created="2026-09-01T10:00:00")
    _insert(db, direction="long", created="2026-09-03T10:00:00")  # 反向 → supersede
    client = _StubClient({"600519": _kline([100, 101])})  # short raw = -(101/100-1) = -1%
    result = settle_open_predictions(client=client, db_path=db)
    assert result["superseded"] == 1
    row = {r["prediction_id"]: r for r in list_predictions(db_path=db)}[old]
    assert row["status"] == "resolved_neutral"


def test_superseded_zero_entry_skips(tmp_path):
    """派生入场价 <= 0 → superseded 路径跳过(不写 resolved_neutral + 入场价 0)。"""
    db = _db(tmp_path)
    old = _insert(db, direction="long", created="2026-09-01T10:00:00")
    _insert(db, direction="short", created="2026-09-03T10:00:00")  # 反向 → 本应 supersede
    langfuse = _FakeLangfuse()
    client = _StubClient({"600519": _kline([0, 99, 98, 97])})  # 派生入场 = 首行收盘 0
    result = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert result["superseded"] == 0 and result["errors"] == 0
    # superseded 路径 1 次(旧行 entry<=0)+ horizon 路径 2 次(旧行 entry<=0 判 None、
    # 新行入场后仅 2 行 < horizon 10)
    assert result["skipped"] == 3
    assert result["scores_reported"] == 0
    row = {r["prediction_id"]: r for r in list_predictions(db_path=db)}[old]
    assert row["status"] == "open"
    assert row["settle_entry_price"] is None
    assert row["resolution_rule"] is None
