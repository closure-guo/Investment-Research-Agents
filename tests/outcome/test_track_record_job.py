"""add-track-record Task 3:predictions 日批判定 job(settle_open_predictions)。

delta update-decision-settlement-contract Task 4:判定落库改用派生 settle_entry_price、
neutral 写 avoidance_status(不写 resolved_*)、long/short 上报 decision_* Score、
neutral 不走 superseded。
"""

import datetime
import logging

import pandas as pd
import pytest

from finance_agent.outcome.track_record.job import settle_open_predictions
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
    list_predictions,
)


def _db(tmp_path):
    # init_track_record_tables：predictions + daily_marks 等 stage-b/c 全表（api.py
    # 启动即双 init；update-track-record-display-clarity 起 list_predictions 读
    # open 行盯市，夹具须与生产 schema 对齐，否则读取批次报 no such table）
    db = tmp_path / "t.db"
    init_track_record_tables(db)
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
    """superseded 提前结算(状态转 resolved_*)上报 3 个 Score。

    update-superseded-excess 起超额不再是未知量：基准不可得时退化为 raw，
    故 decision_excess 一并上报（此前为「无同期基准区间」只报 2 个）。
    """
    db = _db(tmp_path)
    old = _insert(db, direction="long", created="2026-09-01T10:00:00", trace_id="trace-old")
    _insert(db, direction="short", created="2026-09-03T10:00:00")  # 反向 → supersede
    langfuse = _FakeLangfuse()
    client = _StubClient({"600519": _kline([100, 99, 98, 97])})
    result = settle_open_predictions(client=client, db_path=db, langfuse=langfuse)
    assert result["superseded"] == 1 and result["errors"] == 0
    assert result["scores_reported"] == 3
    assert {c["name"] for c in langfuse.calls} == {
        "decision_hit",
        "decision_return",
        "decision_excess",
    }
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


# ── update-superseded-excess：superseded 结算补基准腿（超额不再恒 None）──


def test_superseded_settlement_carries_excess_return(tmp_path):
    """superseded 结算 SHALL 用同期基准腿算超额并落库（此前恒 None）。

    个股 100→90（raw -10%）、基准 4000→3800（-5%）→ excess = -5%。
    """
    db = _db(tmp_path)
    old = _insert(db, direction="long", created="2026-09-01T10:00:00")
    _insert(db, direction="short", created="2026-09-03T10:00:00")  # 反向 → supersede
    client = _StubClient(
        {"600519": _kline([100, 100, 100, 90])},
        benchmark=_kline([4000, 4000, 4000, 3800]),
    )
    result = settle_open_predictions(client=client, db_path=db)
    assert result["superseded"] == 1 and result["errors"] == 0
    row = {r["prediction_id"]: r for r in list_predictions(db_path=db)}[old]
    assert row["raw_return"] == pytest.approx(-0.1, abs=1e-6)
    assert row["excess_return"] == pytest.approx(-0.05, abs=1e-6)


def test_superseded_classifies_on_excess_not_raw(tmp_path):
    """分类口径为**超额**：raw +3%、基准 +6% → excess -3% → resolved_loss。

    旧实现按 raw 分类，此例会记成 resolved_win（与 spec「区间超额收益 > +2%」相悖）。
    """
    db = _db(tmp_path)
    old = _insert(db, direction="long", created="2026-09-01T10:00:00")
    _insert(db, direction="short", created="2026-09-03T10:00:00")
    client = _StubClient(
        {"600519": _kline([100, 100, 100, 103])},
        benchmark=_kline([4000, 4000, 4000, 4240]),
    )
    result = settle_open_predictions(client=client, db_path=db)
    assert result["superseded"] == 1 and result["errors"] == 0
    row = {r["prediction_id"]: r for r in list_predictions(db_path=db)}[old]
    assert row["raw_return"] == pytest.approx(0.03, abs=1e-6)
    assert row["excess_return"] == pytest.approx(-0.03, abs=1e-6)
    assert row["status"] == "resolved_loss"


def test_superseded_without_benchmark_falls_back_to_raw(tmp_path):
    """基准不可得 → 超额退化为 raw（与 horizon 路径同一降级口径），不写 NULL。"""
    db = _db(tmp_path)
    old = _insert(db, direction="long", created="2026-09-01T10:00:00")
    _insert(db, direction="short", created="2026-09-03T10:00:00")
    client = _StubClient({"600519": _kline([100, 100, 100, 90])}, benchmark=None)
    settle_open_predictions(client=client, db_path=db)
    row = {r["prediction_id"]: r for r in list_predictions(db_path=db)}[old]
    assert row["excess_return"] == pytest.approx(row["raw_return"], abs=1e-6)


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


# ---- add-prediction-pool-integrity: 日主两阶段判定 ----


def _insert_open(db, symbol, created, direction="neutral", **kw):
    """落一条 open 观点（timestamp 即 created_at；与 ingest 落库同形）。"""
    return insert_prediction(
        {
            "source_type": "live",
            "symbol": symbol,
            "symbol_name": symbol,
            "direction": direction,
            "entry_price": 10.0,
            "horizon_days": 20,
            "confidence": 0.6,
            "rationale_snapshot": {},
            "langfuse_trace_id": None,
            "timestamp": created,
            "resolution_rule": None,
        },
        db_path=db,
        **kw,
    )


def test_same_day_rerun_closes_earlier_open_as_duplicate_of_day(tmp_path):
    """同日重跑：同 (symbol, 归属日) 仅 created_at 最晚者留池判定，其余关闭 duplicate_of_day。

    duplicate 行无结算读数（raw_return/resolved_at 均 NULL）、resolution_rule 标记；
    日主未到 horizon → 保持 open。
    """
    db = _db(tmp_path)
    # 688072 同日 10:00 与 11:00 各一条 neutral（收盘前 → 同归属日）
    _insert_open(db, "688072.SH", "2026-10-09T10:00:00")
    _insert_open(db, "688072.SH", "2026-10-09T11:00:00")
    klines = {"688072": _kline([10.0, 10.5, 11.0], start="2026-10-09")}
    result = settle_open_predictions(
        client=_StubClient(klines, benchmark=_kline([4000, 4010, 4020], start="2026-10-09")),
        db_path=db,
        langfuse=None,
    )
    rows = {r["prediction_id"]: r for r in list_predictions(db_path=db)}
    assert result["duplicates_closed"] == 1
    # 11:00（created_at 晚）为日主保持 open（未到 horizon）；10:00 关闭为 duplicate_of_day
    dup_rows = [r for r in rows.values() if r["status"] == "duplicate_of_day"]
    assert len(dup_rows) == 1
    dup_row = dup_rows[0]
    assert dup_row["created_at"].startswith("2026-10-09T10:00:00")  # 关的是早的那条
    # duplicate 行无结算读数
    assert dup_row["raw_return"] is None and dup_row["resolved_at"] is None
    assert dup_row["resolution_rule"] == "duplicate_of_day"
    master = next(r for r in rows.values() if r["status"] == "open")
    assert master["created_at"].startswith("2026-10-09T11:00:00")  # 留的是晚的那条


def test_superseded_settles_before_duplicate_dedup_in_same_day_chain(tmp_path):
    """同日观点变更链：superseded 先行（阶段一，读数保留），重跑观点不被误关 duplicate。

    short → long（方向变更 → 旧 short 按 superseded 结算保留读数），剩余 open 仅
    long 一条 → 无同日重复可关，duplicates_closed == 0。
    """
    db = _db(tmp_path)
    # 同日先 short 后 long（方向变更 → superseded 保留读数），再补一条 neutral 重跑
    _insert_open(db, "600519.SH", "2026-10-09T10:00:00", direction="short")
    _insert_open(db, "600519.SH", "2026-10-09T11:00:00", direction="long")
    klines = {"600519": _kline([10.0, 10.5, 11.0], start="2026-10-09")}
    result = settle_open_predictions(
        client=_StubClient(klines, benchmark=_kline([4000, 4010, 4020], start="2026-10-09")),
        db_path=db,
        langfuse=None,
    )
    assert result["superseded"] == 1
    assert result["duplicates_closed"] == 0
    rows = {r["prediction_id"]: r for r in list_predictions(db_path=db)}
    by_status = {}
    for r in rows.values():
        by_status.setdefault(r["status"], []).append(r)
    assert "resolved_loss" in by_status or "resolved_win" in by_status  # short 被结算（读数保留）
    assert all(r["status"] != "duplicate_of_day" for r in rows.values() if r["direction"] == "long")


def test_open_pool_beyond_50_rows_not_truncated(tmp_path):
    """>50 条 open 全量进入判定（修复波 F1：open 池读取截断回归钉）。

    list_predictions 默认 limit=50 会把 open 池静默截断——生产库 76 条 open 时
    最老 26 条永远不进判定/盯市。日批三处取数调用点必须显式 limit=100_000
    （与 model.py 钳制上限一致）：
      1. job.settle_open_predictions 首读（superseded 分组）
      2. job.settle_open_predictions remaining_open 重读（日主/horizon 判定）
      3. marking.mark_open_predictions（盯市）
    本例 55 条 open，最老两行构成 supersede 对：截断首读 → superseded 丢成 0；
    截断重读 → horizon 判定只处理最新 50 条 → settled 丢成 50。两条断言分别
    钉住 job 两处读数；marking 层为同款显式 limit（见该调用点注释）。
    """
    db = _db(tmp_path)
    # 最老两行：同标的方向反转（long → short）→ 阶段一应 supersede 旧观点
    old = _insert(db, symbol="600000.SH", direction="long", created="2026-09-01T09:59:00")
    _insert(db, symbol="600000.SH", direction="short", created="2026-09-01T10:00:00")
    # 其余 53 个标的各 1 条 → 共 55 条 open（created_at 依次递增，截断丢最老 5 条）
    for i in range(1, 54):
        _insert(db, symbol=f"600{i:03d}.SH", created=f"2026-09-01T10:{i:02d}:00")
    kline = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 115]
    codes = [f"600{i:03d}" for i in range(0, 54)]
    client = _StubClient(
        {c: _kline(kline) for c in codes},
        benchmark=_kline([4000.0] * 11, start="2026-09-01"),
    )
    result = settle_open_predictions(client=client, db_path=db)
    assert result["errors"] == 0
    assert result["superseded"] == 1  # 截断首读 → 0（最老 pair 被丢出池）
    assert result["settled"] == 54  # 截断重读 → 50（只判最新 50 条）
    assert result["duplicates_closed"] == 0
    rows = {r["prediction_id"]: r for r in list_predictions(limit=100_000, db_path=db)}
    assert rows[old]["resolution_rule"] == "superseded"


def test_second_pool_read_failure_terminates_batch(tmp_path, monkeypatch, caplog):
    """修复波 F2：remaining_open 重读失败 → 与首读同形守卫（logger.error + errors+1
    + 整批 return），调用方拿到 result dict 而非异常。

    文件头「失败隔离」承诺：读库失败不得炸 APScheduler 日批调用方；首读
    （:133-138）已有守卫，重读漏守卫与其不一致。
    """
    import finance_agent.outcome.track_record.job as job_module

    db = _db(tmp_path)
    _insert(db)
    real_list = job_module.list_predictions
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("db busy")
        return real_list(*args, **kwargs)

    monkeypatch.setattr(job_module, "list_predictions", flaky)
    client = _StubClient({"600519": _kline([100, 101])})
    with caplog.at_level(logging.ERROR):
        result = settle_open_predictions(client=client, db_path=db)
    assert calls["n"] == 2  # 首读成功、重读触发异常
    assert isinstance(result, dict)  # 返回 dict 而非抛出
    assert result["errors"] == 1
    assert result["settled"] == 0  # 整批终止：未进入 horizon 判定
    assert list_predictions(db_path=db)[0]["status"] == "open"
    assert any("读取 open 观点失败" in r.message for r in caplog.records)


def test_cross_day_predictions_are_each_day_master(tmp_path):
    """跨日两条各自为日主：归属日不同 → 不去重，duplicates_closed == 0，各自独立判定。"""
    db = _db(tmp_path)
    # 两个不同交易日各一条（合成日历含 10-09、10-10 → 各归属各日，独立成主）
    _insert_open(db, "600519.SH", "2026-10-09T10:00:00")
    _insert_open(db, "600519.SH", "2026-10-10T10:00:00")
    klines = {"600519": _kline([10.0] * 30, start="2026-10-09")}
    result = settle_open_predictions(
        client=_StubClient(klines, benchmark=_kline([4000.0] * 30, start="2026-10-09")),
        db_path=db,
        langfuse=None,
    )
    assert result["duplicates_closed"] == 0
    rows = list_predictions(db_path=db)
    assert all(r["status"] != "duplicate_of_day" for r in rows)
