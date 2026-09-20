"""TDD tests for nodes/citation_node.py — 引用校验图节点。

节点行为：
1. 从 analyst_reports 中提取所有 Claim
2. 调用 verify_claims 校验
3. 返回 citation_report + citation_pass
4. 上报 citation_pass / citation_unverifiable_ratio 两个 Langfuse Score
"""

import logging

import pandas as pd

import finance_agent.nodes.citation_node as citation_node
from finance_agent.citation import Claim
from finance_agent.models import AnalystReport
from finance_agent.nodes.citation_node import verify_citations


class TestVerifyCitations:
    """引用校验节点测试。"""

    def test_all_claims_pass(self):
        """所有 claim 校验通过时 citation_pass=True。"""
        report = AnalystReport(
            agent_name="fundamental",
            summary="基本面分析",
            plain_conclusion="结论：基本面分析",
            key_findings=["资产负债率 40%"],
            claims=[
                Claim(
                    claim_type="numerical",
                    source_type="data",
                    field_ref="solvency_metrics.资产负债率.2024",
                    stated_value=40.0,
                    interpretation="资产负债率 40%",
                ),
            ],
            markdown="## 基本面分析",
        )
        state = {
            "analyst_reports": {"fundamental": report},
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
        }
        result = verify_citations(state)
        assert result["citation_pass"] is True
        assert result["citation_report"]["total"] == 1
        assert result["citation_report"]["passed"] == 1

    def test_claims_with_failure(self):
        """有 claim 校验失败时 citation_pass=False。"""
        report = AnalystReport(
            agent_name="fundamental",
            summary="基本面分析",
            plain_conclusion="结论：基本面分析",
            key_findings=["资产负债率 45%"],
            claims=[
                Claim(
                    claim_type="numerical",
                    source_type="data",
                    field_ref="solvency_metrics.资产负债率.2024",
                    stated_value=45.0,
                    interpretation="资产负债率 45%",
                ),
            ],
            markdown="## 基本面分析",
        )
        state = {
            "analyst_reports": {"fundamental": report},
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
        }
        result = verify_citations(state)
        assert result["citation_pass"] is False
        assert result["citation_report"]["failed"] == 1

    def test_no_claims_returns_pass(self):
        """没有 claim 时默认通过。"""
        report = AnalystReport(
            agent_name="macro",
            summary="宏观分析",
            plain_conclusion="结论：宏观分析",
            key_findings=["通胀温和"],
            claims=[],
            markdown="## 宏观分析",
        )
        state = {"analyst_reports": {"macro": report}}
        result = verify_citations(state)
        assert result["citation_pass"] is True
        assert result["citation_report"]["total"] == 0

    def test_multiple_reports_claims_aggregated(self):
        """多个 analyst_report 的 claim 被聚合校验。"""
        report_a = AnalystReport(
            agent_name="fundamental",
            summary="基本面",
            plain_conclusion="结论：基本面",
            key_findings=[],
            claims=[
                Claim(
                    claim_type="numerical",
                    source_type="data",
                    field_ref="solvency_metrics.资产负债率.2024",
                    stated_value=40.0,
                    interpretation="",
                ),
            ],
            markdown="",
        )
        report_b = AnalystReport(
            agent_name="technical",
            summary="技术面",
            plain_conclusion="结论：技术面",
            key_findings=[],
            claims=[
                Claim(
                    claim_type="numerical",
                    source_type="data",
                    field_ref="technical_indicators.MA.5.4",
                    stated_value=13.0,
                    interpretation="",
                ),
            ],
            markdown="",
        )
        state = {
            "analyst_reports": {"fundamental": report_a, "technical": report_b},
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
            "technical_indicators": {"MA": {"5": [None, None, None, None, 13.0]}},
        }
        result = verify_citations(state)
        assert result["citation_pass"] is True
        assert result["citation_report"]["total"] == 2

    def test_increments_iteration_count(self):
        """verify_citations 必须递增 iteration_count，否则 after_citation 无限重试（无响应 bug 回归）。"""
        report = AnalystReport(
            agent_name="fundamental",
            summary="基本面分析",
            plain_conclusion="结论：基本面分析",
            key_findings=["资产负债率 45%"],
            claims=[
                Claim(
                    claim_type="numerical",
                    source_type="data",
                    field_ref="solvency_metrics.资产负债率.2024",
                    stated_value=45.0,
                    interpretation="资产负债率 45%",
                ),
            ],
            markdown="## 基本面分析",
        )
        state = {
            "analyst_reports": {"fundamental": report},
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
            "iteration_count": 0,
        }
        result = verify_citations(state)
        assert result["citation_pass"] is False
        assert result["iteration_count"] == 1

    def test_retry_loop_terminates_after_max(self):
        """citation 重试循环必须在 iteration_count 达上限后终止（回归无响应 bug）。"""
        from finance_agent.routing import after_citation

        report = AnalystReport(
            agent_name="fundamental",
            summary="",
            plain_conclusion="结论：分析完成",
            key_findings=[],
            claims=[
                Claim(
                    claim_type="numerical",
                    source_type="data",
                    field_ref="solvency_metrics.资产负债率.2024",
                    stated_value=45.0,
                    interpretation="",
                ),
            ],
            markdown="",
        )
        state = {
            "analyst_reports": {"fundamental": report},
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
        }
        # 模拟图对 verify_citations 的反复调用（每次把返回的 iteration_count 写回 state）
        for expected_count in (1, 2, 3):
            result = verify_citations(state)
            assert result["iteration_count"] == expected_count
            state["iteration_count"] = result["iteration_count"]
            state["citation_pass"] = result["citation_pass"]

        # 重试上限已达 -> after_citation 必须返回 render，不再 retry
        assert state["citation_pass"] is False
        assert after_citation(state) == "render"


class TestUnverifiableRatioScore:
    """spec「UNVERIFIABLE 占比监控」Scenario「占比上报」。"""

    def _run_node(self, claims_payload, state):
        report_dict = {
            "claims": claims_payload,
        }
        state = {**state, "analyst_reports": {"fundamental": report_dict}}
        return verify_citations(state)

    def test_ratio_score_reported(self, monkeypatch):
        from finance_agent.nodes import citation_node

        captured = {}

        class _Client:
            def score_current_trace(self, **kwargs):
                captured[kwargs["name"]] = kwargs

            def update_current_span(self, **kwargs):
                pass

        monkeypatch.setattr(citation_node, "get_langfuse", lambda: _Client())
        claims = [
            {
                "claim_type": "numerical",
                "source_type": "data",
                "field_ref": "solvency_metrics.资产负债率.2024",
                "stated_value": 40.0,
                "interpretation": "",
            },
            {
                "claim_type": "numerical",
                "source_type": "llm_inference",
                "field_ref": "x",
                "stated_value": 1.0,
                "interpretation": "",
            },
        ]
        state = {"solvency_metrics": {"资产负债率": {"2024": 40.0}}}
        self._run_node(claims, state)
        assert "citation_unverifiable_ratio" in captured
        assert captured["citation_unverifiable_ratio"]["value"] == 0.5
        assert captured["citation_pass"]["value"] == 1.0

    def test_ratio_metadata_carries_three_class_counts(self, monkeypatch):
        """spec「UNVERIFIABLE 占比监控」：三类拆报计数随 trace 元数据上报，
        与报告 state 同口径（同一 helper）。"""
        from finance_agent.nodes import citation_node

        captured = {}

        class _Client:
            def score_current_trace(self, **kwargs):
                captured[kwargs["name"]] = kwargs

            def update_current_span(self, **kwargs):
                pass

        monkeypatch.setattr(citation_node, "get_langfuse", lambda: _Client())
        claims = [
            {  # 文本类：entity 不注册
                "claim_type": "entity",
                "source_type": "data",
                "field_ref": "news_list.9.title",
                "stated_value": "不存在的新闻",
                "interpretation": "x",
            },
            {  # 比较型差值：非数值非枚举 stated_value（#123 差值重算后，纯数字申报
                # 走双端重算判 PASS/FAIL，不再 UNVERIFIABLE——见 tests/test_citation.py ①）
                "claim_type": "comparative",
                "source_type": "data",
                "field_ref": "profitability_metrics.ROE.2024",
                "stated_value": "约2.3",
                "interpretation": "2024 年 ROE 较 2023 年低约 2.3",
                "field_ref_b": "profitability_metrics.ROE.2023",
                "stated_value_b": 25.0,
            },
            {  # 未注册：非文本非差值
                "claim_type": "numerical",
                "source_type": "llm_inference",
                "field_ref": "x",
                "stated_value": 1.0,
                "interpretation": "",
            },
        ]
        state = {"profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}}}
        out = self._run_node(claims, state)
        meta = captured["citation_unverifiable_ratio"]["metadata"]
        assert meta["unverifiable_text"] == 1
        assert meta["unverifiable_unregistered"] == 1
        assert meta["unverifiable_comparative_delta"] == 1
        # 报告 state 键与 trace 元数据同口径（同一 helper 产出）
        assert out["citation_unverifiable_text"] == 1
        assert out["citation_unverifiable_unregistered"] == 1
        assert out["citation_unverifiable_comparative_delta"] == 1

    def test_zero_claims_ratio_is_zero(self, monkeypatch):
        from finance_agent.nodes import citation_node

        captured = {}

        class _Client:
            def score_current_trace(self, **kwargs):
                captured[kwargs["name"]] = kwargs

            def update_current_span(self, **kwargs):
                pass

        monkeypatch.setattr(citation_node, "get_langfuse", lambda: _Client())
        verify_citations({"analyst_reports": {}})
        assert captured["citation_unverifiable_ratio"]["value"] == 0.0

    def test_langfuse_failure_warns_not_raises(self, monkeypatch, caplog):
        from finance_agent.nodes import citation_node

        class _Boom:
            def score_current_trace(self, **kwargs):
                raise RuntimeError("langfuse down")

            def update_current_span(self, **kwargs):
                raise RuntimeError("langfuse down")

        monkeypatch.setattr(citation_node, "get_langfuse", lambda: _Boom())
        state = {
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
            "analyst_reports": {
                "a": {
                    "claims": [
                        {
                            "claim_type": "numerical",
                            "source_type": "data",
                            "field_ref": "solvency_metrics.资产负债率.2024",
                            "stated_value": 40.0,
                            "interpretation": "",
                        }
                    ]
                }
            },
        }
        caplog.set_level(logging.WARNING, logger="finance_agent.citation")
        result = verify_citations(state)  # 不抛异常
        assert result["citation_pass"] is True
        # spec：Langfuse 不可用 SHALL 记 WARN（非 debug）且不阻断业务管线
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Langfuse" in r.message for r in warn_records)

    def test_langfuse_unconfigured_warns_not_raises(self, monkeypatch, caplog):
        """get_langfuse 返回 None（未配置）时 SHALL 记 WARN 且不阻断。"""
        from finance_agent.nodes import citation_node

        monkeypatch.setattr(citation_node, "get_langfuse", lambda: None)
        state = {
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
            "analyst_reports": {
                "a": {
                    "claims": [
                        {
                            "claim_type": "numerical",
                            "source_type": "data",
                            "field_ref": "solvency_metrics.资产负债率.2024",
                            "stated_value": 40.0,
                            "interpretation": "",
                        }
                    ]
                }
            },
        }
        caplog.set_level(logging.WARNING, logger="finance_agent.citation")
        result = verify_citations(state)  # 不抛异常
        assert result["citation_pass"] is True
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Langfuse 未配置" in r.message for r in warn_records)


class TestFailRateHistory:
    """citation-retry-policy delta：verify_citations 记录各轮失败率供路由降级。"""

    def _failing_state(self, prior_rates: list | None = None, iteration: int = 1) -> dict:
        report = AnalystReport(
            agent_name="fundamental",
            summary="基本面分析",
            plain_conclusion="结论：基本面分析",
            key_findings=["资产负债率 45%"],
            claims=[
                Claim(
                    claim_type="numerical",
                    source_type="data",
                    field_ref="solvency_metrics.资产负债率.2024",
                    stated_value=45.0,  # 与数据 40.0 不符 → FAIL
                    interpretation="资产负债率 45%",
                ),
            ],
            markdown="## 基本面分析",
        )
        state = {
            "analyst_reports": {"fundamental": report},
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
            "iteration_count": iteration,
        }
        if prior_rates is not None:
            state["citation_fail_rates"] = prior_rates
        return state

    def test_appends_fail_rate_to_history(self):
        result = verify_citations(self._failing_state(prior_rates=[0.35]))
        assert result["citation_fail_rates"] == [0.35, 1.0]
        assert result["iteration_count"] == 2

    def test_pass_records_zero_rate(self):
        state = {
            "analyst_reports": {},  # 零 claim → all_passed=True
            "iteration_count": 1,
        }
        result = verify_citations(state)
        assert result["citation_pass"] is True
        assert result["citation_fail_rates"] == [0.0]

    def test_deescalation_marks_span(self, monkeypatch):
        """降级触发时 verify_citations SHALL 在 span 上留可判读标记。"""
        from finance_agent.nodes import citation_node

        marks: list[dict] = []

        def fake_update_span(**kwargs):
            marks.append(kwargs)

        monkeypatch.setattr(citation_node, "update_current_span", fake_update_span)

        # 上一轮 0.5，本轮 1.0（≥ 0.5×0.8 且轮次未达上限）→ 路由将降级放行
        verify_citations(self._failing_state(prior_rates=[0.5], iteration=1))

        degraded = [m for m in marks if m.get("metadata", {}).get("citation_retry_deescalated")]
        assert degraded, f"降级决策须落 span 标记，实际 marks: {marks}"
        assert degraded[0]["metadata"]["fail_rates"] == [0.5, 1.0]


class TestCitationMinorFail:
    """skip-citation-retry-on-minor-failures：FAIL≤1 且失败率≤5% 免重试标志。"""

    _M2_STATE = {"macro_indicators": {"m2": [{"货币和准货币(M2)-同比增长": 17.37}]}}

    def _claims(self, n_total: int, n_fail: int) -> list[dict]:
        return [
            {
                "claim_type": "numerical",
                "source_type": "data",
                "field_ref": "macro_indicators.m2.0.货币和准货币(M2)-同比增长",
                "stated_value": 16.19 if i < n_fail else 17.37,
                "interpretation": "",
            }
            for i in range(n_total)
        ]

    def _state(self, claims: list[dict]) -> dict:
        return {
            "analyst_reports": {
                "technical": {
                    "agent_name": "technical",
                    "summary": "",
                    "key_findings": [],
                    "claims": claims,
                    "markdown": "",
                }
            },
            **self._M2_STATE,
        }

    def test_minor_fail_sets_flag_and_marks_span(self, monkeypatch):
        """40 条中 1 条 FAIL（2.5% ≤ 5%）：设置 citation_minor_fail 并落 span 标记。"""
        from finance_agent.nodes import citation_node

        marks: list[dict] = []

        def fake_update_span(**kwargs):
            marks.append(kwargs)

        monkeypatch.setattr(citation_node, "update_current_span", fake_update_span)

        result = citation_node.verify_citations(self._state(self._claims(40, 1)))
        assert result["citation_minor_fail"] is True
        marked = [m for m in marks if m.get("metadata", {}).get("citation_minor_fail_deescalated")]
        assert marked, f"轻微失败须落 span 标记，实际 marks: {marks}"

    def test_many_fails_no_minor_flag(self, monkeypatch):
        """失败数/失败率超阈值（13 条全 FAIL，100%）不设 minor_fail。"""
        from finance_agent.nodes import citation_node

        monkeypatch.setattr(citation_node, "update_current_span", lambda **kw: None)
        result = citation_node.verify_citations(self._state(self._claims(13, 13)))
        assert result["citation_minor_fail"] is False

    def test_all_pass_no_flag(self, monkeypatch):
        """全 PASS 不设 minor_fail（渲染由 citation_pass 走既有路径）。"""
        from finance_agent.nodes import citation_node

        monkeypatch.setattr(citation_node, "update_current_span", lambda **kw: None)
        result = citation_node.verify_citations(self._state(self._claims(5, 0)))
        assert result["citation_minor_fail"] is False


def _report(agent: str, claims: list[Claim], markdown: str) -> AnalystReport:
    return AnalystReport(
        agent_name=agent,
        summary=f"{agent} 分析",
        plain_conclusion=f"{agent} 分析结论",
        key_findings=[],
        claims=claims,
        markdown=markdown,
    )


class TestFailBucketAggregation:
    def test_value_mismatch_produces_retry_target_and_feedback(self):
        """基本面 1 条值级 FAIL → 仅基本面进重试目标，反馈带 gt 明细。"""
        good = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=40.0,
            interpretation="资产负债率 40%",
        )
        bad = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2023",
            stated_value=99.0,
            interpretation="2023 年资产负债率 99%",
        )
        state = {
            "analyst_reports": {
                "fundamental": _report("fundamental", [good, bad], "资产负债率 40%"),
                "macro": _report("macro", [], "CPI 温和"),
            },
            "solvency_metrics": {"资产负债率": {"2024": 40.0, "2023": 38.0}},
        }
        result = verify_citations(state)
        assert result["citation_retry_targets"] == ["fundamental"]
        fb = result["citation_retry_feedback"]["fundamental"]
        assert len(fb) == 1
        assert fb[0]["field_ref"] == "solvency_metrics.资产负债率.2023"
        assert fb[0]["ground_truth"] == 38.0
        assert fb[0]["stated_value"] == 99.0
        assert result["citation_fail_buckets"] == {"value_mismatch": 1}

    def test_format_class_fail_no_retry_target(self):
        """纯格式类 FAIL（路径不可解析）→ 无重试目标，桶计数照记。"""
        bad = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.不存在.2024",
            stated_value=40.0,
            interpretation="x",
        )
        state = {
            "analyst_reports": {"fundamental": _report("fundamental", [bad], "x")},
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
        }
        result = verify_citations(state)
        assert result["citation_retry_targets"] == []
        assert result["citation_retry_feedback"] == {}
        assert result["citation_fail_buckets"] == {"path_unresolvable": 1}
        assert result["citation_pass"] is False

    def test_semantic_fail_no_retry_target(self):
        """术语张冠李戴 → 格式类桶，不触发重试。"""
        bad = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="profitability_metrics.毛利率.2024",
            stated_value=45.2,
            interpretation="净利率为 45.2%",
            metric_name="净利率",
        )
        state = {
            "analyst_reports": {"fundamental": _report("fundamental", [bad], "净利率 45.2%")},
            "profitability_metrics": {"毛利率": {"2024": 45.2}},
        }
        result = verify_citations(state)
        assert result["citation_retry_targets"] == []
        assert result["citation_fail_buckets"] == {"semantic_term_mismatch": 1}


class TestCoverageScore:
    def test_coverage_computed_from_markdown(self):
        """markdown 黑数字拉低 citation_coverage。"""
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=40.0,
            interpretation="资产负债率 40%",
        )
        state = {
            "analyst_reports": {
                "fundamental": _report("fundamental", [claim], "资产负债率 40%，营收 10.39 亿")
            },
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
        }
        result = verify_citations(state)
        # 40% 被认领、10.39 亿未认领 → 1/2
        assert result["citation_coverage"] == 0.5

    def test_coverage_full_when_no_dark_numbers(self):
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=40.0,
            interpretation="资产负债率 40%",
        )
        state = {
            "analyst_reports": {"fundamental": _report("fundamental", [claim], "资产负债率 40%")},
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
        }
        result = verify_citations(state)
        assert result["citation_coverage"] == 1.0

    def test_coverage_reported_to_langfuse(self, monkeypatch):
        """citation_coverage 作为 NUMERIC Score 上报；<0.8 产生告警 metadata。"""
        import finance_agent.nodes.citation_node as cn

        calls: list[dict] = []

        class _FakeClient:
            def score_current_trace(self, **kwargs):
                calls.append(kwargs)

            def update_current_span(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr(cn, "get_langfuse", lambda: _FakeClient())
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=40.0,
            interpretation="资产负债率 40%",
        )
        state = {
            "analyst_reports": {
                "fundamental": _report("fundamental", [claim], "资产负债率 40%，营收 10.39 亿")
            },
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
        }
        verify_citations(state)
        score = next(c for c in calls if c.get("name") == "citation_coverage")
        assert score["data_type"] == "NUMERIC"
        assert score["value"] == 0.5
        span = next(
            c for c in calls if "metadata" in c and "citation_coverage_alert" in c["metadata"]
        )
        assert span["metadata"]["citation_coverage_alert"] is True
        assert span["level"] == "WARNING"


class TestAnomalySupplement:
    """refine-citation-coverage-v3 D2：state anomalies 自动补登记（issue #107-B 设计规格）。"""

    def _state(self, anomalies, growth_rates=None):
        return {
            "stock_code": "002412",
            "anomalies": anomalies,
            "growth_rates": growth_rates
            or {
                "solvency": {"净债务/EBITDA": -3.676},
                "efficiency": {"利息覆盖倍数": -0.52},
            },
        }

    def test_supplements_when_value_and_metric_cooccur(self):
        from finance_agent.nodes.citation_node import supplement_anomaly_claims

        md = "偿债能力：净债务/EBITDA 变化率-368%，风险显著。"
        claims = supplement_anomaly_claims(
            md, self._state(["solvency.净债务/EBITDA: 变化率-368%"]), []
        )
        assert len(claims) == 1
        c = claims[0]
        assert c.field_ref == "growth_rates.solvency.净债务/EBITDA"
        assert abs(c.stated_value - (-3.68)) < 1e-9  # -368% → 分数制 -3.68

    def test_metric_name_not_in_sentence_no_supplement(self):
        from finance_agent.nodes.citation_node import supplement_anomaly_claims

        md = "变化率-368%，风险显著。"  # 无指标名共现
        claims = supplement_anomaly_claims(
            md, self._state(["solvency.净债务/EBITDA: 变化率-368%"]), []
        )
        assert claims == []

    def test_fabricated_number_no_supplement(self):
        from finance_agent.nodes.citation_node import supplement_anomaly_claims

        md = "账上资金超过1400亿元。"  # state 无对应 anomaly
        claims = supplement_anomaly_claims(md, self._state([]), [])
        assert claims == []

    def test_dedup_existing_claim_same_field_ref(self):
        from finance_agent.models import Claim  # noqa: F401
        from finance_agent.nodes.citation_node import supplement_anomaly_claims

        existing = [
            {
                "claim_type": "numerical",
                "source_type": "data",
                "field_ref": "growth_rates.solvency.净债务/EBITDA",
                "stated_value": -3.68,
                "interpretation": "人工申报",
            }
        ]
        md = "净债务/EBITDA 变化率-368%，风险显著。"
        claims = supplement_anomaly_claims(
            md, self._state(["solvency.净债务/EBITDA: 变化率-368%"]), existing
        )
        assert claims == []

    def test_supplemented_claim_passes_standard_verification(self):
        """取整感知：stated=-3.68 vs truth=-3.676（0.5pp 内）→ 标准校验 PASS。"""
        from finance_agent.citation import verify_claims
        from finance_agent.nodes.citation_node import supplement_anomaly_claims

        md = "净债务/EBITDA 变化率-368%，风险显著。"
        claims = supplement_anomaly_claims(
            md, self._state(["solvency.净债务/EBITDA: 变化率-368%"]), []
        )
        results = verify_claims(claims, self._state(["solvency.净债务/EBITDA: 变化率-368%"]))
        assert all(r.status == "PASS" for r in results)


class TestD4GrowthSupplement:
    """refine-citation-coverage-v3 D4：growth_rates 全量补登记（吸收 anomalies）。"""

    def test_non_anomaly_growth_supplemented(self):
        """FCF 同比 96.6%：growth_rates 有真值但非 anomaly（|growth|≤0.5 之外的场景
        也可；此处 0.966 即使无 anomaly 字符串也能补）。"""
        from finance_agent.nodes.citation_node import supplement_anomaly_claims

        state = {
            "growth_rates": {"cashflow": {"FCF": 0.966}},
            "anomalies": [],  # 无 anomaly 触发
        }
        md = "FCF 约2.34亿元，同比增长96.6%。"
        claims = supplement_anomaly_claims(md, state, [])
        assert len(claims) == 1
        c = claims[0]
        assert c.field_ref == "growth_rates.cashflow.FCF"
        assert abs(c.stated_value - 0.97) < 1e-9  # 96.6% → 0.966 → 整数渲染 97%

    def test_growth_value_close_rounding_passes_verification(self):
        """补登记 stated=0.97 vs truth=0.966（0.5pp 内）→ 标准校验 PASS。"""
        from finance_agent.citation import verify_claims
        from finance_agent.nodes.citation_node import supplement_anomaly_claims

        state = {"growth_rates": {"cashflow": {"FCF": 0.966}}}
        md = "FCF 同比增长96.6%。"
        claims = supplement_anomaly_claims(md, state, [])
        results = verify_claims(claims, state)
        assert all(r.status == "PASS" for r in results)


class TestReportMarkdownLogged:
    """refine: verify_citations 的报告 markdown 随 span 落库（离线重判解锁）。"""

    def test_report_markdown_in_span_metadata(self, monkeypatch):
        from finance_agent.nodes import citation_node

        captured: dict = {}

        class FakeClient:
            def score_current_trace(self, *a, **kw):
                pass

            def update_current_span(self, metadata=None, **kw):
                captured["metadata"] = metadata

        monkeypatch.setattr(citation_node, "get_langfuse", lambda: FakeClient())
        state = {
            "analyst_reports": {
                "fundamental": {
                    "agent_name": "fundamental",
                    "summary": "x",
                    "markdown": "## 基本面\n\n毛利率 45.2%，营收 10.39 亿",
                    "claims": [
                        {
                            "claim_type": "numerical",
                            "source_type": "data",
                            "field_ref": "profitability_metrics.毛利率.2024",
                            "stated_value": 45.2,
                            "interpretation": "x",
                        }
                    ],
                }
            },
            "iteration_count": 0,
        }
        citation_node.verify_citations(state)
        md = (captured.get("metadata") or {}).get("report_markdown", "")
        assert "毛利率 45.2%" in md
        assert "10.39 亿" in md


class TestCoverageGapFeedbackD6:
    """D6 打回补 claim：普查 unmatched → 按来源分析师打回（复用重试机制）。"""

    def _state(self, md, claims=None):
        return {
            "analyst_reports": {
                "fundamental": {
                    "agent_name": "fundamental",
                    "summary": "x",
                    "markdown": md,
                    "claims": claims
                    or [
                        {
                            "claim_type": "numerical",
                            "source_type": "data",
                            "field_ref": "income_statement.20251231.营业总收入",
                            "stated_value": 172.05e8,
                            "interpretation": "营收",
                        }
                    ],
                }
            },
            "iteration_count": 0,
        }

    def test_unmatched_number_sets_gap_and_retry_target(self):
        from finance_agent.nodes.citation_node import verify_citations

        # 1400亿：claims 无认领、D2/D5 不补 → 打回 fundamental
        md = "账上货币资金合计超过1400亿元，财务稳健。"
        out = verify_citations(self._state(md))
        assert out["citation_coverage_gap"] is True
        assert "fundamental" in out["citation_retry_targets"]
        fb = (out["citation_retry_feedback"] or {}).get("fundamental") or []
        assert any("1400亿" in str(i.get("raw", "")) for i in fb)

    def test_no_unmatched_no_gap(self):
        from finance_agent.nodes.citation_node import verify_citations

        md = "2025 年营业总收入 172.05 亿元。"
        out = verify_citations(
            self._state(
                md,
                [
                    {
                        "claim_type": "numerical",
                        "source_type": "data",
                        "field_ref": "income_statement.20251231.营业总收入",
                        "stated_value": 172.05e8,
                        "interpretation": "营收 172.05 亿",
                    }
                ],
            )
        )
        assert out["citation_coverage_gap"] is False


class TestAfterCitationCoverageGap:
    """D6：after_citation 对 coverage 缺口走重试（共享迭代上限）。"""

    def test_coverage_gap_routes_retry(self, monkeypatch):
        from finance_agent import routing

        monkeypatch.setattr(routing, "CITATION_AUTO_RETRY_ENABLED", True)
        from finance_agent.routing import after_citation

        assert (
            after_citation(
                {
                    "citation_pass": True,  # 值级全过但 coverage 有缺口
                    "citation_coverage_gap": True,
                    "citation_retry_targets": ["fundamental"],
                    "iteration_count": 0,
                    "citation_fail_rates": [],
                }
            )
            == "retry"
        )

    def test_gap_respected_iteration_cap(self):
        from finance_agent.routing import after_citation

        assert (
            after_citation(
                {
                    "citation_pass": True,
                    "citation_coverage_gap": True,
                    "citation_retry_targets": ["fundamental"],
                    "iteration_count": 3,
                    "citation_fail_rates": [],
                }
            )
            == "render"
        )


class TestDirectionFeedback:
    """ehr-style-claim-direction：direction_mismatch 定向重试反馈 + 打回 direction 提示。"""

    def test_direction_mismatch_retry_feedback_carries_truth_sign_and_example(self):
        good = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=40.0,
            interpretation="资产负债率 40%",
            direction="positive",
        )
        bad = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="growth_rates.profitability.roe.yoy.2024",
            stated_value=12.0,
            interpretation="ROE 同比表现",
            metric_name="ROE",
            period="2024",
            direction="positive",
        )
        state = {
            "analyst_reports": {
                "fundamental": _report("fundamental", [good, bad], "资产负债率 40%"),
            },
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
            "growth_rates": {"profitability": {"roe": {"yoy": {"2024": -5.0}}}},
        }
        result = verify_citations(state)
        assert "fundamental" in result["citation_retry_targets"]
        fb = result["citation_retry_feedback"]["fundamental"]
        d = [i for i in fb if i.get("bucket") == "direction_mismatch"]
        assert len(d) == 1
        assert d[0]["ground_truth"] == -5.0
        assert "负" in d[0]["direction_hint"]
        assert "direction=negative" in d[0]["direction_hint"]

    def test_coverage_gap_feedback_carries_direction_hint(self):
        md = "账上货币资金合计超过1400亿元，财务稳健。"
        state = {
            "analyst_reports": {
                "fundamental": _report("fundamental", [], md),
            },
            "income_statement": {"20251231": {"营业总收入": 172.05e8}},
        }
        out = verify_citations(state)
        assert out["citation_coverage_gap"] is True
        fb = (out["citation_retry_feedback"] or {}).get("fundamental") or []
        gaps = [i for i in fb if i.get("kind") == "coverage_gap"]
        assert gaps, "coverage_gap 条目应存在"
        assert all("direction" in i.get("direction_hint", "") for i in gaps)


class TestSurgicalRepair:
    """surgical-citation-repair：value_mismatch 稀疏失败的单点修复分流。"""

    def _state(self, claims, md):
        return {
            "analyst_reports": {"fundamental": _report("fundamental", claims, md)},
            "solvency_metrics": {"资产负债率": {"2024": 40.0, "2023": 38.0}},
        }

    def _bad(self, stated=99.0):
        return Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2023",
            stated_value=stated,
            interpretation="2023 年资产负债率 99.0%",
        )

    def test_sparse_fail_repairs_and_reverifies(self, monkeypatch):
        md = "2023 年资产负债率为 99.0%，杠杆水平异常偏高。"
        calls = []

        def fake_repair(markdown, failures, llm_config=None):
            calls.append(failures)
            return markdown.replace("99.0%", "38.0%"), [
                {
                    "agent": "fundamental",
                    "field_ref": "x",
                    "ground_truth": 38.0,
                    "repaired": True,
                    "updated_claim": self._bad(stated=38.0).model_copy(
                        update={"interpretation": "2023 年资产负债率为 38.0%，杠杆水平异常偏高。"}
                    ),
                }
            ]

        monkeypatch.setattr(citation_node, "repair_claims", fake_repair)
        out = verify_citations(self._state([self._bad()], md))
        assert len(calls) == 1
        assert out["citation_pass"] is True
        assert out["citation_retry_targets"] == []
        assert out["value_mismatch_repaired"] == 1
        # 原桶保留（prompt 归因信号不吞）
        assert out["citation_fail_buckets"] == {"value_mismatch": 1}
        # 修复后 markdown 回填 state 供渲染/下游
        rpt = out["analyst_reports"]["fundamental"]
        md2 = rpt.markdown if hasattr(rpt, "markdown") else rpt["markdown"]
        assert "38.0%" in md2 and "99.0%" not in md2

    def test_per_claim_accounting_when_unrelated_fail_blocks_all_passed(self, monkeypatch):
        """incident 029 处置（任务 3）：修复改对了目标 claim，但同分析师另有无关 FAIL
        （path_unresolvable，all_passed=False）——目标 claim 仍须计入
        value_mismatch_repaired_claims；旧 all_passed 口径字段保持 0（deprecated 对照）。"""
        md = "2023 年资产负债率为 99.0%，杠杆水平异常偏高。"
        unrelated = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.不存在的指标.2023",
            stated_value=1.0,
            interpretation="无关指标（制造非 value_mismatch 的残余 FAIL）",
        )

        def fake_repair(markdown, failures, llm_config=None):
            return markdown.replace("99.0%", "38.0%"), [
                {
                    "agent": "fundamental",
                    "field_ref": "x",
                    "ground_truth": 38.0,
                    "repaired": True,
                    "updated_claim": self._bad(stated=38.0).model_copy(
                        update={"interpretation": "2023 年资产负债率为 38.0%，杠杆水平异常偏高。"}
                    ),
                }
            ]

        monkeypatch.setattr(citation_node, "repair_claims", fake_repair)
        out = verify_citations(self._state([self._bad(), unrelated], md))
        # 新口径：目标 claim 修好即计（不被无关 FAIL 拦截）
        assert out["value_mismatch_repaired_claims"] == 1
        # 旧口径（deprecated）：all_passed=False → 0
        assert out["value_mismatch_repaired"] == 0
        # 行为不变：仍有残余 FAIL → 该分析师照走全量定向重试
        assert "fundamental" in out["citation_retry_targets"]

    def test_dense_fail_falls_back_to_full_retry(self, monkeypatch):
        claims = [self._bad(99.0 + i) for i in range(4)]
        md = "资产负债率 99.0%、99.1%、99.2%、99.3% 均异常。"

        def no_repair(markdown, failures, llm_config=None):
            raise AssertionError("≥3 处不应触发单点修复")

        monkeypatch.setattr(citation_node, "repair_claims", no_repair)
        out = verify_citations(self._state(claims, md))
        assert out["citation_retry_targets"] == ["fundamental"]
        assert out["citation_pass"] is False

    def test_repaired_still_failing_falls_back_not_repaired_twice(self, monkeypatch):
        md = "2023 年资产负债率为 99.0%，杠杆水平异常偏高。"
        calls = []

        def bad_repair(markdown, failures, llm_config=None):
            calls.append(1)
            # 修复了但没修对（数字原样）：重校验仍 value_mismatch
            return markdown, [
                {
                    "agent": "fundamental",
                    "field_ref": "x",
                    "ground_truth": 38.0,
                    "repaired": True,
                    "updated_claim": self._bad(stated=99.0),
                }
            ]

        monkeypatch.setattr(citation_node, "repair_claims", bad_repair)
        out = verify_citations(self._state([self._bad()], md))
        assert len(calls) == 1, "同处不得二次单点修复"
        assert out["citation_pass"] is False
        assert out["citation_retry_targets"] == ["fundamental"]

    def test_repair_module_crash_does_not_break_verification(self, monkeypatch):
        md = "2023 年资产负债率为 99.0%，杠杆水平异常偏高。"

        def boom(markdown, failures, llm_config=None):
            raise RuntimeError("repair down")

        monkeypatch.setattr(citation_node, "repair_claims", boom)
        out = verify_citations(self._state([self._bad()], md))
        assert out["citation_pass"] is False
        assert out["citation_retry_targets"] == ["fundamental"]
        assert out["citation_fail_buckets"] == {"value_mismatch": 1}


class TestSurgicalRepairStagnation:
    """surgical-citation-repair 2.5：停滞降级对单点修复轮同样生效。"""

    def test_stagnation_renders_even_when_repair_applicable(self, monkeypatch):
        import finance_agent.nodes.citation_node as citation_node

        md = "2023 年资产负债率为 99.0%，杠杆水平异常偏高。"
        state = {
            "analyst_reports": {
                "fundamental": _report(
                    "fundamental",
                    [
                        Claim(
                            claim_type="numerical",
                            source_type="data",
                            field_ref="solvency_metrics.资产负债率.2023",
                            stated_value=99.0,
                            interpretation="2023 年资产负债率 99.0%",
                        )
                    ],
                    md,
                )
            },
            "solvency_metrics": {"资产负债率": {"2024": 40.0, "2023": 38.0}},
            "citation_fail_rates": [0.35, 0.31],
            "iteration_count": 1,
        }

        def fake_repair(markdown, failures, llm_config=None):
            return markdown.replace("99.0%", "38.0%"), [
                {
                    "agent": "fundamental",
                    "field_ref": "x",
                    "ground_truth": 38.0,
                    "repaired": True,
                    "updated_claim": Claim(
                        claim_type="numerical",
                        source_type="data",
                        field_ref="solvency_metrics.资产负债率.2023",
                        stated_value=38.0,
                        interpretation="2023 年资产负债率为 38.0%，杠杆水平异常偏高。",
                    ),
                }
            ]

        monkeypatch.setattr(citation_node, "repair_claims", fake_repair)
        out = verify_citations(state)
        from finance_agent.routing import after_citation

        # 修复成功（citation_pass=True）→ render；若未修复，停滞判定同样 render
        route = after_citation(out)
        assert route == "render"
        # 停滞序列透传（未被修复轮清空或改写）
        assert out["citation_fail_rates"] == [0.35, 0.31, 0.0]


class TestSurgicalRepairIntegration:
    """surgical-citation-repair 4.2 全链路（真实 repair_claims + mock LLM 契约）：
    修复调用 → 回填 → 重校验 PASS → value_mismatch_repaired 遥测。"""

    def test_real_repair_chain_reverify_and_telemetry(self, monkeypatch):
        from finance_agent.nodes import citation_node, citation_repair

        md = "2023 年资产负债率为 99.0%，杠杆水平异常偏高。"
        claims = [
            Claim(
                claim_type="numerical",
                source_type="data",
                field_ref="solvency_metrics.资产负债率.2023",
                stated_value=99.0,
                interpretation="2023 年资产负债率 99.0%",
            )
        ]
        state = {
            "analyst_reports": {"fundamental": _report("fundamental", claims, md)},
            "solvency_metrics": {"资产负债率": {"2021": 36.0, "2022": 37.0, "2023": 38.0}},
        }

        # 真实 repair_claims 的 LLM 输出契约：report 修正值 + 整句回填
        seen = {}

        def fake_llm(prompt, system, **kw):
            seen["system"] = system
            seen["llm_config"] = kw.get("llm_config")
            return {
                "repaired_sentence": "2023 年资产负债率为 38.0%，杠杆水平异常偏高。",
                "stated_value": 38.0,
            }

        monkeypatch.setattr(citation_repair, "call_llm_for_json", fake_llm)
        out = citation_node.verify_citations(state)

        assert seen["llm_config"] is None
        assert "单点修复器" in seen["system"]  # 走真实单点修复系统模板
        assert out["value_mismatch_repaired"] == 1
        assert out["citation_pass"] is True
        assert out["citation_retry_targets"] == []
        rpt = out["analyst_reports"]["fundamental"]
        md2 = rpt.markdown if hasattr(rpt, "markdown") else rpt["markdown"]
        assert "38.0%" in md2 and "99.0%" not in md2


class TestRetryNoProgress:
    """阶段 0 停滞保护（incident 026）：重试启用态下，目标分析师重跑后输出内容
    不变（哈希一致）→ 置 citation_retry_no_progress，路由立即放行，不等失败率
    停滞判定——重跑分析师不改变表述时继续重跑纯属烧钱。"""

    def _claims(self) -> list[Claim]:
        # ≥3 处值级失败才进全量重试目标（<3 走 surgical 单点修复分流）
        return [
            Claim(
                claim_type="numerical",
                source_type="data",
                field_ref=f"solvency_metrics.资产负债率.{year}",
                stated_value=99.0,
                interpretation=f"{year} 年资产负债率 99%",
            )
            for year in ("2021", "2022", "2023")
        ]

    def test_first_round_records_prev_hash(self):
        report = _report("fundamental", self._claims(), "资产负债率 99%")
        state = {
            "analyst_reports": {"fundamental": report, "macro": _report("macro", [], "CPI 温和")},
            "solvency_metrics": {"资产负债率": {"2021": 36.0, "2022": 37.0, "2023": 38.0}},
        }
        out = verify_citations(state)
        import hashlib

        expected = hashlib.md5(report.markdown.encode("utf-8"), usedforsecurity=False).hexdigest()
        assert out["citation_retry_prev_hash"] == {"fundamental": expected}
        assert out["citation_retry_no_progress"] is False

    def test_unchanged_rerun_sets_no_progress(self):
        import hashlib

        report = _report("fundamental", self._claims(), "资产负债率 99%")
        h = hashlib.md5(report.markdown.encode("utf-8"), usedforsecurity=False).hexdigest()
        state = {
            "analyst_reports": {"fundamental": report, "macro": _report("macro", [], "CPI 温和")},
            "solvency_metrics": {"资产负债率": {"2021": 36.0, "2022": 37.0, "2023": 38.0}},
            "citation_retry_prev_hash": {"fundamental": h},
        }
        out = verify_citations(state)
        assert out["citation_retry_no_progress"] is True

    def test_changed_rerun_keeps_retrying(self):
        report = _report("fundamental", self._claims(), "改写后的资产负债率 99%")
        state = {
            "analyst_reports": {"fundamental": report, "macro": _report("macro", [], "CPI 温和")},
            "solvency_metrics": {"资产负债率": {"2021": 36.0, "2022": 37.0, "2023": 38.0}},
            "citation_retry_prev_hash": {"fundamental": "旧哈希"},
        }
        out = verify_citations(state)
        assert out["citation_retry_no_progress"] is False

    def test_no_progress_flag_renders_in_routing(self, monkeypatch):
        from finance_agent import routing

        monkeypatch.setattr(routing, "CITATION_AUTO_RETRY_ENABLED", True)
        assert (
            routing.after_citation(
                {
                    "citation_pass": False,
                    "citation_retry_targets": ["fundamental"],
                    "citation_retry_no_progress": True,
                    "iteration_count": 1,
                }
            )
            == "render"
        )


class TestAutoClaim:
    """阶段 4（incident 026）：正文未认领数字在注册根键中唯一匹配 → 自动合成 claim；
    多匹配/零匹配不合成（防歧义洗白）。"""

    def test_unique_match_synthesizes_and_covers(self):
        report = _report("fundamental", [], "公司自由现金流110.6亿元，现金充沛。")
        state = {
            "analyst_reports": {"fundamental": report},
            "cashflow_metrics": {"自由现金流": {"2025": 1.106e10}},
        }
        out = verify_citations(state)
        assert out["citation_coverage"] == 1.0
        assert out["auto_claims"] == 1

    def test_ambiguous_match_not_synthesized(self):
        report = _report("fundamental", [], "公司自由现金流110.6亿元，现金充沛。")
        state = {
            "analyst_reports": {"fundamental": report},
            "cashflow_metrics": {"自由现金流": {"2025": 1.106e10, "2024": 1.106e10}},
        }
        out = verify_citations(state)
        assert out["auto_claims"] == 0
        assert out["citation_coverage"] < 1.0


class TestGateLayers:
    """阶段 5：门禁三层分置 + 指标拆报——残余 FAIL 阻断、覆盖警告、文本/未注册
    UNVERIFIABLE 分开计数、归一转 PASS 单独量化。"""

    def test_layer_keys_and_counts(self):
        # ≥3 处值级失败走全量 FAIL（<3 走单点修复分支，会用真值回填）
        bads = [
            Claim(
                claim_type="numerical",
                source_type="data",
                field_ref=f"solvency_metrics.资产负债率.{y}",
                stated_value=99.0,
                interpretation=f"{y} 年资产负债率 99%",
            )
            for y in ("2021", "2022", "2023")
        ]
        ghost = Claim(
            claim_type="entity",
            source_type="data",
            field_ref="news_list.9.title",
            stated_value="不存在的新闻",
            interpretation="x",
        )
        report = _report("fundamental", [*bads, ghost], "资产负债率 99%")
        state = {
            "analyst_reports": {"fundamental": report},
            "solvency_metrics": {"资产负债率": {"2021": 36.0, "2022": 37.0, "2023": 38.0}},
        }
        out = verify_citations(state)
        assert out["citation_blocked"] is True
        assert out["citation_analyst_true_fail"] == 3
        assert out["citation_unverifiable_text"] == 1
        assert out["citation_unverifiable_unregistered"] == 0
        assert out["citation_verifier_normalized"] == 0

    def test_comparative_delta_counted_and_split_from_unregistered(self):
        """非数值非枚举差值：独立计数，且不再计入 unregistered（拆报三类不重叠）。

        #123 差值重算后纯数字申报走双端重算（PASS/FAIL，见 tests/test_citation.py ①）；
        本测试守的是非数值申报的显式降级桶（comparative_delta_unregistered）在 node
        层的独立计数与拆报口径。
        """
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="profitability_metrics.ROE.2024",
            stated_value="约2.3",
            interpretation="2024 年 ROE 较 2023 年低约 2.3",
            field_ref_b="profitability_metrics.ROE.2023",
            stated_value_b=25.0,
        )
        report = _report("fundamental", [claim], "ROE 较上年下滑 2.3")
        state = {
            "analyst_reports": {"fundamental": report},
            "profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}},
        }
        out = verify_citations(state)
        assert out["citation_unverifiable_comparative_delta"] == 1
        assert out["citation_unverifiable_unregistered"] == 0
        assert out["citation_blocked"] is False

    def test_normalized_count_counts_unit_fix(self):
        from finance_agent.citation import Claim as NumericClaim

        df = pd.DataFrame([{"报告日": "20251231", "归属于母公司的净利润": 5.040734e9}])
        c = NumericClaim(
            claim_type="numerical",
            source_type="data",
            field_ref="income_statement.20251231.归属于母公司的净利润",
            stated_value=50.40734,
            interpretation="2025年归母净利润50.41亿元",
            direction="flat",
        )
        report = _report("fundamental", [c], "2025年归母净利润50.41亿元")
        state = {"analyst_reports": {"fundamental": report}, "income_statement": df}
        out = verify_citations(state)
        assert out["citation_blocked"] is False
        assert out["citation_verifier_normalized"] == 1
