"""add-decision-memory Task 1：情景记忆检索与注入（spec decision-memory / agent-node-contracts）。

纪律（Global Constraints）：
- 数据源 = outcome track-record 库（predictions，只读）；测试经 db_path 显式注入；
- 相关性钉死：同标的 > 同行业，同级内按日期降序；as_of 严格早于（防自污染）；
- 条目逐字含来源决策 ID 与日期；rationale 截断 ≤200 字，缺字段空串不编造；
- 无历史 → 空列表；缺库/DB 异常 fail-open（不阻断分析）；
- 记忆条目落 state `decision_memory`，field_ref 前缀 `decision_memory.episodic.<i>.<field>`
  由既有 citation 直读路径解析（纳入既有校验覆盖）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from finance_agent.citation import Claim, verify_claims
from finance_agent.memory import (
    PURPOSE_TAG_DECISION_MEMORY,
    build_memory_block,
    memory_block_for_state,
    render_memory_state,
    retrieve_episodic,
)
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
    update_prediction_status,
)

AS_OF = "2026-10-13"


def _insert_settled(
    db: Path,
    prediction_id: str,
    symbol: str,
    created_at: str,
    *,
    status: str = "resolved_win",
    raw_return: float | None = 0.12,
    direction: str = "long",
    reasoning: str = "",
) -> str:
    """落一条已结算 predictions 行（status 终态 + 结算读数）。"""
    insert_prediction(
        {
            "prediction_id": prediction_id,
            "source_type": "live",
            "symbol": symbol,
            "symbol_name": symbol,
            "direction": direction,
            "entry_price": 100.0,
            "horizon_days": 20,
            "confidence": 0.7,
            "rationale_snapshot": {"fund_manager_decision_reasoning": reasoning},
            "created_at": created_at,
        },
        db_path=db,
        status=status,
    )
    if raw_return is not None:
        update_prediction_status(prediction_id, {"raw_return": raw_return}, db_path=db)
    return prediction_id


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "sessions.db"
    init_track_record_tables(path)
    return path


class TestRetrieveEpisodic:
    """检索口径：已结算定义 / 相关性排序 / as_of / top_k / fail-open。"""

    def test_spec_scenario_same_symbol_three_settled(self, db: Path):
        """spec 场景：300750 存在 3 条已结算历史 → 检索到 3 条（含来源 ID 与日期）。"""
        _insert_settled(db, "p1", "300750.SZ", "2026-08-01T09:00:00", raw_return=0.12)
        _insert_settled(db, "p2", "300750.SZ", "2026-09-01T09:00:00", raw_return=-0.05)
        _insert_settled(db, "p3", "300750.SZ", "2026-10-01T09:00:00", raw_return=0.03)

        entries = retrieve_episodic("300750", as_of=AS_OF, db_path=db)

        assert len(entries) == 3
        assert [e["decision_id"] for e in entries] == ["p3", "p2", "p1"]
        assert [e["date"] for e in entries] == ["2026-10-01", "2026-09-01", "2026-08-01"]
        assert all(e["settlement_status"] == "resolved_win" for e in entries)
        assert entries[0]["raw_return"] == pytest.approx(0.03)

    def test_same_symbol_priority_over_industry_then_date_desc(self, db: Path):
        """相关性钉死：同标的 > 同行业；同级内按日期降序。"""
        _insert_settled(db, "own_old", "300750.SZ", "2026-01-05T09:00:00")
        _insert_settled(db, "ind_new", "000858.SZ", "2026-10-01T09:00:00")
        _insert_settled(db, "ind_mid", "000858.SZ", "2026-06-01T09:00:00")

        entries = retrieve_episodic("300750", industry_codes=["000858"], as_of=AS_OF, db_path=db)

        assert [e["decision_id"] for e in entries] == ["own_old", "ind_new", "ind_mid"]

    def test_top_k_caps_at_five_by_default(self, db: Path):
        for i in range(7):
            _insert_settled(db, f"p{i}", "300750.SZ", f"2026-0{i + 1}-01T09:00:00")
        entries = retrieve_episodic("300750", as_of=AS_OF, db_path=db)
        assert len(entries) == 5
        assert [e["decision_id"] for e in entries] == ["p6", "p5", "p4", "p3", "p2"]

    def test_as_of_is_strict_exclusive(self, db: Path):
        """as_of 当日与之后不检索（防同一标的当日决策自污染）。"""
        _insert_settled(db, "before", "300750.SZ", "2026-10-12T09:00:00")
        _insert_settled(db, "same_day", "300750.SZ", "2026-10-13T09:00:00")
        _insert_settled(db, "after", "300750.SZ", "2026-10-14T09:00:00")

        entries = retrieve_episodic("300750", as_of=AS_OF, db_path=db)

        assert [e["decision_id"] for e in entries] == ["before"]

    def test_open_and_duplicate_rows_are_not_memory(self, db: Path):
        """未结算（open）与同日重复（无结算读数）不算已结算记忆。"""
        _insert_settled(
            db, "open1", "300750.SZ", "2026-09-01T09:00:00", status="open", raw_return=None
        )
        _insert_settled(
            db,
            "dupe",
            "300750.SZ",
            "2026-09-02T09:00:00",
            status="duplicate_of_day",
            raw_return=None,
        )
        _insert_settled(db, "ok", "300750.SZ", "2026-09-03T09:00:00")

        entries = retrieve_episodic("300750", as_of=AS_OF, db_path=db)

        assert [e["decision_id"] for e in entries] == ["ok"]

    def test_avoidance_and_unresolvable_are_terminal_settled(self, db: Path):
        """回避/不可判定同为终态（无读数 → raw_return None，渲染不编造数字）。"""
        _insert_settled(
            db, "av", "300750.SZ", "2026-09-01T09:00:00", status="avoidance", raw_return=None
        )
        _insert_settled(
            db, "un", "300750.SZ", "2026-09-02T09:00:00", status="unresolvable", raw_return=None
        )

        entries = retrieve_episodic("300750", as_of=AS_OF, db_path=db)

        assert [e["settlement_status"] for e in entries] == ["unresolvable", "avoidance"]
        assert all(e["raw_return"] is None for e in entries)

    def test_rationale_truncated_and_missing_is_empty(self, db: Path):
        _insert_settled(db, "long", "300750.SZ", "2026-09-01T09:00:00", reasoning="甲" * 500)
        _insert_settled(db, "short", "300750.SZ", "2026-09-02T09:00:00", reasoning="")

        entries = retrieve_episodic("300750", as_of=AS_OF, db_path=db)

        by_id = {e["decision_id"]: e for e in entries}
        assert len(by_id["long"]["rationale_summary"]) == 200
        assert by_id["short"]["rationale_summary"] == ""

    def test_no_history_returns_empty(self, db: Path):
        assert retrieve_episodic("300750", as_of=AS_OF, db_path=db) == []

    def test_missing_db_fails_open(self, tmp_path: Path):
        """库不存在 → 空列表（查询不可用 ≠ 分析不可行）。"""
        assert retrieve_episodic("300750", as_of=AS_OF, db_path=tmp_path / "nope.db") == []

    def test_symbol_suffix_normalization(self, db: Path):
        """入参 6 位代码；库内 symbol 带 .SZ/.SH 后缀（与 ingest 同映射）。"""
        _insert_settled(db, "sh", "600519.SH", "2026-09-01T09:00:00")
        assert [e["decision_id"] for e in retrieve_episodic("600519", as_of=AS_OF, db_path=db)] == [
            "sh"
        ]
        # 同行业档同样接受 6 位代码
        _insert_settled(db, "sz", "300750.SZ", "2026-09-02T09:00:00")
        entries = retrieve_episodic("300750", industry_codes=["600519"], as_of=AS_OF, db_path=db)
        assert [e["decision_id"] for e in entries] == ["sz", "sh"]


class TestBuildMemoryBlock:
    """注入块逐字要求：来源决策 ID + 日期；空记忆零块。"""

    def test_block_contains_source_id_and_date(self):
        block = build_memory_block(
            [
                {
                    "decision_id": "p1",
                    "date": "2026-09-01",
                    "symbol": "300750",
                    "direction": "long",
                    "settlement_status": "resolved_win",
                    "raw_return": 0.12,
                    "rationale_summary": "估值修复逻辑兑现",
                }
            ],
            [],
        )
        assert "[记忆] 来源: p1 (2026-09-01)" in block
        assert "估值修复逻辑兑现" in block
        assert "12.00%" in block

    def test_empty_memory_renders_empty_string(self):
        assert build_memory_block([], []) == ""

    def test_no_reading_renders_honest_placeholder(self):
        block = build_memory_block(
            [
                {
                    "decision_id": "p1",
                    "date": "2026-09-01",
                    "symbol": "300750",
                    "direction": "neutral",
                    "settlement_status": "unresolvable",
                    "raw_return": None,
                    "rationale_summary": "",
                }
            ],
            [],
        )
        assert "无结算读数" in block

    def test_field_ref_prefix_declared_for_citations(self):
        block = build_memory_block(
            [
                {
                    "decision_id": "p1",
                    "date": "2026-09-01",
                    "symbol": "300750",
                    "direction": "long",
                    "settlement_status": "resolved_win",
                    "raw_return": 0.12,
                    "rationale_summary": "",
                }
            ],
            [],
        )
        assert "decision_memory.episodic." in block


class TestRenderMemoryState:
    """state 键组装（与滞回键同风格）+ trace 用途标签。"""

    def test_writes_decision_memory_key(self, db: Path):
        _insert_settled(db, "p1", "300750.SZ", "2026-09-01T09:00:00")
        out = render_memory_state(
            {"stock_code": "300750", "stock_name": "宁德时代"},
            db_path=db,
            as_of=AS_OF,
        )
        memory = out["decision_memory"]
        assert [e["decision_id"] for e in memory["episodic"]] == ["p1"]
        assert memory["semantic"] == []
        assert memory["as_of"] == AS_OF

    def test_trace_carries_purpose_tag(self, db: Path):
        """Langfuse trace 用途标签逐字 `decision_memory`。"""
        _insert_settled(db, "p1", "300750.SZ", "2026-09-01T09:00:00")
        client = MagicMock()
        span = MagicMock()
        client.start_as_current_observation.return_value.__enter__.return_value = span
        with patch("finance_agent.langfuse_tracing.get_langfuse", return_value=client):
            render_memory_state({"stock_code": "300750"}, db_path=db, as_of=AS_OF)

        kwargs = client.start_as_current_observation.call_args.kwargs
        assert kwargs["name"] == PURPOSE_TAG_DECISION_MEMORY == "decision_memory"
        metadata = span.update.call_args.kwargs["metadata"]
        assert metadata["purpose_tag"] == "decision_memory"
        assert metadata["episodic_count"] == 1

    def test_trace_absent_client_is_zero_overhead(self, db: Path):
        with patch("finance_agent.langfuse_tracing.get_langfuse", return_value=None):
            out = render_memory_state({"stock_code": "300750"}, db_path=db, as_of=AS_OF)
        assert out["decision_memory"]["episodic"] == []

    def test_memory_block_for_state_none_when_absent_or_empty(self):
        assert memory_block_for_state({}) is None
        assert memory_block_for_state({"decision_memory": {}}) is None
        assert memory_block_for_state({"decision_memory": {"episodic": [], "semantic": []}}) is None

    def test_memory_block_for_state_renders_entries(self):
        state = {
            "decision_memory": {
                "episodic": [
                    {
                        "decision_id": "p1",
                        "date": "2026-09-01",
                        "symbol": "300750",
                        "direction": "long",
                        "settlement_status": "resolved_win",
                        "raw_return": 0.12,
                        "rationale_summary": "",
                    }
                ],
                "semantic": [],
            }
        }
        block = memory_block_for_state(state)
        assert block is not None
        assert "[记忆] 来源: p1 (2026-09-01)" in block


class TestCitationCoverage:
    """记忆条目纳入既有 citation 校验覆盖（field_ref 直读路径）。"""

    def test_claim_on_memory_raw_return_passes(self, db: Path):
        _insert_settled(db, "p1", "300750.SZ", "2026-09-01T09:00:00", raw_return=0.12)
        out = render_memory_state({"stock_code": "300750"}, db_path=db, as_of=AS_OF)
        state = {"stock_code": "300750", **out}

        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="decision_memory.episodic.0.raw_return",
            stated_value=0.12,
            interpretation="历史决策 p1 的结算收益为 12%",
            direction="positive",
            metric_name="raw_return",
        )
        (verdict,) = verify_claims([claim], state)

        assert verdict.status == "PASS"
