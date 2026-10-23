"""add-decision-memory Task 2：语义记忆（月批提炼 + 人工确认 + 未确认不生效）。

spec decision-memory「语义记忆人工确认」：
- 月批从已结算决策提炼候选条款（LLM 提炼可注入 stub；无 client → 规则式降级，零 LLM 可测）；
- 候选落库 pending；人工 confirm/reject；
- 经确认方可注入风控辩论层；**未确认条款 SHALL NOT 出现在任何 prompt**——测试遍历
  全部 prompt 组装路径检索未确认文本，零命中（含静态单读入口检查）。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from finance_agent.memory import render_memory_state
from finance_agent.memory.semantic import (
    CLAUSE_STATUSES,
    confirm_clause,
    extract_candidate_clauses,
    list_clauses,
    list_confirmed_clauses,
    list_pending_clauses,
    reject_clause,
    run_monthly_extraction,
    store_candidate_clauses,
)
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
    update_prediction_status,
)

AS_OF = "2026-10-13"


def _settled(symbol: str, day: str, *, status: str, direction: str = "long") -> dict:
    """合成记忆条目（extract_candidate_clauses 的输入形态）。"""
    return {
        "decision_id": f"p_{symbol}_{day}",
        "date": day,
        "symbol": symbol,
        "direction": direction,
        "settlement_status": status,
        "raw_return": 0.05 if status == "resolved_win" else None,
        "rationale_summary": "",
    }


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "sessions.db"
    init_track_record_tables(path)
    return path


class TestRuleBasedExtraction:
    """规则式降级提炼（无 client → 零 LLM 可测）。"""

    def test_direction_by_status_distribution_clauses(self):
        settled = [
            _settled("300750", "2026-08-01", status="resolved_win"),
            _settled("300750", "2026-08-02", status="resolved_win"),
            _settled("300750", "2026-08-03", status="resolved_loss"),
            _settled("300750", "2026-08-04", status="resolved_loss", direction="short"),
            _settled("300750", "2026-08-05", status="resolved_loss", direction="short"),
        ]
        clauses = extract_candidate_clauses(settled)
        texts = [c["text"] for c in clauses]
        assert "看多方向历史决策 2 条：结算命中（占该方向 67%）" in texts
        assert "看多方向已结算读数 3 条：胜率 67%（命中 2 / 未中 1）" in texts
        assert "看空方向历史决策 2 条：结算未中（占该方向 100%）" in texts
        # 每族条款带证据（可人工复核，不裸文本）
        assert all("evidence" in c for c in clauses)

    def test_win_rate_clause_requires_min_samples(self):
        settled = [
            _settled("300750", "2026-08-01", status="resolved_win"),
            _settled("300750", "2026-08-02", status="resolved_loss"),
            _settled("300750", "2026-08-03", status="resolved_win"),
        ]
        texts = [c["text"] for c in extract_candidate_clauses(settled)]
        assert "看多方向已结算读数 3 条：胜率 67%（命中 2 / 未中 1）" in texts

    def test_single_sample_groups_skipped(self):
        settled = [_settled("300750", "2026-08-01", status="resolved_win")]
        assert extract_candidate_clauses(settled) == []

    def test_deterministic_order(self):
        settled = [
            _settled("300750", "2026-08-01", status="resolved_loss", direction="short"),
            _settled("300750", "2026-08-02", status="resolved_win"),
            _settled("300750", "2026-08-03", status="resolved_win"),
        ]
        first = [c["text"] for c in extract_candidate_clauses(settled)]
        shuffled = [settled[2], settled[0], settled[1]]
        assert [c["text"] for c in extract_candidate_clauses(shuffled)] == first

    def test_injected_client_used_when_available(self):
        prompt_seen: list[str] = []

        def _client(prompt: str) -> str:
            prompt_seen.append(prompt)
            return "小盘股高换手信号命中率持续低于四成\n应避免在缩量反弹中加仓"

        clauses = extract_candidate_clauses(
            [_settled("300750", "2026-08-01", status="resolved_win")], client=_client
        )
        assert [c["text"] for c in clauses] == [
            "小盘股高换手信号命中率持续低于四成",
            "应避免在缩量反弹中加仓",
        ]
        assert prompt_seen and "已结算决策" in prompt_seen[0]

    def test_client_failure_falls_back_to_rules(self):
        """LLM 提炼失败不阻断月批（降级规则式提炼）。"""

        def _boom(_prompt: str) -> str:
            raise RuntimeError("endpoint down")

        settled = [
            _settled("300750", "2026-08-01", status="resolved_win"),
            _settled("300750", "2026-08-02", status="resolved_win"),
        ]
        texts = [c["text"] for c in extract_candidate_clauses(settled, client=_boom)]
        assert "看多方向历史决策 2 条：结算命中（占该方向 100%）" in texts

    def test_client_garbage_falls_back_to_rules(self):
        settled = [
            _settled("300750", "2026-08-01", status="resolved_win"),
            _settled("300750", "2026-08-02", status="resolved_win"),
        ]
        texts = [c["text"] for c in extract_candidate_clauses(settled, client=lambda _p: "  \n ")]
        assert any("看多方向历史决策 2 条" in t for t in texts)


class TestClauseStore:
    """pending 落库 + 人工确认门禁 + 幂等/去重。"""

    def test_store_lands_pending_and_dedupes(self, db: Path):
        ids = store_candidate_clauses(["条款甲", "条款乙"], db_path=db)
        assert len(ids) == 2
        assert [c["status"] for c in list_pending_clauses(db_path=db)] == ["pending", "pending"]
        # 同文本重提炼不重复入库（任何状态都算已存在）
        assert store_candidate_clauses(["条款甲", "条款丙"], db_path=db) != ids
        assert len(list_clauses(db_path=db)) == 3

    def test_store_creates_table_on_write(self, tmp_path: Path):
        """写入路径建表；读路径不建表（只读纪律）。"""
        fresh = tmp_path / "fresh.db"
        assert list_confirmed_clauses(db_path=fresh) == []
        assert not fresh.exists()
        store_candidate_clauses(["条款甲"], db_path=fresh)
        assert fresh.exists()

    def test_confirm_and_reject_transitions(self, db: Path):
        (first, second) = store_candidate_clauses(["条款甲", "条款乙"], db_path=db)
        assert confirm_clause(first, db_path=db) is True
        assert reject_clause(second, db_path=db) is True
        assert confirm_clause("c_不存在", db_path=db) is False
        assert reject_clause("c_不存在", db_path=db) is False

        confirmed = list_confirmed_clauses(db_path=db)
        assert [c["clause_id"] for c in confirmed] == [first]
        assert confirmed[0]["confirmed_at"]
        assert [c["status"] for c in list_clauses(db_path=db)] == ["confirmed", "rejected"]
        assert CLAUSE_STATUSES == ("pending", "confirmed", "rejected")

    def test_monthly_batch_end_to_end(self, db: Path):
        for i, status in enumerate(("resolved_win", "resolved_win", "resolved_loss")):
            insert_prediction(
                {
                    "prediction_id": f"p{i}",
                    "source_type": "live",
                    "symbol": "300750.SZ",
                    "direction": "long",
                    "rationale_snapshot": {},
                    "created_at": f"2026-0{i + 1}-01T09:00:00",
                },
                db_path=db,
                status=status,
            )
            update_prediction_status(f"p{i}", {"raw_return": 0.05}, db_path=db)

        ids = run_monthly_extraction(db_path=db, as_of=AS_OF)

        assert len(ids) >= 1
        pending = list_pending_clauses(db_path=db)
        assert all(c["source"] == "monthly_batch" for c in pending)
        assert any("看多方向" in c["text"] for c in pending)


class TestConfirmationGate:
    """spec 场景：5 条候选、2 条确认 → 注入仅 2 条；未确认 3 条全路径零命中。"""

    CANDIDATES = ("候选条款一", "候选条款二", "候选条款三", "候选条款四", "候选条款五")

    def _state_with_recall(self, db: Path) -> dict:
        out = render_memory_state({"stock_code": "300750"}, db_path=db, as_of=AS_OF)
        return {"stock_code": "300750", **out}

    def test_spec_scenario_only_confirmed_injected(self, db: Path):
        ids = store_candidate_clauses(list(self.CANDIDATES), db_path=db)
        confirm_clause(ids[0], db_path=db)
        confirm_clause(ids[1], db_path=db)

        state = self._state_with_recall(db)

        semantic = state["decision_memory"]["semantic"]
        assert [c["text"] for c in semantic] == ["候选条款一", "候选条款二"]
        # 未确认 3 条确实存在于库（控制组），但注入快照不含
        assert len(list_pending_clauses(db_path=db)) == 3
        assert all(text not in str(state) for text in self.CANDIDATES[2:])

    def test_risk_debate_context_contains_confirmed_only(self, db: Path):
        ids = store_candidate_clauses(list(self.CANDIDATES), db_path=db)
        confirm_clause(ids[0], db_path=db)
        confirm_clause(ids[1], db_path=db)
        state = self._state_with_recall(db)

        from finance_agent.nodes.risk import _build_risk_context

        context = _build_risk_context(state)
        assert "候选条款一" in context
        assert "候选条款二" in context
        for text in self.CANDIDATES[2:]:
            assert text not in context

    def test_unconfirmed_unreachable_from_every_prompt_path(self, db: Path):
        """全 prompt 组装路径扫描未确认文本 → 零命中（动态）。"""
        ids = store_candidate_clauses(list(self.CANDIDATES), db_path=db)
        confirm_clause(ids[0], db_path=db)
        unconfirmed = self.CANDIDATES[1:]

        from finance_agent.nodes.analysts import (
            _build_fundamental_context,
            _build_macro_context,
            _build_sentiment_context,
            _build_technical_context,
            _factor_library_block,
            _retry_feedback_section,
        )
        from finance_agent.nodes.debate import _build_debate_context
        from finance_agent.nodes.fund_manager import _build_fund_manager_context
        from finance_agent.nodes.research_manager import _build_research_context
        from finance_agent.nodes.risk import _build_hysteresis_context, _build_risk_context
        from finance_agent.nodes.trader import _build_trader_context

        builders = (
            _build_technical_context,
            _build_macro_context,
            _build_fundamental_context,
            _build_sentiment_context,
            _build_trader_context,
            _build_risk_context,
            _build_hysteresis_context,
            _build_debate_context,
            _build_research_context,
            _build_fund_manager_context,
        )
        states = [self._state_with_recall(db), {"stock_code": "300750"}]
        for state in states:
            for builder in builders:
                text = builder(state)
                for candidate in unconfirmed:
                    assert candidate not in text, (builder.__name__, candidate)
            for candidate in unconfirmed:
                assert candidate not in str(_retry_feedback_section(state, "technical"))
                assert candidate not in str(_factor_library_block(state))

    def test_unconfirmed_text_only_reachable_via_semantic_module(self):
        """静态门禁：`memory_clauses` 表名只出现在 semantic.py（无第二读入口）。

        按 SQL 字面量匹配而非裸子串——operator 工具名 get_memory_clauses /
        API 名 memory_clause_list 属标识符，不构成第二读取入口。
        """
        src = Path("src/finance_agent")
        pat = re.compile(r"(?<![A-Za-z0-9_])memory_clauses(?![A-Za-z0-9_])")
        hits = [
            path.relative_to(src).as_posix()
            for path in src.rglob("*.py")
            if pat.search(path.read_text(encoding="utf-8"))
        ]
        assert hits == ["memory/semantic.py"]

    def test_reader_returns_confirmed_only(self, db: Path):
        ids = store_candidate_clauses(list(self.CANDIDATES), db_path=db)
        confirm_clause(ids[0], db_path=db)
        reject_clause(ids[1], db_path=db)
        confirmed = list_confirmed_clauses(db_path=db)
        assert [c["text"] for c in confirmed] == ["候选条款一"]

    def test_missing_db_returns_no_clauses(self, tmp_path: Path):
        assert list_confirmed_clauses(db_path=tmp_path / "absent.db") == []
        assert list_pending_clauses(db_path=tmp_path / "absent.db") == []

    def test_legacy_db_without_table_returns_empty(self, tmp_path: Path):
        """旧库（无 memory_clauses 表）→ 空条款，不建表（读路径只读）。"""
        db = tmp_path / "legacy.db"
        init_track_record_tables(db)
        assert list_confirmed_clauses(db_path=db) == []
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_clauses'"
            ).fetchone()
        finally:
            conn.close()
        assert row is None
