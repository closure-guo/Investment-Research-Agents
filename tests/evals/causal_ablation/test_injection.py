"""注入载荷接线测试：payload 目标必须是真实 state 结构（income_statement / kline /
news_list / macro_indicators / 决策 dict），且 apply_injection 返回可审计的 applied 记录。

v1 的通用占位键（revenue/price/close_series/macro_as_of/stop_loss...）管线从不消费，
注入到不了模型输入 —— 本文件的用例把「注入必须打在真实结构上」钉成契约。
"""

from __future__ import annotations

import copy
import json

import pandas as pd
import pytest
from evals.causal_ablation.injection import (
    COST_CLASS,
    INJECTION_POINTS,
    MECHANISM_SWITCHES,
    POLLUTION_TYPES,
    InjectionCase,
    apply_injection,
    build_injection_cases,
)

BASE = {"revenue": 1.0e9, "price": 100.0, "growth": -0.1005, "entry": 100.0}


# ── 真实形态 fixture（列名/键名取自 tests/fixtures/citation_rejudge_002412.json 与
#    finance_agent/data/akshare_client.py 的 fetch 输出）──


def _income_statement() -> pd.DataFrame:
    """利润表：报告日降序（首行 = 最新报告期）+ 营业总收入列。"""
    return pd.DataFrame(
        {
            "报告日": ["20251231", "20241231"],
            "营业总收入": [1.0e9, 9.0e8],
            "归母净利润": [1.0e8, 9.0e7],
        }
    )


def _kline() -> pd.DataFrame:
    """日 K：时间正序（旧→新），末行 = 最新交易日（与 fetch 契约一致）。"""
    return pd.DataFrame(
        {"日期": ["2026-08-01", "2026-08-04", "2026-08-05"], "收盘": [99.0, 100.0, 101.0]}
    )


def _macro_indicators() -> dict:
    """fetch_macro_indicators 的真实结构：{指标: {as_of_date, freshness, records}}。"""
    return {
        "cpi": {
            "as_of_date": "2026-07-01",
            "freshness": "fresh",
            "records": [{"月份": "2026-06", "全国-同比增长": 0.3}],
        },
        "pmi": [],  # 拉取失败的真实形态（空列表，非 dict）
    }


def _snapshot() -> dict:
    return {
        "stock_code": "600519",
        "income_statement": _income_statement(),
        "kline": _kline(),
        "news_list": [{"title": "公司公告：拟回购股份", "datetime": "2026-08-01"}],
        "macro_indicators": _macro_indicators(),
        "trader_plan": {
            "action": "buy",
            "confidence": 0.6,
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "target_price": 115.0,
        },
    }


def _case(pollution_type: str, *, seed: int = 0, idx: int = 0) -> InjectionCase:
    return build_injection_cases(
        pollution_type, ticker="600519", base_values=BASE, n=idx + 1, seed=seed
    )[idx]


def _identical(first: dict, second: dict) -> bool:
    """快照等价（DataFrame 走 .equals，dict/标量走 ==；DataFrame 的 == 是逐元素矩阵）。"""
    if set(first) != set(second):
        return False
    for key, value in first.items():
        counterpart = second[key]
        if hasattr(value, "equals"):
            if not value.equals(counterpart):
                return False
        elif value != counterpart:
            return False
    return True


def _hand_case(pollution_type: str, payload: dict) -> InjectionCase:
    from evals.causal_ablation.injection import _POLLUTION_ROUTING

    point, mechanism = _POLLUTION_ROUTING[pollution_type]
    return InjectionCase(
        case_id=f"600519-{pollution_type}-hand",
        pollution_type=pollution_type,
        injection_point=point,
        mechanism_id=mechanism,
        payload=payload,
    )


# ── 污染矩阵 ──


class TestMatrix:
    def test_all_eight_pollutions_registered(self):
        assert len(POLLUTION_TYPES) == 8
        assert set(POLLUTION_TYPES) == {
            "value_error",
            "direction_error",
            "unit_error",
            "period_shift",
            "mirror_narrative",
            "fabricated_event",
            "stale_macro",
            "illegal_price",
        }

    def test_three_injection_points(self):
        assert set(INJECTION_POINTS) == {"context", "analyst_output", "decision"}

    def test_every_pollution_has_cost_class(self):
        assert set(COST_CLASS) == set(POLLUTION_TYPES)
        assert set(COST_CLASS.values()) <= {"offline_replay", "real_run"}

    def test_verifier_side_pollutions_are_offline(self):
        for pt in (
            "direction_error",
            "unit_error",
            "period_shift",
            "fabricated_event",
            "stale_macro",
        ):
            assert COST_CLASS[pt] == "offline_replay"

    def test_input_side_pollutions_are_real_run(self):
        for pt in ("value_error", "mirror_narrative", "illegal_price"):
            assert COST_CLASS[pt] == "real_run"

    def test_every_switch_maps_to_registered_mechanism(self):
        from evals.causal_ablation.claims import DEFAULT_REGISTRY

        ids = {c.id for c in DEFAULT_REGISTRY}
        assert set(MECHANISM_SWITCHES) <= ids

    def test_switch_declares_on_and_off(self):
        for sw in MECHANISM_SWITCHES.values():
            assert sw.off_value != sw.on_value


class TestDeterminism:
    @pytest.mark.parametrize("pollution_type", POLLUTION_TYPES)
    def test_same_seed_same_payload(self, pollution_type):
        first = _case(pollution_type, seed=7)
        second = _case(pollution_type, seed=7)
        assert first.payload == second.payload

    def test_different_seed_differs(self):
        assert _case("value_error", seed=1).payload != _case("value_error", seed=2).payload

    def test_n_cases_respected(self):
        assert len(build_injection_cases("value_error", ticker="T", base_values=BASE, n=4)) == 4

    def test_unknown_pollution_rejected(self):
        with pytest.raises(ValueError, match="污染类型"):
            build_injection_cases("meltdown", ticker="T", base_values=BASE)


# ── 载荷目标（真实结构，不是占位键）──


class TestPayloadTargets:
    def test_value_error_even_instances_target_citeable_derived_series(self):
        """P1 重校：偶数实例打 derived_series（分析师被提示直接引用的派生值表，
        且在重算注册表里）——旧靶点（利润表格单元格）analyst 从不逐字引用，真跑 7/8 void。"""
        case = _case("value_error", idx=0)
        assert case.payload["op"] == "derived_series_value"
        assert case.payload["target"] == "derived_series"
        assert case.payload["key"] == "chg_5d"
        assert case.payload["field"] == "derived_series.chg_5d"
        # 旧单元格形态保留为回落（derived_series 缺失时）
        assert case.payload["fallback"]["op"] == "dataframe_cell"
        assert case.payload["fallback"]["target"] == "income_statement"
        # 旧口径（写通用 revenue 键）不得回归
        assert "set" not in case.payload

    @pytest.mark.parametrize("idx", [1, 3])
    def test_value_error_odd_instances_keep_cell_form(self, idx):
        case = _case("value_error", idx=idx)
        assert case.payload["op"] == "dataframe_cell"
        assert case.payload["target"] == "income_statement"
        assert case.payload["column"] == "营业总收入"
        assert case.payload["field"].startswith("income_statement")
        assert "set" not in case.payload

    def test_value_error_factor_is_5_to_50_pct(self):
        for idx in range(4):
            factor = float(_case("value_error", idx=idx).payload["factor"])
            assert 0.05 <= abs(factor - 1.0) <= 0.50

    def test_mirror_narrative_targets_indicator_series_with_kline_fallback(self):
        """P1 重校：打技术指标序列（分析师直读），kline 行序保留为回落。"""
        case = _case("mirror_narrative")
        assert case.payload["op"] == "reverse_series"
        assert case.payload["target"] == "technical_indicators"
        assert case.payload["path"] == ["MA", "5"]
        assert case.payload["fallback"] == {
            "op": "reverse_rows",
            "target": "kline",
            "field": "kline",
        }
        assert "close_series" not in case.payload

    def test_fabricated_event_is_claim_side_without_snapshot_op(self):
        """round-3 重校：假事件**只**注入 claim（不写 news_list）。

        同源注入等于给污染自证出处 → A6 回声匹配必然 PASS，单元不携带机制信息
        （round-2 实证 10/10 案例如此）。真实形态 = 断言的事件在输入里不存在。
        """
        case = _case("fabricated_event")
        assert case.payload["op"] == "fabricated_claim"
        assert case.payload["target"] == "analyst_reports"
        assert isinstance(case.payload["claim_text"], str)
        assert "news_list" not in case.payload
        assert "news_append" not in case.payload

    def test_stale_macro_targets_real_freshness_fields(self):
        case = _case("stale_macro")
        assert case.payload["op"] == "macro_stale"
        assert case.payload["target"] == "macro_indicators"
        assert case.payload["as_of_date"] < "2026-09-01"
        assert case.payload["freshness"] == "stale"
        assert "set" not in case.payload

    def test_illegal_price_targets_decision_dict(self):
        case = _case("illegal_price")
        assert case.payload["op"] == "decision_price"
        assert case.payload["targets"] == ["trader_plan", "final_trade_decision"]
        assert float(case.payload["long_factor"]) > 1.0
        assert float(case.payload["short_factor"]) < 1.0

    @pytest.mark.parametrize("pollution_type", ["direction_error", "unit_error", "period_shift"])
    def test_claim_side_payloads_stay_offline(self, pollution_type):
        """claims 目标（枚举值的离线注入器消费）不带快照 op：真跑腿据此判 void 而非空转。"""
        case = _case(pollution_type)
        assert "op" not in case.payload
        assert case.payload["set"]


# ── apply_injection：value_error（利润表单元格 = 奇数实例 / 回落形态）──


class TestApplyValueError:
    def test_applies_to_latest_revenue_cell(self):
        snapshot = _snapshot()
        out, effect = apply_injection(snapshot, _case("value_error", idx=1))
        assert effect.applied is True
        assert effect.reason == ""
        assert effect.target == "income_statement"
        before = float(snapshot["income_statement"]["营业总收入"].iloc[0])
        after = float(out["income_statement"]["营业总收入"].iloc[0])
        assert 0.05 <= abs(after - before) / before <= 0.50
        assert effect.polluted_values == (after,)
        assert "income_statement" in effect.field_names
        # 其他单元格原样
        assert out["income_statement"]["归母净利润"].tolist() == [1.0e8, 9.0e7]

    def test_latest_row_is_chosen_not_first_row(self):
        """行序即使不是降序，也必须打最新报告期（按 报告日 取最新）。"""
        snapshot = _snapshot()
        snapshot["income_statement"] = pd.DataFrame(
            {
                "报告日": ["20241231", "20251231"],
                "营业总收入": [9.0e8, 1.0e9],
            }
        )
        out, effect = apply_injection(snapshot, _case("value_error", idx=1))
        assert effect.applied is True
        assert out["income_statement"]["营业总收入"].tolist()[0] == pytest.approx(9.0e8)
        assert out["income_statement"]["营业总收入"].tolist()[1] == effect.polluted_values[0]

    def test_column_alias_resolved_through_vocab(self):
        snapshot = _snapshot()
        snapshot["income_statement"] = pd.DataFrame(
            {"报告日": ["20251231", "20241231"], "营业收入": [1.0e9, 9.0e8]}
        )
        out, effect = apply_injection(snapshot, _case("value_error", idx=1))
        assert effect.applied is True
        assert float(out["income_statement"]["营业收入"].iloc[0]) == effect.polluted_values[0]

    def test_integer_column_is_upcast_not_crashed(self):
        snapshot = _snapshot()
        snapshot["income_statement"] = pd.DataFrame(
            {"报告日": ["20251231", "20241231"], "营业总收入": [1_000_000_000, 900_000_000]}
        )
        out, effect = apply_injection(snapshot, _case("value_error", idx=1))
        assert effect.applied is True
        assert float(out["income_statement"]["营业总收入"].iloc[0]) == effect.polluted_values[0]

    @pytest.mark.parametrize(
        "mutate, expected_words",
        [
            (lambda s: s.pop("income_statement"), ["income_statement"]),
            (lambda s: s.update(income_statement=[1.0, 2.0]), ["DataFrame"]),
            (
                lambda s: s.update(
                    income_statement=pd.DataFrame({"营业总收入": [1.0e9]}),
                ),
                ["报告日"],
            ),
            (
                lambda s: s.update(income_statement=pd.DataFrame({"报告日": ["20251231"]})),
                ["营业总收入", "营业收入"],
            ),
            (
                lambda s: s.update(
                    income_statement=pd.DataFrame({"报告日": ["20251231"], "营业总收入": ["N/A"]})
                ),
                ["数值"],
            ),
        ],
    )
    def test_missing_structure_records_applied_false(self, mutate, expected_words):
        snapshot = _snapshot()
        mutate(snapshot)
        out, effect = apply_injection(snapshot, _case("value_error", idx=1))
        assert effect.applied is False
        assert any(word in effect.reason for word in expected_words), effect.reason
        assert "income_statement" in effect.target or effect.target == ""
        assert set(out) == set(snapshot)  # 不改快照

    def test_hand_case_without_op_is_not_applied(self):
        """占位键载荷（v1 形态）不得再被静默写入快照。"""
        case = _hand_case("value_error", {"set": {"revenue": 1.13e9}, "field": "revenue"})
        snapshot = _snapshot()
        out, effect = apply_injection(snapshot, case)
        assert effect.applied is False
        assert "op" in effect.reason or "载荷" in effect.reason
        assert _identical(out, snapshot)


# ── apply_injection：value_error 真靶点（derived_series，偶数实例）──


class TestApplyDerivedSeriesValue:
    """derived_series 是分析师被提示直接引用的字段，且在 citation._COMPUTATIONAL_RECALC
    重算注册表里（A3 重算可抓）；缺该结构时回落利润表格旧形态。"""

    def test_mutates_named_derived_value_only(self):
        snapshot = _snapshot()
        snapshot["derived_series"] = {"chg_5d": 0.02, "chg_20d": 0.05}
        case = _case("value_error", idx=0)
        out, effect = apply_injection(snapshot, case)
        assert effect.applied is True
        assert effect.target == "derived_series.chg_5d"
        assert out["derived_series"]["chg_5d"] == pytest.approx(
            0.02 * float(case.payload["factor"])
        )
        assert out["derived_series"]["chg_20d"] == 0.05  # 其他派生项原样
        assert effect.polluted_values == (pytest.approx(out["derived_series"]["chg_5d"]),)
        assert effect.field_names == ("derived_series.chg_5d", "derived_series")
        assert snapshot["derived_series"]["chg_5d"] == 0.02  # 入参不被修改

    def test_falls_back_to_income_statement_cell_when_absent(self):
        snapshot = _snapshot()  # 无 derived_series（真实产物缺该键的形态）
        case = _case("value_error", idx=0)
        out, effect = apply_injection(snapshot, case)
        assert effect.applied is True
        assert effect.target == "income_statement"
        assert float(out["income_statement"]["营业总收入"].iloc[0]) == effect.polluted_values[0]
        assert "回落" in effect.reason

    def test_falls_back_when_named_derived_value_is_not_numeric(self):
        snapshot = _snapshot()
        snapshot["derived_series"] = {"chg_5d": None}  # 数据不足的真实形态
        out, effect = apply_injection(snapshot, _case("value_error", idx=0))
        assert effect.applied is True
        assert effect.target == "income_statement"
        assert "回落" in effect.reason

    def test_both_targets_absent_records_applied_false(self):
        snapshot = _snapshot()
        snapshot.pop("income_statement")
        out, effect = apply_injection(snapshot, _case("value_error", idx=0))
        assert effect.applied is False
        assert "derived_series" in effect.reason and "income_statement" in effect.reason
        assert _identical(out, snapshot)


# ── apply_injection：mirror_narrative（技术指标序列倒序；kline 回落）──


class TestApplyMirrorNarrative:
    def test_reverses_technical_ma_series_when_present(self):
        """真靶点：分析师直读 technical_indicators（技术面 context 的唯一序列来源）。"""
        snapshot = _snapshot()
        snapshot["technical_indicators"] = {
            "MA": {"5": [None, None, 10.0, 11.0, 12.0], "10": [1.0, 2.0]},
            "RSI": {"14": [30.0, 40.0]},
        }
        out, effect = apply_injection(snapshot, _case("mirror_narrative"))
        assert effect.applied is True
        assert effect.target == "technical_indicators.MA.5"
        assert out["technical_indicators"]["MA"]["5"] == [12.0, 11.0, 10.0, None, None]
        # 倒序后末位 = 原首值；均线窗口前导 None 跳过 → 正序读者会把最旧的可用均线当最新
        assert effect.polluted_values == (10.0,)
        assert effect.field_names == ("technical_indicators.MA.5", "technical_indicators")
        # 其他序列与 kline 原样（注入只打一个序列）
        assert out["technical_indicators"]["MA"]["10"] == [1.0, 2.0]
        assert out["technical_indicators"]["RSI"]["14"] == [30.0, 40.0]
        assert out["kline"]["收盘"].tolist() == [99.0, 100.0, 101.0]
        assert snapshot["technical_indicators"]["MA"]["5"] == [None, None, 10.0, 11.0, 12.0]

    def test_falls_back_to_kline_rows_when_indicators_absent(self):
        snapshot = _snapshot()  # 无 technical_indicators：回落旧靶点
        out, effect = apply_injection(snapshot, _case("mirror_narrative"))
        assert effect.applied is True
        assert effect.target == "kline"
        assert out["kline"]["收盘"].tolist() == [101.0, 100.0, 99.0]
        assert out["kline"]["日期"].tolist() == ["2026-08-05", "2026-08-04", "2026-08-01"]
        # 镜像后「末行 = 原首行」：LLM 按正序读会把最旧收盘当最新
        assert effect.polluted_values == (99.0,)
        assert effect.field_names == ("kline",)
        assert "回落" in effect.reason

    def test_missing_both_targets_records_applied_false(self):
        snapshot = _snapshot()
        snapshot.pop("kline")
        out, effect = apply_injection(snapshot, _case("mirror_narrative"))
        assert effect.applied is False
        assert "technical_indicators" in effect.reason and "kline" in effect.reason
        assert set(out) == set(snapshot)


# ── apply_injection：fabricated_event（news_list 追加）──


class TestApplyDictPathValue:
    """value_error 真靶点（round-3 二阶段）：claim 实际引用且可重算的嵌套字段。"""

    def test_pollutes_nested_metric_leaf(self):
        snapshot = {**_snapshot(), "profitability_metrics": {"ROE": {"2025": 32.5}}}
        case = InjectionCase(
            "t-value_error-0",
            "value_error",
            "context",
            "A3",
            {
                "op": "dict_path_value",
                "target": "profitability_metrics",
                "path": ["ROE", "2025"],
                "factor": 0.5,
                "field": "profitability_metrics.ROE.2025",
            },
        )
        out, effect = apply_injection(snapshot, case)
        assert effect.applied is True
        assert effect.target == "profitability_metrics.ROE.2025"
        assert out["profitability_metrics"]["ROE"]["2025"] == pytest.approx(16.25)
        assert snapshot["profitability_metrics"]["ROE"]["2025"] == 32.5  # 不改入参

    def test_missing_leaf_falls_back_with_auditable_reason(self):
        snapshot = _snapshot()
        case = InjectionCase(
            "t-value_error-0",
            "value_error",
            "context",
            "A3",
            {
                "op": "dict_path_value",
                "target": "profitability_metrics",
                "path": ["ROE", "2025"],
                "factor": 0.5,
                "field": "profitability_metrics.ROE.2025",
                "fallback": {
                    "op": "dataframe_cell",
                    "target": "income_statement",
                    "column": "营业总收入",
                    "row": "latest",
                    "factor": 0.5,
                },
            },
        )
        out, effect = apply_injection(snapshot, case)
        assert effect.applied is True
        assert "回落" in effect.reason
        assert float(out["income_statement"]["营业总收入"].iloc[0]) == pytest.approx(5.0e8)


class TestApplyFabricatedEvent:
    def test_claim_side_payload_is_not_a_snapshot_op(self):
        """载荷是分析师输出侧（离线腿消费）：apply_injection 不得静默写占位键。"""
        snapshot = _snapshot()
        out, effect = apply_injection(snapshot, _case("fabricated_event"))
        assert effect.applied is False
        assert "未登记快照操作" in effect.reason
        assert set(out) == set(snapshot)


# ── apply_injection：stale_macro（时效标记）──


class TestApplyStaleMacro:
    def test_marks_each_macro_item_stale(self):
        snapshot = _snapshot()
        out, effect = apply_injection(snapshot, _case("stale_macro"))
        assert effect.applied is True
        case = _case("stale_macro")
        marked = out["macro_indicators"]["cpi"]
        assert marked["as_of_date"] == case.payload["as_of_date"]
        assert marked["freshness"] == "stale"
        # 数据本体与空指标项（拉取失败形态）原样
        assert marked["records"] == snapshot["macro_indicators"]["cpi"]["records"]
        assert out["macro_indicators"]["pmi"] == []
        assert effect.polluted_values == (case.payload["as_of_date"],)
        assert "macro_indicators.as_of_date" in effect.field_names

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda s: s.pop("macro_indicators"),
            lambda s: s.update(macro_indicators={"cpi": []}),
            lambda s: s.update(macro_indicators={}),
        ],
    )
    def test_missing_macro_items_record_applied_false(self, mutate):
        snapshot = _snapshot()
        mutate(snapshot)
        out, effect = apply_injection(snapshot, _case("stale_macro"))
        assert effect.applied is False
        assert "macro_indicators" in effect.reason
        assert _identical(out, snapshot)


# ── apply_injection：illegal_price（决策价位）──


class TestApplyIllegalPrice:
    def test_long_plan_gets_stop_above_entry(self):
        snapshot = _snapshot()
        out, effect = apply_injection(snapshot, _case("illegal_price"))
        assert effect.applied is True
        plan = out["trader_plan"]
        assert plan["action"] == "buy"
        assert plan["stop_loss"] > plan["entry_price"]
        assert effect.polluted_values == (plan["stop_loss"],)
        assert "stop_loss" in effect.field_names
        # 其余字段原样保留（只破价位关系）
        assert {k: v for k, v in plan.items() if k != "stop_loss"} == {
            k: v for k, v in snapshot["trader_plan"].items() if k != "stop_loss"
        }

    def test_short_plan_gets_stop_below_entry(self):
        snapshot = _snapshot()
        snapshot["trader_plan"] = {"action": "sell", "entry_price": 100.0, "stop_loss": 105.0}
        out, effect = apply_injection(snapshot, _case("illegal_price"))
        assert effect.applied is True
        assert out["trader_plan"]["stop_loss"] < out["trader_plan"]["entry_price"]

    def test_falls_back_to_final_trade_decision(self):
        snapshot = _snapshot()
        snapshot.pop("trader_plan")
        snapshot["final_trade_decision"] = {
            "action": "buy",
            "entry_price": 100.0,
            "stop_loss": 95.0,
        }
        out, effect = apply_injection(snapshot, _case("illegal_price"))
        assert effect.applied is True
        assert effect.target == "final_trade_decision"
        assert out["final_trade_decision"]["stop_loss"] > 100.0

    @pytest.mark.parametrize(
        "mutate, expected_words",
        [
            (lambda s: (s.pop("trader_plan"),), ["trader_plan"]),
            (lambda s: s.update(trader_plan={"action": "hold", "entry_price": 100.0}), ["hold"]),
            (lambda s: s.update(trader_plan={"action": "buy"}), ["entry_price"]),
            (lambda s: s.update(trader_plan="buy 100"), ["dict"]),
        ],
    )
    def test_unapplicable_decision_records_applied_false(self, mutate, expected_words):
        snapshot = _snapshot()
        mutate(snapshot)
        out, effect = apply_injection(snapshot, _case("illegal_price"))
        assert effect.applied is False
        assert any(word in effect.reason for word in expected_words), effect.reason
        assert _identical(out, snapshot)


# ── 深拷贝与确定性 ──


class TestApplyIsolation:
    def test_apply_does_not_mutate_input(self):
        snapshot = _snapshot()
        baseline = copy.deepcopy(snapshot)
        apply_injection(snapshot, _case("value_error"))
        assert snapshot["income_statement"].equals(baseline["income_statement"])
        assert snapshot["kline"].equals(baseline["kline"])
        assert snapshot["macro_indicators"] == baseline["macro_indicators"]
        assert snapshot["news_list"] == baseline["news_list"]
        assert snapshot["trader_plan"] == baseline["trader_plan"]

    @pytest.mark.parametrize("pollution_type", POLLUTION_TYPES)
    def test_same_case_is_deterministic(self, pollution_type):
        first, first_effect = apply_injection(_snapshot(), _case(pollution_type))
        second, second_effect = apply_injection(_snapshot(), _case(pollution_type))
        assert first_effect == second_effect
        assert set(first) == set(second)
        for key, value in first.items():
            counterpart = second[key]
            if hasattr(value, "equals"):
                assert value.equals(counterpart), key
            else:
                assert value == counterpart, key

    def test_effect_is_json_friendly(self):
        _, effect = apply_injection(_snapshot(), _case("value_error"))
        payload = json.loads(json.dumps(effect.as_dict(), ensure_ascii=False))
        assert payload["applied"] is True
        assert payload["polluted_values"]
