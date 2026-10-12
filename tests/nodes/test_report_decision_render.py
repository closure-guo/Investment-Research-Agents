"""TDD tests for update-decision-integrity-gates Task 2/3 — 交易决策节渲染。

Task 2：非法仓位档位字面量归一渲染（report-decision-rendering「参数缺失时诚实标注」
MODIFIED：档位词表 light/moderate/heavy 大小写不敏感，非法字面量渲染前归一为缺失
「未提供」，归一 MUST NOT 回写决策对象）。
Task 3：buy/sell 终稿再评估触发条件渲染（缺失「未申报」）；update-decision-price-gate：
报警仅进 trace，渲染链不接收 anomalies。
update-decision-price-gate T4 收口：gate 复核注（pass 形态）不泄「结构不完整」报告标注。
add-watch-trigger-tracking Task 5：watch 触发位行（缺失如实「未申报」，禁文本回填）、
入池跟踪声明行（FM approve 时）、数据真空提示行（报告日与行情截止日间隔 >阈值，
默认 3 自然日、REPORT_DATA_VACUUM_THRESHOLD_DAYS 可配）。
Fix round 1：触发位两行收窄为 watch 专属（buy/sell/hold 不渲染）；真空阈值配置化。
"""

import importlib
import inspect
from datetime import date
from typing import Any

import pandas as pd
import pytest

from finance_agent.models import TradeDecision
from finance_agent.nodes.report import (
    SHORT_DISCLOSURE,
    _fmt_reeval_triggers,
    _format_trade_decision,
    generate_report,
)


@pytest.fixture(autouse=True)
def _stub_focus_summary(monkeypatch):
    """研究聚焦摘要打桩（不烧 LLM，渲染测试只关心交易决策节）。"""
    from finance_agent.nodes import report as report_mod

    monkeypatch.setattr(report_mod, "complete_text", lambda *a, **k: ("聚焦摘要（测试数据）", {}))


class TestPositionSizeVocabNormalization:
    """非法仓位档位字面量归一渲染（600515 实证：watch 决策 position_size="none"
    原样透传到报告）。"""

    @staticmethod
    def _state(decision: TradeDecision | dict) -> dict:
        return {"stock_code": "600515", "final_trade_decision": decision}

    def test_illegal_literal_none_renders_weitigong(self):
        """600515 形态：position_size="none" 不在词表 → 渲染「未提供」，不透传字面量。"""
        decision = {
            "action": "watch",
            "confidence": 0.5,
            "position_size": "none",
            "reasoning": "多因素均衡",
            "inaction_reason": "等待右侧信号",
            "reeval_triggers": ["放量站上 20 日线"],
        }
        md = generate_report(self._state(decision))["final_report"]
        assert "- **仓位**: 未提供" in md
        assert "- **仓位**: none" not in md

    @pytest.mark.parametrize("literal", ["null", "", "  ", "full", "30%", None])
    def test_other_illegal_literals_renders_weitigong(self, literal):
        """词表外其余非法形态（"null"/空串/纯空白/自由文本/None）一律「未提供」。"""
        decision = {
            "action": "watch",
            "confidence": 0.5,
            "position_size": literal,
            "reasoning": "r",
            "inaction_reason": "等待",
            "reeval_triggers": ["放量站上 20 日线"],
        }
        md = generate_report(self._state(decision))["final_report"]
        assert "- **仓位**: 未提供" in md

    @pytest.mark.parametrize("level", ["light", "moderate", "heavy", "Light", "MODERATE", "Heavy"])
    def test_valid_levels_render_original_value(self, level):
        """合法档位（含大小写变体）按原值渲染，MUST NOT 归一为「未提供」。"""
        decision = {
            "action": "buy",
            "confidence": 0.5,
            "position_size": level,
            "entry_price": 10.0,
            "stop_loss": 9.0,
            "target_price": 12.0,
            "reasoning": "r",
            "reeval_triggers": ["跌破 9 元止损离场"],
        }
        md = generate_report(self._state(decision))["final_report"]
        assert f"- **仓位**: {level}" in md

    def test_normalization_does_not_mutate_decision_dict(self):
        """归一只作用于渲染：dict 决策对象原值保留（落库与 trace 可观测）。"""
        decision = {
            "action": "watch",
            "confidence": 0.5,
            "position_size": "none",
            "reasoning": "r",
            "inaction_reason": "等待",
            "reeval_triggers": [],
        }
        generate_report(self._state(decision))
        assert decision["position_size"] == "none"

    def test_normalization_does_not_mutate_decision_object(self):
        """归一只作用于渲染：pydantic 决策对象属性原值保留。"""
        decision = TradeDecision.model_validate(
            {
                "action": "watch",
                "confidence": 0.5,
                "position_size": "none",
                "reasoning": "r",
                "inaction_reason": "等待",
                "reeval_triggers": [],
            }
        )
        generate_report(self._state(decision))
        assert decision.position_size == "none"

    def test_legacy_dict_without_position_key(self):
        """历史 dict 无 position_size 键 → 「未提供」，不抛异常。"""
        decision = {"action": "watch", "confidence": 0.5, "reasoning": "r"}
        md = _format_trade_decision(decision)
        assert "- **仓位**: 未提供" in md


class TestBuySellReevalTriggersRender:
    """Task 3：buy/sell 也渲染「再评估触发条件」行（report-decision-rendering
    「交易决策节渲染操作参数」MODIFIED：非空逐条编号；空/缺「未申报」不省略整行）。"""

    @staticmethod
    def _buy_state(decision: TradeDecision | dict) -> dict:
        return {"stock_code": "601818", "final_trade_decision": decision}

    def test_buy_full_triggers_numbered_and_ordered(self):
        state = self._buy_state(
            {
                "action": "buy",
                "confidence": 0.55,
                "position_size": "light",
                "entry_price": 6.12,
                "stop_loss": 5.8,
                "target_price": 6.8,
                "reasoning": "r",
                "reeval_triggers": ["跌破 5.8 元止损离场", "放量站上 20 日线加仓"],
            }
        )
        md = generate_report(state)["final_report"]
        assert "- **再评估触发条件**: ① 跌破 5.8 元止损离场；② 放量站上 20 日线加仓" in md
        # spec 顺序：…入场价、止损价、目标价、再评估触发条件、理由
        order = [
            md.index(k)
            for k in ("**入场价**", "**止损价**", "**目标价**", "**再评估触发条件**", "**理由**")
        ]
        assert order == sorted(order)

    def test_sell_empty_triggers_marked_unreported(self):
        """601818 形态：sell 清洗后触发条件为空（含「已打回仍未申报」终检形态）→
        「未申报」行保留，不省略整行、不编造条目。"""
        state = self._buy_state(
            {
                "action": "sell",
                "confidence": 0.52,
                "position_size": "light",
                "entry_price": 3.42,
                "stop_loss": 0,
                "target_price": 0,
                "reasoning": "维持卖出方向",
                "reeval_triggers": [],
            }
        )
        md = generate_report(state)["final_report"]
        assert "- **再评估触发条件**: 未申报" in md
        assert md.count("- **再评估触发条件**:") == 1

    def test_buy_missing_triggers_key_renders_unreported(self):
        """buy 决策无 reeval_triggers 键 → 「未申报」（MUST NOT 整行省略）。"""
        state = self._buy_state(
            {
                "action": "buy",
                "confidence": 0.5,
                "position_size": "light",
                "entry_price": 10.0,
                "stop_loss": 9.0,
                "target_price": 12.0,
                "reasoning": "r",
            }
        )
        md = generate_report(state)["final_report"]
        assert "- **再评估触发条件**: 未申报" in md

    def test_watch_legacy_object_without_fields(self):
        """watch 旧决策对象（无 inaction_reason/reeval_triggers 字段）→ 「未申报」，
        渲染不因字段缺失抛异常。"""
        state = {
            "stock_code": "600519",
            "final_trade_decision": {"action": "watch", "confidence": 0.5, "reasoning": "r"},
        }
        md = generate_report(state)["final_report"]
        assert "- **不行动原因**: 未申报" in md
        assert "- **再评估触发条件**: 未申报" in md


class TestPriceAlarmNeverRendered:
    """update-decision-price-gate：报警信息属于内部 trace——报告 MUST NOT 渲染
    「价位待核实」类标注；渲染链签名不再接收 anomalies（阻断语义下报告只可能
    由无残留 anomaly 的终稿产出）。"""

    ANOMALY = {
        "kind": "deviation",
        "source_text": "价格回落至 1500 以下",
        "indicator": "近期低点",
        "verified_value": 1450.0,
        "deviation_pct": 3.45,
        "message": "文本价位 1500 与近期低点已验证值 1450 偏差 3.45%，价位待核实",
    }

    def test_rendering_chain_no_longer_accepts_anomalies(self):
        assert "anomalies" not in inspect.signature(_format_trade_decision).parameters
        assert "anomalies" not in inspect.signature(_fmt_reeval_triggers).parameters
        # anomalies 实参必须被拒绝（签名收窄后 TypeError）。add-watch-trigger-tracking
        # Task 5：新参数（data_cutoff/fund_approved/report_date）均为 keyword-only——
        # positional 第二实参仍被运行时 TypeError 拒绝，mypy 错误码由 call-arg 变为
        # misc（位置超限）+ arg-type（误绑 data_cutoff），护栏语义不变
        with pytest.raises(TypeError):
            _format_trade_decision(  # type: ignore[misc]
                TradeDecision.model_validate(
                    {
                        "action": "watch",
                        "confidence": 0.5,
                        "reasoning": "r",
                        "inaction_reason": "等待回落确认",
                        "reeval_triggers": ["价格回落至 1500 以下", "跌破 1400 元离场"],
                    }
                ),
                [self.ANOMALY],  # type: ignore[arg-type]
            )

    def test_trigger_entry_rendered_without_annotation(self):
        decision = TradeDecision.model_validate(
            {
                "action": "watch",
                "confidence": 0.5,
                "reasoning": "r",
                "inaction_reason": "等待回落确认",
                "reeval_triggers": ["价格回落至 1500 以下", "跌破 1400 元离场"],
            }
        )
        md = _format_trade_decision(decision)
        assert "① 价格回落至 1500 以下" in md
        assert "价位待核实" not in md
        assert "② 跌破 1400 元离场" in md

    def test_sell_reasoning_rendered_without_annotation(self):
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "reasoning": "股价跌破近期低点 26.28 支撑，趋势走弱",
                "entry_price": 26.0,
                "stop_loss": 27.5,
                "target_price": 24.0,
                "reeval_triggers": ["反弹至 27.5 元减仓"],
            }
        )
        md = _format_trade_decision(decision)
        assert "- **理由**: 股价跌破近期低点 26.28 支撑，趋势走弱" in md
        assert "价位待核实" not in md


class TestGateRecheckNoteNeverLeaksReport:
    """update-decision-price-gate T4 收口回归锁：gate 复核注不泄报告标注。

    decision_price_gate 的复核性 note（pass 形态「打回后已修正」）随三个终稿完整性
    检查一并被 fund_manager.final_integrity_notes 收集进 FM 上下文；报告渲染只挑
    「结构不完整」标注（_FM_INCOMPLETE_MARKERS 过滤）——gate 复核注文案 MUST NOT
    撞上词表导致 pass 状态误渲染「审批对象结构不完整标注」。
    """

    @staticmethod
    def _gate_pass_state() -> dict:
        return {
            "stock_code": "600519",
            "final_trade_decision": {
                "action": "buy",
                "confidence": 0.6,
                "position_size": "light",
                "entry_price": 10.0,
                "stop_loss": 9.0,
                "target_price": 12.0,
                "reasoning": "r",
                "reeval_triggers": ["跌破 9 元止损离场"],
            },
            # 全 pass 的真实 note 文案（risk.py：打回后复核通过形态）
            "final_price_check": {"result": "pass", "note": "打回后已申报"},
            "final_inaction_check": {"result": "pass", "note": "打回后已申报"},
            "final_reeval_check": {"result": "pass", "note": "打回后已申报"},
            "final_trigger_check": {"result": "pass", "note": "打回后已申报"},
            "decision_price_gate": {"result": "pass", "note": "打回后已修正"},
            # 报告产出前提：gate pass 后管线走完 FM 审批（标注渲染挂 FM 分支）
            "fund_manager_decision": "approve",
        }

    def test_pass_gate_note_produces_report_without_annotation(self):
        """全 pass + gate 复核注「打回后已修正」→ 报告正常产出，无「不完整」标注。"""
        md = generate_report(self._gate_pass_state())["final_report"]
        assert md
        assert "审批对象结构不完整标注" not in md

    def test_incomplete_note_still_renders_annotation(self):
        """敏感性对照：note 真含「缺失」时标注 MUST 照常渲染（锁死过滤器在位）。"""
        state = self._gate_pass_state()
        state["final_price_check"] = {"result": "warn", "note": "止损价缺失"}
        md = generate_report(state)["final_report"]
        assert "审批对象结构不完整标注" in md
        assert "止损价缺失" in md


class TestSellTypeSplitRendering:
    """update-sell-action-typing（#188）：sell 按 sell_type 分模板——exit 渲染
    减仓节奏、不渲染建仓价位行；short/None 维持现行参数行。"""

    def test_exit_renders_schedule_not_prices(self):
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "position_size": "light",
                "sell_type": "exit",
                "exit_schedule": "分两批：现价减半、跌破600清仓",
                "reasoning": "持有者退出敞口",
                "reeval_triggers": ["重新站上 660 恢复持有"],
            }
        )
        md = _format_trade_decision(decision)
        assert "- **减仓节奏**: 分两批：现价减半、跌破600清仓" in md
        assert "入场价" not in md and "止损价" not in md and "目标价" not in md
        assert "① 重新站上 660 恢复持有" in md  # 重新介入条件由 reeval 承载

    def test_exit_missing_schedule_honest_placeholder(self):
        decision = TradeDecision.model_validate(
            {"action": "sell", "confidence": 0.5, "sell_type": "exit", "reasoning": "r"}
        )
        md = _format_trade_decision(decision)
        assert "- **减仓节奏**: 未申报" in md
        assert "入场价" not in md

    def test_short_renders_price_template(self):
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "position_size": "light",
                "sell_type": "short",
                "entry_price": 640.0,
                "stop_loss": 700.0,
                "target_price": 571.0,
                "reasoning": "做空建仓",
                "reeval_triggers": ["跌破 570 重估"],
            }
        )
        md = _format_trade_decision(decision)
        assert "入场价" in md and "止损价" in md and "目标价" in md
        assert "减仓节奏" not in md

    def test_untyped_sell_defaults_to_price_template(self):
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "entry_price": 640.0,
                "stop_loss": 700.0,
                "target_price": 571.0,
                "reasoning": "r",
            }
        )
        md = _format_trade_decision(decision)
        assert "入场价" in md  # None → short 模板（历史兼容）

    def test_short_renders_short_disclosure(self):
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "sell_type": "short",
                "entry_price": 640.0,
                "stop_loss": 700.0,
                "target_price": 571.0,
                "reasoning": "做空建仓",
                "reeval_triggers": ["跌破 570 重估"],
            }
        )
        md = _format_trade_decision(decision)
        assert "做空披露" in md
        assert "融资融券信用账户" in md
        assert "A 股普通证券账户不可执行" in md

    def test_short_disclosure_constant_matches_spec_literal(self):
        """规格固定文本逐字完整相等（review M-3）：三处子串断言杀不死删句变异
        （删「标的须为两融标的且有券源」或「本条为方向研判，不代表可直接执行」
        子串均不会红）——规格字面量全文钉死，渲染行须原样内嵌该常量。"""
        spec_literal = (
            "做空建仓需融资融券信用账户（标的须为两融标的且有券源），"
            "A 股普通证券账户不可执行；本条为方向研判，不代表可直接执行"
        )
        assert spec_literal == SHORT_DISCLOSURE
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "sell_type": "short",
                "entry_price": 640.0,
                "stop_loss": 700.0,
                "target_price": 571.0,
                "reasoning": "做空建仓",
                "reeval_triggers": ["跌破 570 重估"],
            }
        )
        assert f"- **做空披露**: {spec_literal}" in _format_trade_decision(decision)

    def test_legacy_none_sell_renders_short_disclosure(self):
        """历史 None 形态同样按做空模板呈现——不披露即把不可执行结构呈现为可执行建议。"""
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "entry_price": 3.0,
                "stop_loss": 3.1,
                "target_price": 2.9,
                "reasoning": "降低敞口",
            }
        )
        md = _format_trade_decision(decision)
        assert "做空披露" in md

    def test_exit_does_not_render_short_disclosure(self):
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "sell_type": "exit",
                "exit_schedule": "分两批减持",
                "reasoning": "退出敞口",
            }
        )
        md = _format_trade_decision(decision)
        assert "做空披露" not in md

    def test_buy_does_not_render_short_disclosure(self):
        decision = TradeDecision.model_validate(
            {
                "action": "buy",
                "confidence": 0.7,
                "entry_price": 100.0,
                "stop_loss": 92.0,
                "target_price": 120.0,
                "reasoning": "建仓",
                "reeval_triggers": ["跌破 92"],
            }
        )
        assert "做空披露" not in _format_trade_decision(decision)


class TestWatchTriggerRendering:
    """add-watch-trigger-tracking：watch 触发位行/入池声明/数据真空提示。

    触发位缺失如实「未申报」（与建仓参数「未提供」词形刻意区分），MUST NOT 从
    reeval_triggers 文本解析回填；触发位两行 watch 专属（buy/sell/hold 不渲染）；
    入池声明仅 FM approve 渲染；真空提示作用于全部 action，间隔 >阈值才提示
    （默认阈值 3 自然日、env 可配；=阈值不提示，截止晚于报告日不提示）。
    """

    @staticmethod
    def _watch(**kw: Any) -> TradeDecision:
        base: dict[str, Any] = {
            "action": "watch",
            "confidence": 0.55,
            "reasoning": "r",
            "inaction_reason": "观望",
            "reeval_triggers": ["站上 24.6 重估"],
        }
        base.update(kw)
        return TradeDecision.model_validate(base)

    @staticmethod
    def _buy() -> TradeDecision:
        return TradeDecision.model_validate(
            {
                "action": "buy",
                "confidence": 0.6,
                "reasoning": "r",
                "entry_price": 10.0,
                "stop_loss": 9.0,
                "target_price": 12.0,
            }
        )

    def test_watch_renders_trigger_rows(self):
        md = _format_trade_decision(self._watch(trigger_high=24.6, trigger_low=22.91))
        assert "- **上破触发位**: 24.6" in md
        assert "- **下破触发位**: 22.91" in md

    def test_watch_missing_triggers_annotated_not_parsed(self):
        md = _format_trade_decision(self._watch())
        assert "- **上破触发位**: 未申报" in md
        assert "- **下破触发位**: 未申报" in md
        # MUST NOT 从 reeval_triggers 文本解析回填（「站上 24.6 重估」只属再评估行）
        assert "24.6" not in md.split("上破触发位")[1].split("下破触发位")[0]

    def test_buy_does_not_render_trigger_rows(self):
        md = _format_trade_decision(self._buy())
        assert "上破触发位" not in md
        assert "下破触发位" not in md

    def test_hold_does_not_render_trigger_rows(self):
        """spec「watch 触发位与入池跟踪渲染」：buy/sell/hold 决策不渲染触发位两行——
        触发位是 watch 决策专属申报参数，hold 的重新介入条件由 reeval_triggers 承载。"""
        decision = TradeDecision.model_validate(
            {
                "action": "hold",
                "confidence": 0.5,
                "reasoning": "r",
                "inaction_reason": "持有观察",
                "reeval_triggers": ["跌破 22 重估"],
            }
        )
        md = _format_trade_decision(decision)
        assert "上破触发位" not in md
        assert "下破触发位" not in md

    def test_pool_declaration_rendered_when_approved(self):
        md = _format_trade_decision(self._watch(trigger_high=24.6), fund_approved=True)
        assert "- **跟踪**:" in md
        assert "已入池跟踪" in md and "交易日窗口结算" in md

    def test_pool_declaration_absent_when_not_approved(self):
        md = _format_trade_decision(self._watch(trigger_high=24.6), fund_approved=False)
        assert "已入池跟踪" not in md

    def test_data_vacuum_notice_rendered(self):
        md = _format_trade_decision(
            self._watch(trigger_high=24.6),
            data_cutoff=date(2026, 9, 30),
            report_date=date(2026, 10, 8),
        )
        assert "数据真空" in md and "跳空缺口" in md
        assert "2026-09-30" in md

    def test_no_vacuum_notice_when_fresh(self):
        md = _format_trade_decision(
            self._watch(trigger_high=24.6),
            data_cutoff=date(2026, 10, 8),
            report_date=date(2026, 10, 8),
        )
        assert "跳空缺口" not in md
        assert "数据真空" not in md

    def test_vacuum_notice_at_threshold_boundary_absent(self):
        """间隔恰为阈值（3 自然日，如节前最后交易日 + 假期 3 天）不提示——spec 为
        「间隔 >3 自然日」。"""
        md = _format_trade_decision(
            self._watch(trigger_high=24.6),
            data_cutoff=date(2026, 10, 5),
            report_date=date(2026, 10, 8),
        )
        assert "跳空缺口" not in md

    def test_vacuum_notice_absent_when_cutoff_after_report_date(self):
        """截止晚于报告日的异常态（间隔 <=0）不提示，不渲染负数天数。"""
        md = _format_trade_decision(
            self._watch(trigger_high=24.6),
            data_cutoff=date(2026, 10, 9),
            report_date=date(2026, 10, 8),
        )
        assert "跳空缺口" not in md

    def test_vacuum_notice_renders_for_buy_too(self):
        """真空提示作用于全部 action——buy/sell 价位行同样锚定行情截止日收盘价。"""
        md = _format_trade_decision(
            self._buy(),
            data_cutoff=date(2026, 9, 30),
            report_date=date(2026, 10, 8),
        )
        assert "跳空缺口" in md

    def test_vacuum_threshold_is_configurable(self, monkeypatch):
        """spec「间隔阈值 SHALL 为配置项」：env 置 1 时 2 自然日间隔（默认阈值下
        不提示）即提示。常量为 import 时读取——reload 重读 env，finally 恢复默认，
        不向后续测试泄漏阈值状态（test_agent_factory_testing_branch 同款 reload 先例）。"""
        from finance_agent.nodes import report as report_mod

        monkeypatch.setenv("REPORT_DATA_VACUUM_THRESHOLD_DAYS", "1")
        importlib.reload(report_mod)
        try:
            md = report_mod._format_trade_decision(
                self._watch(trigger_high=24.6),
                data_cutoff=date(2026, 10, 6),
                report_date=date(2026, 10, 8),  # 间隔 2 自然日 > 1
            )
            assert "跳空缺口" in md
        finally:
            monkeypatch.delenv("REPORT_DATA_VACUUM_THRESHOLD_DAYS", raising=False)
            importlib.reload(report_mod)

    def test_generate_report_wires_cutoff_triggers_pool_and_vacuum(self):
        """生产路径接线：kline 截止日 → 真空提示；FM approve → 入池声明；触发位行
        进交易决策节（头部行情截止行既有行为不回归）。"""
        kline = pd.DataFrame({"日期": ["2026-09-29", "2026-09-30"], "收盘": [100.0, 101.0]})
        state = {
            "stock_code": "688072",
            "kline": kline,
            "final_trade_decision": {
                "action": "watch",
                "confidence": 0.55,
                "reasoning": "r",
                "inaction_reason": "观望",
                "reeval_triggers": ["站上 24.6 重估"],
                "trigger_high": 24.6,
                "trigger_low": 22.91,
            },
            "fund_manager_decision": "approve",
        }
        md = generate_report(state)["final_report"]
        assert "行情数据截止: 2026-09-30" in md
        assert "- **上破触发位**: 24.6" in md
        assert "- **下破触发位**: 22.91" in md
        assert "已入池跟踪" in md
        # 2026-09-30 距实际报告生成日恒 >3 自然日（今日 2026-10-08 起）→ 提示在场
        assert "数据真空" in md
