"""报告头增量摘要与多空辩论分歧卡测试（add-report-revision-view Task 3/4）。

对应 delta specs:
- report-decision-rendering「报告头增量摘要」：研究聚焦之后渲染「距上次报告」节，
  五维度（方向/置信度/触发位或价位/现价/PE）基于结构化数据 diff；
  变化「旧 → 新」、无变化如实标注、缺失「未申报」、首份报告整节不渲染、
  MUST NOT 解析上一报告 markdown 回填。
- report-decision-rendering「多空辩论结论分歧卡」：结论文本前渲染
  评级 + 置信度 + 结论首句；结构化评级缺失时退化纯文本，MUST NOT 编造评级。
"""

from __future__ import annotations

from finance_agent.nodes.report import generate_report


def _base_state() -> dict:
    """最小可渲染 state（预置 focus_summary 走幂等复用路径，不触发 LLM）。"""
    return {
        "stock_name": "中信建投",
        "stock_code": "601066",
        "focus_summary": "预置研究聚焦摘要",
        "final_trade_decision": {
            "action": "watch",
            "confidence": 0.60,
            "trigger_low": 22.91,
            "trigger_high": 24.60,
        },
        "stock_quote": {"price": 23.50, "PE": 16.10},
    }


def _prev_snapshot() -> dict:
    return {
        "session_id": "prev-sid",
        "created_at": "2026-09-30T16:05:00",
        "final_trade_decision": {
            "action": "watch",
            "confidence": 0.55,
            "trigger_low": 22.91,
            "trigger_high": 24.60,
        },
        "kpi": {"current_price": 23.03, "pe": 15.20},
    }


class TestRevisionSummary:
    def test_rendered_with_changes_and_nochange(self):
        """spec 场景：方向无变化（值+标注）、置信度变化、触发位无变化、现价变化。"""
        state = _base_state()
        state["previous_report_snapshot"] = _prev_snapshot()
        report = generate_report(state)["final_report"]

        assert "## 距上次报告（2026-09-30）" in report
        assert "watch → watch（无变化）" in report
        assert "0.55 → 0.60" in report
        assert "23.03 → 23.50" in report
        # 触发位双侧一致 → 复合维度如实「无变化」
        assert "无变化" in report

    def test_first_report_not_rendered(self):
        """回溯为 None（首份报告）→ 整节不渲染，不留空节标题。"""
        report = generate_report(_base_state())["final_report"]
        assert "距上次报告" not in report

    def test_missing_decision_marks_unreported_but_kpi_compares(self):
        """spec 场景：历史行 final_trade_decision 为 NULL → 决策三维「未申报」，
        现价/PE 正常对比，MUST NOT 从 markdown 回填。"""
        state = _base_state()
        prev = _prev_snapshot()
        prev["final_trade_decision"] = None
        state["previous_report_snapshot"] = prev
        report = generate_report(state)["final_report"]

        assert "## 距上次报告（2026-09-30）" in report
        assert "未申报" in report
        assert "23.03 → 23.50" in report

    def test_all_dimensions_missing_not_rendered(self):
        """全部维度均不可得（无决策且无 kpi）→ 整节不渲染。"""
        state = _base_state()
        prev = _prev_snapshot()
        prev["final_trade_decision"] = None
        prev["kpi"] = {}
        state["previous_report_snapshot"] = prev
        report = generate_report(state)["final_report"]

        assert "距上次报告" not in report

    def test_buy_prices_dimension_when_no_triggers(self):
        """无触发位字段时退到入场/止损/目标价维度（buy 决策形态）。"""
        state = _base_state()
        state["final_trade_decision"] = {
            "action": "buy",
            "confidence": 0.72,
            "entry_price": 25.0,
            "stop_loss": 23.5,
            "target_price": 28.0,
        }
        prev = _prev_snapshot()
        prev["final_trade_decision"] = {
            "action": "buy",
            "confidence": 0.66,
            "entry_price": 24.0,
            "stop_loss": 22.8,
            "target_price": 27.0,
        }
        state["previous_report_snapshot"] = prev
        report = generate_report(state)["final_report"]

        assert "## 距上次报告（2026-09-30）" in report
        assert "buy → buy（无变化）" in report
        assert "0.66 → 0.72" in report
        assert "24.00" in report and "25.00" in report


class TestDivergenceCard:
    def test_card_rendered_with_rating_and_first_sentence(self):
        """spec 场景：结构化评级 + 置信度 + 结论首句组成分歧卡，置于结论文本前。"""
        state = _base_state()
        state["research_manager_rating"] = "中性"
        state["research_manager_confidence"] = 0.55
        state["research_manager_conclusion"] = (
            "评级: 中性（置信度 0.55）\n"
            "多空双方分歧聚焦于'强劲基本面与弱势技术面'的背离如何解读。"
            "其余论据展开若干段落。"
        )
        report = generate_report(state)["final_report"]

        assert "评级: 中性 · 置信度 0.55 · 分歧焦点: 多空双方分歧聚焦于" in report
        # 卡片在结论文本之前
        card_idx = report.index("分歧焦点")
        body_idx = report.index("其余论据展开")
        assert card_idx < body_idx

    def test_card_degrades_without_structured_rating(self):
        """结构化评级缺失（历史会话/解析降级）→ 纯文本现状渲染，MUST NOT 编造评级。"""
        state = _base_state()
        state["research_manager_conclusion"] = "多空各执一词，结论偏谨慎。"
        report = generate_report(state)["final_report"]

        assert "分歧焦点" not in report
        assert "多空各执一词，结论偏谨慎。" in report


class TestRealPipelineShapes:
    """真实管线形态回归（本地实跑 601066 抓到的两处断点）。"""

    def test_analysis_state_declares_previous_report_snapshot(self):
        """AnalysisState 是 TypedDict，未声明键在图入口被静默丢弃——
        previous_report_snapshot 必须在 schema 中声明，否则渲染恒不触发。"""
        from finance_agent.state import AnalysisState

        assert "previous_report_snapshot" in AnalysisState.__annotations__

    def test_revision_summary_with_pydantic_decision_in_state(self):
        """state 中 final_trade_decision 为 TradeDecision pydantic 对象时
        增量摘要正常取值（渲染链不得 .get 炸 AttributeError）。"""
        from finance_agent.models import TradeDecision

        state = _base_state()
        state["final_trade_decision"] = TradeDecision(
            action="watch",
            confidence=0.60,
            reasoning="新报告推理",
            trigger_low=22.91,
            trigger_high=24.60,
        )
        state["previous_report_snapshot"] = _prev_snapshot()
        report = generate_report(state)["final_report"]

        assert "## 距上次报告（2026-09-30）" in report
        assert "watch → watch（无变化）" in report
        assert "0.55 → 0.60" in report


class TestRevisionEndToEnd:
    """Task 5 端到端：DB 落库 → 回溯查询 → state 注入 → generate_report 渲染。

    复刻入口链路（api fast path / ReAct 工具路径共用的一段：
    get_previous_completed_session → initial_state["previous_report_snapshot"]），
    覆盖「无变化」与「未申报」两种形态。
    """

    def _seed_previous(self, tmp_path, monkeypatch, decision: dict | None) -> None:
        from finance_agent import session_store

        monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
        session_store.init_db()
        prev_sid = session_store.create_session(
            stock_code="601066", stock_name="中信建投", status="running"
        )
        session_store.update_session_report(
            prev_sid,
            report_markdown="# 旧报告",
            chart_data={"kpi": {"current_price": 23.03, "pe": 15.20}},
            final_trade_decision=decision,
            status="completed",
        )
        conn = session_store._get_db()
        conn.execute(
            "UPDATE sessions SET created_at = '2026-09-30T16:05:00' WHERE session_id = ?",
            (prev_sid,),
        )
        conn.commit()
        conn.close()

    def test_full_chain_nochange_and_change(self, tmp_path, monkeypatch):
        """同标的连跑两份：旧报告 watch 0.55 / 现价 23.03 → 新报告 watch 0.60 /
        现价 23.50 —— 方向无变化、置信度与现价走「旧 → 新」。"""
        from finance_agent import session_store

        self._seed_previous(
            tmp_path,
            monkeypatch,
            {"action": "watch", "confidence": 0.55, "trigger_low": 22.91, "trigger_high": 24.60},
        )
        state = _base_state()
        state["previous_report_snapshot"] = session_store.get_previous_completed_session(
            "601066", "current-sid"
        )
        report = generate_report(state)["final_report"]

        assert "## 距上次报告（2026-09-30）" in report
        assert "watch → watch（无变化）" in report
        assert "0.55 → 0.60" in report
        assert "23.03 → 23.50" in report

    def test_full_chain_unreported_when_legacy_row(self, tmp_path, monkeypatch):
        """旧报告行无终稿决策（历史 NULL）→ 决策三维「未申报」，kpi 维度正常。"""
        from finance_agent import session_store

        self._seed_previous(tmp_path, monkeypatch, None)
        state = _base_state()
        state["previous_report_snapshot"] = session_store.get_previous_completed_session(
            "601066", "current-sid"
        )
        report = generate_report(state)["final_report"]

        assert "## 距上次报告（2026-09-30）" in report
        assert "决策方向: 未申报" in report
        assert "置信度: 未申报" in report
        assert "触发位/价位: 未申报" in report
        assert "23.03 → 23.50" in report
