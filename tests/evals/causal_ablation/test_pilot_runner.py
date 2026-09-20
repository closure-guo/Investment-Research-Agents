"""P1 注入 pilot 跑批器测试（零 LLM）：注入确定性、机制开关隔离、离线重放、真跑接口、校准接线。

所有断言都打到行为（注入前后的差异 / 开关两态判定 / 报告字段），不 mock 被测系统：
引用校验链走真实 `finance_agent.citation`，真跑腿用可控的 fake graph_runner。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from evals.causal_ablation.escape import EscapePair
from evals.causal_ablation.injection import POLLUTION_TYPES, InjectionCase
from evals.causal_ablation.preregister import MissingPreregistrationError
from evals.causal_ablation.units import UnitJudgment

from evals.causal_ablation import pilot_runner as pr

# ── fixtures ──


def _analyst_reports() -> dict:
    """合成分析师产物：4 条 claim 覆盖 4 类离线注入的目标形态。"""
    return {
        "fundamental": {
            "claims": [
                {
                    "claim_type": "numerical",
                    "source_type": "data",
                    "field_ref": "income_statement.20251231.营业总收入",
                    "stated_value": 1.0e9,
                    "interpretation": "营业总收入 10.00亿元",
                    "direction": "positive",
                },
                {
                    "claim_type": "numerical",
                    "source_type": "data",
                    "field_ref": "income_statement.20251231.营业总收入",
                    "stated_value": 1.0e9,
                    "interpretation": "营业总收入 10.00亿元",
                    "direction": "positive",
                },
            ],
            "markdown": "# 基本面\n营业总收入 10.00亿元。",
        },
        "technical": {
            "claims": [
                {
                    "claim_type": "numerical",
                    "source_type": "data",
                    "field_ref": "kline.20260804.收盘",
                    "stated_value": 100.0,
                    "interpretation": "收盘 100.0 元",
                    "period": "20260804",
                    "direction": "positive",
                },
                {
                    "claim_type": "numerical",
                    "source_type": "data",
                    "field_ref": "growth_rates.fundamental.revenue",
                    "stated_value": 0.1005,
                    "interpretation": "营业总收入同比下滑 10.05%",
                    "direction": "negative",
                },
            ],
            "markdown": "# 技术面\n收盘 100.0 元，营业总收入同比下滑 10.05%。",
        },
    }


def _snapshot() -> dict:
    """可重放快照子集：键与列名同真实 fetch 输出（income_statement/kline/news_list/
    macro_indicators + 决策 dict）；revenue/price/growth/entry 是产物自带的注入基值。"""
    return {
        "stock_code": "600519",
        "revenue": 1.0e9,
        "growth": -0.1005,
        "price": 100.0,
        "entry": 100.0,
        "growth_rates": {"fundamental": {"revenue": -0.1005}},
        "income_statement": pd.DataFrame(
            {"报告日": ["20251231", "20241231"], "营业总收入": [1.0e9, 9.0e8]}
        ),
        "macro_indicators": {
            "cpi": {
                "records": [{"月份": "2026-07", "全国-同比增长": 0.3}],
                "as_of_date": "2026-08-01",
                "freshness": "fresh",
            }
        },
        "news_list": [{"title": "公司公告：拟回购股份", "date": "2026-08-01"}],
        "kline": pd.DataFrame({"日期": ["2026-08-01", "2026-08-04"], "收盘": [99.0, 100.0]}),
        "trader_plan": {
            "action": "buy",
            "confidence": 0.6,
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "target_price": 115.0,
        },
    }


def _period_reports(
    *, field_ref: str = "income_statement.20251231.营业总收入", stated: float = 1.0e9
):
    """期次可解析的 claim 产物（period_shift 双形态：field_ref 期次段 20251231）。"""
    return {
        "fundamental": {
            "claims": [
                {
                    "claim_type": "numerical",
                    "source_type": "data",
                    "field_ref": field_ref,
                    "stated_value": stated,
                    "interpretation": "营业总收入 10.00亿元",
                    "period": "20251231",
                    "direction": "flat",
                }
            ],
            "markdown": "# 基本面\n营业总收入 10.00亿元。",
        }
    }


def _make_product(*, reports: dict | None = None, snapshot: dict | None = None) -> dict:
    snap = snapshot if snapshot is not None else _snapshot()
    return {
        "ticker": "600519",
        "snapshot_digest": "deadbeef",
        "snapshot": snap,
        "analyst_reports": reports if reports is not None else _analyst_reports(),
        "citation": {"pass": True},
        "base_values": {"revenue": 1.0e9, "price": 100.0, "growth": -0.1005, "entry": 100.0},
        "paths": {"json": "reports/ablation/p1/materials/600519.json", "pickle": "x.pkl"},
    }


def _reports_without_claim_targets() -> dict:
    """共享夹具剔除「根在重算注册表」的 claim，供默认靶点/回落路径测试使用。

    #123 注册表扩容后 growth_rates 成为合法 value_error 靶点（claim picked 优先于
    默认派生值形态），共享夹具的 growth claim 会抢占靶点；过滤条件与
    `claim_value_targets` 同源，对后续注册表扩容稳健。
    """
    from finance_agent import citation as citation_mod

    reports = _analyst_reports()
    for agent in reports:
        claims = reports[agent].get("claims") or []
        reports[agent]["claims"] = [
            claim
            for claim in claims
            if str(claim.get("field_ref") or "").split(".")[0]
            not in citation_mod._COMPUTATIONAL_RECALC
        ]
    return reports


def _case(pollution_type: str, *, payload: dict | None = None) -> InjectionCase:
    from evals.causal_ablation.injection import _POLLUTION_ROUTING

    point, mechanism = _POLLUTION_ROUTING[pollution_type]
    payloads = {
        "unit_error": {"set": {"revenue": 1.0e17}, "field": "revenue"},
        "period_shift": {
            "set": {"period_label": "2026Q3", "period_value": 0.0},
            "field": "period_label",
        },
        "fabricated_event": {
            "op": "append_news",
            "target": "news_list",
            "news_append": ["某公司宣布重大战略合作（注入）"],
            "field": "news_list",
        },
        "stale_macro": {
            "op": "macro_stale",
            "target": "macro_indicators",
            "as_of_date": "2026-05-01",
            "freshness": "stale",
            "field": "macro_indicators.as_of_date",
        },
        "illegal_price": {
            "op": "decision_price",
            "targets": ["trader_plan", "final_trade_decision"],
            "long_factor": 1.1,
            "short_factor": 0.9,
            "field": "trader_plan.stop_loss",
        },
        # factor 固定为 1.13：1.0e9 → 1.13e9（假 runner 的产物文本按 11.30亿元 断言）
        "value_error": {
            "op": "dataframe_cell",
            "target": "income_statement",
            "column": "营业总收入",
            "row": "latest",
            "factor": 1.13,
            "field": "income_statement.营业总收入",
        },
        "mirror_narrative": {"op": "reverse_rows", "target": "kline", "field": "kline"},
    }
    return InjectionCase(
        case_id=f"600519-{pollution_type}-0",
        pollution_type=pollution_type,
        injection_point=point,
        mechanism_id=mechanism,
        payload=payload if payload is not None else payloads.get(pollution_type, {}),
    )


def _stable(polluted: dict) -> dict:
    """去掉 DataFrame（相等性二义）后的可比较结构，用于确定性断言。"""
    out = {k: v for k, v in polluted.items() if k != "snapshot"}
    out["snapshot"] = {
        k: v for k, v in (polluted.get("snapshot") or {}).items() if not hasattr(v, "equals")
    }
    return out


def _replacement_of(mechanism_id: str) -> object:
    """机制在校验面的 OFF 替换体（供身份断言：补丁落地/还原）。"""
    return pr._PATCHES[(mechanism_id, "verification")][0].replacement


# ── 产物 I/O ──


class TestProductIO:
    def test_roundtrip_preserves_dataframe_and_records_paths(self, tmp_path: Path):
        path = tmp_path / "materials" / "600519.json"
        paths = pr.save_product(path, _make_product())
        assert Path(paths["json"]) == path
        assert Path(paths["pickle"]).exists()

        loaded = pr.load_product(path)
        assert loaded["ticker"] == "600519"
        assert isinstance(loaded["snapshot"]["kline"], pd.DataFrame)
        assert list(loaded["snapshot"]["kline"]["收盘"]) == [99.0, 100.0]
        # 两条路径都随产物落盘（离线重放须记录重放来源产物路径）
        assert Path(loaded["paths"]["json"]) == path
        assert Path(loaded["paths"]["pickle"]) == Path(paths["pickle"])
        summary = json.loads(path.read_text(encoding="utf-8"))
        assert summary["paths"]["pickle"] == paths["pickle"]
        assert summary["snapshot_digest"] == "deadbeef"

    def test_load_product_from_pickle_path(self, tmp_path: Path):
        path = tmp_path / "600519.json"
        paths = pr.save_product(path, _make_product())
        loaded = pr.load_product(Path(paths["pickle"]))
        assert loaded["analyst_reports"]["technical"]["claims"][0]["stated_value"] == 100.0

    def test_missing_product_names_the_materials_step(self, tmp_path: Path):
        with pytest.raises(pr.MissingProductError) as err:
            pr.load_products(tmp_path, ["600519", "000001"])
        message = str(err.value)
        assert "600519" in message and "000001" in message
        assert "materials" in message

    def test_base_values_missing_field_is_explicit(self):
        product = _make_product()
        del product["base_values"]["growth"]
        product["snapshot"].pop("growth")
        with pytest.raises(pr.InjectionError) as err:
            pr.base_values_from_product(product)
        assert "growth" in str(err.value)


# ── 离线注入 ──


class TestOfflineInjection:
    def test_direction_error_flips_direction_and_is_deterministic(self):
        product = _make_product()
        first = pr.inject_offline(product, _case("direction_error"))
        second = pr.inject_offline(product, _case("direction_error"))
        claims = first["analyst_reports"]["technical"]["claims"]
        # 目标 = 首个「符号型 + 已申报方向」claim（growth_rates 根键）
        assert first["injection"]["mutated_flat_index"] == 3
        assert first["injection"]["mutated_agent"] == "technical"
        assert claims[1]["direction"] == "positive"
        assert claims[1]["stated_value"] == 0.1005
        assert _stable(first) == _stable(second)
        # 非变异：原产物不被修改
        assert product["analyst_reports"]["technical"]["claims"][1]["direction"] == "negative"

    def test_unit_error_multiplies_stated_value(self):
        polluted = pr.inject_offline(_make_product(), _case("unit_error"))
        claims = polluted["analyst_reports"]["fundamental"]["claims"]
        assert claims[0]["stated_value"] == pytest.approx(1.0e9 * 1.0e8)
        assert claims[1]["stated_value"] == pytest.approx(1.0e9)  # 只动一条
        assert polluted["injection"]["mutated_flat_index"] == 0

    def test_period_shift_rewrites_period_only(self):
        polluted = pr.inject_offline(_make_product(), _case("period_shift"))
        claim = polluted["analyst_reports"]["technical"]["claims"][0]
        assert claim["period"] == "2026Q3"
        assert claim["stated_value"] == 100.0
        assert polluted["injection"]["mutated_flat_index"] == 2

    def test_fabricated_event_appends_claim_only(self):
        """round-3 重校：只注入 claim（编造事件），**不**写 news_list——出处不得由注入自造。"""
        product = _make_product()
        polluted = pr.inject_offline(product, _case("fabricated_event"))
        news = [n["title"] for n in polluted["snapshot"]["news_list"]]
        assert "某公司宣布重大战略合作（注入）" not in news
        appended = polluted["analyst_reports"]["technical"]["claims"][-1]
        assert appended["source_type"] == "event"
        assert "某公司宣布重大战略合作（注入）" in appended["field_ref"]
        assert polluted["injection"]["mutated_fields"] == ["technical.claims[2]"]
        # 原产物与原始快照均未被写入
        assert len(product["snapshot"]["news_list"]) == 1
        assert len(product["analyst_reports"]["technical"]["claims"]) == 2

    def test_stale_macro_marks_real_freshness_structure_only(self):
        """时效污染打在 macro_indicators 各项的 as_of_date/freshness 上（真实守卫结构）——
        不再写管线从不消费的顶层 macro_as_of 占位键。"""
        product = _make_product()
        polluted = pr.inject_offline(product, _case("stale_macro"))
        cpi = polluted["snapshot"]["macro_indicators"]["cpi"]
        assert cpi["as_of_date"] == "2026-05-01"
        assert cpi["freshness"] == "stale"
        assert "macro_as_of" not in polluted["snapshot"]
        assert product["snapshot"]["macro_indicators"]["cpi"]["as_of_date"] == "2026-08-01"
        assert polluted["injection"]["mutated_flat_index"] is None
        assert polluted["injection"]["mutated_fields"] == ["macro_indicators.cpi"]

    def test_real_run_types_are_rejected_offline(self):
        for pollution_type in ("value_error", "mirror_narrative", "illegal_price"):
            with pytest.raises(pr.InjectionError) as err:
                pr.inject_offline(_make_product(), _case(pollution_type))
            assert pollution_type in str(err.value)

    def test_missing_target_claim_is_explicit(self):
        reports = _analyst_reports()
        # 去掉符号型 claim：direction_error 无目标 → 显式报错（不得静默跳过）
        reports["technical"]["claims"] = [reports["technical"]["claims"][0]]
        with pytest.raises(pr.InjectionError) as err:
            pr.inject_offline(_make_product(reports=reports), _case("direction_error"))
        assert "direction_error" in str(err.value)


# ── period_shift 双形态（P1 重校轮）──


class TestClaimValueTargets:
    """value_error 真跑靶点（round-3 二阶段）：产物 claim 实际引用 + 校验器可重算的字段。"""

    def _reports(self):
        return {
            "fundamental": {
                "claims": [
                    {
                        "claim_type": "computational",
                        "source_type": "data",
                        "field_ref": "profitability_metrics.ROE.2025",
                        "stated_value": 32.53,
                    },
                    {
                        "claim_type": "computational",
                        "source_type": "data",
                        "field_ref": "profitability_metrics.ROE.2025",
                        "stated_value": 32.53,
                    },
                    {
                        "claim_type": "numerical",
                        "source_type": "data",
                        "field_ref": "income_statement.20251231.营业总收入",
                        "stated_value": 1.0e9,
                    },
                ],
                "markdown": "",
            }
        }

    def _snapshot(self):
        return {**_snapshot(), "profitability_metrics": {"ROE": {"2025": 32.53, "2024": 36.02}}}

    def test_picks_cited_recomputable_fields_by_citation_count(self):
        product = _make_product(reports=self._reports(), snapshot=self._snapshot())
        targets = pr.claim_value_targets(product)
        assert targets[0]["field_ref"] == "profitability_metrics.ROE.2025"
        assert targets[0]["citations"] == 2
        assert targets[0]["root"] == "profitability_metrics"
        assert targets[0]["path"] == ["ROE", "2025"]
        # income_statement 不在重算注册表 → 不作为 value_error 靶点（A3 无从抓偏差）
        assert all(t["root"] != "income_statement" for t in targets)

    def test_unresolvable_cited_field_is_skipped(self):
        reports = self._reports()
        reports["fundamental"]["claims"].append(
            {
                "claim_type": "computational",
                "source_type": "data",
                "field_ref": "profitability_metrics.不存在.2025",
                "stated_value": 1.0,
            }
        )
        targets = pr.claim_value_targets(_make_product(reports=reports, snapshot=self._snapshot()))
        assert all(t["field_ref"] != "profitability_metrics.不存在.2025" for t in targets)

    def test_value_error_payload_uses_claim_target(self):
        product = _make_product(reports=self._reports(), snapshot=self._snapshot())
        cases = pr.build_pilot_cases(product, instances=2, pollution_types=["value_error"])
        assert cases[0].payload["op"] == "dict_path_value"
        assert cases[0].payload["field"] == "profitability_metrics.ROE.2025"
        assert cases[0].payload["target"] == "profitability_metrics"


class TestEffectiveInjectionTargets:
    """round-3 重校：注入必须**真产生**可检偏差——空操作/等价换算的单元不携带机制信息。

    判据走校验器单一实现（`pr._injection_effective`）：原版非 FAIL 且污染版 FAIL。
    """

    def test_direction_error_skips_inert_flip_targets(self):
        snapshot = {
            **_snapshot(),
            "growth_rates": {"fundamental": {"revenue": -0.1005, "profit": 0.25}},
        }
        reports = {
            "fundamental": {
                "claims": [
                    {  # 惰性：stated 自带负号 → 翻转后 eff 不变（对齐规则不改号）
                        "claim_type": "numerical",
                        "source_type": "data",
                        "field_ref": "growth_rates.fundamental.revenue",
                        "stated_value": -0.1005,
                        "direction": "negative",
                        "interpretation": "营业总收入同比下滑 10.05%",
                    },
                    {  # 有效：stated 为正 → 翻成 negative 后 eff 变号 → A3 可拦
                        "claim_type": "numerical",
                        "source_type": "data",
                        "field_ref": "growth_rates.fundamental.profit",
                        "stated_value": 0.25,
                        "direction": "positive",
                        "interpretation": "归母净利润同比增长 25%",
                    },
                ],
                "markdown": "",
            }
        }
        polluted = pr.inject_offline(
            _make_product(reports=reports, snapshot=snapshot), _case("direction_error")
        )
        assert polluted["injection"]["mutated_fields"] == ["fundamental.claims[1].direction"]
        assert polluted["injection"]["injected_values"]["direction"] == "negative"

    def test_direction_error_records_real_polluted_marker(self):
        """报告列 `polluted_values` 必须是**真实污染值**：方向型 = 翻转后的方向词。

        round-3 实测缺陷：方向型未写标记 → 回落到载荷 `set` 的申报对照值（从未被施加），
        人工终裁按它去产物里找污染必然找不到。
        """
        polluted = pr.inject_offline(_make_product(), _case("direction_error"))
        injection = polluted["injection"]
        claim = polluted["analyst_reports"][injection["mutated_agent"]]["claims"][
            injection["mutated_claim_index"]
        ]
        assert injection["polluted_values"] == [claim["direction"]]
        assert injection["polluted_values"] != list(
            (_case("direction_error").payload.get("set") or {}).values()
        )
        assert injection["injected_values"]["field_ref"] == claim["field_ref"]

    def test_direction_error_without_effective_target_raises(self):
        snapshot = {
            **_snapshot(),
            "growth_rates": {"fundamental": {"revenue": -0.1005}},
        }
        reports = {
            "fundamental": {
                "claims": [
                    {
                        "claim_type": "numerical",
                        "source_type": "data",
                        "field_ref": "growth_rates.fundamental.revenue",
                        "stated_value": -0.1005,
                        "direction": "negative",
                        "interpretation": "营业总收入同比下滑 10.05%",
                    }
                ],
                "markdown": "",
            }
        }
        with pytest.raises(pr.InjectionError, match="无可选目标 claim"):
            pr.inject_offline(
                _make_product(reports=reports, snapshot=snapshot), _case("direction_error")
            )

    def test_unit_error_skips_scales_absorbed_by_normalization(self):
        """被归一化吸收的量级必须跳过：×1e8（恰等于真值，亿元→元）、×1e4（「万」换算）、
        ×1e6（比值 100 落入 percent 带）——所选污染值须**确实**让校验器判 FAIL。"""
        claim = {
            "claim_type": "numerical",
            "source_type": "data",
            "field_ref": "income_statement.20251231.营业总收入",
            "stated_value": 10.0,
            "direction": "positive",
            "interpretation": "营业总收入 10.00亿元",
        }
        reports = {"fundamental": {"claims": [dict(claim)], "markdown": ""}}
        polluted = pr.inject_offline(_make_product(reports=reports), _case("unit_error"))
        polluted_claim = polluted["analyst_reports"]["fundamental"]["claims"][0]
        # ×1e8 → 1.0e9：恰好等于真值（合法换算），不构成错误 → 不得被选中
        assert polluted_claim["stated_value"] != pytest.approx(1.0e9)
        assert pr._injection_effective(claim, polluted_claim, _snapshot()) is True


class TestPeriodShiftDualForm:
    """round-1：period_shift 只改 period 标签 → 仅 A2 期次检查能拦，不一致比 1.000（too_hard）。

    重校：按实例序号交替两种形态——form A 只改标签（现状）；form B 同时把 field_ref 期次段
    改到「真值不同的相邻真实期次」→ A2 关态下值级校验也能拦（两态都 caught）。
    确定性按实例序号（无 RNG）：偶数 → label_only，奇数 → label_and_ref。
    """

    def test_forms_alternate_by_instance_index(self):
        product = _make_product(reports=_period_reports())
        cases = pr.build_pilot_cases(product, instances=4, pollution_types=["period_shift"])
        forms = [pr.inject_offline(product, c)["injection"]["shift_form"] for c in cases]
        assert forms == ["label_only", "label_and_ref", "label_only", "label_and_ref"]

    def test_form_b_shifts_ref_to_adjacent_period_with_different_truth(self):
        product = _make_product(reports=_period_reports())
        cases = pr.build_pilot_cases(product, instances=2, pollution_types=["period_shift"])
        polluted = pr.inject_offline(product, cases[1])
        claim = polluted["analyst_reports"]["fundamental"]["claims"][0]
        assert claim["period"] == "2026Q3"
        assert claim["field_ref"] == "income_statement.20241231.营业总收入"
        assert claim["stated_value"] == 1.0e9  # 申报值不动：真值已换期（9.0e8）
        injected = polluted["injection"]["injected_values"]
        assert injected["period_segment"] == {"from": "20251231", "to": "20241231"}
        assert "field_ref" in polluted["injection"]["mutated_fields"][1]

    def test_form_a_rewrites_label_only(self):
        product = _make_product(reports=_period_reports())
        polluted = pr.inject_offline(
            product, pr.build_pilot_cases(product, instances=1, pollution_types=["period_shift"])[0]
        )
        claim = polluted["analyst_reports"]["fundamental"]["claims"][0]
        assert claim["period"] == "2026Q3"
        assert claim["field_ref"] == "income_statement.20251231.营业总收入"
        assert "field_ref" not in polluted["injection"]["injected_values"]

    def test_form_b_falls_back_when_adjacent_period_unresolvable(self):
        reports = _period_reports(field_ref="income_statement.20201231.营业总收入")
        product = _make_product(reports=reports)
        cases = pr.build_pilot_cases(product, instances=2, pollution_types=["period_shift"])
        polluted = pr.inject_offline(product, cases[1])
        claim = polluted["analyst_reports"]["fundamental"]["claims"][0]
        assert polluted["injection"]["shift_form"] == "label_only_fallback"
        assert claim["period"] == "2026Q3"
        assert claim["field_ref"] == "income_statement.20201231.营业总收入"

    def test_form_b_falls_back_when_adjacent_value_matches_stated(self):
        # 申报值 = 相邻期次真值（9.0e8）→ 换期后值级校验抓不到 → 如实回落，不假造 form B
        reports = _period_reports(stated=9.0e8)
        product = _make_product(reports=reports)
        cases = pr.build_pilot_cases(product, instances=2, pollution_types=["period_shift"])
        polluted = pr.inject_offline(product, cases[1])
        assert polluted["injection"]["shift_form"] == "label_only_fallback"
        assert (
            polluted["analyst_reports"]["fundamental"]["claims"][0]["field_ref"]
            == "income_statement.20251231.营业总收入"
        )

    def test_dual_form_moves_discordance_to_half(self):
        """合成配对集：4 实例（2×form A + 2×form B）→ 不一致比 0.5（旧全 label-only = 1.000）。"""
        product = _make_product(reports=_period_reports())
        cases = pr.build_pilot_cases(product, instances=4, pollution_types=["period_shift"])
        pairs = []
        for case in cases:
            pair, unit = pr.run_offline_case(product, case)
            assert unit["status"] == "ok"
            pairs.append(pair)
        table = pr.mcnemar_table(pairs)
        assert table["b"] == 2.0 and table["c"] == 0.0
        assert table["discordant_ratio"] == 0.5
        states = {p.case_id: (p.on_state, p.off_state) for p in pairs}
        # form A：只有 A2 能拦；form B：A2 关态下值级校验也拦（两态一致 → 不携带 A2 信息）
        assert states["600519-period_shift-0"] == ("caught", "escaped")
        assert states["600519-period_shift-1"] == ("caught", "caught")
        assert states["600519-period_shift-3"] == ("caught", "caught")


# ── 机制开关 ──


class TestMechanismToggles:
    def test_patch_targets_are_registered_and_disjoint(self):
        seen: dict[str, set[str]] = {}
        for mechanism_id in ("A2", "A3", "A4", "A5", "A6"):
            for target in pr.patch_targets(mechanism_id, surface=pr.surface_for(mechanism_id)):
                seen.setdefault(target, set()).add(mechanism_id)
        clashes = {t: ids for t, ids in seen.items() if len(ids) > 1}
        assert clashes == {}, f"补丁点被多机制共用：{clashes}"

    def test_a3_off_skips_verification_chain(self):
        from finance_agent import citation

        original = citation.verify_claims
        with pr.mechanism_toggle("A3", on=False) as toggle:
            assert citation.verify_claims is not original
            assert citation.verify_claims([], {}) == []
            assert toggle.patched == pr.patch_targets("A3", surface="verification")
        assert citation.verify_claims is original

    def test_a6_off_patches_textual_only_and_returns_pass(self):
        from finance_agent import citation

        original_text, original_num, original_period = (
            citation._verify_textual,
            citation._verify_numerical,
            citation._check_period,
        )
        with pr.mechanism_toggle("A6", on=False):
            assert citation._verify_textual is not original_text
            assert citation._verify_numerical is original_num
            assert citation._check_period is original_period
            result = citation._verify_textual(
                citation.Claim(
                    claim_type="entity",
                    source_type="event",
                    field_ref="编造事件标题",
                    stated_value="编造事件标题",
                    interpretation="编造事件标题",
                ),
                {},
            )
            assert result.status == "PASS"
        assert citation._verify_textual is original_text

    def test_a2_off_patches_period_check_only(self):
        from finance_agent import citation

        original_period, original_text = citation._check_period, citation._verify_textual
        with pr.mechanism_toggle("A2", on=False):
            assert citation._check_period is not original_period
            assert citation._check_period(None, {}) == (None, False)  # type: ignore[arg-type]
            assert citation._verify_textual is original_text
        assert citation._check_period is original_period

    def test_a2_context_surface_shuts_semantic_header(self):
        from finance_agent.nodes import analysts

        original = analysts._series_semantic_header
        with pr.mechanism_toggle("A2", on=False, surface="context"):
            assert analysts._series_semantic_header is not original
            assert (
                analysts._series_semantic_header("时间正序(旧→新)", "index -1 = 最新一期", 3) == ""
            )
        assert analysts._series_semantic_header is original

    def test_a4_off_patches_repair_only(self):
        from finance_agent.nodes import citation_repair

        original = citation_repair.repair_claims
        with pr.mechanism_toggle("A4", on=False):
            assert citation_repair.repair_claims is not original
            markdown, records = citation_repair.repair_claims("# 正文\n收盘 100.0 元。", [])
            assert markdown == "# 正文\n收盘 100.0 元。" and records == []
        assert citation_repair.repair_claims is original

    def test_a5_off_patches_price_check_only(self):
        from finance_agent.nodes import validate

        original = validate.validate_trade_prices
        with pr.mechanism_toggle("A5", on=False, surface="decision"):
            assert validate.validate_trade_prices is not original
            out = validate.validate_trade_prices(
                {"trader_plan": {"action": "buy", "entry_price": 100}}
            )
            assert out["price_check"]["result"] == "pass"
        assert validate.validate_trade_prices is original

    def test_toggle_records_surface_and_targets(self):
        with pr.mechanism_toggle("A3", on=False, surface="graph") as toggle:
            assert toggle.on is False
            assert toggle.surface == "graph"
            assert toggle.patch_targets == pr.patch_targets("A3", surface="graph")
            payload = toggle.payload()
        assert payload["mechanism_id"] == "A3"
        assert payload["patched"] == list(toggle.patch_targets)

    def test_on_state_applies_no_patches(self):
        from finance_agent import citation

        original = citation.verify_claims
        with pr.mechanism_toggle("A3", on=True) as toggle:
            assert citation.verify_claims is original
            assert toggle.patched == ()
            assert toggle.payload()["patched"] == []

    def test_strip_macro_freshness_marks_removes_marks_only(self):
        state = _snapshot()
        stripped = pr.strip_macro_freshness_marks(state)
        # 时效标记（macro_as_of + as_of_date + freshness）被剥；数据本体与其他键原样
        assert "macro_as_of" not in stripped
        assert "as_of_date" not in stripped["macro_indicators"]["cpi"]
        assert "freshness" not in stripped["macro_indicators"]["cpi"]
        assert stripped["macro_indicators"]["cpi"]["records"] == [
            {"月份": "2026-07", "全国-同比增长": 0.3}
        ]
        assert stripped["revenue"] == 1.0e9
        assert stripped["kline"] is state["kline"]
        # 原 state 不被修改（深拷贝）
        assert state["macro_indicators"]["cpi"]["freshness"] == "fresh"
        assert state["macro_indicators"]["cpi"]["as_of_date"] == "2026-08-01"

    def test_a7_toggle_strips_marks_only_when_off(self):
        with pr.mechanism_toggle("A7", on=False) as toggle:
            assert toggle.state_transform == "strip_macro_freshness_marks"
            assert toggle.patch_targets == pr.patch_targets("A7", surface="verification")
            state = {**_snapshot(), "macro_as_of": "2026-05-01"}
            transformed = toggle.transform_state(state)
            assert "macro_as_of" not in transformed
        with pr.mechanism_toggle("A7", on=True) as on_toggle:
            assert on_toggle.state_transform is None
            state = {"macro_as_of": "2026-05-01"}
            assert on_toggle.transform_state(state) == state


# ── 离线重放 ──


class TestOfflineReplay:
    def test_verification_chain_flags_polluted_claim_in_clean_state(self):
        from finance_agent.citation import Claim

        claims = [Claim.model_validate(c) for c in _analyst_reports()["fundamental"]["claims"]]
        chain = pr.run_citation_chain(claims, _snapshot())
        assert [v["status"] for v in chain["verdicts"]] == ["PASS", "PASS"]
        assert chain["flagged"] is False
        assert chain["unverifiable"] == 0

    def test_a3_off_passes_claims_through_without_verdicts(self):
        from finance_agent.citation import Claim

        claims = [Claim.model_validate(c) for c in _analyst_reports()["fundamental"]["claims"]]
        with pr.mechanism_toggle("A3", on=False):
            chain = pr.run_citation_chain(claims, _snapshot())
        assert chain["verdicts"] == []
        assert chain["flagged"] is False

    def test_replay_is_deterministic_and_zero_llm(self, monkeypatch):
        import finance_agent.nodes._llm_utils as llm_utils
        import finance_agent.nodes.citation_repair as citation_repair
        from finance_agent.llm.adapters import litellm_adapter

        def boom(*args, **kwargs):
            raise AssertionError("离线重放不得调用 LLM")

        monkeypatch.setattr(citation_repair, "call_llm_for_json", boom)
        monkeypatch.setattr(llm_utils, "call_llm_for_json", boom)
        monkeypatch.setattr(litellm_adapter, "raw_completion", boom)
        product = _make_product()
        case = _case("unit_error")
        first = pr.replay_offline(product, case, mechanism_on=True)
        second = pr.replay_offline(product, case, mechanism_on=True)
        assert first == second
        assert first["llm_calls"] == 0
        assert first["artifact_paths"]["json"] == "reports/ablation/p1/materials/600519.json"

    def test_replay_records_polluted_claim_flag_and_unverifiable(self):
        product = _make_product()
        on = pr.replay_offline(product, _case("unit_error"), mechanism_on=True)
        assert on["flagged"] is True
        assert on["polluted_present"] is True
        assert [c for c in on["claims"] if c["field_ref"].startswith("income_statement.")]
        assert on["fail_buckets"].get("value_mismatch") == 1
        assert on["unverifiable"] == 0
        assert on["mechanism"]["surface"] == "verification"

    def test_replay_flags_period_shift_only_through_a2(self):
        product = _make_product()
        on = pr.replay_offline(product, _case("period_shift"), mechanism_on=True)
        off = pr.replay_offline(product, _case("period_shift"), mechanism_on=False)
        assert on["flagged"] is True and on["polluted_present"] is True
        assert off["flagged"] is False and off["polluted_present"] is True

    def test_fabricated_event_traceability_surface(self):
        """A6 可测面（round-3）：无出处的编造事件在开态判 UNVERIFIABLE（未静默放行），
        关态被补丁为裸 PASS → 该型在**可追溯性面**上可测（机制无 FAIL 面）。"""
        product = _make_product()
        on = pr.replay_offline(product, _case("fabricated_event"), mechanism_on=True)
        off = pr.replay_offline(product, _case("fabricated_event"), mechanism_on=False)
        assert [v["status"] for v in on["verdicts"]][-1] == "UNVERIFIABLE"
        assert [v["status"] for v in off["verdicts"]][-1] == "PASS"
        assert on["flagged"] is True and off["flagged"] is False
        # 污染值仍在产物里（claim 原文）——拦截是「未静默放行」而非「值被删」
        assert on["polluted_present"] is True and off["polluted_present"] is True

    def test_stale_macro_marks_and_strip_are_recorded(self):
        product = _make_product()
        on = pr.replay_offline(product, _case("stale_macro"), mechanism_on=True)
        off = pr.replay_offline(product, _case("stale_macro"), mechanism_on=False)
        assert on["freshness_marks_present"] is True
        assert off["freshness_marks_present"] is False
        assert on["polluted_present"] is True and off["polluted_present"] is True
        # A7 输入侧面（round-3）：告警进分析师 context 与否 = 该机制的可观测差
        assert on["freshness_warning_present"] is True
        assert off["freshness_warning_present"] is False

    def test_a4_repair_participates_only_when_injected(self):
        product = _make_product()
        case = _case("unit_error")
        calls: list[str] = []

        def fake_repair(markdown: str, failures: list[dict]):
            calls.append(markdown)
            return markdown, [{"repaired": False} for _ in failures]

        no_repair = pr.replay_offline(product, case, mechanism_on=True, a4_on=True)
        assert calls == []
        assert no_repair["repair_participated"] is False

        with_repair = pr.replay_offline(
            product, case, mechanism_on=True, a4_on=True, repair_fn=fake_repair
        )
        assert len(calls) == 1
        assert with_repair["repair_participated"] is True

    def test_a4_module_repair_resolution_honours_toggle(self):
        """`repair_fn="module"` 按模块属性解析——A4 补丁在解析点才生效。"""
        markdown = "# 基本面\n营业总收入 10.00亿元。"
        with pr.mechanism_toggle("A4", on=False):
            assert pr.resolve_module_repair()(markdown, []) == (markdown, [])
        assert pr.resolve_module_repair() is not pr._PATCHES[("A4", "verification")][0].replacement


# ── 分类与配对 ──


class TestClassification:
    @pytest.mark.parametrize(
        ("polluted_present", "flagged", "expected"),
        [
            (True, True, "caught"),
            (True, False, "escaped"),
            (False, True, "caught"),
            (False, False, "caught"),
        ],
    )
    def test_classification_matrix(self, polluted_present: bool, flagged: bool, expected: str):
        assert pr.classify_offline(polluted_present=polluted_present, flagged=flagged) == expected

    def test_run_offline_case_returns_pair_for_same_polluted_input(self):
        product = _make_product()
        for pollution_type in ("direction_error", "unit_error", "period_shift"):
            case = _case(pollution_type)
            pair, unit = pr.run_offline_case(product, case)
            assert pair == EscapePair(case.case_id, "caught", "escaped")
            assert unit["status"] == "ok"
            assert unit["cost_class"] == "offline_replay"
            assert unit["on_state"] == "caught" and unit["off_state"] == "escaped"
            assert unit["llm_calls"] == 0
            assert unit["injection"]["mutated_flat_index"] is not None
            # 两态跑的是同一污染输入
            assert unit["on"]["injection"] == unit["off"]["injection"]
            assert unit["on"]["claims"] == unit["off"]["claims"]

    def test_mechanism_switch_does_not_disturb_other_types(self):
        """开关只影响目标机制：拨动与目标型无关的机制不得改变其两态判定。"""
        product = _make_product()
        baseline, _ = pr.run_offline_case(product, _case("direction_error"))
        for other in ("A2", "A6", "A4"):
            with pr.mechanism_toggle(other, on=False):
                pair, _ = pr.run_offline_case(product, _case("direction_error"))
            assert pair == baseline, f"{other} 关闭连带影响了 A3 型判定"
        period_pair, _ = pr.run_offline_case(product, _case("period_shift"))
        for other in ("A6", "A4"):
            with pr.mechanism_toggle(other, on=False):
                assert pr.run_offline_case(product, _case("period_shift"))[0] == period_pair

    def test_nested_toggles_each_patch_only_their_own_targets(self):
        """共享代码路径上的机制不得被连带停用：A3 关闭时 A2 的补丁仍照常落地。"""
        from finance_agent import citation

        with pr.mechanism_toggle("A3", on=False), pr.mechanism_toggle("A2", on=False) as a2:
            assert a2.patched == pr.patch_targets("A2", surface="verification")
            assert citation._check_period is _replacement_of("A2")
            assert citation.verify_claims is _replacement_of("A3")
        assert citation._check_period is not _replacement_of("A2")
        assert citation.verify_claims is not _replacement_of("A3")

    def test_verifier_false_positive_does_not_count_as_interception(self):
        """spec「误报不进分母」：与污染无关的 FAIL 不得被读成「拦下」。"""
        reports = _analyst_reports()
        reports["fundamental"]["claims"].append(
            {
                "claim_type": "numerical",
                "source_type": "data",
                "field_ref": "no_such_field",
                "stated_value": 1.0,
                "interpretation": "无法解析的字段 1.0",
                "direction": "flat",
            }
        )
        # 目标型机制关：被污染 claim 无 FAIL，同时另有一条与污染无关的恒 FAIL（误报）
        on = pr.replay_offline(
            _make_product(reports=reports), _case("period_shift"), mechanism_on=False
        )
        assert on["unrelated_fails"], "误报须在册（四桶拆报口径）"
        assert on["flagged"] is False  # 误报不算拦截
        assert on["polluted_present"] is True
        assert (
            pr.classify_offline(polluted_present=on["polluted_present"], flagged=on["flagged"])
            == "escaped"
        )

    def test_to_unit_judgment_shape(self):
        _, unit = pr.run_offline_case(_make_product(), _case("unit_error"))
        judged = pr.to_unit_judgment(unit)
        assert isinstance(judged, UnitJudgment)
        assert judged.method == "code" and judged.unit_type == "claim"
        assert judged.confidence == 1.0
        assert judged.ticker == "600519" and judged.run == unit["case_id"]
        assert judged.variant == pr.LEG_OFFLINE
        assert "escaped" in judged.judgment


# ── 真跑腿（graph_runner 注入） ──


class TestTrafficColumns:
    """三栏口径（owner 裁决②）：拦截率（主）/ 暴露率（辅）/ 关态穿透率（对照）+ 附录。"""

    def _unit(
        self,
        uid,
        *,
        on_present,
        on_flagged,
        off_present,
        off_flagged,
        status="ok",
        pollution_type="direction_error",
    ):
        return {
            "unit_id": uid,
            "pollution_type": pollution_type,
            "status": status,
            "on": {"polluted_present": on_present, "flagged": on_flagged},
            "off": {"polluted_present": off_present, "flagged": off_flagged},
        }

    def test_columns_over_mixed_units(self):
        units = [
            self._unit("a", on_present=True, on_flagged=True, off_present=True, off_flagged=False),
            self._unit("b", on_present=True, on_flagged=False, off_present=True, off_flagged=False),
            self._unit(
                "c",
                on_present=False,
                on_flagged=False,
                off_present=False,
                off_flagged=False,
                status="void_injection",
            ),
        ]
        cols = pr.traffic_columns(units)
        # 拦截率（主）：开态到达 2，其中被拦 1
        assert cols["interception_rate"]["value"] == pytest.approx(0.5)
        assert cols["interception_rate"]["denominator"] == 2
        # 暴露率：开态到达 2 / 注入 3
        assert cols["exposure_rate"]["value"] == pytest.approx(2 / 3)
        # 关态穿透率（对照）：关态到达 2 / 注入 3
        assert cols["off_state_penetration"]["value"] == pytest.approx(2 / 3)
        # 附录：关态逃逸 2 / 注入 3
        assert cols["batch_ratio_appendix"]["value"] == pytest.approx(2 / 3)

    def test_input_side_types_excluded_from_all_columns(self):
        units = [
            self._unit(
                "s",
                on_present=True,
                on_flagged=False,
                off_present=True,
                off_flagged=False,
                pollution_type="stale_macro",
            )
        ]
        cols = pr.traffic_columns(units)
        assert cols["population"]["injected"] == 0
        assert cols["interception_rate"]["value"] is None  # 分母 0 → None（非 0）


class TestFrozenReplay:
    """冻结重放（round-3 三层分工的离线构造层推荐形态）：1 趟真跑 + 2 态确定性重放。"""

    def _product(self):
        return _make_product(snapshot={**_snapshot(), "derived_series": {"chg_5d": -0.0279}})

    def _case(self):
        return InjectionCase(
            "600519-value_error-0",
            "value_error",
            "context",
            "A3",
            {
                "op": "dict_path_value",
                "target": "derived_series",
                "path": ["chg_5d"],
                "factor": 1.5,
                "field": "derived_series.chg_5d",
            },
        )

    def _runner_with(self, stated: float):
        def runner(*, variant, snapshot, query=""):
            return {
                "analyst_reports": {
                    "technical": {
                        "claims": [
                            {
                                "claim_type": "computational",
                                "source_type": "data",
                                "field_ref": "derived_series.chg_5d",
                                "stated_value": stated,
                                "interpretation": "5日涨跌幅",
                            }
                        ],
                        "markdown": "",
                    }
                }
            }

        return runner

    def test_frozen_run_yields_deterministic_pair(self):
        polluted = -0.0279 * 1.5
        pair, unit = pr.run_frozen_case(
            self._product(), self._case(), graph_runner=self._runner_with(polluted)
        )
        assert unit["leg"] == pr.LEG_FROZEN
        assert unit["status"] == pr.STATUS_OK
        assert (pair.on_state, pair.off_state) == ("caught", "escaped")
        assert unit["graph_runs"] == 1 and unit["on"]["llm_calls"] == 0

    def test_model_reports_are_normalized(self):
        """真实运行的 analyst_reports 是 pydantic 模型 → 冻结前后必须归一为 dict。"""

        class _Report:
            def __init__(self, payload):
                self._payload = payload

            def model_dump(self):
                return self._payload

        def runner(*, variant, snapshot, query=""):
            claim = {
                "claim_type": "computational",
                "source_type": "data",
                "field_ref": "derived_series.chg_5d",
                "stated_value": -0.0279 * 1.5,
                "interpretation": "5日涨跌幅",
            }
            return {"analyst_reports": {"technical": _Report({"claims": [claim], "markdown": ""})}}

        _, unit = pr.run_frozen_case(self._product(), self._case(), graph_runner=runner)
        assert unit["status"] == pr.STATUS_OK
        assert unit["frozen"]["frozen_claims"] == 1

    def test_pollution_not_reaching_frozen_claims_is_void(self):
        """污染未到达冻结 claim = 暴露未实现（与「拦下」区分——真跑腿此前被误记的那类）。"""
        pair, unit = pr.run_frozen_case(
            self._product(), self._case(), graph_runner=self._runner_with(-0.0279)
        )
        assert unit["status"] == pr.STATUS_VOID
        assert "暴露未实现" in unit["status_reason"]
        assert unit["leg"] == pr.LEG_FROZEN  # void 也不得标成 real_run


class TestPriceSurfaceMissing:
    """价位面缺失（price_levels 不可用 → sanity 跳过）= 没测到，不得记作「逃逸」。"""

    def test_skipped_price_check_yields_void(self):
        snapshot = _snapshot()
        snapshot.pop("trader_plan")

        # 假图：产出可执行决策，但**不带** price_levels → sanity 跳过
        def runner(*, variant, snapshot, query=""):
            plan = {"action": "buy", "entry_price": 100.0, "stop_loss": 95.0, "target_price": 115.0}
            return {"trader_plan": dict(plan), "final_trade_decision": dict(plan)}

        product = _make_product(snapshot=snapshot)
        case = pr.build_pilot_cases(product, instances=1, pollution_types=["illegal_price"])[0]
        _, unit = pr.run_real_case(product, case, graph_runner=runner)
        assert unit["status"] == pr.STATUS_VOID
        assert "价位校验面缺失" in unit["status_reason"]
        assert unit["on_state"] == ""  # 不是 escaped


class TestDecisionReuse:
    """round-3 二阶段：同一标的的决策层单元复用一趟全图（round-2 实测 4 实例各跑一趟）。"""

    def _product_without_decision(self):
        snapshot = {k: v for k, v in _snapshot().items() if k not in ("trader_plan",)}
        return _make_product(snapshot=snapshot)

    def _actionable_state(self, snapshot: dict) -> dict:
        """可执行买入决策 + sanity 消费面（价位带）——A5 才能判出 fail（同已有用例 fixture）。"""
        plan = {"action": "buy", "entry_price": 100.0, "stop_loss": 95.0, "target_price": 115.0}
        return {
            "trader_plan": dict(plan),
            "final_trade_decision": dict(plan),
            "kline": snapshot["kline"],
            "price_levels": {
                "available": True,
                "entry_ref": 100.0,
                "atr": 3.0,
                "full_band": [90.0, 130.0],
                "stop_band_long": {"low": 95.0, "high": 98.0},
                "target_band_long": {"low": 110.0, "high": 116.0},
            },
            "final_report": "# 报告",
        }

    def _illegal_cases(self, n=4):
        from evals.causal_ablation.injection import build_injection_cases

        return build_injection_cases(
            "illegal_price",
            ticker="600519",
            base_values={"revenue": 1.0e9, "price": 100.0, "growth": -0.1005, "entry": 100.0},
            n=n,
        )

    def test_four_units_share_one_graph_run(self):
        calls: list[int] = []

        def runner(*, variant, snapshot, query=""):
            calls.append(1)
            return self._actionable_state(snapshot)

        cache: dict[str, dict] = {}
        units = [
            pr.run_real_case(
                self._product_without_decision(), case, graph_runner=runner, decision_cache=cache
            )[1]
            for case in self._illegal_cases()
        ]
        assert len(calls) == 1  # 4 个实例只跑一趟图
        assert [u["graph_runs"] for u in units] == [1, 0, 0, 0]
        assert all(u["status"] == "ok" for u in units)
        assert all(u["on_state"] == "caught" and u["off_state"] == "escaped" for u in units)

    def test_non_actionable_decision_short_circuits_later_units(self):
        calls: list[int] = []

        def runner(*, variant, snapshot, query=""):
            calls.append(1)
            plan = {"action": "watch", "confidence": 0.5}
            return {"trader_plan": dict(plan), "final_trade_decision": dict(plan)}

        cache: dict[str, dict] = {}
        units = [
            pr.run_real_case(
                self._product_without_decision(), case, graph_runner=runner, decision_cache=cache
            )[1]
            for case in self._illegal_cases()
        ]
        assert len(calls) == 1  # 首单元判定「不可执行」后，后续实例零成本跳过
        assert all(u["status"] == "void_injection" for u in units)
        assert "决策复用" in units[1]["status_reason"]
        assert [u["graph_runs"] for u in units] == [1, 0, 0, 0]


class TestRealRun:
    def test_value_error_pair_flips_with_a3_graph_surface(self, monkeypatch):
        """fake graph_runner 调真实（可被补丁的）verify_citations 节点：A3 关 → 逃逸。

        `citation_node` 的稀疏 value_mismatch 会触发单点修复（真实 LLM）——本测试把该入口
        换成 no-op，保证零 LLM（修复回路本身另有 A4 开关用例覆盖）。
        """
        import evals.ablation as ablation

        import finance_agent.nodes.citation_node as citation_node

        monkeypatch.setattr(
            citation_node,
            "repair_claims",
            lambda markdown, failures, llm_config=None: (markdown, []),
        )
        ledger: list[int] = []

        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            assert variant == "analysts"
            ledger.extend(range(7))  # 模拟本次 run 的 7 次 LLM 调用
            state = {
                "analyst_reports": _analyst_reports(),
                "iteration_count": 0,
                "final_report": "# 报告\n营业总收入 11.30亿元。",
            }
            return {**state, **ablation.verify_citations({**state, **snapshot})}

        product = _make_product()
        case = _case("value_error")
        pair, unit = pr.run_real_case(
            product, case, graph_runner=fake_runner, llm_meter=lambda: len(ledger)
        )
        assert pair.case_id == case.case_id
        assert unit["cost_class"] == "real_run"
        assert unit["variant"] == "analysts"
        assert unit["on_state"] == "caught" and unit["off_state"] == "escaped"
        assert unit["llm_calls"] == 14  # 2 态 × 7 次（按 meter 差量归属，不是估算）
        assert unit["graph_runs"] == 2  # 机制开/关各一次图运行（预算 runs 以图运行为单位）
        assert unit["status"] == "ok"
        assert unit["on"]["citation"]["flagged"] is True
        assert unit["on"]["citation"]["scope"] == "polluted_claim"
        assert unit["on"]["citation"]["addressed_fails"] == 2
        assert unit["off"]["citation"]["flagged"] is False

    def test_illegal_price_uses_price_check_channel(self):
        """决策层价位污染的完整通路：快照决策 dict → 注入 → 图 → A5 价位 sanity。"""

        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            assert variant == "full"
            return {
                "trader_plan": dict(snapshot["trader_plan"]),
                "kline": snapshot["kline"],
                "price_levels": {
                    "available": True,
                    "entry_ref": 100.0,
                    "atr": 3.0,
                    "full_band": [90.0, 130.0],
                    "stop_band_long": {"low": 95.0, "high": 98.0},
                    "target_band_long": {"low": 110.0, "high": 116.0},
                },
            }

        case = _case("illegal_price")
        pair, unit = pr.run_real_case(_make_product(), case, graph_runner=fake_runner)
        assert unit["injection"]["applied"] is True
        assert unit["injection"]["consumed_overlap"] == ["trader_plan"]
        assert unit["injection"]["polluted_values"] == [110.0]
        assert pair == EscapePair(case.case_id, "caught", "escaped")
        assert unit["on"]["decision"]["price_check"]["result"] == "fail"
        assert unit["off"]["decision"]["price_check"]["result"] == "pass"
        assert unit["on_state"] == "caught"

    def test_value_error_overlap_hits_income_statement(self):
        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            income = snapshot["income_statement"]
            return {"final_report": f"营业总收入 {float(income['营业总收入'].max())} 元"}

        case = _case("value_error")
        _, unit = pr.run_real_case(_make_product(), case, graph_runner=fake_runner)
        assert unit["injection"]["applied"] is True
        assert unit["injection"]["changed_keys"] == ["income_statement"]
        assert unit["injection"]["consumed_overlap"] == ["income_statement"]
        assert unit["injection"]["polluted_values"] == [pytest.approx(1.13e9)]
        assert unit["status"] == pr.STATUS_OK

    def test_mirror_narrative_overlap_hits_kline(self):
        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            kline = snapshot["kline"]
            return {"final_report": f"最新交易日收盘 {float(kline['收盘'].iloc[-1])} 元"}

        case = _case("mirror_narrative")
        _, unit = pr.run_real_case(_make_product(), case, graph_runner=fake_runner)
        assert unit["injection"]["applied"] is True
        assert unit["injection"]["changed_keys"] == ["kline"]
        assert unit["injection"]["consumed_overlap"] == ["kline"]
        # 镜像后末行 = 原首行（最旧收盘）：正序读者会把 99.0 当最新
        assert unit["injection"]["polluted_values"] == [99.0]
        assert unit["status"] == pr.STATUS_OK

    def test_value_error_default_instance_targets_citeable_derived_series(self):
        """重校靶点：派生值表是分析师被提示直接引用的字段（round-1 打利润表格 → 7/8 void）。"""

        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            polluted = float(snapshot["derived_series"]["chg_5d"])
            claim = {
                "claim_type": "numerical",
                "source_type": "data",
                "field_ref": "derived_series.chg_5d",
                "stated_value": polluted,
                "interpretation": f"5 日涨跌幅 {polluted * 100:.2f}%",
                "period": "2026-09-15",
                "direction": "flat",
            }
            state = {
                "analyst_reports": {"technical": {"claims": [claim], "markdown": "5 日涨跌幅"}},
                "iteration_count": 0,
            }
            return state

        snapshot = _snapshot()
        snapshot["derived_series"] = {"chg_5d": 0.02, "chg_20d": 0.05}
        product = _make_product(reports=_reports_without_claim_targets(), snapshot=snapshot)
        case = pr.build_pilot_cases(product, instances=1, pollution_types=["value_error"])[0]
        _, unit = pr.run_real_case(product, case, graph_runner=fake_runner)
        assert unit["injection"]["applied"] is True
        assert unit["injection"]["target"] == "derived_series.chg_5d"
        assert unit["injection"]["changed_keys"] == ["derived_series"]
        assert unit["injection"]["consumed_overlap"] == ["derived_series"]
        assert unit["injection"]["polluted_values"] == [
            pytest.approx(0.02 * case.payload["factor"])
        ]
        # 污染到达终态产物：round-1 该型判 void 的正是这一环
        assert unit["status"] == pr.STATUS_OK
        assert unit["on"]["polluted_present"] is True
        assert unit["off"]["polluted_present"] is True

    def test_derived_series_recompute_claim_is_caught_through_a3(self, monkeypatch):
        """derived_series 在重算注册表里：computational claim 走 A3 重算 → 两态差可测。"""
        import evals.ablation as ablation

        import finance_agent.nodes.citation_node as citation_node

        monkeypatch.setattr(
            citation_node,
            "repair_claims",
            lambda markdown, failures, llm_config=None: (markdown, []),
        )
        kline = pd.DataFrame(
            {
                "日期": [f"2026-09-{i:02d}" for i in range(1, 8)],
                "收盘": [100.0 + i for i in range(7)],
            }
        )

        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            polluted = float(snapshot["derived_series"]["chg_5d"])
            claim = {
                "claim_type": "computational",
                "source_type": "data",
                "field_ref": "derived_series.chg_5d",
                "stated_value": polluted,
                "interpretation": f"5 日涨跌幅 {polluted * 100:.2f}%",
                "period": "2026-09-07",
                "direction": "flat",
            }
            state = {
                "analyst_reports": {"technical": {"claims": [claim], "markdown": "5 日涨跌幅"}},
                "iteration_count": 0,
            }
            return {**state, **ablation.verify_citations({**state, **snapshot})}

        snapshot = _snapshot()
        snapshot["kline"] = kline
        snapshot["derived_series"] = {"chg_5d": 0.02}
        snapshot["technical_indicators"] = {}
        product = _make_product(reports=_reports_without_claim_targets(), snapshot=snapshot)
        case = pr.build_pilot_cases(product, instances=1, pollution_types=["value_error"])[0]
        _, unit = pr.run_real_case(product, case, graph_runner=fake_runner)
        assert unit["status"] == pr.STATUS_OK
        assert unit["on_state"] == "caught" and unit["off_state"] == "escaped"
        assert unit["on"]["citation"]["flagged"] is True
        assert unit["off"]["citation"]["flagged"] is False

    def test_value_error_falls_back_to_income_statement_when_no_derived_series(self):
        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            income = snapshot["income_statement"]
            return {"final_report": f"营业总收入 {float(income['营业总收入'].max())} 元"}

        product = _make_product(reports=_reports_without_claim_targets())
        case = pr.build_pilot_cases(product, instances=1, pollution_types=["value_error"])[0]
        _, unit = pr.run_real_case(product, case, graph_runner=fake_runner)
        assert unit["injection"]["applied"] is True
        assert unit["injection"]["target"] == "income_statement"
        assert unit["injection"]["changed_keys"] == ["income_statement"]
        assert unit["injection"]["consumed_overlap"] == ["income_statement"]
        assert unit["status"] == pr.STATUS_OK

    def test_mirror_narrative_reaches_artifact_through_technical_indicators(self):
        """重校靶点：分析师直读 technical_indicators 序列（round-1 打 kline → 全 void）。"""

        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            ma5 = list(snapshot["technical_indicators"]["MA"]["5"])
            quoted = next(v for v in reversed(ma5) if v is not None)
            claim = {
                "claim_type": "numerical",
                "source_type": "data",
                "field_ref": "technical_indicators.MA.5.-1",
                "stated_value": quoted,
                "interpretation": f"MA5 最新为 {quoted}",
                "period": "2026-09-15",
                "direction": "flat",
            }
            return {
                "analyst_reports": {
                    "technical": {"claims": [claim], "markdown": f"MA5 最新为 {quoted}"}
                },
                "final_report": f"# 技术面\nMA5 最新为 {quoted}",
            }

        snapshot = _snapshot()
        snapshot["technical_indicators"] = {"MA": {"5": [None, None, 1.0, 2.0, 3.0]}}
        product = _make_product(snapshot=snapshot)
        case = pr.build_pilot_cases(product, instances=1, pollution_types=["mirror_narrative"])[0]
        _, unit = pr.run_real_case(product, case, graph_runner=fake_runner)
        assert unit["injection"]["applied"] is True
        assert unit["injection"]["target"] == "technical_indicators.MA.5"
        assert unit["injection"]["changed_keys"] == ["technical_indicators"]
        assert unit["injection"]["consumed_overlap"] == ["technical_indicators"]
        assert unit["injection"]["polluted_values"] == [1.0]
        assert unit["status"] == pr.STATUS_OK
        assert unit["on"]["polluted_present"] is True

    def test_illegal_price_post_graph_flow_runs_once_and_validates_both_states(self):
        """产物无决策 dict（round-1 的 4/4 void 场景）：跑图产出决策 → 注入 → A5 两态校验。

        选「1 次图运行 + 2 次校验」：配对要求两态喂同一污染决策；每态各跑一次图只会得到
        两个不同的 LLM 决策（非确定性）且成本翻倍。sanity 是纯规则函数（无 LLM）。
        """
        snapshot = _snapshot()
        snapshot.pop("trader_plan")
        runs: list[str] = []
        ledger: list[int] = []

        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            runs.append(variant)
            ledger.extend(range(11))  # 模拟本趟图的 11 次 LLM 调用
            plan = {
                "action": "buy",
                "entry_price": 100.0,
                "stop_loss": 95.0,
                "target_price": 115.0,
            }
            return {
                "trader_plan": dict(plan),
                "final_trade_decision": dict(plan),
                "kline": snapshot["kline"],
                "price_levels": {
                    "available": True,
                    "entry_ref": 100.0,
                    "atr": 3.0,
                    "full_band": [90.0, 130.0],
                    "stop_band_long": {"low": 95.0, "high": 98.0},
                    "target_band_long": {"low": 110.0, "high": 116.0},
                },
                "final_report": "# 报告",
            }

        product = _make_product(snapshot=snapshot)
        case = pr.build_pilot_cases(product, instances=1, pollution_types=["illegal_price"])[0]
        pair, unit = pr.run_real_case(
            product, case, graph_runner=fake_runner, llm_meter=lambda: len(ledger)
        )
        assert runs == ["full"]  # 只跑一趟图（两态共用同一污染决策输入）
        assert unit["status"] == pr.STATUS_OK
        assert unit["graph_runs"] == 1
        assert unit["llm_calls"] == 11
        assert unit["post_graph"]["decision_key"] == "trader_plan"
        assert unit["post_graph"]["shared_graph_runs"] == 1
        assert unit["injection"]["applied"] is True
        assert unit["injection"]["polluted_values"] == [pytest.approx(110.0)]
        # sanity 看到的是注入后的决策（reason 点名被破的价位关系）
        assert unit["on"]["decision"]["price_check"]["result"] == "fail"
        assert "110" in unit["on"]["decision"]["price_check"]["reason"]
        assert unit["on"]["polluted_present"] is True
        assert unit["off"]["decision"]["price_check"]["result"] == "pass"
        assert unit["off"]["polluted_present"] is True
        assert pair == EscapePair(case.case_id, "caught", "escaped")
        # 关态的补丁确实落在 A5 的校验面上（两态差异来自机制开关，不是输入）
        assert unit["on"]["mechanism"]["patched"] == []
        assert (
            "finance_agent.nodes.validate.validate_trade_prices"
            in unit["off"]["mechanism"]["patched"]
        )

    def test_illegal_price_post_graph_injects_into_final_decision_when_no_trader_plan(self):
        """终态只有风控裁决（final_trade_decision）时也按同一决策对象喂 sanity（读 trader_plan）。"""

        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            return {
                "final_trade_decision": {
                    "action": "buy",
                    "entry_price": 100.0,
                    "stop_loss": 95.0,
                    "target_price": 115.0,
                },
                "kline": snapshot["kline"],
                "price_levels": {
                    "available": True,
                    "entry_ref": 100.0,
                    "atr": 3.0,
                    "full_band": [90.0, 130.0],
                    "stop_band_long": {"low": 95.0, "high": 98.0},
                    "target_band_long": {"low": 110.0, "high": 116.0},
                },
            }

        snapshot = _snapshot()
        snapshot.pop("trader_plan")
        product = _make_product(snapshot=snapshot)
        case = pr.build_pilot_cases(product, instances=1, pollution_types=["illegal_price"])[0]
        _, unit = pr.run_real_case(product, case, graph_runner=fake_runner)
        assert unit["post_graph"]["decision_key"] == "final_trade_decision"
        assert unit["status"] == pr.STATUS_OK
        assert unit["on_state"] == "caught" and unit["off_state"] == "escaped"
        assert "110" in unit["on"]["decision"]["price_check"]["reason"]

    def test_illegal_price_post_graph_uninjectable_decision_is_void_with_honest_cost(self):
        """跑图后仍不可施加（hold 无价位要求）→ 判 void，但图已跑：成本如实入账（不写 0）。"""
        ledger: list[int] = []

        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            ledger.extend(range(5))
            return {"trader_plan": {"action": "hold", "entry_price": 100.0}}

        snapshot = _snapshot()
        snapshot.pop("trader_plan")
        product = _make_product(snapshot=snapshot)
        case = pr.build_pilot_cases(product, instances=1, pollution_types=["illegal_price"])[0]
        _, unit = pr.run_real_case(
            product, case, graph_runner=fake_runner, llm_meter=lambda: len(ledger)
        )
        assert unit["status"] == pr.STATUS_VOID
        assert unit["graph_runs"] == 1  # 图已跑：不得记 0（诚实计账）
        assert unit["llm_calls"] == 5
        assert "hold" in unit["status_reason"]
        assert unit["on_state"] == "" and unit["off_state"] == ""  # 未判定，不得读成 caught

    def test_illegal_price_post_graph_without_decision_is_void_with_honest_cost(self):
        ledger: list[int] = []

        def fake_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            ledger.extend(range(3))  # 图跑了（有成本），但没产出决策
            return {"final_report": "# 报告（无决策）"}

        snapshot = _snapshot()
        snapshot.pop("trader_plan")
        product = _make_product(snapshot=snapshot)
        case = pr.build_pilot_cases(product, instances=1, pollution_types=["illegal_price"])[0]
        _, unit = pr.run_real_case(
            product, case, graph_runner=fake_runner, llm_meter=lambda: len(ledger)
        )
        assert unit["status"] == pr.STATUS_VOID
        assert unit["graph_runs"] == 1
        assert unit["llm_calls"] == 3
        assert "决策 dict" in unit["status_reason"]
        assert unit["on"]["ran"] is True and unit["on"]["polluted_present"] is None

    def test_void_injection_is_excluded_from_pairs(self):
        def empty_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            return {}

        case = _case("value_error")
        pair, unit = pr.run_real_case(_make_product(), case, graph_runner=empty_runner)
        assert unit["status"] == "void_injection"
        assert "void" in unit["status_reason"]
        # 注入本身落在消费键上（接线正确）；void 的原因是两态产物都没带污染
        assert unit["injection"]["applied"] is True
        assert unit["injection"]["consumed_overlap"] == ["income_statement"]
        assert "污染未到达产物" in unit["status_reason"]
        assert pair.on_state == pair.off_state  # 配对仍是合法 EscapePair，但报告须剔除

    def test_unapplicable_injection_is_void_with_reason(self):
        """注入目标结构缺失（真实材料风险）：预判 void，不冒充「没逃逸」。

        决策层（illegal_price）不再走此路：产物缺决策时改为「跑图产出决策 → 注入」
        （见 post_graph 用例）；本用例覆盖非决策点（mirror_narrative）的预判 void。
        """
        snapshot = _snapshot()
        snapshot.pop("kline")

        def empty_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            return {}

        _, unit = pr.run_real_case(
            _make_product(snapshot=snapshot), _case("mirror_narrative"), graph_runner=empty_runner
        )
        assert unit["status"] == pr.STATUS_VOID
        assert unit["injection"]["applied"] is False
        assert "kline" in unit["injection"]["applied_reason"]
        assert "注入不可施加" in unit["status_reason"]

    def test_unapplicable_injection_is_prevoided_with_zero_graph_runs(self):
        """不可施加是运行前已知事实（applied=False）→ SHALL 跳过图运行：0 次 run、0 次 LLM。

        P1 真跑腿实证：illegal_price 单元缺决策 dict 时仍跑满 38 次调用，事后才判 void。
        非决策点仍按此预判；决策点改为后置流程（图必须先跑出决策才谈得上注入）。
        """
        snapshot = _snapshot()
        snapshot.pop("kline")
        invocations: list[str] = []

        def counting_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            invocations.append(variant)
            return {}

        def boom_meter() -> int:
            raise AssertionError("预判 void 的单元不得读 meter（未运行任何图）")

        _, unit = pr.run_real_case(
            _make_product(snapshot=snapshot),
            _case("mirror_narrative"),
            graph_runner=counting_runner,
            llm_meter=boom_meter,
        )
        assert invocations == [], "注入不可施加的单元不得运行图（浪费的消耗是合规缺陷）"
        assert unit["status"] == pr.STATUS_VOID
        assert unit["llm_calls"] == 0
        assert unit["graph_runs"] == 0
        assert "预判" in unit["status_reason"]
        assert "0 次图运行" in unit["status_reason"]
        assert unit["on"]["ran"] is False and unit["off"]["ran"] is False
        assert unit["on"]["graph_runs"] == 0 and unit["off"]["llm_calls"] == 0
        assert unit["on_state"] == "" and unit["off_state"] == ""  # 未判定，不得读成 caught

    def test_unchanged_snapshot_is_prevoided_with_zero_graph_runs(self):
        """注入后快照与原件逐一相同（本产物已带该标记）→ 运行前即可判 void，零消耗。"""
        invocations: list[str] = []

        def counting_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            invocations.append(variant)
            return {}

        # 快照 macro 已含 as_of_date=2026-08-01 / freshness=fresh：注入同值 → changed 为空
        already = {
            "op": "macro_stale",
            "target": "macro_indicators",
            "as_of_date": "2026-08-01",
            "freshness": "fresh",
            "field": "macro_indicators.as_of_date",
        }
        _, unit = pr.run_real_case(
            _make_product(),
            _case("stale_macro", payload=already),
            graph_runner=counting_runner,
        )
        assert invocations == []
        assert unit["status"] == pr.STATUS_VOID
        assert unit["llm_calls"] == 0 and unit["graph_runs"] == 0
        assert "未改变快照" in unit["status_reason"]
        assert "预判" in unit["status_reason"]

    def test_void_reason_names_wiring_gap_when_overlap_empty(self):
        """注入键不在消费面上时，void 原因须点名接线缺口（该分支在接线修好后仍是活路径）。"""
        snapshot = {**_snapshot(), "legacy_series": pd.DataFrame({"值": [1.0, 2.0, 3.0]})}
        case = InjectionCase(
            case_id="600519-mirror_narrative-legacy",
            pollution_type="mirror_narrative",
            injection_point="context",
            mechanism_id="A2",
            payload={"op": "reverse_rows", "target": "legacy_series", "field": "legacy_series"},
        )

        def empty_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            return {}

        _, unit = pr.run_real_case(
            _make_product(snapshot=snapshot), case, graph_runner=empty_runner
        )
        assert unit["injection"]["applied"] is True
        assert unit["injection"]["consumed_overlap"] == []
        assert unit["status"] == pr.STATUS_VOID
        assert "消费键" in unit["status_reason"]

    def test_real_run_classification_matrix_helpers(self):
        case = _case("illegal_price")
        flagged = {"trader_plan": {"entry_price": 110.0}, "price_check": {"result": "corrected"}}
        outcome = pr.classify_terminal(flagged, case, injected={"polluted_values": [110.0]})
        assert outcome["polluted_present"] is True
        assert outcome["flagged"] is True
        quiet = pr.classify_terminal(
            {"trader_plan": {"entry_price": 100.0}}, case, injected={"polluted_values": [110.0]}
        )
        assert quiet["polluted_present"] is False and quiet["flagged"] is False


# ── 报告与门禁 ──


def _prereg_dir(tmp_path: Path, *, valid: bool = True) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    body = (
        "- 主指标: 逃逸率\n- MDE: 6pp\n- 决策阈值: 拦截率 < 50% 判薄防线（依据：见 D6）\n"
        "- 样本量依据: 10 标的 × 8 类 × 4 实例\n- 停止规则: 不一致对子 < 10% 停跑\n"
        "- rubric 版本: judge-v8\n"
    )
    if not valid:
        body = "- 主指标: 逃逸率\n"
    # 文件名须含本实验子串：门禁按名锁定自己的预登记（同目录后来者不得顶替）
    (tmp_path / "2026-09-16-p1-injection-pilot.md").write_text(body, encoding="utf-8")
    return tmp_path


class TestPreregisterGate:
    def test_assert_launch_allowed_returns_doc(self, tmp_path: Path):
        doc = pr.assert_launch_allowed(_prereg_dir(tmp_path))
        assert doc.valid is True
        assert "逃逸率" in doc.fields["主指标"]

    def test_empty_dir_rejected(self, tmp_path: Path):
        with pytest.raises(MissingPreregistrationError) as err:
            pr.assert_launch_allowed(tmp_path / "absent")
        assert "预登记" in str(err.value)

    def test_invalid_doc_rejected(self, tmp_path: Path):
        with pytest.raises(MissingPreregistrationError) as err:
            pr.assert_launch_allowed(_prereg_dir(tmp_path, valid=False))
        assert "决策阈值" in str(err.value)

    def test_pilot_report_enforces_gate(self, tmp_path: Path):
        with pytest.raises(MissingPreregistrationError):
            pr.pilot_report([], prereg_dir=tmp_path / "absent")


class TestPilotReport:
    def _units(self) -> list[dict]:
        product = _make_product()
        units: list[dict] = []
        for i, pollution_type in enumerate(("direction_error", "unit_error", "period_shift")):
            case = _case(pollution_type)
            case = InjectionCase(
                case_id=f"600519-{pollution_type}-{i}",
                pollution_type=case.pollution_type,
                injection_point=case.injection_point,
                mechanism_id=case.mechanism_id,
                payload=case.payload,
            )
            _, unit = pr.run_offline_case(product, case)
            units.append(unit)
        return units

    def test_report_wiring(self, tmp_path: Path):
        units = self._units()
        report = pr.pilot_report(
            units,
            prereg_dir=_prereg_dir(tmp_path / "prereg"),
            positive_control_units=units[:2],
        )
        assert report["status"] == "active"
        assert report["preregistration"]["valid"] is True
        assert report["counts"] == {"units_total": 3, "ok": 3, "void_injection": 0, "error": 0}
        assert report["types_planned"]["real_run"] == [
            "value_error",
            "mirror_narrative",
            "illegal_price",
        ]
        assert report["discordant"]["overall"]["b"] == 3.0
        assert report["discordant"]["overall"]["c"] == 0.0
        assert set(report["discordant"]["by_type"]) == {
            "direction_error",
            "unit_error",
            "period_shift",
        }
        # 每型 n=1 < 阈值 → 不给校准判词（不得用样本不足的数字下结论）
        assert report["calibration"]["by_type"]["unit_error"]["verdict"] == "insufficient"
        assert report["calibration"]["overall"]["verdict"] == "insufficient"
        # 未终裁：rate 为 None，pending 计数在册（关态逃逸 3 个 = 待终裁）
        overall_rate = report["escape_rate"]["overall"]
        assert overall_rate["rate"] is None
        assert overall_rate["pending"] == 3
        assert overall_rate["denominator"] == 0
        assert len(report["adjudication_worklist"]) == 3
        assert report["adjudication_worklist"][0]["evidence_paths"]
        assert report["budget"]["offline_replay"]["llm_calls"] == 0
        assert report["budget"]["real_run"]["runs"] == 0
        assert "llm_calls_total" not in report["budget"]
        assert report["stop_rules"]["too_easy"] is False

    def test_calibration_verdict_above_threshold(self, tmp_path: Path):
        units = []
        # 10 对中 4 对不一致（b=4、c=0）、6 对一致 → 不一致比例 0.4，落在健康区间
        for i in range(pr.MIN_PAIRS_FOR_CALIBRATION):
            escaped = i < 4
            units.append(
                {
                    "unit_id": f"u{i}",
                    "case_id": f"c{i}",
                    "ticker": "600519",
                    "leg": pr.LEG_OFFLINE,
                    "cost_class": "offline_replay",
                    "pollution_type": "direction_error",
                    "injection_point": "analyst_output",
                    "mechanism_id": "A3",
                    "on_state": "caught",
                    "off_state": "escaped" if escaped else "caught",
                    "status": "ok",
                    "llm_calls": 0,
                    "evidence_paths": ["reports/ablation/p1/materials/600519.json"],
                }
            )
        report = pr.pilot_report(
            units, prereg_dir=_prereg_dir(tmp_path / "prereg"), positive_control_units=units
        )
        assert report["calibration"]["by_type"]["direction_error"]["verdict"] == "ok"
        assert report["calibration"]["overall"]["verdict"] == "ok"
        # 未终裁：rate=None、pending = 逃逸数（4），不一致对子 = 4
        assert report["escape_rate"]["overall"]["rate"] is None
        assert report["escape_rate"]["overall"]["pending"] == 4
        assert report["discordant"]["overall"]["b"] == 4.0
        worklist = report["adjudication_worklist"]
        assert len(worklist) == 4
        assert worklist[0]["evidence_paths"]
        assert report["blind_spots"] == []  # direction_error 有校验侧拦截面
        assert report["stop_rules"]["too_easy"] is False

    def test_adjudicated_escapes_enter_escape_rate(self, tmp_path: Path):
        """终裁回填 → rate 出数（未回填一律 None）；未终裁的仍计 pending。"""
        units = self._units()
        report = pr.pilot_report(
            units,
            prereg_dir=_prereg_dir(tmp_path / "prereg"),
            positive_control_units=[],
            adjudicated={"600519-direction_error-0", "600519-unit_error-1"},
        )
        overall = report["escape_rate"]["overall"]
        assert overall["denominator"] == 2
        assert overall["escapes"] == 2
        assert overall["rate"] == 1.0
        assert overall["pending"] == 1
        assert report["escape_rate"]["adjudicated"] == 2

    def test_stop_rule_too_easy_flagged(self, tmp_path: Path):
        units = [
            {
                "unit_id": f"u{i}",
                "case_id": f"c{i}",
                "ticker": "600519",
                "leg": pr.LEG_OFFLINE,
                "cost_class": "offline_replay",
                "pollution_type": "direction_error",
                "injection_point": "analyst_output",
                "mechanism_id": "A3",
                "on_state": "caught",
                "off_state": "caught",
                "status": "ok",
                "llm_calls": 0,
                "evidence_paths": [],
            }
            for i in range(pr.MIN_PAIRS_FOR_CALIBRATION)
        ]
        report = pr.pilot_report(
            units, prereg_dir=_prereg_dir(tmp_path / "prereg"), positive_control_units=[]
        )
        assert report["calibration"]["overall"]["verdict"] == "too_easy"
        assert report["stop_rules"]["too_easy"] is True
        assert "停跑" in report["stop_rules"]["action"]

    def test_void_units_excluded_from_pairs(self, tmp_path: Path):
        units = self._units()
        units.append({**units[0], "unit_id": "void", "case_id": "void", "status": "void_injection"})
        report = pr.pilot_report(
            units, prereg_dir=_prereg_dir(tmp_path / "prereg"), positive_control_units=[]
        )
        assert report["counts"]["void_injection"] == 1
        assert report["discordant"]["overall"]["b"] == 3.0

    def test_blind_spots_only_for_types_without_any_surface(self, tmp_path: Path):
        """round-3：A6 走可追溯性面、A7 走输入侧面 → 不再列盲区；只有 kind=none 才列。"""
        units = self._units()
        fabricated = pr.run_offline_case(_make_product(), _case("fabricated_event"))[1]
        stale = pr.run_offline_case(_make_product(), _case("stale_macro"))[1]
        mirror = {
            **units[0],
            "unit_id": "mirror",
            "case_id": "mirror",
            "pollution_type": "mirror_narrative",
            "mechanism_id": "A2",
        }
        report = pr.pilot_report(
            units + [fabricated, stale, mirror],
            prereg_dir=_prereg_dir(tmp_path / "prereg"),
            positive_control_units=[],
        )
        assert {b["pollution_type"] for b in report["blind_spots"]} == {"mirror_narrative"}
        assert all(b["reason"] for b in report["blind_spots"])
        assert report["by_type"]["fabricated_event"]["flag_surface"] == "traceability"
        assert report["by_type"]["stale_macro"]["flag_surface"] == "input_side"

    def test_input_side_types_excluded_from_worklist_but_disclosed(self, tmp_path: Path):
        """输入侧面型无「拦截/逃逸」语义：不进终裁清单，但排除项必须可见（不得静默）。"""
        stale = pr.run_offline_case(_make_product(), _case("stale_macro"))[1]
        report = pr.pilot_report(
            [stale], prereg_dir=_prereg_dir(tmp_path / "prereg"), positive_control_units=[]
        )
        assert report["adjudication_worklist"] == []
        assert report["worklist_excluded"]["stale_macro"]["units"] == 1
        assert "输入侧面" in report["worklist_excluded"]["stale_macro"]["reason"]

    def test_input_side_evidence_reports_presence_verdict(self, tmp_path: Path):
        """A7 输入侧证据：ON 全部有告警 / OFF 全无 → 「机制生效」（presence 检查，非拦截率）。"""
        stale = pr.run_offline_case(_make_product(), _case("stale_macro"))[1]
        report = pr.pilot_report(
            [stale], prereg_dir=_prereg_dir(tmp_path / "prereg"), positive_control_units=[]
        )
        block = report["input_side_evidence"]["stale_macro"]
        assert block["surface"] == "context_freshness_warning"
        assert block["units"] == 1
        assert block["present_on"] == 1 and block["present_off"] == 0
        assert "机制生效" in block["verdict"]


class TestBudgetHonesty:
    """spec「预算分型申报」：已尝试单元（ok + void + error）的消耗 SHALL 全部入账。

    P1 真跑腿实证：报告只算 ok 单元的 20 次调用，丢掉 void/error 单元的消耗（≈10× 低估）；
    且 illegal_price 单元在「注入不可施加」已知的情况下仍跑满图（38 次/单元）。
    """

    @staticmethod
    def _real_unit(
        unit_id: str, *, status: str, llm_calls: int | None, graph_runs: int | None = None
    ) -> dict:
        unit: dict[str, object] = {
            "unit_id": f"{unit_id}::{pr.LEG_REAL}",
            "case_id": unit_id,
            "ticker": "600519",
            "leg": pr.LEG_REAL,
            "cost_class": pr.LEG_REAL,
            "pollution_type": "illegal_price",
            "injection_point": "decision",
            "mechanism_id": "A5",
            "on_state": "caught",
            "off_state": "escaped",
            "status": status,
            "status_reason": "",
            "llm_calls": llm_calls,
            "evidence_paths": [],
        }
        if graph_runs is not None:
            unit["graph_runs"] = graph_runs
        return unit

    def _report(self, units: list[dict], tmp_path: Path, **kwargs) -> dict:
        return pr.pilot_report(
            units,
            prereg_dir=_prereg_dir(tmp_path / "prereg"),
            positive_control_units=[],
            **kwargs,
        )

    def test_budget_sums_void_and_error_units(self, tmp_path: Path):
        units = [
            self._real_unit("v-ok", status="ok", llm_calls=10, graph_runs=2),
            self._real_unit("v-void", status="void_injection", llm_calls=38, graph_runs=2),
            self._real_unit("v-err", status="error", llm_calls=None),  # 中途失败：计量未知
        ]
        budget = self._report(units, tmp_path)["budget"]["real_run"]
        assert budget["llm_calls"] == 48  # 旧实现只报 ok 单元的 10（void/error 的 38 被丢）
        assert budget["llm_calls_unknown_units"] == 1
        assert budget["llm_calls_by_status"] == {"ok": 10, "void_injection": 38, "error": 0}
        assert budget["units"] == 3
        assert budget["units_by_status"] == {"ok": 1, "void_injection": 1, "error": 1}
        # runs = 图运行次数（每单元 2 态）；旧实现把它当单元数
        assert budget["runs"] == 4
        assert budget["runs_unknown_units"] == 1  # error 单元的运行数未知，显式披露

    def test_budget_counts_prevoid_units_with_zero_runs(self, tmp_path: Path):
        units = [
            self._real_unit("v-prevoid", status="void_injection", llm_calls=0, graph_runs=0),
        ]
        budget = self._report(units, tmp_path)["budget"]["real_run"]
        assert budget["units"] == 1  # 已尝试即入账（不得静默丢单元）
        assert budget["runs"] == 0  # 但预判单元零图运行
        assert budget["llm_calls"] == 0
        assert budget["llm_calls_unknown_units"] == 0

    def test_budget_exposes_meter_total_cross_check(self, tmp_path: Path):
        units = [
            self._real_unit("v-ok", status="ok", llm_calls=10, graph_runs=2),
            self._real_unit("v-void", status="void_injection", llm_calls=38, graph_runs=2),
        ]
        matched = self._report(units, tmp_path, llm_meter_total=48)["budget"]["real_run"]
        assert matched["llm_meter_total_calls"] == 48
        assert matched["llm_meter_matches_unit_sum"] is True
        mismatched = self._report(units, tmp_path, llm_meter_total=47)["budget"]["real_run"]
        assert mismatched["llm_meter_matches_unit_sum"] is False
        absent = self._report(units, tmp_path)["budget"]["real_run"]
        assert absent["llm_meter_total_calls"] is None
        assert absent["llm_meter_matches_unit_sum"] is None

    def test_budget_keeps_split_blocks_and_offline_zero_semantics(self, tmp_path: Path):
        offline = pr.run_offline_case(_make_product(), _case("unit_error"))[1]
        units = [offline, self._real_unit("v-ok", status="ok", llm_calls=10, graph_runs=2)]
        budget = self._report(units, tmp_path)["budget"]
        assert budget["offline_replay"]["units"] == 1
        assert budget["offline_replay"]["llm_calls"] == 0
        assert budget["offline_replay"]["zero_llm_verified"] is True
        assert budget["real_run"]["llm_calls"] == 10
        assert "SHALL NOT 合并" in budget["note"]
        assert "llm_calls_total" not in budget

    def test_prevoided_real_case_flows_into_budget_with_zero_calls(self, tmp_path: Path):
        """端到端：不可施加的注入（非决策点）经 run_real_case 落成 0 调用单元，报告如实计 0。"""
        snapshot = _snapshot()
        snapshot.pop("kline")

        def boom_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            raise AssertionError("预判 void 的单元不得运行图")

        _, unit = pr.run_real_case(
            _make_product(snapshot=snapshot),
            _case("mirror_narrative"),
            graph_runner=boom_runner,
            llm_meter=lambda: 999,
        )
        budget = self._report([unit], tmp_path)["budget"]["real_run"]
        assert budget["units"] == 1 and budget["units_by_status"]["void_injection"] == 1
        assert budget["runs"] == 0 and budget["llm_calls"] == 0


class TestPositiveControl:
    def _pc_units(self, b: int, c: int) -> list[dict]:
        units = [
            {
                "unit_id": f"pc{i}",
                "case_id": f"pc{i}",
                "ticker": "600519",
                "leg": pr.LEG_OFFLINE,
                "cost_class": "offline_replay",
                "pollution_type": "direction_error",
                "injection_point": "analyst_output",
                "mechanism_id": "A3",
                "on_state": "caught",
                "off_state": "escaped",
                "status": "ok",
                "llm_calls": 0,
                "evidence_paths": ["reports/ablation/p1/materials/600519.json"],
            }
            for i in range(b)
        ]
        units += [
            {
                **units[0],
                "unit_id": f"pc-rev{i}",
                "case_id": f"pc-rev{i}",
                "on_state": "escaped",
                "off_state": "caught",
            }
            for i in range(c)
        ]
        units += [{**units[0], "unit_id": "pc-flat", "case_id": "pc-flat", "on_state": "caught"}]
        return units

    def test_exact_mcnemar_p(self):
        assert pr.mcnemar_exact_p(0, 0) == 1.0
        assert pr.mcnemar_exact_p(1, 0) == 1.0
        assert pr.mcnemar_exact_p(16, 0) < 0.001
        assert pr.mcnemar_exact_p(10, 10) > 0.05

    def test_positive_control_confirms_sensitivity(self, tmp_path: Path):
        report = pr.pilot_report(
            self._pc_units(b=16, c=0),
            prereg_dir=_prereg_dir(tmp_path / "prereg"),
            positive_control_units=self._pc_units(b=16, c=0),
        )
        pc = report["positive_control"]
        assert pc["legs"] == [pr.LEG_OFFLINE]
        assert pc["sensitivity_confirmed"] is True
        assert pc["void_negative_results"] is False
        assert pc["exact_p"] < 0.05
        assert pc["ratio_criterion_met"] is True
        assert report["negative_results_status"] == "ok"

    def test_positive_control_failure_voids_negative_results(self, tmp_path: Path):
        report = pr.pilot_report(
            self._pc_units(b=1, c=1),
            prereg_dir=_prereg_dir(tmp_path / "prereg"),
            positive_control_units=self._pc_units(b=1, c=1),
        )
        pc = report["positive_control"]
        assert pc["sensitivity_confirmed"] is False
        assert pc["void_negative_results"] is True
        assert report["negative_results_status"] == "void"
        assert "作废" in report["positive_control"]["advice"]

    def test_missing_positive_control_voids_batch(self, tmp_path: Path):
        report = pr.pilot_report(
            self._pc_units(b=3, c=0),
            prereg_dir=_prereg_dir(tmp_path / "prereg"),
            positive_control_units=[],
        )
        assert report["positive_control"]["sensitivity_confirmed"] is False
        assert report["negative_results_status"] == "void"


# ── 用例构造 ──


class TestCaseBuilding:
    def test_build_pilot_cases_covers_matrix(self):
        cases = pr.build_pilot_cases(_make_product(), instances=4)
        assert {c.pollution_type for c in cases} == set(POLLUTION_TYPES)
        assert len(cases) == len(POLLUTION_TYPES) * 4
        assert all(c.case_id.startswith("600519-") for c in cases)
        assert all(c.mechanism_id for c in cases)

    def test_build_pilot_cases_is_deterministic(self):
        first = pr.build_pilot_cases(_make_product(), instances=2)
        second = pr.build_pilot_cases(_make_product(), instances=2)
        assert [c.case_id for c in first] == [c.case_id for c in second]
        assert [c.payload for c in first] == [c.payload for c in second]

    def test_build_pilot_cases_subset(self):
        cases = pr.build_pilot_cases(_make_product(), instances=1, pollution_types=["unit_error"])
        assert [c.pollution_type for c in cases] == ["unit_error"]
