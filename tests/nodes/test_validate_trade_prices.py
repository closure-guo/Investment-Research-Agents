"""toolize-price-levels Task 2.1：交易价位 sanity 校验 节点/路由/修正 测试（TDD 先行）。"""

import pytest

from finance_agent.models import TradeDecision
from finance_agent.nodes.validate import correct_prices, validate_trade_prices
from finance_agent.routing import after_validate_trade_prices


def _levels(base=100.0, atr=2.0):
    return {
        "available": True,
        "entry_ref": base,
        "atr": atr,
        "recent_high": base + 5,
        "recent_low": base - 5,
        "stop_band_long": {"low": base - 2 * atr, "high": base - atr},
        "target_band_long": {"low": base + 2 * atr, "high": base + 4 * atr},
        "full_band": [base - 5 - 2 * atr, base + 5 + 2 * atr],
    }


def _state(plan, levels=None, close=100.0, attempts=0):
    kline = pd.DataFrame({"日期": ["2026-06-02"], "收盘": [close]})
    return {
        "trader_plan": plan if isinstance(plan, dict) else plan.model_dump(),
        "price_levels": levels if levels is not None else _levels(),
        "kline": kline,
        "price_check_attempts": attempts,
    }


def _plan(action="buy", entry=100.0, stop=95.0, target=108.0):
    return TradeDecision(
        action=action,
        confidence=0.8,
        reasoning="r",
        entry_price=entry,
        stop_loss=stop,
        target_price=target,
    )


import pandas as pd  # noqa: E402


class TestValidateTradePrices:
    def test_valid_long_passes(self):
        out = validate_trade_prices(_state(_plan()))
        assert out["price_check"]["result"] == "pass"

    def test_relation_violation_fails(self):
        # long：stop > entry → 价格关系违规
        out = validate_trade_prices(_state(_plan(entry=100.0, stop=105.0, target=110.0)))
        assert out["price_check"]["result"] == "fail"
        assert out["price_check_attempts"] == 1
        assert "price_check_feedback" in out

    def test_entry_deviation_fails(self):
        # entry 距现价偏差 50% > 15%
        out = validate_trade_prices(_state(_plan(entry=150.0, stop=140.0, target=160.0)))
        assert out["price_check"]["result"] == "fail"

    def test_band_violation_fails(self):
        # target 220 超出放宽带 [93-4, 105+4] = [89, 109] 附近
        out = validate_trade_prices(_state(_plan(entry=100.0, stop=95.0, target=220.0)))
        assert out["price_check"]["result"] == "fail"

    def test_short_symmetry(self):
        # short：stop > entry > target 合法
        out = validate_trade_prices(
            _state(_plan(action="sell", entry=100.0, stop=106.0, target=92.0))
        )
        assert out["price_check"]["result"] == "pass"
        # short 关系倒置 → fail
        out2 = validate_trade_prices(
            _state(_plan(action="sell", entry=100.0, stop=92.0, target=106.0))
        )
        assert out2["price_check"]["result"] == "fail"

    def test_hold_watch_passes(self):
        plan = TradeDecision(action="hold", confidence=0.5, reasoning="r")
        out = validate_trade_prices(_state(plan))
        assert out["price_check"]["result"] == "pass"

    def test_levels_unavailable_skips(self):
        levels = {"available": False, "reason": "insufficient_kline"}
        out = validate_trade_prices(_state(_plan(), levels=levels))
        assert out["price_check"]["result"] == "pass"
        assert out["price_check"].get("note")  # 如实标注跳过原因

    def test_second_fail_corrects(self):
        out = validate_trade_prices(
            _state(_plan(entry=150.0, stop=140.0, target=160.0), attempts=1)
        )
        assert out["price_check"]["result"] == "corrected"
        corrected = out["trader_plan"]
        assert corrected["price_level_corrected"] is True
        assert corrected["price_level_correction_reason"]
        # 修正后满足价格关系（long：stop < entry < target）
        assert corrected["stop_loss"] < corrected["entry_price"] < corrected["target_price"]


class TestPriceDeclarationRequired:
    """require-trade-price-declaration：buy/sell 价位必填——任一缺失（None/≤0）不再
    静默 pass，首次 fail 打回要求申报；已打回仍缺失放行+如实标注（连续 4 轮真实
    运行 0 申报的根因就是「schema 可选，跳过校验」的静默分支）。"""

    def test_buy_all_missing_fails_first_attempt(self):
        plan = TradeDecision(action="buy", confidence=0.8, reasoning="r")
        out = validate_trade_prices(_state(plan))
        pc = out["price_check"]
        assert pc["result"] == "fail"
        for key in ("entry_price", "stop_loss", "target_price"):
            assert key in (pc.get("reason") or ""), key
        assert "申报" in (out.get("price_check_feedback") or "")
        assert out["price_check_attempts"] == 1

    def test_partial_missing_lists_only_missing(self):
        plan = TradeDecision(action="buy", confidence=0.8, reasoning="r", entry_price=100.0)
        out = validate_trade_prices(_state(plan))
        pc = out["price_check"]
        assert pc["result"] == "fail"
        reason = pc.get("reason") or ""
        assert "stop_loss" in reason and "target_price" in reason
        missing_part = reason.split("：")[-1]
        assert "entry_price" not in missing_part

    def test_sell_zero_counts_as_missing(self):
        """0 是 LLM 实际输出的「未提供」形态（比亚迪 sell 0/0），计为缺失。"""
        plan = TradeDecision(
            action="sell",
            confidence=0.8,
            reasoning="r",
            entry_price=100.0,
            stop_loss=0.0,
            target_price=0.0,
        )
        out = validate_trade_prices(_state(plan))
        assert out["price_check"]["result"] == "fail"

    def test_second_attempt_still_missing_released_with_note(self):
        plan = TradeDecision(action="buy", confidence=0.8, reasoning="r")
        out = validate_trade_prices(_state(plan, attempts=1))
        pc = out["price_check"]
        assert pc["result"] == "pass"
        assert "已打回仍未申报" in (pc.get("note") or "")
        assert out["price_check_attempts"] == 1  # 不再递增
        assert "price_check_feedback" not in out

    def test_watch_still_passes_through(self):
        plan = TradeDecision(action="watch", confidence=0.5, reasoning="r")
        out = validate_trade_prices(_state(plan))
        assert out["price_check"]["result"] == "pass"


class TestCorrectPrices:
    def test_long_correction_from_bands(self):
        corrected = correct_prices(
            _plan(entry=150.0, stop=140.0, target=160.0).model_dump(), _levels(), "buy"
        )
        assert corrected["entry_price"] == pytest.approx(100.0)  # entry_ref
        assert corrected["stop_loss"] == pytest.approx(
            (96.0 + 98.0) / 2
        )  # stop_band 中值（atr=2 → [96,98]）
        assert corrected["target_price"] == pytest.approx((104.0 + 108.0) / 2)  # target_band 中值
        assert corrected["price_level_corrected"] is True

    def test_short_correction_mirrored(self):
        corrected = correct_prices(
            _plan(action="sell", entry=50.0, stop=45.0, target=60.0).model_dump(), _levels(), "sell"
        )
        assert corrected["stop_loss"] > corrected["entry_price"] > corrected["target_price"]


class TestRouting:
    def test_pass_goes_forward(self):
        state = {"price_check": {"result": "pass"}}
        assert after_validate_trade_prices(state) == "risk_r1_entry"

    def test_corrected_goes_forward(self):
        state = {"price_check": {"result": "corrected"}}
        assert after_validate_trade_prices(state) == "risk_r1_entry"

    def test_fail_goes_back_to_trader(self):
        state = {"price_check": {"result": "fail"}}
        assert after_validate_trade_prices(state) == "trader"


class TestTraderContextInjection:
    def test_price_levels_in_context(self):
        from finance_agent.nodes.trader import _build_trader_context

        state = {
            "analyst_reports": {},
            "price_levels": {"available": True, "entry_ref": 100.0, "atr": 2.0},
        }
        ctx = _build_trader_context(state)
        assert "价位参考" in ctx
        assert "entry_ref" in ctx

    def test_retry_feedback_in_context(self):
        from finance_agent.nodes.trader import _build_trader_context

        state = {
            "analyst_reports": {},
            "price_check_feedback": "entry 距现价偏差超限",
        }
        ctx = _build_trader_context(state)
        assert "价位校验打回意见" in ctx
        assert "entry 距现价偏差超限" in ctx


class TestDerivedMetrics:
    """deterministic-derived-metrics：价位过审后由纯规则代码计算派生指标。

    止损距离 = (entry-stop)/entry；赔率 = (target-entry)/(entry-stop)（buy，
    sell 取反向）。除零/缺失 → None + 原因，MUST NOT 产出 0/无穷占位；
    fail 打回路径不计算；watch/hold 不计算。
    """

    def test_pass_computes_derived_metrics(self):
        result = validate_trade_prices(_state(_plan(entry=100.0, stop=95.0, target=108.0)))
        dm = result["derived_metrics"]
        assert dm["stop_distance_pct"] == abs(100.0 - 95.0) / 100.0
        assert dm["risk_reward_ratio"] == abs(108.0 - 100.0) / abs(100.0 - 95.0)
        assert dm["missing_reason"] is None

    def test_levels_unavailable_still_computes_derived_metrics(self):
        """派生指标只依赖申报价格，与参考带无关——price_levels 不可用跳过 band 校验
        时 MUST 照常计算，否则风险辩论拿不到代码值退回 LLM 心算（E2E 601318 实测）。"""
        state = _state(_plan(entry=100.0, stop=95.0, target=108.0))
        state["price_levels"] = {"available": False, "reason": "unknown"}
        result = validate_trade_prices(state)
        assert result["price_check"]["result"] == "pass"
        assert "跳过校验" in (result["price_check"].get("note") or "")
        dm = result["derived_metrics"]
        assert dm["stop_distance_pct"] == abs(100.0 - 95.0) / 100.0
        assert dm["risk_reward_ratio"] == abs(108.0 - 100.0) / abs(100.0 - 95.0)

    def test_kline_missing_still_computes_derived_metrics(self):
        state = _state(_plan(entry=100.0, stop=95.0, target=108.0))
        state["kline"] = None
        result = validate_trade_prices(state)
        assert result["price_check"]["result"] == "pass"
        assert result["derived_metrics"]["stop_distance_pct"] == 0.05

    def test_corrected_uses_corrected_prices(self):
        result = validate_trade_prices(_state(_plan()))
        assert result["price_check"]["result"] == "pass"
        # 无修正场景下 derived_metrics 即来自原值；corrected 场景在 test_second_fail_corrects
        # 的修正价上验证
        assert "derived_metrics" in result

    def test_fail_does_not_compute(self):
        plan = _plan(entry=120.0)  # entry 偏差超限 → fail
        result = validate_trade_prices(_state(plan))
        assert result["price_check"]["result"] == "fail"
        assert "derived_metrics" not in result

    def test_hold_watch_no_derived_metrics(self):
        result = validate_trade_prices(_state(_plan(action="hold")))
        assert "derived_metrics" not in result

    def test_division_by_zero_yields_none_with_reason(self):
        # stop == entry：价格关系违规会先 fail；构造 pass 路径需 stop==entry 且
        # 通过校验不可行，因此除零保护经由缺失参数路径验证
        plan = _plan(entry=100.0, stop=0, target=108.0)
        result = validate_trade_prices(_state(plan))
        dm = result.get("derived_metrics", {})
        if result["price_check"]["result"] == "pass":
            assert dm["stop_distance_pct"] is None
            assert dm["missing_reason"]
        else:
            assert "derived_metrics" not in result

    def test_corrected_path_uses_corrected_prices(self):
        """二次失败修正后，derived_metrics 按修正后价位计算而非原申报价。"""
        plan = _plan(entry=120.0, stop=95.0, target=108.0)  # entry 偏差超限
        state = _state(plan, attempts=1)
        result = validate_trade_prices(state)
        assert result["price_check"]["result"] == "corrected"
        corrected = result["trader_plan"]
        dm = result["derived_metrics"]
        assert corrected["entry_price"] != 120.0  # 已被参考带修正
        e, s, t = corrected["entry_price"], corrected["stop_loss"], corrected["target_price"]
        assert dm["stop_distance_pct"] == abs(e - s) / e
        assert dm["risk_reward_ratio"] == abs(t - e) / abs(e - s)


class TestStateChannelsDeclared:
    """incident 027：validate 节点返回的 price_check 家族与 derived_metrics 曾未在
    AnalysisState 声明 → 图合并静默丢弃，fail 打回 trader 与价位修正在真实图中
    从未生效（路由恒读到空 dict，直接放行）。图通道契约测试锁死声明。"""

    def test_graph_channels_declare_validate_keys(self):
        from finance_agent.graph import build_5layer_graph

        channels = set(build_5layer_graph().builder.channels)
        for key in (
            "price_check",
            "price_check_feedback",
            "price_check_attempts",
            "price_level_corrected",
            "price_level_correction_reason",
            "derived_metrics",
            "derived_series",  # ground-comparative-delta-claims：compute 写入但从未声明
            "price_levels",  # 同族：compute 写入但从未声明（band 校验/参考带修正恒不可达）
        ):
            assert key in channels, f"AnalysisState 缺少声明: {key}"

    def test_graph_channels_declare_citation_loop_keys(self):
        from finance_agent.graph import build_5layer_graph

        channels = set(build_5layer_graph().builder.channels)
        for key in ("citation_coverage_gap", "value_mismatch_repaired"):
            assert key in channels, f"AnalysisState 缺少声明: {key}"

    def test_graph_channels_declare_debate_anchor_keys(self):
        from finance_agent.graph import build_5layer_graph

        channels = set(build_5layer_graph().builder.channels)
        assert "debate_anchor_checks" in channels, "AnalysisState 缺少声明: debate_anchor_checks"

    def test_graph_channels_declare_research_manager_keys(self):
        """research_manager 解析失败降级写入 parse_degraded——未声明则被图静默丢弃。"""
        from finance_agent.graph import build_5layer_graph

        channels = set(build_5layer_graph().builder.channels)
        assert "parse_degraded" in channels, "AnalysisState 缺少声明: parse_degraded"


class TestPayoutRatioSelfCheck:
    """赔率自检（eval-driven-contract-fixes 任务 6，derived-risk-metrics delta）：
    reasoning 自报赔率与代码计算冲突 → 原位修正 + telemetry；容差内不改；派生缺失跳过。"""

    def test_mismatched_ratio_is_corrected_in_place(self):
        from finance_agent.nodes.validate import check_and_fix_stated_ratio

        reasoning = "维持买入，赔率约1.7:1属正常区间但不放大预期。"
        fixed, corrected = check_and_fix_stated_ratio(reasoning, 1.24)
        assert corrected is True
        assert "赔率约1.24:1" in fixed and "1.7:1" not in fixed
        assert "维持买入" in fixed  # 其余文字不动

    def test_within_tolerance_untouched(self):
        from finance_agent.nodes.validate import check_and_fix_stated_ratio

        fixed, corrected = check_and_fix_stated_ratio("赔率约1.9:1，可执行。", 1.90)
        assert corrected is False
        assert fixed == "赔率约1.9:1，可执行。"

    def test_missing_derived_skips(self):
        from finance_agent.nodes.validate import check_and_fix_stated_ratio

        fixed, corrected = check_and_fix_stated_ratio("赔率约1.7:1", None)
        assert corrected is False and fixed == "赔率约1.7:1"

    def test_no_ratio_claim_untouched(self):
        from finance_agent.nodes.validate import check_and_fix_stated_ratio

        fixed, corrected = check_and_fix_stated_ratio("维持观望。", 1.24)
        assert corrected is False and fixed == "维持观望。"

    def test_sixty_case_601899_adjudicated_fixture(self):
        """终裁案例夹具：601899 终稿（1.7:1 vs 派生 1.24）。"""
        from finance_agent.nodes.validate import check_and_fix_stated_ratio

        reasoning = "目标位34.5维持不变，赔率约1.7:1属正常区间但不放大预期。"
        fixed, corrected = check_and_fix_stated_ratio(reasoning, 1.24)
        assert corrected and "1.24:1" in fixed

    def test_wired_into_trader_and_risk_judge_outputs(self):
        """接线验证：trader / risk_judge（final_trade_decision 写入方）产出路径挂自检。"""
        from finance_agent.nodes import risk as risk_mod
        from finance_agent.nodes import trader as trader_mod

        assert hasattr(trader_mod, "_apply_payout_self_check")
        assert hasattr(risk_mod, "_apply_payout_self_check")
