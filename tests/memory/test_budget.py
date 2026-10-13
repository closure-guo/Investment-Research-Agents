"""add-decision-memory Task 3：记忆块预算核算（spec analyst-context-budget）。

裁剪顺序逐字：语义条款优先于情景记忆；情景记忆按结算结果相关性降序截断
（相关性 = 同标的 > 同行业 + 时效 → 先丢列表尾部）；裁剪事件记 trace
（用途标签 `decision_memory` + `trimmed` 计数）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from finance_agent.memory import (
    MEMORY_BLOCK_BUDGET_CHARS,
    apply_memory_budget,
    build_memory_block,
    render_memory_state,
)
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
)

AS_OF = "2026-10-13"


def _entry(decision_id: str, *, tier: int = 0, rationale: str = "") -> dict:
    """合成记忆条目（tier=0 同标的 / 1 同行业；列表顺序即相关性降序）。"""
    return {
        "decision_id": decision_id,
        "date": f"2026-0{tier + 8}-01",
        "symbol": "300750" if tier == 0 else "000858",
        "direction": "long",
        "settlement_status": "resolved_win",
        "raw_return": 0.1,
        "rationale_summary": rationale,
    }


def _clause(clause_id: str, text: str) -> dict:
    return {"clause_id": clause_id, "text": text, "confirmed_at": "2026-10-13T00:00:00+00:00"}


class TestApplyMemoryBudget:
    def test_semantic_preserved_episodic_trimmed_from_tail(self):
        episodic = [_entry(f"p{i}", tier=i % 2, rationale="论据" * 20) for i in range(5)]
        semantic = [_clause("c1", "已确认条款甲")]
        # 预算 = 语义段 + 前 2 条情景（逐条试加，首次超限即停）
        two_entries = build_memory_block(episodic[:2], semantic)
        kept, event = apply_memory_budget(episodic, semantic, budget_chars=len(two_entries))

        assert [e["decision_id"] for e in kept] == ["p0", "p1"]
        assert event["trimmed"] == 3
        assert event["dropped_ids"] == ["p2", "p3", "p4"]
        assert event["used_chars"] <= event["budget_chars"]
        assert event["over_budget"] is False
        # 语义条款完整保留（逐字）
        assert "已确认条款甲" in build_memory_block(kept, semantic)

    def test_semantic_only_block_kept_even_over_budget(self):
        """语义条款不可裁：语义段自身超预算 → 情景全丢，如实标注 over_budget。"""
        episodic = [_entry("p0")]
        semantic = [_clause("c1", "已确认条款" * 40)]
        kept, event = apply_memory_budget(episodic, semantic, budget_chars=10)

        assert kept == []
        assert event["trimmed"] == 1
        assert event["over_budget"] is True
        assert "已确认条款" in build_memory_block(kept, semantic)

    def test_no_trim_within_budget(self):
        episodic = [_entry("p0"), _entry("p1")]
        kept, event = apply_memory_budget(episodic, [], budget_chars=MEMORY_BLOCK_BUDGET_CHARS)
        assert [e["decision_id"] for e in kept] == ["p0", "p1"]
        assert event["trimmed"] == 0
        assert event["dropped_ids"] == []


class TestRecallBudgetTracing:
    @staticmethod
    def _db_with_many(tmp_path: Path) -> Path:
        db = tmp_path / "sessions.db"
        init_track_record_tables(db)
        for i in range(6):
            insert_prediction(
                {
                    "prediction_id": f"p{i}",
                    "source_type": "live",
                    "symbol": "300750.SZ",
                    "direction": "long",
                    "rationale_snapshot": {"fund_manager_decision_reasoning": "论据" * 60},
                    "created_at": f"2026-0{i + 1}-01T09:00:00",
                },
                db_path=db,
                status="resolved_win",
            )
        return db

    def test_recall_trims_and_traces_trim_event(self, tmp_path: Path):
        db = self._db_with_many(tmp_path)
        client = MagicMock()
        span = MagicMock()
        client.start_as_current_observation.return_value.__enter__.return_value = span

        with patch("finance_agent.langfuse_tracing.get_langfuse", return_value=client):
            out = render_memory_state(
                {"stock_code": "300750"}, db_path=db, as_of=AS_OF, budget_chars=700
            )

        memory = out["decision_memory"]
        assert len(memory["episodic"]) < 5
        assert memory["trimmed"] >= 1
        assert memory["dropped_ids"]
        assert memory["used_chars"] <= 700
        metadata = span.update.call_args.kwargs["metadata"]
        assert metadata["purpose_tag"] == "decision_memory"
        assert metadata["trimmed"] == memory["trimmed"]
        assert metadata["budget_chars"] == 700
        assert metadata["dropped_ids"] == memory["dropped_ids"]

    def test_recall_within_budget_has_zero_trim(self, tmp_path: Path):
        db = tmp_path / "sessions.db"
        init_track_record_tables(db)
        insert_prediction(
            {
                "prediction_id": "p0",
                "source_type": "live",
                "symbol": "300750.SZ",
                "direction": "long",
                "rationale_snapshot": {"fund_manager_decision_reasoning": "短论据"},
                "created_at": "2026-09-01T09:00:00",
            },
            db_path=db,
            status="resolved_win",
        )
        with patch("finance_agent.langfuse_tracing.get_langfuse", return_value=None):
            out = render_memory_state({"stock_code": "300750"}, db_path=db, as_of=AS_OF)
        assert out["decision_memory"]["trimmed"] == 0
        assert out["decision_memory"]["episodic"]

    def test_analyst_context_uses_trimmed_snapshot(self, tmp_path: Path):
        """预算核算落在上下文组装：分析师上下文块长度不超预算（含语义段）。"""
        from finance_agent.nodes.analysts import _build_technical_context

        db = self._db_with_many(tmp_path)
        with patch("finance_agent.langfuse_tracing.get_langfuse", return_value=None):
            out = render_memory_state(
                {"stock_code": "300750"}, db_path=db, as_of=AS_OF, budget_chars=700
            )
        context = _build_technical_context({"stock_code": "300750", **out})
        block = context[context.index("【历史决策记忆") :]
        assert len(block) <= 700
        assert out["decision_memory"]["dropped_ids"]
        for dropped in out["decision_memory"]["dropped_ids"]:
            assert f"[记忆] 来源: {dropped} " not in block
