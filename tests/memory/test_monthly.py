"""add-monthly-batch-scheduling 2.1：语义记忆月批入口 ``run_memory_extract_monthly``。

spec decision-memory delta「月批提炼排程」三个场景：
- 月批提炼落候选态（``/memory`` 待确认列表可见；注入集合不变）；
- 无素材如实空摘要（``candidates=0``，MUST NOT 伪造候选）；
- 手动补跑经统一通道（``run_job`` 侧见 tests/outcome/test_ops_jobs.py）。

本文件的月批编排用例全部零 LLM：
- ``client`` 缺省 → 规则式降级路径（``path="rule"``，模块既有契约）；
- 注入 stub client → LLM 路径（``path="llm"``）；stub 抛异常 → 自动降级规则式。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finance_agent.memory import render_memory_state
from finance_agent.memory.monthly import (
    DEFAULT_WINDOW_DAYS,
    run_memory_extract_monthly,
)
from finance_agent.memory.semantic import (
    list_clauses,
    list_confirmed_clauses,
    list_pending_clauses,
)
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
    update_prediction_status,
)

AS_OF = "2026-10-13"


@pytest.fixture(autouse=True)
def _no_default_gateway(monkeypatch):
    """缺省 client 的网关 seam 钉成 None：未显式注入 client 的用例走规则路径，零网络。

    默认网关适配器自身由 ``test_zero_arg_job_entry_uses_env_db_and_default_client``
    以 stub 覆盖（真实网关调用不在单测范围）。
    """
    from finance_agent.memory import monthly

    monkeypatch.setattr(monthly, "_default_client", lambda: None)
    yield


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "sessions.db"
    init_track_record_tables(path)
    return path


def _settled(
    db: Path,
    pid: str,
    day: str,
    status: str,
    *,
    direction: str = "long",
    symbol: str = "300750.SZ",
) -> None:
    """合成一条已结算决策（predictions 行；记忆提炼的既有数据源）。"""
    insert_prediction(
        {
            "prediction_id": pid,
            "source_type": "live",
            "symbol": symbol,
            "direction": direction,
            "rationale_snapshot": {},
            "created_at": f"{day}T09:00:00",
        },
        db_path=db,
        status=status,
    )
    update_prediction_status(pid, {"raw_return": 0.05}, db_path=db)


class TestWindowExtraction:
    """近窗口已结算决策 → 提炼 → 候选态落库（零 LLM 规则路径）。"""

    def test_rule_path_lands_pending_with_summary(self, db: Path):
        _settled(db, "p1", "2026-09-01", "resolved_win")
        _settled(db, "p2", "2026-09-02", "resolved_win")
        _settled(db, "p3", "2026-09-03", "resolved_loss")

        summary = run_memory_extract_monthly(db_path=db, as_of=AS_OF)

        assert summary == {
            "window_days": DEFAULT_WINDOW_DAYS,
            "settled_count": 3,
            "candidates": 2,
            "path": "rule",
        }
        pending = list_pending_clauses(db_path=db)
        assert len(pending) == 2
        assert all(
            row["status"] == "pending" and row["source"] == "monthly_batch" for row in pending
        )
        assert any("看多方向" in row["text"] for row in pending)

    def test_unconfirmed_candidates_not_injected(self, db: Path):
        """注入契约不变：月批产物落 pending，未确认不进入任何注入路径。"""
        _settled(db, "p1", "2026-09-01", "resolved_win")
        _settled(db, "p2", "2026-09-02", "resolved_win")

        run_memory_extract_monthly(db_path=db, as_of=AS_OF)

        assert list_confirmed_clauses(db_path=db) == []
        state = render_memory_state({"stock_code": "300750"}, db_path=db, as_of=AS_OF)
        assert state["decision_memory"]["semantic"] == []

    def test_empty_source_reports_zero_candidates(self, db: Path):
        """无素材 → 成功且 candidates=0；MUST NOT 以编造条款冒充提炼产物。"""
        summary = run_memory_extract_monthly(db_path=db, as_of=AS_OF)

        assert summary == {
            "window_days": DEFAULT_WINDOW_DAYS,
            "settled_count": 0,
            "candidates": 0,
            "path": "rule",
        }
        assert list_clauses(db_path=db) == []

    def test_window_excludes_older_settled(self, db: Path):
        """窗口外的已结算决策不进提炼输入（近 90 天口径）。"""
        _settled(db, "old", "2026-01-05", "resolved_win")
        _settled(db, "edge_out", "2026-04-14", "resolved_win")
        _settled(db, "in1", "2026-08-01", "resolved_win")
        _settled(db, "in2", "2026-09-20", "resolved_loss")

        summary = run_memory_extract_monthly(db_path=db, as_of=AS_OF)

        assert summary["settled_count"] == 2
        assert summary["window_days"] == 90

    def test_custom_window_days_echoed(self, db: Path):
        _settled(db, "in1", "2026-09-20", "resolved_win")
        _settled(db, "out1", "2026-08-01", "resolved_win")

        summary = run_memory_extract_monthly(db_path=db, as_of=AS_OF, window_days=30)

        assert summary["window_days"] == 30
        assert summary["settled_count"] == 1

    def test_non_positive_window_rejected(self, db: Path):
        with pytest.raises(ValueError, match="window_days"):
            run_memory_extract_monthly(db_path=db, as_of=AS_OF, window_days=0)

    def test_rerun_is_idempotent(self, db: Path):
        """同窗口重复提炼：store 侧既有去重语义 → 第二次零新增，候选面不变。"""
        _settled(db, "p1", "2026-09-01", "resolved_win")
        _settled(db, "p2", "2026-09-02", "resolved_win")

        first = run_memory_extract_monthly(db_path=db, as_of=AS_OF)
        second = run_memory_extract_monthly(db_path=db, as_of=AS_OF)

        assert first["candidates"] >= 1
        assert second["candidates"] == 0
        assert len(list_pending_clauses(db_path=db)) == first["candidates"]


class TestLlmPath:
    """LLM 优先 / 规则兜底（既有提炼契约；client 可注入，零网络）。"""

    def test_empty_source_never_calls_llm(self, db: Path):
        """空素材零候选且**不调 LLM**（实跑暴露回归：空输入下真实网关曾产出 1 条候选）。

        2026-10-15 本地真实栈手动补跑（空库 + 真实网关）曾得
        ``{settled_count: 0, candidates: 1, path: "llm"}``——空输入下的模型产出属编造，
        违反本 delta 规范场景「无素材 MUST NOT 以编造条款冒充提炼产物」；本用例以
        「会编造的 stub client」钉死防线：空素材不得触达 client。
        """
        calls: list[str] = []

        def _hallucinating_client(prompt: str) -> str:
            calls.append(prompt)
            return "空输入也会编出的一条条款"

        summary = run_memory_extract_monthly(db_path=db, as_of=AS_OF, client=_hallucinating_client)

        assert calls == []  # 空素材不调 LLM（防线在 extract_candidate_clauses 入口）
        assert summary["settled_count"] == 0
        assert summary["candidates"] == 0
        assert list_pending_clauses(db_path=db) == []

    def test_client_success_records_llm_path(self, db: Path):
        _settled(db, "p1", "2026-09-01", "resolved_win")

        def _client(prompt: str) -> str:
            assert "已结算决策" in prompt
            return "小盘股高换手信号命中率持续低于四成\n应避免在缩量反弹中加仓"

        summary = run_memory_extract_monthly(db_path=db, as_of=AS_OF, client=_client)

        assert summary["path"] == "llm"
        assert summary["candidates"] == 2
        assert [row["text"] for row in list_pending_clauses(db_path=db)] == [
            "小盘股高换手信号命中率持续低于四成",
            "应避免在缩量反弹中加仓",
        ]

    def test_client_failure_falls_back_to_rule(self, db: Path):
        _settled(db, "p1", "2026-09-01", "resolved_win")
        _settled(db, "p2", "2026-09-02", "resolved_win")

        def _boom(_prompt: str) -> str:
            raise RuntimeError("endpoint down")

        summary = run_memory_extract_monthly(db_path=db, as_of=AS_OF, client=_boom)

        assert summary["path"] == "rule"
        assert summary["candidates"] >= 1
        assert any("看多方向" in row["text"] for row in list_pending_clauses(db_path=db))

    def test_zero_arg_job_entry_uses_env_db_and_default_client(self, db: Path, monkeypatch):
        """job 调用形态（零参数）：库路径读 env，缺省 client 走默认网关适配器。"""
        from datetime import date, timedelta

        from finance_agent.memory import monthly

        # 记录日相对「今天」一天前——零参数调用用 date.today() 作 as_of，固定日期会随
        # 运行时钟漂出 90 天窗口
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        _settled(db, "p1", yesterday, "resolved_win")
        monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
        monkeypatch.setattr(
            monthly, "_default_client", lambda: lambda _prompt: "月批默认客户端条款"
        )

        summary = run_memory_extract_monthly()

        assert summary["path"] == "llm"
        assert summary["settled_count"] == 1
        assert [row["text"] for row in list_pending_clauses(db_path=db)] == ["月批默认客户端条款"]
