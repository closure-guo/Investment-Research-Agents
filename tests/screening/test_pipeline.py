"""screening/pipeline.py 单测（add-screening-pipeline T1/T2/T3 前置）。

计划 docs/superpowers/plans/2026-10-13-add-screening-pipeline.md 关键设计决策：

- S1 日批编排：快照新鲜度（``latest_snapshot_date == as_of``，as_of 缺省 = 最新
  快照日）→ L1 过滤 → L2 打分（Top-N 全量落库）→ L3 triage → 精选池
  （``keep`` ∩ ``eligible_for_selection``）；**fail-closed**：任一步失败 → 批次行
  ``status=failed`` + 当日池零行；批次 ok 与池行**单事务**同生共死；
- S2 时效性校验：``SUSPENDED`` / ``LIMIT_LOCKED`` / ``NEW_EVENT``（逐字），
  无问题者不入返回列表，多因按 S2 顺序全量返回。

零网络：面板/事件/stub triage client 全部注入；默认 client 与默认事件装配被
autouse fixture 钉死为不可用/空（任何测试不得触网）。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from finance_agent.data.snapshot import set_meta_many, write_snapshot_rows
from finance_agent.factors.eval_engine import MarketPanel, build_panel_from_rows
from finance_agent.factors.library import add_factor, record_ic
from finance_agent.screening import pipeline
from finance_agent.screening.ranker import Candidate
from finance_agent.screening.store import (
    consecutive_days_in_top,
    latest_screening_batch,
    list_l2_candidates,
    list_selection_pool,
    list_triage_decisions,
    record_l2_candidates,
    record_screening_batch,
)

_AS_OF = "2026-10-13"
_CODES = ("600519", "000001", "300750")
_ST_CODE = "600999"  # 预置 ST 行：L1 必须剔除


# ── 合成快照 / 面板 / 因子库 ────────────────────────────────────────────────


def _business_days(end: str, count: int) -> list[str]:
    """``end`` 起向前 ``count`` 个工作日（升序）；足够长以覆盖 60 交易日/20 日窗口。"""
    day = date.fromisoformat(end)
    days: list[str] = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(days)


_CALENDAR = _business_days(_AS_OF, 80)  # 上市 80 个交易日 > 门槛 60
_PANEL_DATES = _CALENDAR[-25:]  # 面板回看 25 日 ≥ 流动性窗口 20 日


def _snapshot_rows() -> list[dict[str, Any]]:
    """逐日快照行：3 只正常标的 + 1 只 ST（金额 1 亿 > 5000 万门槛，仅 ST 被剔）。"""
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(_PANEL_DATES):
        for code_index, code in enumerate((*_CODES, _ST_CODE)):
            close = 10.0 + code_index * 5.0 + index * 0.1
            rows.append(
                {
                    "trade_date": day,
                    "code": code,
                    "name": f"合成{code}",
                    "open": close,
                    "high": close,
                    "low": close,
                    "close_raw": close,
                    "close_qfq": close,
                    "volume": 1_000_000.0,
                    "amount": 100_000_000.0 + code_index * 1_000_000.0,
                    "pct_chg": 0.0,
                    "prev_close": close,
                    "is_st": 1 if code == _ST_CODE else 0,
                    "is_suspended": 0,
                    "limit_up_sealed": 0,
                    "limit_down_sealed": 0,
                    "listed_days": len(_CALENDAR),
                    "list_date": _CALENDAR[0],
                }
            )
    return rows


@dataclass
class _Workspace:
    """一套隔离的库 + 注入面板（screening/快照/因子库三库互不污染）。"""

    root: Path
    snapshot_db: Path
    library_db: Path
    screening_db: Path
    panel: MarketPanel
    factor_id: int
    ic_values: list[float] = field(default_factory=list)


def _build_workspace(root: Path) -> _Workspace:
    snapshot_db = root / "market_snapshot.db"
    library_db = root / "factor_library.db"
    screening_db = root / "screening.db"
    rows = _snapshot_rows()
    frame = pd.DataFrame(rows)
    for day in _PANEL_DATES:
        write_snapshot_rows(
            day, frame.loc[frame["trade_date"] == day].to_dict("records"), db_path=snapshot_db
        )
    set_meta_many(
        {"last_success_date": _AS_OF, "trade_calendar": json.dumps(_CALENDAR)},
        db_path=snapshot_db,
    )
    panel = build_panel_from_rows(frame)
    factor_id = add_factor(
        expression="close",
        hypothesis="测试假设文本（≥30 字长度达标的合成因子说明）",
        category="builtin",
        status="active",
        db_path=library_db,
    )
    ic_values = [0.01 + 0.001 * index for index in range(60)]
    for day, value in zip(_CALENDAR[-60:], ic_values, strict=True):
        record_ic(factor_id, day, value, db_path=library_db)
    return _Workspace(
        root=root,
        snapshot_db=snapshot_db,
        library_db=library_db,
        screening_db=screening_db,
        panel=panel,
        factor_id=factor_id,
        ic_values=ic_values,
    )


@pytest.fixture
def ws(tmp_path) -> _Workspace:
    return _build_workspace(tmp_path)


# ── 零网络纪律（autouse） ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_default_io(monkeypatch):
    """默认 triage client 与默认事件装配在测试内不可用（须显式注入，零网络）。"""

    def _forbidden_client(*args, **kwargs):
        raise AssertionError("测试不得构造默认 triage client（须注入 stub，零网络）")

    monkeypatch.setattr(pipeline, "GatewayTriageClient", _forbidden_client)
    monkeypatch.setattr(pipeline, "_assemble_events_by_symbol", lambda candidates: {})
    yield


class _ScriptedClient:
    """脚本化 triage stub：按 symbol 回放 payload；``fail_all`` → 全部抛错（网关故障）。"""

    def __init__(self, payloads: dict[str, dict] | None = None, *, fail_all: bool = False) -> None:
        self._payloads = dict(payloads or {})
        self._fail_all = fail_all
        self.calls: list[str] = []

    def complete_json(self, prompt: str, *, purpose, metadata: dict) -> dict:
        symbol = str(metadata.get("symbol"))
        self.calls.append(symbol)
        if self._fail_all:
            raise RuntimeError("LLM 网关不可用")
        return dict(self._payloads.get(symbol, {"verdict": "keep", "reason": "无风险事件"}))


def _run(ws: _Workspace, **overrides) -> dict[str, Any]:
    """缺省注入：面板 / 空事件 / 全 keep stub / 三库路径（零网络、全确定）。"""
    kwargs: dict[str, Any] = {
        "panel": ws.panel,
        "events_by_symbol": {},
        "triage_client": _ScriptedClient(),
        "db_path": ws.screening_db,
        "snapshot_db_path": ws.snapshot_db,
        "library_db_path": ws.library_db,
    }
    kwargs.update(overrides)
    return pipeline.run_screening_batch(**kwargs)


def _seed_l2(screening_db: Path, trade_date: str, symbols: list[str]) -> None:
    record_l2_candidates(
        trade_date,
        [
            Candidate(
                symbol=symbol,
                trade_date=trade_date,
                composite_score=1.0,
                factor_contribs=(),
                rank=index,
            )
            for index, symbol in enumerate(symbols, 1)
        ],
        db_path=screening_db,
    )


def _seed_pool_days(ws: _Workspace) -> None:
    """预置前两交易日 L2 榜：600519/000001 两日在榜（+当日 = 3 日达标）；
    300750 只在上一交易日在榜（+当日 = 2 日，不达门槛）。"""
    _seed_l2(ws.screening_db, _CALENDAR[-3], ["600519", "000001"])
    _seed_l2(ws.screening_db, _CALENDAR[-2], ["600519", "000001", "300750"])


def _drop_code_from_universe(ws: _Workspace, code: str) -> None:
    """从快照库与面板移除一只标的（构造「同日重跑集合变小」场景；screening 库不动）。"""
    conn = sqlite3.connect(ws.snapshot_db)
    try:
        conn.execute("DELETE FROM market_snapshot WHERE code = ?", (code,))
        conn.commit()
    finally:
        conn.close()
    frame = pd.DataFrame([row for row in _snapshot_rows() if row["code"] != code])
    ws.panel = build_panel_from_rows(frame)


def _count(db: Path, sql: str) -> int:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(sql).fetchone()
    finally:
        conn.close()
    return int(row[0])


# ── T1 日批编排（S1） ───────────────────────────────────────────────────────


def test_batch_ok_writes_batch_l2_full_list_and_eligible_pool(ws: _Workspace) -> None:
    """正常日批：as_of 缺省 = 最新快照日；批次 ok + 计数；L2 全量落库；
    精选池 = keep ∩ 连续 ≥3 日在榜。"""
    _seed_pool_days(ws)

    result = _run(ws)

    assert result["trade_date"] == _AS_OF
    assert result["status"] == "ok"
    assert result["error"] is None
    assert result["l1_passed"] == 3  # ST 行被 L1 剔除
    assert result["l2_top"] == 3
    assert result["l3_keep"] == 3
    assert result["pool_size"] == 2  # 300750 仅 2 日连续，未达门槛

    batch = latest_screening_batch(db_path=ws.screening_db)
    assert batch is not None
    assert batch["trade_date"] == _AS_OF and batch["status"] == "ok"
    assert batch["error"] is None
    assert (batch["l1_passed"], batch["l2_top"], batch["l3_keep"], batch["pool_size"]) == (
        3,
        3,
        3,
        2,
    )
    assert batch["started_at"] and batch["finished_at"]

    l2_symbols = {row["symbol"] for row in list_l2_candidates(_AS_OF, db_path=ws.screening_db)}
    assert l2_symbols == set(_CODES)  # L2 全量名单（Top-N）落库，ST 不在内

    pool = list_selection_pool(_AS_OF, db_path=ws.screening_db)
    ranks = {
        row["symbol"]: row["rank"] for row in list_l2_candidates(_AS_OF, db_path=ws.screening_db)
    }
    # 池序 = L2 rank 升序（闭合：池内两只的相对顺序与 L2 榜一致）
    assert [row["symbol"] for row in pool] == sorted(["600519", "000001"], key=ranks.__getitem__)
    assert all(row["consecutive_days"] == 3 for row in pool)
    assert all(row["l3_verdict"] == "keep" and row["l3_reason"] for row in pool)
    contribs = pool[0]["factor_contribs"]
    assert contribs and contribs[0]["factor_id"] == ws.factor_id
    # 贡献分解勾稽：Σ contribution == composite_score（1e-9）
    assert abs(sum(c["contribution"] for c in contribs) - pool[0]["composite_score"]) <= 1e-9


def test_batch_drops_excluded_from_pool(ws: _Workspace) -> None:
    """L3 drop 的标的即使连续在榜也进不了精选池（keep ∩ eligible）。"""
    _seed_pool_days(ws)
    client = _ScriptedClient({"000001": {"verdict": "drop", "reason": "收到证监会立案告知书"}})

    result = _run(ws, triage_client=client)

    assert result["status"] == "ok"
    assert result["l3_keep"] == 2
    assert result["pool_size"] == 1
    assert [row["symbol"] for row in list_selection_pool(_AS_OF, db_path=ws.screening_db)] == [
        "600519"
    ]


def test_batch_l3_systemic_failure_records_failed_and_zero_pool(ws: _Workspace) -> None:
    """spec 场景「中途失败不产残缺池」：LLM 网关整体不可用（全部降级）→ 批次 failed、
    当日精选池零行。"""
    _seed_pool_days(ws)

    result = _run(ws, triage_client=_ScriptedClient(fail_all=True))

    assert result["status"] == "failed"
    assert result["error"] and "L3" in result["error"]
    assert result["pool_size"] == 0

    batch = latest_screening_batch(db_path=ws.screening_db)
    assert batch["status"] == "failed" and batch["error"] == result["error"]
    assert list_selection_pool(_AS_OF, db_path=ws.screening_db) == []
    assert _count(ws.screening_db, "SELECT COUNT(*) FROM selection_pool") == 0


def test_batch_fails_closed_when_snapshot_not_fresh(ws: _Workspace) -> None:
    """快照不新鲜（as_of ≠ 最新快照日）→ 批次 failed、当日零池零 L2 行。"""
    stale = _CALENDAR[-5]

    result = _run(ws, as_of=stale)

    assert result["status"] == "failed"
    assert _AS_OF in result["error"]  # 消息含最新快照日
    batch = latest_screening_batch(db_path=ws.screening_db)
    assert batch["trade_date"] == stale and batch["status"] == "failed"
    assert list_selection_pool(stale, db_path=ws.screening_db) == []
    assert list_l2_candidates(stale, db_path=ws.screening_db) == []


def test_batch_fails_closed_when_calendar_missing(ws: _Workspace) -> None:
    """快照 meta 缺交易日历 → 批次 failed（fail-closed，不猜天数）。"""
    set_meta_many({"trade_calendar": ""}, db_path=ws.snapshot_db)

    result = _run(ws)

    assert result["status"] == "failed"
    assert "trade_calendar" in result["error"]
    assert list_selection_pool(_AS_OF, db_path=ws.screening_db) == []


def test_rerun_same_day_overwrites_without_duplication(ws: _Workspace) -> None:
    """幂等重跑：同日二次覆盖不翻倍（批次单行、L2 单行/标的、池单行/标的）。"""
    _seed_pool_days(ws)

    assert _run(ws)["status"] == "ok"
    assert _run(ws)["status"] == "ok"

    assert _count(ws.screening_db, "SELECT COUNT(*) FROM screening_batches") == 1
    assert len(list_l2_candidates(_AS_OF, db_path=ws.screening_db)) == 3  # 当日榜不翻倍
    assert _count(ws.screening_db, "SELECT COUNT(*) FROM selection_pool") == 2


def test_rerun_same_day_smaller_set_leaves_no_ghost_rows(ws: _Workspace) -> None:
    """同日重跑换**小集合**：当日 L2/triage 行 = 本次集合精确相等（无幸存者）。

    残留行会把已出榜标的的连续在榜天数虚增（C4 沿 ``l2_candidates`` 日期轴回溯），
    故重跑必须原子替换当日榜（store ``rewrite_day_l2_board``）。
    """
    _seed_pool_days(ws)
    assert _run(ws)["status"] == "ok"
    assert {row["symbol"] for row in list_l2_candidates(_AS_OF, db_path=ws.screening_db)} == set(
        _CODES
    )

    _drop_code_from_universe(ws, "300750")  # 重跑集合 = _CODES ∖ {300750}
    result = _run(ws)

    assert result["status"] == "ok"
    assert result["l2_top"] == 2
    assert sorted(row["symbol"] for row in list_l2_candidates(_AS_OF, db_path=ws.screening_db)) == [
        "000001",
        "600519",
    ]
    assert sorted(
        row["symbol"] for row in list_triage_decisions(trade_date=_AS_OF, db_path=ws.screening_db)
    ) == ["000001", "600519"]
    # 出榜标的在当日轴上彻底消失：连续在榜天数不因旧行虚增
    assert consecutive_days_in_top("300750", _AS_OF, db_path=ws.screening_db) == 0


def test_latest_successful_pool_returns_latest_ok_batch_with_date(ws: _Workspace) -> None:
    """返回最近 ok 批次（带日期）及其池；失败批次不入选；无成功批次 → None。"""
    row = {
        "symbol": "600519",
        "composite_score": 1.5,
        "rank": 1,
        "consecutive_days": 4,
        "l3_verdict": "keep",
        "l3_reason": "无风险事件",
        "factor_contribs": [{"factor_id": 1, "zscore": 0.5, "weight": 1.0, "contribution": 0.5}],
    }
    record_screening_batch(
        "2026-10-09",
        status="ok",
        started_at="2026-10-09T15:30:00",
        finished_at="2026-10-09T15:33:00",
        l1_passed=3,
        l2_top=3,
        l3_keep=1,
        pool_rows=[row],
        db_path=ws.screening_db,
    )
    record_screening_batch(
        "2026-10-12",
        status="failed",
        error="RuntimeError: LLM 网关不可用",
        started_at="2026-10-12T15:30:00",
        finished_at="2026-10-12T15:30:04",
        db_path=ws.screening_db,
    )

    latest = pipeline.latest_successful_pool(db_path=ws.screening_db)

    assert latest is not None
    assert latest["batch"]["trade_date"] == "2026-10-09"
    assert latest["batch"]["status"] == "ok"
    assert [entry["symbol"] for entry in latest["pool"]] == ["600519"]
    assert latest["pool"][0]["factor_contribs"][0]["factor_id"] == 1

    assert pipeline.latest_successful_pool(db_path=ws.root / "empty.db") is None


def test_record_screening_batch_rejects_bad_input(ws: _Workspace) -> None:
    """store 合同：status 逐字 ok/failed；池行字段 fail loud（不落坏值）。"""
    with pytest.raises(ValueError, match="status"):
        record_screening_batch(
            "2026-10-09",
            status="running",
            started_at="t0",
            finished_at="t1",
            db_path=ws.screening_db,
        )
    with pytest.raises(ValueError, match="l3_verdict"):
        record_screening_batch(
            "2026-10-09",
            status="ok",
            started_at="t0",
            finished_at="t1",
            pool_rows=[
                {
                    "symbol": "600519",
                    "composite_score": 1.0,
                    "rank": 1,
                    "consecutive_days": 3,
                    "l3_verdict": "maybe",
                    "l3_reason": "x",
                    "factor_contribs": [],
                }
            ],
            db_path=ws.screening_db,
        )
    # 失败批次不落池行：先写 ok 池行，再写 failed → 当日池清空（fail-closed 不变量）
    record_screening_batch(
        "2026-10-09",
        status="ok",
        started_at="t0",
        finished_at="t1",
        pool_rows=[
            {
                "symbol": "600519",
                "composite_score": 1.0,
                "rank": 1,
                "consecutive_days": 3,
                "l3_verdict": "keep",
                "l3_reason": "无风险事件",
                "factor_contribs": [],
            }
        ],
        db_path=ws.screening_db,
    )
    assert len(list_selection_pool("2026-10-09", db_path=ws.screening_db)) == 1
    record_screening_batch(
        "2026-10-09",
        status="failed",
        error="boom",
        started_at="t2",
        finished_at="t3",
        db_path=ws.screening_db,
    )
    assert list_selection_pool("2026-10-09", db_path=ws.screening_db) == []


# ── T2 时效性校验（S2） ─────────────────────────────────────────────────────


def _fresh_row(**overrides) -> dict:
    row = {
        "code": "600519",
        "is_suspended": 0,
        "limit_up_sealed": 0,
        "limit_down_sealed": 0,
    }
    row.update(overrides)
    return row


def _freshness(
    symbols, snapshot_rows, events_by_symbol, *, as_of: str = _AS_OF, calendar=None
) -> list[dict]:
    return pipeline.check_signal_freshness(
        symbols,
        snapshot_rows=snapshot_rows,
        events_by_symbol=events_by_symbol,
        as_of=as_of,
        calendar=_CALENDAR if calendar is None else calendar,
    )


def test_freshness_flags_suspended_and_leaves_others_untouched_spec_scenario() -> None:
    """spec 场景「盘前停牌移除」：Z 停牌 → 仅 Z/SUSPENDED，其余标的不受影响。"""
    rows = {
        "600519": _fresh_row(),
        "000001": _fresh_row(code="000001", is_suspended=1),
        "300750": _fresh_row(code="300750"),
    }

    findings = _freshness(["600519", "000001", "300750"], rows, {})

    assert findings == [{"symbol": "000001", "reason": "SUSPENDED", "detail": "停牌"}]


def test_freshness_flags_limit_locked() -> None:
    """一字涨停/跌停封板 → LIMIT_LOCKED（逐字原因码）。"""
    rows = {
        "600519": _fresh_row(limit_up_sealed=1),
        "000001": _fresh_row(code="000001", limit_down_sealed=1),
        "300750": _fresh_row(code="300750"),
    }

    findings = _freshness(["600519", "000001", "300750"], rows, {})

    assert [(item["symbol"], item["reason"]) for item in findings] == [
        ("600519", "LIMIT_LOCKED"),
        ("000001", "LIMIT_LOCKED"),
    ]
    assert "涨停" in findings[0]["detail"] and "跌停" in findings[1]["detail"]


def test_freshness_flags_new_event_on_previous_trading_day() -> None:
    """新事件 → NEW_EVENT：按**交易日**（as_of 的前一交易日）而非自然日；
    干净标的与无日期事件不入返回列表。"""
    # 周一 2026-10-12 是 as_of（周二 10-13）的前一交易日；10-12 的事件算「新」，
    # 而 10-09（上周五）的事件不算。
    rows = {
        "600519": _fresh_row(),
        "000001": _fresh_row(code="000001"),
        "300750": _fresh_row(code="300750"),
    }
    events = {
        "600519": [{"date": "2026-10-12", "type": "立案调查", "title": "收到证监会立案告知书"}],
        "000001": [{"date": "2026-10-09", "type": "公告", "title": "上周五旧事件"}],
        "300750": [{"date": "", "type": "数据状态", "title": "事件数据暂时不可用"}],
    }

    findings = _freshness(["600519", "000001", "300750"], rows, events)

    assert [(item["symbol"], item["reason"]) for item in findings] == [("600519", "NEW_EVENT")]
    assert "2026-10-12" in findings[0]["detail"]


def test_freshness_without_calendar_only_counts_same_day_events() -> None:
    """无交易日历 → 只认 as_of 当日事件（保守下限：不猜前一交易日）。"""
    rows = {"600519": _fresh_row(), "000001": _fresh_row(code="000001")}
    events = {
        "600519": [{"date": _AS_OF, "title": "当日公告"}],
        "000001": [{"date": "2026-10-12", "title": "前一交易日公告"}],
    }

    findings = _freshness(["600519", "000001"], rows, events, calendar=[])

    assert [(item["symbol"], item["reason"]) for item in findings] == [("600519", "NEW_EVENT")]


def test_freshness_returns_all_reasons_in_s2_order() -> None:
    """一次可多因：SUSPENDED → LIMIT_LOCKED → NEW_EVENT（逐字顺序）全量返回。"""
    rows = {"600519": _fresh_row(is_suspended=1, limit_up_sealed=1)}
    events = {"600519": [{"date": _AS_OF, "title": "当日立案"}]}

    findings = _freshness(["600519"], rows, events)

    assert [(item["symbol"], item["reason"]) for item in findings] == [
        ("600519", "SUSPENDED"),
        ("600519", "LIMIT_LOCKED"),
        ("600519", "NEW_EVENT"),
    ]


def test_freshness_treats_missing_snapshot_row_as_suspended() -> None:
    """无当日快照行 → 视同不可交易（fail-closed；原因码仍为 SUSPENDED）。"""
    findings = _freshness(["600519"], {}, {})

    assert findings == [
        {"symbol": "600519", "reason": "SUSPENDED", "detail": "无当日快照行（视同不可交易）"}
    ]


def test_freshness_clean_symbols_return_empty() -> None:
    """干净标的（快照正常、无新事件）→ 不在返回列表。"""
    rows = {"600519": _fresh_row()}

    assert _freshness(["600519"], rows, {"600519": []}) == []
