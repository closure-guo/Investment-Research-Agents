"""cohort 评估读数脚本测试（delta add-cohort-evaluation-program）。

fixture：2~3 只票 × 2026-10 真实窗口日期的小面板，断言四轨道口径行为：
反事实 r_e 手算值 / 日主去重 / 缺日剔除 / 窗口切分 / 熔断日剔除 / 聚类统计单位 /
只读零写入 / preliminary 到期闸 / confidence 归因闸。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "cohort_eval_readout.py"

spec = importlib.util.spec_from_file_location("cohort_eval_readout", _SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("cohort_eval_readout", mod)
spec.loader.exec_module(mod)


def _business_days(start: date, count: int) -> list[str]:
    days: list[str] = []
    d = start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def _db(path: Path, ddl: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(ddl)
    return conn


_COHORT_DDL = """
CREATE TABLE cohort_runs (
  run_id TEXT PRIMARY KEY, universe_version TEXT, ticker TEXT, trade_date TEXT,
  session_id TEXT, langfuse_trace_id TEXT, status TEXT, failure_reason TEXT,
  run_seq INTEGER DEFAULT 0, llm_calls INTEGER DEFAULT 0,
  tokens_prompt INTEGER DEFAULT 0, tokens_completion INTEGER DEFAULT 0,
  tokens_total INTEGER DEFAULT 0, trigger_time TEXT, created_at TEXT);
CREATE TABLE predictions (
  prediction_id TEXT PRIMARY KEY, source_type TEXT, symbol TEXT, symbol_name TEXT,
  direction TEXT, entry_price REAL, target_price REAL, horizon_days INTEGER DEFAULT 20,
  confidence REAL, benchmark TEXT DEFAULT '000300.SH', rationale_snapshot TEXT,
  langfuse_trace_id TEXT, status TEXT DEFAULT 'open', created_at TEXT,
  resolved_at TEXT, exit_price REAL, raw_return REAL, excess_return REAL,
  resolution_rule TEXT, updated_at TEXT, version_seq INTEGER DEFAULT 1,
  snapshot_hash TEXT, avoidance_status TEXT, session_id TEXT);
CREATE TABLE job_runs (
  run_id INTEGER PRIMARY KEY, job_id TEXT, started_at TEXT, status TEXT,
  summary TEXT);
CREATE TABLE index_closes (
  index_code TEXT, trade_date TEXT, close REAL);
"""

_SNAPSHOT_DDL = """
CREATE TABLE market_snapshot (
  trade_date TEXT, code TEXT, name TEXT, open REAL, high REAL, low REAL,
  close_raw REAL, close_qfq REAL, volume REAL, amount REAL, pct_chg REAL,
  prev_close REAL, is_st INTEGER DEFAULT 0, is_suspended INTEGER DEFAULT 0,
  limit_up_sealed INTEGER DEFAULT 0, limit_down_sealed INTEGER DEFAULT 0,
  listed_days INTEGER DEFAULT 0, list_date TEXT);
"""


def _build_fixture(tmp_path: Path) -> dict[str, object]:
    """2+1 票面板；返回路径与手算期望值。"""
    days = _business_days(date(2026, 9, 25), 60)  # 覆盖 9-25 ~ 12 月中
    d0, d1 = days[0], days[1]  # 2026-09-25, 09-28
    t20 = days[20]  # d0 的 T+20 交易日

    sess = _db(tmp_path / "sessions.db", _COHORT_DDL)
    snap = _db(tmp_path / "snapshot.db", _SNAPSHOT_DDL)

    # 指数：3000 起步、每日 +3 → close(T)=3000, close(T+20)=3060 → 基准腿 = 0.02
    idx_rows = [("000300", d, 3000.0 + 3.0 * i) for i, d in enumerate(days)]
    sess.executemany("INSERT INTO index_closes VALUES (?,?,?)", idx_rows)

    # 600001：T+1 开盘 10.0，T+20 收盘 11.0 → 个股腿 0.10，r_e = 0.10-0.02 = 0.08
    for i, d in enumerate(days):
        snap.execute(
            "INSERT INTO market_snapshot (trade_date, code, open, close_raw) VALUES (?,?,?,?)",
            (d, "600001", 10.0 + 0.05 * i, 11.0 if d == t20 else 10.0 + 0.05 * i),
        )
    # 600002：T+1 开盘 20.0，T+20 收盘 19.0 → 个股腿 -0.05，r_e = -0.05-0.02 = -0.07
    for i, d in enumerate(days):
        snap.execute(
            "INSERT INTO market_snapshot (trade_date, code, open, close_raw) VALUES (?,?,?,?)",
            (d, "600002", 20.0 - 0.05 * i, 19.0 if d == t20 else 20.0 - 0.05 * i),
        )
    # 600003：无行情行 → 缺日剔除样本

    # cohort_runs：d0 600001/600002 成功 + d1 600001 成功 + 10-13(W2) 600002 成功
    runs = [
        ("r-d0-a", "600001", d0, "s-d0-a", "t-d0-a", 80000),
        ("r-d0-b", "600002", d0, "s-d0-b", "t-d0-b", 80000),
        ("r-d1-a", "600001", d1, "s-d1-a", "t-d1-a", 80000),
        ("r-w2-a", "600002", "2026-10-13", "s-w2-a", "t-w2-a", 210000),
    ]
    for rid, tk, td, sid, tid, tok in runs:
        sess.execute(
            "INSERT INTO cohort_runs (run_id, universe_version, ticker, trade_date,"
            " session_id, langfuse_trace_id, status, tokens_total, trigger_time, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, "v1", tk, td, sid, tid, "success", tok, td + "T18:00:00", td + "T18:01:00"),
        )
    # 熔断日 10-09：success=0, failure=3, tokens=500000（剔成本、留成功率分母）
    sess.execute(
        "INSERT INTO job_runs (run_id, job_id, started_at, status, summary) VALUES (?,?,?,?,?)",
        (
            1,
            "cohort_batch",
            "2026-10-09T18:00:00",
            "ok",
            json.dumps(
                {"success": 0, "failure": 3, "tokens_total": 500000, "daily_budget": 3000000}
            ),
        ),
    )
    # 常规日 10-08：两行 ok（主跑 + 补跑）按日聚合 + 一行 failed 残行（跳过、计数）
    sess.execute(
        "INSERT INTO job_runs (run_id, job_id, started_at, status, summary) VALUES (?,?,?,?,?)",
        (
            2,
            "cohort_batch",
            "2026-10-08T18:00:00",
            "ok",
            json.dumps(
                {"success": 3, "failure": 0, "tokens_total": 240000, "daily_budget": 3000000}
            ),
        ),
    )
    sess.execute(
        "INSERT INTO job_runs (run_id, job_id, started_at, status, summary) VALUES (?,?,?,?,?)",
        (
            3,
            "cohort_batch",
            "2026-10-08T22:07:00",
            "ok",
            json.dumps(
                {"success": 1, "failure": 0, "tokens_total": 60000, "daily_budget": 3000000}
            ),
        ),
    )
    sess.execute(
        "INSERT INTO job_runs (run_id, job_id, started_at, status, summary) VALUES (?,?,?,?,?)",
        (4, "cohort_batch", "2026-10-28T20:34:00", "failed", None),
    )

    # predictions：d0 600001 neutral ×2（同票同日 → 取 created_at 最晚一条）+ 600002 short + d1 600001 neutral
    preds = [
        ("p-1", "600001.SH", "neutral", 0.55, "open", d0 + "T18:05:00", "s-d0-a"),  # 旧，弃
        ("p-2", "600001.SH", "neutral", 0.62, "open", d0 + "T18:09:00", "s-d0-a"),  # 日主
        ("p-3", "600002.SZ", "short", 0.60, "open", d0 + "T18:05:00", "s-d0-b"),
        ("p-4", "600001.SH", "neutral", 0.55, "open", d1 + "T18:05:00", "s-d1-a"),
        ("p-5", "600002.SZ", "short", 0.58, "resolved_win", "2026-10-13T18:05:00", "s-w2-a"),
    ]
    for pid, sym, dirc, conf, status, created, sid in preds:
        sess.execute(
            "INSERT INTO predictions (prediction_id, symbol, direction, confidence, status,"
            " created_at, session_id, langfuse_trace_id) VALUES (?,?,?,?,?,?,?,?)",
            (pid, sym, dirc, conf, status, created, sid, "t-x"),
        )
    sess.commit()
    sess.close()
    snap.commit()
    snap.close()

    return {"sessions": tmp_path / "sessions.db", "snapshot": tmp_path / "snapshot.db", "t20": t20}


def _run_readout(fx: dict[str, object]) -> dict:
    return mod.build_readout(
        sessions_db=Path(str(fx["sessions"])),
        snapshot_db=Path(str(fx["snapshot"])),
        as_of="2026-10-29",
        universe_version="v1",
        langfuse_fetch=lambda tid: [
            {"name": "citation_coverage", "value": 0.9},
            {"name": "citation_pass", "value": 0.8},
        ],
    )


def test_counterfactual_r_e_hand_computed(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    out = _run_readout(fx)
    neutral = out["track_c"]["neutral"]
    # d0 日主 600001 neutral：个股腿 = close(t20)/open(T+1) = 11/10.05，
    # 基准腿 = idx(t20)/idx(T) = 3060/3000 → r_e = 0.0945274 - 0.02 = 0.0745274
    sample = [
        s for s in neutral["samples"] if s["ticker"] == "600001" and s["trade_date"] == "2026-09-25"
    ]
    assert len(sample) == 1, "同票同日去重后应只剩日主一条"
    expected = (11.0 / 10.05 - 1.0) - (3060.0 / 3000.0 - 1.0)
    assert sample[0]["r_e"] == pytest.approx(expected, abs=1e-12)
    assert sample[0]["symbol"] == "600001", "symbol 归一为 6 位码"


def test_daily_cluster_aggregation_unit(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    out = _run_readout(fx)
    neutral = out["track_c"]["neutral"]
    # d0（600001 日主）与 d1（600001）各 1 条：先日均值再跨日（n_days=2）
    daily = {d["trade_date"]: d for d in neutral["daily_means"]}
    exp_d0 = (11.0 / 10.05 - 1.0) - (3060.0 / 3000.0 - 1.0)
    exp_d1 = (11.05 / 10.10 - 1.0) - (3063.0 / 3003.0 - 1.0)
    assert daily["2026-09-25"]["mean_r_e"] == pytest.approx(exp_d0, abs=1e-12)
    assert daily["2026-09-25"]["n"] == 1
    assert daily["2026-09-28"]["mean_r_e"] == pytest.approx(exp_d1, abs=1e-12)
    assert neutral["cross_day_mean"] == pytest.approx((exp_d0 + exp_d1) / 2, abs=1e-12)
    assert neutral["n_days"] == 2


def test_missing_bars_excluded_and_counted(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    out = _run_readout(fx)
    assert out["track_c"]["neutral"]["missing_bars"] == 0  # fixture 未造 600003 neutral 观点
    # 补一条无行情样本验证剔除：600003 无行情但有 neutral 观点
    sess = sqlite3.connect(str(fx["sessions"]))
    sess.execute(
        "INSERT INTO cohort_runs (run_id, universe_version, ticker, trade_date, session_id,"
        " langfuse_trace_id, status, tokens_total, trigger_time, created_at)"
        " VALUES ('r-miss','v1','600003','2026-09-25','s-miss','t-miss','success',80000,'x','y')"
    )
    sess.execute(
        "INSERT INTO predictions (prediction_id, symbol, direction, confidence, status,"
        " created_at, session_id) VALUES ('p-miss','600003.SH','neutral',0.5,'open','z','s-miss')"
    )
    sess.commit()
    sess.close()
    out2 = _run_readout(fx)
    assert out2["track_c"]["neutral"]["missing_bars"] == 1
    assert all(s["ticker"] != "600003" for s in out2["track_c"]["neutral"]["samples"])


def test_window_split_and_circuit_breaker_day(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    out = _run_readout(fx)
    a = out["track_a"]
    # W1 含 09-25/09-28 三次成功（tokens 240k）；W2 含 10-13 一次（210k）
    assert a["by_window"]["W1"]["success"] == 3
    assert a["by_window"]["W1"]["tokens_per_success"] == pytest.approx(80000.0)
    assert a["by_window"]["W2"]["success"] == 1
    assert a["by_window"]["W2"]["tokens_per_success"] == pytest.approx(210000.0)
    # 熔断日：10-09 success=0 → 出现在 circuit_days，且不进任何窗口成本均值
    assert [d["trade_date"] for d in a["circuit_days"]] == ["2026-10-09"]
    w1_cost_days = [d for d in a["cost_days"] if d["window"] == "W1"]
    assert all(d["trade_date"] != "2026-10-09" for d in w1_cost_days)
    # 同日多行 ok 聚合为一行（10-08：3+1 次、240k+60k）；failed 残行跳过并计数
    d08 = [d for d in a["cost_days"] if d["trade_date"] == "2026-10-08"]
    assert len(d08) == 1
    assert d08[0]["success"] == 4
    assert d08[0]["tokens_total"] == 300000
    assert a["job_rows_anomalous"] == 1
    # attempt 口径（§1.9⑧）：W1 = (3+1)/(3+1+0+3) —— 分母照计熔断日 failure
    assert a["by_window"]["W1"]["attempt_success_rate"] == pytest.approx(4 / 7)
    # failed-only 日（10-28 无 ok 行）不进 circuit 也不进 cost
    assert all(d["trade_date"] != "2026-10-28" for d in a["cost_days"])


def test_directional_preliminary_gate(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    out = _run_readout(fx)
    directional = out["track_c"]["directional"]
    # 仅 1 条已结算方向性观点 + as_of < 2026-11-15 → preliminary
    assert directional["settled_total"] == 1
    assert directional["formal_ready"] is False
    assert directional["preliminary"] is True


def test_flip_rate_and_shelf_life(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    out = _run_readout(fx)
    d = out["track_d"]
    # 基线：600001 d0 neutral → d1 neutral = 0 次翻转；600002 short→short = 0
    flips = {f["ticker"]: f["flips"] for f in d["flips_by_ticker"]}
    assert flips["600001"] == 0
    assert flips["600002"] == 0
    # 保鲜期：两条 short（600002 d0 open + 600002 10-13 resolved）在各 k 的超额符号均为负 → 一致
    shelf = {s["k"]: s for s in d["shelf_life"]}
    assert shelf[20]["agree_rate"] == pytest.approx(1.0)
    assert shelf[20]["n"] == 2
    # 正向用例：给 600001 加一条 10-13 short → neutral→short 翻转 1 次
    sess = sqlite3.connect(str(fx["sessions"]))
    sess.execute(
        "INSERT INTO cohort_runs (run_id, universe_version, ticker, trade_date, session_id,"
        " langfuse_trace_id, status, tokens_total, trigger_time, created_at)"
        " VALUES ('r-flip','v1','600001','2026-10-13','s-flip','t-flip','success',210000,'x','y')"
    )
    sess.execute(
        "INSERT INTO predictions (prediction_id, symbol, direction, confidence, status,"
        " created_at, session_id) VALUES ('p-flip','600001.SH','short',0.6,'open','z2','s-flip')"
    )
    sess.commit()
    sess.close()
    out2 = _run_readout(fx)
    flips2 = {f["ticker"]: f["flips"] for f in out2["track_d"]["flips_by_ticker"]}
    assert flips2["600001"] == 1


def test_confidence_gate_blocks_downstream(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    out = _run_readout(fx)
    conf = out["confidence"]
    # 归因闸：只允许分布与分桶，不允许校准/分组下游字段
    assert conf["attribution_required"] is True
    assert "calibration" not in json.dumps(conf)
    # 分布只统计日主（p-1 同票同日被去重，其 0.55 不得重复计数）
    assert sum(conf["dist"].values()) == 4


def test_readonly_zero_write(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    paths = [Path(str(fx["sessions"])), Path(str(fx["snapshot"]))]
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    _run_readout(fx)
    after = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    assert before == after, "评估读数必须零写入"


def test_citation_coverage_reported(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    out = _run_readout(fx)
    b = out["track_b"]
    # 4 条成功 trace，全部有 citation 分数 → 覆盖率 1.0；日均值进时间序列
    assert b["coverage"]["scored"] == 4
    assert b["coverage"]["total"] == 4
    assert b["coverage"]["rate"] == pytest.approx(1.0)
    dm = {r["trade_date"]: r for r in b["daily_means"]}
    assert dm["2026-09-25"]["citation_coverage"] == pytest.approx(0.9)


def test_track_b_only_citation_scores(tmp_path: Path) -> None:
    """非 citation 分数（judge 四维 / decision_* 收益值）不得混入轨道 B 日均值。"""
    fx = _build_fixture(tmp_path)
    out = mod.build_readout(
        sessions_db=Path(str(fx["sessions"])),
        snapshot_db=Path(str(fx["snapshot"])),
        as_of="2026-10-29",
        universe_version="v1",
        langfuse_fetch=lambda tid: [
            {"name": "citation_coverage", "value": 0.9},
            {"name": "decision_grounding（决策接地）", "value": 4.0},
            {"name": "decision_return", "value": -0.5},
        ],
    )
    dm = {r["trade_date"]: r for r in out["track_b"]["daily_means"]}
    assert "decision_grounding（决策接地）" not in dm["2026-09-25"]
    assert "decision_return" not in dm["2026-09-25"]
    assert dm["2026-09-25"]["citation_coverage"] == pytest.approx(0.9)


def test_score_index_filters_by_trace_id(tmp_path: Path) -> None:
    """scores API traceId 参数不可信（实测该版本返回全局分页）——
    索引必须按 score 行自身的 traceId 过滤，跨 trace 分数不得串。"""
    pages = [
        [
            {"traceId": "t-a", "name": "citation_coverage", "value": 0.9},
            {"traceId": "t-other", "name": "citation_coverage", "value": 0.1},
            {"traceId": "t-a", "name": "citation_pass", "value": 0.0},
        ],
        [
            {"traceId": "t-b", "name": "citation_coverage", "value": 0.5},
        ],
    ]
    cursor = {"page": 1}

    def fetch_page(page: int) -> tuple[list[dict], int]:
        assert page == cursor["page"], "分页必须顺序拉取（1 基，与 Langfuse API 一致）"
        data = pages[page - 1]
        cursor["page"] += 1
        return data, len(pages)

    index = mod.build_score_index(fetch_page)
    assert index("t-a") == [
        {"traceId": "t-a", "name": "citation_coverage", "value": 0.9},
        {"traceId": "t-a", "name": "citation_pass", "value": 0.0},
    ]
    assert index("t-b") == [{"traceId": "t-b", "name": "citation_coverage", "value": 0.5}]
    assert index("t-missing") == []
