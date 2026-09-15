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

    def test_fabricated_event_appends_claim_and_news(self):
        product = _make_product()
        polluted = pr.inject_offline(product, _case("fabricated_event"))
        news = [n["title"] for n in polluted["snapshot"]["news_list"]]
        assert "某公司宣布重大战略合作（注入）" in news
        appended = polluted["analyst_reports"]["technical"]["claims"][-1]
        assert appended["source_type"] == "event"
        assert "某公司宣布重大战略合作（注入）" in appended["field_ref"]
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

    def test_fabricated_event_verdict_is_pass_echo_or_unverifiable(self):
        product = _make_product()
        on = pr.replay_offline(product, _case("fabricated_event"), mechanism_on=True)
        off = pr.replay_offline(product, _case("fabricated_event"), mechanism_on=False)
        # 文本型：开态走真实回声匹配（命中注入的假新闻 → PASS(echo)），关态被补丁为裸 PASS
        assert [v["status"] for v in on["verdicts"]][-1] == "PASS"
        assert [v.get("unit_normalized") for v in on["verdicts"]][-1] == "echo"
        assert [v.get("unit_normalized") for v in off["verdicts"]][-1] is None
        # spec 口径：无 FAIL → 两态均未拦截（该型无校验侧拦截面，报告按盲区上报）
        assert on["flagged"] is False and off["flagged"] is False

    def test_stale_macro_marks_and_strip_are_recorded(self):
        product = _make_product()
        on = pr.replay_offline(product, _case("stale_macro"), mechanism_on=True)
        off = pr.replay_offline(product, _case("stale_macro"), mechanism_on=False)
        assert on["freshness_marks_present"] is True
        assert off["freshness_marks_present"] is False
        assert on["polluted_present"] is True and off["polluted_present"] is True

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
        """产物缺决策 dict（真实材料风险）：注入不可施加 → 单元判 void，不冒充「没逃逸」。"""
        snapshot = _snapshot()
        snapshot.pop("trader_plan")

        def empty_runner(*, variant: str, snapshot: dict, query: str) -> dict:
            return {}

        _, unit = pr.run_real_case(
            _make_product(snapshot=snapshot), _case("illegal_price"), graph_runner=empty_runner
        )
        assert unit["status"] == pr.STATUS_VOID
        assert unit["injection"]["applied"] is False
        assert "trader_plan" in unit["injection"]["applied_reason"]
        assert "注入不可施加" in unit["status_reason"]

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
    (tmp_path / "2026-09-16-p1.md").write_text(body, encoding="utf-8")
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

    def test_blind_spots_listed_for_types_without_verification_surface(self, tmp_path: Path):
        units = self._units()
        fabricated = pr.run_offline_case(_make_product(), _case("fabricated_event"))[1]
        stale = pr.run_offline_case(_make_product(), _case("stale_macro"))[1]
        report = pr.pilot_report(
            units + [fabricated, stale],
            prereg_dir=_prereg_dir(tmp_path / "prereg"),
            positive_control_units=[],
        )
        assert {b["pollution_type"] for b in report["blind_spots"]} == {
            "fabricated_event",
            "stale_macro",
        }
        assert all(b["reason"] for b in report["blind_spots"])


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
