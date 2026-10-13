"""add-decision-memory Task 1：节点上下文注入与图接线（agent-node-contracts 契约扩展）。

向后兼容为本文件的核心断言：记忆块缺省（无 `decision_memory` 键）时，各节点上下文
与「扩展前」逐字节一致（空记忆键 == 无键）；有历史时分析师与 Trader 上下文含
来源决策 ID 与日期。
"""

from __future__ import annotations

import functools
import inspect
from pathlib import Path

import pytest

from finance_agent import memory
from finance_agent.memory import render_memory_state
from finance_agent.nodes.analysts import (
    _build_fundamental_context,
    _build_macro_context,
    _build_sentiment_context,
    _build_technical_context,
)
from finance_agent.nodes.risk import _build_risk_context
from finance_agent.nodes.trader import _build_trader_context
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
)

AS_OF = "2026-10-13"

_ANALYST_BUILDERS = (
    _build_technical_context,
    _build_macro_context,
    _build_fundamental_context,
    _build_sentiment_context,
)
_ALL_BUILDERS = (*_ANALYST_BUILDERS, _build_trader_context, _build_risk_context)


def _base_state() -> dict:
    return {
        "stock_code": "300750",
        "stock_name": "宁德时代",
        "industry_info": {"industry": "电池", "name": "宁德时代"},
        "analyst_reports": {
            "technical": {"summary": "技术面偏多"},
            "macro": {"summary": "宏观中性"},
        },
        "research_manager_conclusion": "维持看多",
        "trader_plan": {"action": "buy", "confidence": 0.6},
        "risk_metrics": {"max_drawdown": 0.2},
        "derived_metrics": {"stop_distance_pct": 0.05, "risk_reward_ratio": 2.0},
    }


@pytest.fixture
def db_with_history(tmp_path: Path) -> Path:
    db = tmp_path / "sessions.db"
    init_track_record_tables(db)
    for pid, day in (("p1", "2026-08-01"), ("p2", "2026-09-01"), ("p3", "2026-10-01")):
        insert_prediction(
            {
                "prediction_id": pid,
                "source_type": "live",
                "symbol": "300750.SZ",
                "direction": "long",
                "entry_price": 100.0,
                "rationale_snapshot": {"fund_manager_decision_reasoning": f"{pid} 论据"},
                "created_at": f"{day}T09:00:00",
            },
            db_path=db,
            status="resolved_win",
        )
    return db


class TestMemoryInjection:
    """spec decision-memory 场景：分析师与 Trader 上下文含 3 条记忆条目。"""

    def test_all_analysts_and_trader_see_entries(self, db_with_history: Path):
        out = render_memory_state(
            {"stock_code": "300750", "stock_name": "宁德时代"},
            db_path=db_with_history,
            as_of=AS_OF,
        )
        state = {**_base_state(), **out}

        for builder in _ANALYST_BUILDERS:
            context = builder(state)
            for pid, day in (("p1", "2026-08-01"), ("p2", "2026-09-01"), ("p3", "2026-10-01")):
                assert f"[记忆] 来源: {pid} ({day})" in context, builder.__name__
        trailer = _build_trader_context(state)
        assert "[记忆] 来源: p3 (2026-10-01)" in trailer

    def test_risk_context_has_no_episodic_entries(self, db_with_history: Path):
        """情景记忆注入分析师与 Trader；风控辩论层只收语义条款（T2 另行覆盖）。"""
        out = render_memory_state({"stock_code": "300750"}, db_path=db_with_history, as_of=AS_OF)
        state = {**_base_state(), **out}
        assert "[记忆] 来源:" not in _build_risk_context(state)


class TestBackwardCompatibility:
    """无记忆 → 与扩展前逐字节一致（空记忆键 == 无键）。"""

    @pytest.mark.parametrize("builder", _ALL_BUILDERS, ids=lambda b: b.__name__)
    def test_absent_key_equals_empty_key(self, builder):
        base = _base_state()
        without = builder(base)
        with_empty = builder({**base, "decision_memory": {"episodic": [], "semantic": []}})
        assert without == with_empty
        assert "[记忆]" not in without

    def test_empty_history_renders_no_block(self, tmp_path: Path):
        """无任何历史决策的新标的：检索为空 → 各上下文无记忆块。"""
        db = tmp_path / "empty.db"
        init_track_record_tables(db)
        out = render_memory_state({"stock_code": "301999"}, db_path=db, as_of=AS_OF)
        state = {**_base_state(), **out}
        for builder in _ALL_BUILDERS:
            assert "[记忆]" not in builder(state)

    def test_recall_short_circuits_when_key_present(self, monkeypatch):
        """citation 重试回边复用已写入的键：不重复查库。"""

        def _spy(state, **kwargs):  # noqa: ANN001, ANN202 - 测试替身
            raise AssertionError("重复召回")

        monkeypatch.setattr("finance_agent.memory.episodic.render_memory_state", _spy)
        out = memory.memory_recall_entry({"stock_code": "300750", "decision_memory": {}})
        assert out == {}


class TestGraphWiring:
    """生产图接线：analysts_entry = 记忆召回入口（深度分析启动时检索）。"""

    @staticmethod
    def _unwrap(spec) -> object:  # noqa: ANN001
        seen: set[int] = set()
        stack = [spec]
        while stack:
            obj = stack.pop()
            if obj is None or id(obj) in seen:
                continue
            seen.add(id(obj))
            # 只认真实函数上的标记：functools.partial 经 __getattr__ 透传到底层函数，
            # 直接 getattr 会把 partial/包装器误判为召回入口
            if inspect.isfunction(obj) and getattr(obj, "memory_recall_enabled", False):
                return obj
            for attr in ("bound", "func", "afunc", "runnable"):
                stack.append(getattr(obj, attr, None))
            if isinstance(obj, functools.partial):  # PregelNode 包装：run_in_executor(None, fn)
                stack.extend(obj.args)
        return None

    def test_analysts_entry_is_memory_recall(self):
        from finance_agent.graph import build_5layer_graph

        graph = build_5layer_graph()
        assert self._unwrap(graph.nodes["analysts_entry"]) is memory.memory_recall_entry

    def test_recall_entry_returns_decision_memory_key(self, db_with_history: Path, monkeypatch):
        monkeypatch.setenv("SESSIONS_DB_PATH", str(db_with_history))
        out = memory.memory_recall_entry({"stock_code": "300750"})
        assert [e["decision_id"] for e in out["decision_memory"]["episodic"]] == ["p3", "p2", "p1"]
