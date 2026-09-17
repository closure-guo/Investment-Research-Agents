"""A4 单点修复回路补测测试（零 LLM）：注入形态、稀疏门控、两态读数、成本记法、报告口径。

修复回路本身用可控 fake 替换（`citation_repair.repair_claims`），断言打到行为：
关态必滞留 / 开态重校验仲裁 / 正文是否真被改掉。引用校验链走真实 `finance_agent.citation`。
"""

from __future__ import annotations

import pandas as pd
import pytest

from evals.causal_ablation import pilot_runner as pr
from evals.causal_ablation import repair_a4 as a4


def _product(*, stated: float = 100.0, markdown: str | None = None) -> dict:
    """单 claim 产物：正文含该值（正文一致的注入前提）。

    取收盘价 claim（真值 100.0 元）：文字与申报值同值型（元），修复的定位步骤能命中——
    这正是回路的有效覆盖子集；不同值型（正文 亿元 / 申报 元）的覆盖差见 `_coverage`。
    """
    text = markdown if markdown is not None else f"# 技术面\n收盘 {stated:.1f} 元。"
    return {
        "ticker": "600519",
        "snapshot_digest": "d",
        "snapshot": {
            "stock_code": "600519",
            "price": 100.0,
            "entry": 100.0,
            "kline": pd.DataFrame({"日期": ["2026-08-01", "2026-08-04"], "收盘": [99.0, 100.0]}),
        },
        "analyst_reports": {
            "technical": {
                "claims": [
                    {
                        "claim_type": "numerical",
                        "source_type": "data",
                        "field_ref": "kline.20260804.收盘",
                        "stated_value": stated,
                        "interpretation": f"收盘 {stated:.1f} 元",
                        "period": "20260804",
                        "direction": "positive",
                    }
                ],
                "markdown": text,
            }
        },
        "base_values": {},
    }


def _claim_of(product: dict) -> dict:
    return product["analyst_reports"]["technical"]["claims"][0]


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """任何用例都不得打到真实 LLM：修复入口默认 no-op，LLM 通道设为硬失败。"""
    from finance_agent.nodes import citation_repair

    def boom(*args, **kwargs):
        raise AssertionError("A4 测试不得调用 LLM")

    def noop_repair(markdown: str, failures: list[dict], llm_config=None):
        return markdown, [{"agent": f.get("agent"), "repaired": False} for f in failures]

    monkeypatch.setattr(citation_repair, "call_llm_for_json", boom)
    monkeypatch.setattr(citation_repair, "repair_claims", noop_repair)


class TestValueDrift:
    def test_drift_produces_real_fail(self):
        product = _product()
        claim = _claim_of(product)
        drifted = a4.value_drift(claim, product["snapshot"])
        assert drifted is not None
        assert drifted != claim["stated_value"]
        assert pr._injection_effective(
            claim, {**claim, "stated_value": drifted}, product["snapshot"]
        )

    def test_non_numeric_claim_has_no_drift(self):
        assert a4.value_drift({"stated_value": "10.00亿元"}, {}) is None

    def test_claim_that_already_fails_is_not_a_target(self):
        """原版就 FAIL 的 claim 不作目标（_injection_effective 判据：偏差须由污染造成）。"""
        product = _product(stated=120.0)  # 申报 120 vs 真值 100 → 本就 FAIL
        assert a4.value_drift(_claim_of(product), product["snapshot"]) is None


class TestTextRewrite:
    def test_rewrite_replaces_first_matching_token(self):
        md = "毛利率 91.2%，净利率 48.76%。另有 91.2 重复。"
        got = a4.rewrite_number(md, 91.2, "114.0")
        assert got == "毛利率 114.0%，净利率 48.76%。另有 91.2 重复。"

    def test_rewrite_returns_none_when_absent(self):
        assert a4.rewrite_number("毛利率 91.2%。", 12.3, "1.0") is None

    def test_fmt_like_keeps_decimals_and_commas(self):
        assert a4._fmt_like("91.2", 114.0) == "114.0"
        assert a4._fmt_like("1,234", 1542.5) == "1,542"
        assert a4._fmt_like("7", 8.0) == "8"

    def test_fmt_like_raises_precision_until_roundtrip(self):
        """小数位不足会写出定位器认不出的 token（9.375→「9.4」）→ 回路无句可改，故须加精度。"""
        from finance_agent.nodes.citation_repair import _close

        rendered = a4._fmt_like("7.5", 9.375)
        assert rendered != "9.4"  # 沿用原小数位会写成 9.4，与申报值差 0.025 > 容差
        assert _close(float(rendered), 9.375)  # 往返一致 = 定位器能按申报值找到它


class TestInjection:
    def test_injection_touches_claim_and_text_and_leaves_input_intact(self):
        product = _product()
        got = a4.inject_text_consistent_error(product)
        assert got["ok"] is True
        polluted = got["product"]
        claim = _claim_of(polluted)
        assert claim["stated_value"] != 100.0
        assert a4._text_carries(
            polluted["analyst_reports"]["technical"]["markdown"], float(claim["stated_value"])
        )
        # 入参未被就地改写（深拷贝）
        assert _claim_of(product)["stated_value"] == 100.0
        assert "收盘 100.0 元。" in product["analyst_reports"]["technical"]["markdown"]

    def test_coverage_reports_three_gates(self):
        """覆盖交代：数值 claim → 可产生 value_mismatch 的 → 正文可定位的（两道门都会缩样本）。"""
        product = _product()  # 1 条数值 claim，两道门均通过
        product["analyst_reports"]["macro"] = {
            "claims": [
                {
                    "claim_type": "numerical",
                    "source_type": "data",
                    "field_ref": "macro_indicators.cpi.0.全国-同比增长",
                    "stated_value": 0.5,
                    "interpretation": "CPI 同比 0.5%",
                    "direction": "positive",
                }
            ],
            "markdown": "# 宏观\nCPI 同比 0.5%。",
        }
        got = a4.inject_text_consistent_error(product)
        coverage = got["coverage"]
        assert coverage["numeric_claims"] == 2
        # 快照里没有 macro_indicators：该 claim 解析不出真值 → 落不进 value_mismatch 桶
        assert coverage["value_mismatch_claims"] == 1
        assert coverage["locatable_claims"] == 1

    def test_injection_yields_value_mismatch_fail(self):
        got = a4.inject_text_consistent_error(_product())
        checked = a4._verify(got["product"])
        assert checked["vm_count"] == 1
        verdict = checked["verdicts"][0]
        assert verdict.status == "FAIL" and verdict.bucket == "value_mismatch"

    def test_injection_is_deterministic(self):
        first = a4.inject_text_consistent_error(_product())
        second = a4.inject_text_consistent_error(_product())
        assert first["injection"] == second["injection"]
        assert _claim_of(first["product"]) == _claim_of(second["product"])
        assert (
            first["product"]["analyst_reports"]["technical"]["markdown"]
            == second["product"]["analyst_reports"]["technical"]["markdown"]
        )

    def test_no_target_is_void_not_silent(self):
        product = _product()
        product["analyst_reports"]["technical"]["markdown"] = ""  # 正文为空 → 无可定位句
        got = a4.inject_text_consistent_error(product)
        assert got["ok"] is False
        assert "无可注入目标" in got["reason"]
        unit = a4.run_repair_case(product, mode=a4.MODE_INJECTED)
        assert unit["status"] == pr.STATUS_VOID


class TestSparseGate:
    def test_natural_clean_product_has_no_sparse_unit(self):
        got = a4.sparse_vm_units(_product())
        assert got["vm_count"] == 0
        assert got["sparse_gate_fired"] is False

    def test_dense_value_mismatch_is_void(self):
        """value_mismatch ≥3 不落在稀疏区间（同 citation_node 分流语义）→ void 而非静默跳过。"""
        product = _product(stated=120.0)  # 本就 FAIL 的 claim
        claim = _claim_of(product)
        product["analyst_reports"]["technical"]["claims"] = [dict(claim) for _ in range(3)]
        unit = a4.run_repair_case(product, mode=a4.MODE_NATURAL)
        assert unit["status"] == pr.STATUS_VOID
        assert "稀疏修复区间" in unit["status_reason"]


def _fake_repair(monkeypatch, *, new_value: float | None, text: str | None = None):
    """可控修复入口：返回 (新正文, 记录)。new_value=None → 只改正文不给新值。"""
    from finance_agent.nodes import citation_repair

    def fake(markdown: str, failures: list[dict], llm_config=None):
        records: list[dict] = []
        current = markdown
        for failure in failures:
            record: dict = {"agent": failure.get("agent"), "repaired": False}
            if text is not None:
                stated = float(failure["claim"].stated_value)
                rewritten = a4.rewrite_number(current, stated, text)
                if rewritten is not None:
                    record.update(before=current, after=text, repaired=True)
                    current = rewritten
                    if new_value is not None:
                        record["updated_claim"] = failure["claim"].model_copy(
                            update={"stated_value": new_value, "interpretation": text}
                        )
            records.append(record)
        return current, records

    monkeypatch.setattr(citation_repair, "repair_claims", fake)


class TestTwoState:
    def test_off_state_keeps_error_in_text(self):
        unit = a4.run_repair_case(_product(), mode=a4.MODE_INJECTED)
        assert unit["status"] == pr.STATUS_OK
        assert unit["off"]["residual_fail"] is True
        assert unit["off"]["repair_participated"] is False  # 真回路未参与（入口被补丁换 no-op）
        assert unit["off"]["repair_entry_called"] is True  # 链确实调过入口，只是调的是关态替换件
        assert unit["off"]["rewrote_text"] == 0
        assert unit["off"]["markdown_carries_polluted"]
        assert unit["true_fail_before"] is True
        assert unit["llm_calls"] is None  # 未接线 usage_reader → unknown，不得伪造成 0

    def test_on_state_repair_clears_fail_and_text(self, monkeypatch):
        _fake_repair(monkeypatch, new_value=100.0, text="100.0 元")
        unit = a4.run_repair_case(_product(), mode=a4.MODE_INJECTED)
        assert unit["on"]["repair_participated"] is True
        assert unit["on"]["rewrote_text"] == 1
        assert unit["on"]["reverify_all_pass"] is True
        assert unit["on"]["residual_fail"] is False
        assert unit["on"]["markdown_carries_polluted"] == []
        assert unit["on"]["markdown_has_ground_truth"] == [100.0]
        assert unit["true_fail_after"] is False
        assert unit["off"]["markdown_carries_polluted"]  # 关态确实滞留才说明注入有效

    def test_on_state_rewrote_but_reverify_failed_counts_as_fallback(self, monkeypatch):
        """改了正文但重校验仍 FAIL → 生产回退全量重试（本读数单列，不得记成修复成功）。"""
        _fake_repair(monkeypatch, new_value=77.0, text="77.0 元")
        unit = a4.run_repair_case(_product(), mode=a4.MODE_INJECTED)
        assert unit["on"]["rewrote_text"] == 1
        assert unit["on"]["reverify_all_pass"] is False
        assert unit["on"]["residual_fail"] is True
        report = a4.a4_report([unit])
        assert report["fallback_to_full_retry"] == 1
        assert report["reverify_all_pass"] == 0

    def test_repair_that_does_nothing_is_not_a_success(self, monkeypatch):
        _fake_repair(monkeypatch, new_value=None, text=None)
        unit = a4.run_repair_case(_product(), mode=a4.MODE_INJECTED)
        assert unit["on"]["repair_participated"] is True
        assert unit["on"]["rewrote_text"] == 0
        assert unit["on"]["residual_fail"] is True

    def test_cost_recorded_from_usage_reader(self, monkeypatch):
        """成本按台账差量归属：OFF 段不记账，ON 段的调用计入（reader 只被读两次）。"""
        from finance_agent.nodes import citation_repair

        ledger: list[dict] = []
        reads: list[int] = []

        def fake(markdown: str, failures: list[dict], llm_config=None):
            ledger.append({"prompt_tokens": 300, "completion_tokens": 60})
            claim = failures[0]["claim"]
            return markdown, [
                {
                    "agent": None,
                    "repaired": True,
                    "before": markdown,
                    "after": "100.0 元",
                    "updated_claim": claim.model_copy(
                        update={"stated_value": 100.0, "interpretation": "收盘 100.0 元"}
                    ),
                }
            ]

        def reader():
            reads.append(len(ledger))
            return list(ledger)

        monkeypatch.setattr(citation_repair, "repair_claims", fake)
        unit = a4.run_repair_case(_product(), mode=a4.MODE_INJECTED, usage_reader=reader)
        assert reads == [0, 1]  # 读两次：ON 段前后
        assert unit["llm_calls"] == 1
        assert unit["usage"] == {"prompt_tokens": 300, "completion_tokens": 60, "entries": 1}


class TestNaturalMode:
    def test_natural_hit_targets_the_value_mismatch_claim(self, monkeypatch):
        product = _product(stated=120.0)  # 正文写 120.0 元 而真值 100 → claim 本就 FAIL
        natural = a4.sparse_vm_units(product)
        assert natural["sparse_gate_fired"] is True
        _fake_repair(monkeypatch, new_value=100.0, text="100.0 元")
        unit = a4.run_repair_case(product, mode=a4.MODE_NATURAL)
        assert unit["status"] == pr.STATUS_OK
        assert unit["targets"][0]["flat_index"] == 0
        assert unit["targets"][0]["polluted_value"] is None
        assert unit["targets"][0]["sentence"] == "收盘 120.0 元。"  # 出错句导出（人工终裁用）
        assert unit["true_fail_before"] is True

    def test_natural_clean_product_is_void(self):
        unit = a4.run_repair_case(_product(), mode=a4.MODE_NATURAL)
        assert unit["status"] == pr.STATUS_VOID
        assert unit["status_reason"]

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="未知 mode"):
            a4.run_repair_case(_product(), mode="whatever")


class TestReport:
    def _unit(self, **overrides) -> dict:
        base = {
            "unit_id": "600519::a4::injected:0",
            "ticker": "600519",
            "mode": a4.MODE_INJECTED,
            "status": pr.STATUS_OK,
            "true_fail_before": True,
            "true_fail_after": False,
            "llm_calls": 1,
            "usage": {"prompt_tokens": 300, "completion_tokens": 60, "entries": 1},
            "targets": [
                {
                    "agent": "fundamental",
                    "flat_index": 0,
                    "stated_value": 1.0e9,
                    "polluted_value": 1.25e9,
                    "claim_index": 0,
                    "sentence": "s",
                }
            ],
            "off": {
                "repair_participated": False,
                "rewrote_text": 0,
                "reverify_all_pass": False,
                "markdown_carries_polluted": [0],
                "markdown_has_ground_truth": [],
            },
            "on": {
                "repair_participated": True,
                "rewrote_text": 1,
                "reverify_all_pass": True,
                "markdown_carries_polluted": [],
                "markdown_has_ground_truth": [1.0e9],
            },
        }
        base.update(overrides)
        return base

    def test_rates_and_delta(self):
        stuck = self._unit(unit_id="u2", true_fail_after=True)
        stuck["on"] = dict(stuck["on"], markdown_carries_polluted=[0], reverify_all_pass=False)
        units = [self._unit(), stuck]
        report = a4.a4_report(units, tickers=["600519"])
        assert report["true_fail_rate_before"] == 1.0
        assert report["true_fail_rate_after"] == pytest.approx(0.5)
        assert report["true_fail_rate_delta"] == pytest.approx(0.5)
        assert report["text_cleared"] == 1
        assert report["cost"]["llm_calls"] == 2
        assert report["cost"]["prompt_tokens"] == 600

    def test_empty_batch_rates_are_none(self):
        report = a4.a4_report([])
        assert report["true_fail_rate_before"] is None
        assert report["true_fail_rate_after"] is None
        assert report["true_fail_rate_delta"] is None
        assert report["cost"]["llm_calls"] is None

    def test_unknown_cost_is_not_zero(self):
        report = a4.a4_report([self._unit(llm_calls=None, usage=None)])
        assert report["cost"]["llm_calls"] is None

    def test_void_and_error_are_listed_with_reasons(self):
        units = [
            self._unit(status=pr.STATUS_VOID, status_reason="密度超门槛"),
            self._unit(unit_id="u2", status=pr.STATUS_ERROR, status_reason="boom"),
        ]
        report = a4.a4_report(units)
        assert report["units_void"] == 1 and report["void_reasons"] == ["密度超门槛"]
        assert report["units_error"] == 1
        assert report["error_reasons"][0]["reason"] == "boom"
        assert report["units_ok"] == 0

    def test_natural_csv_has_empty_verdict_column(self):
        unit = self._unit(
            mode=a4.MODE_NATURAL,
            targets=[
                {
                    "agent": "fundamental",
                    "flat_index": 0,
                    "stated_value": 5.0e8,
                    "polluted_value": None,
                    "claim_index": 0,
                    "sentence": "营业总收入 5.00亿元。",
                }
            ],
            on={
                "repair_participated": True,
                "rewrote_text": 0,
                "reverify_all_pass": False,
                "target_states": {
                    "0": {
                        "status": "FAIL",
                        "bucket": "value_mismatch",
                        "stated_value": 5.0e8,
                        "ground_truth": 1.0e9,
                    }
                },
            },
        )
        rows = a4.natural_cases_rows([unit])
        assert len(rows) == 1
        csv = a4.natural_cases_csv(rows)
        header, row = csv.strip().splitlines()
        assert header.endswith("human_verdict(真错误?/误报?/待查)")
        assert row.endswith(",")  # 终裁列留空：机器不代答
        assert "营业总收入 5.00亿元。" in csv


class TestSentenceScope:
    """句子级读数：污染值是否已从**目标句**消失（全库扫描会把别处同值算进来）。"""

    def test_final_sentence_follows_applied_rewrite(self):
        records = [{"before": "收盘 125.0 元。", "after": "收盘 100.0 元。", "repaired": True}]
        assert a4.final_target_sentence(records, "收盘 125.0 元。") == "收盘 100.0 元。"
        assert a4.sentence_cleared(records, "收盘 125.0 元。", 125.0) is True

    def test_unrewritten_sentence_keeps_polluted_value(self):
        assert a4.sentence_cleared([], "收盘 125.0 元。", 125.0) is False

    def test_rewrite_to_another_wrong_value_is_not_cleared(self):
        """改写成另一个错值（真案例 002352：污染 2.725 → LLM 写 2.73）不算清除。"""
        records = [{"before": "权益乘数 2.725", "after": "权益乘数 2.73", "repaired": True}]
        assert a4.sentence_cleared(records, "权益乘数 2.725", 2.725) is False

    def test_natural_unit_is_unjudged(self):
        assert a4.sentence_cleared([], "收盘 120.0 元。", None) is None

    def test_report_counts_sentence_scope(self):
        unit = {
            "unit_id": "u",
            "ticker": "600519",
            "mode": a4.MODE_INJECTED,
            "status": pr.STATUS_OK,
            "true_fail_before": True,
            "true_fail_after": False,
            "targets": [],
            "off": {"markdown_carries_polluted": [0]},
            "on": {
                "markdown_carries_polluted": [0],
                "target_sentence_cleared": True,
                "production_text_carries_polluted": [0],
            },
        }
        report = a4.a4_report([unit])
        assert report["target_sentence_cleared"] == 1
        assert report["target_sentence_judged"] == 1
        assert report["text_cleared"] == 0
        assert report["production_text_cleared"] == 0


class TestValueCorrectness:
    """把「LLM 值修没修对」与「流水线是否记账」分开读（仲裁口径会把二者混为一谈）。"""

    def test_verdicts_and_target_correctness(self):
        chain = {
            "repaired_claims": {
                "0": {
                    "claim_type": "numerical",
                    "source_type": "data",
                    "field_ref": "kline.20260804.收盘",
                    "stated_value": 100.0,
                    "interpretation": "收盘 100.0 元",
                    "period": "20260804",
                    "direction": "positive",
                }
            }
        }
        verdicts = a4._repaired_value_verdicts(chain, _product()["snapshot"])
        assert verdicts == {"0": "PASS"}
        target = a4.Target("technical", 0, 0, 125.0, 125.0)
        assert a4._target_value_correct(verdicts, [target]) is True

    def test_wrong_value_is_not_correct(self):
        chain = {
            "repaired_claims": {
                "0": {
                    "claim_type": "numerical",
                    "source_type": "data",
                    "field_ref": "kline.20260804.收盘",
                    "stated_value": 77.0,
                    "interpretation": "收盘 77.0 元",
                    "period": "20260804",
                    "direction": "positive",
                }
            }
        }
        verdicts = a4._repaired_value_verdicts(chain, _product()["snapshot"])
        assert verdicts == {"0": "FAIL"}
        assert (
            a4._target_value_correct(verdicts, [a4.Target("technical", 0, 0, 125.0, 125.0)])
            is False
        )

    def test_no_output_is_unjudged(self):
        assert a4._target_value_correct({}, [a4.Target("technical", 0, 0, 125.0, 125.0)]) is None
        assert a4._repaired_value_verdicts({}, {}) == {}

    def test_error_left_in_report_uses_rewrite_and_value(self, monkeypatch):
        """终稿留错 = 没改写 或 改写值没修对（仲裁是否记账不影响正文）。"""
        _fake_repair(monkeypatch, new_value=100.0, text="100.0 元")
        fixed = a4.run_repair_case(_product(), mode=a4.MODE_INJECTED)
        assert fixed["on"]["target_value_correct"] is True
        assert fixed["error_left_in_report"] is False
        assert fixed["on"]["reverify_all_pass"] is True

    def test_error_left_in_report_when_value_wrong(self, monkeypatch):
        _fake_repair(monkeypatch, new_value=77.0, text="77.0 元")
        unit = a4.run_repair_case(_product(), mode=a4.MODE_INJECTED)
        assert unit["on"]["rewrote_text"] == 1
        assert unit["on"]["target_value_correct"] is False
        assert unit["error_left_in_report"] is True
        report = a4.a4_report([unit])
        assert report["error_left_in_report"] == 1
        assert report["target_value_correct"] == 0

    def test_natural_unit_error_flag_is_unjudged(self, monkeypatch):
        _fake_repair(monkeypatch, new_value=None, text=None)
        unit = a4.run_repair_case(_product(stated=120.0), mode=a4.MODE_NATURAL)
        assert unit["error_left_in_report"] is None


class TestSentenceScopeAgentFallback:
    def test_agent_scope_avoids_whole_product_false_positive(self):
        """同一句里合法出现同值（CPI 前后月）不算「未清除」——回落目标分析师范围判。"""
        records = [
            {
                "before": "8月CPI同比上涨1.0%（7月为0.8%，6月为1.0%）。",
                "after": "8月CPI同比上涨0.8%（7月为0.5%，6月为1.0%）。",
                "repaired": True,
            }
        ]
        assert (
            a4.sentence_cleared(records, "8月CPI同比上涨1.0%（7月为0.8%，6月为1.0%）。", 1.0)
            is False
        )
        assert (
            a4.sentence_cleared(
                [],
                "8月CPI同比上涨1.0%（7月为0.8%，6月为1.0%）。",
                1.0,
                agent_markdown="8月CPI同比上涨0.8%（7月为0.5%，6月为1.0%）。",
            )
            is False
        )
        assert (
            a4.sentence_cleared(
                [],
                "8月CPI同比上涨1.0%",
                1.0,
                agent_markdown="8月CPI同比上涨0.8%。",
            )
            is True
        )


class TestSentenceScopedInjection:
    """注入只落在定位句内：同值在其他句子合法出现时不得被改（2026-09-17 实测 600276）。"""

    def _product_with_twin_value(self) -> dict:
        product = _product()
        markdown = "# 技术面\n收盘 100.0 元，较 2026Q1 的 1 元面值溢价明显。"
        product["analyst_reports"]["technical"]["markdown"] = markdown
        return product

    def test_only_located_sentence_is_rewritten(self):
        product = self._product_with_twin_value()
        got = a4.inject_text_consistent_error(product)
        assert got["ok"] is True
        markdown = got["product"]["analyst_reports"]["technical"]["markdown"]
        assert "2026Q1 的 1 元面值" in markdown  # 别处的同值字样保持原样
        assert got["injection"]["injected_sentence"] != got["injection"]["original_sentence"]
        assert "125.0" in got["injection"]["injected_sentence"]

    def test_injection_records_original_sentence_for_audit(self):
        got = a4.inject_text_consistent_error(_product())
        assert got["injection"]["original_sentence"] == "收盘 100.0 元。"
        assert a4._text_carries(
            got["injection"]["injected_sentence"],
            float(got["injection"]["injected_values"]["stated_value"]),
        )
