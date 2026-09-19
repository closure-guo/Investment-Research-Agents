"""TDD tests for citation.py — Claim schema 扩展与 FAIL 分桶。"""

from finance_agent.citation import Claim, verify_claims


class TestClaimSchemaCompat:
    """D5：旧格式 claim（无 metric_name/period）反序列化兼容。"""

    def test_old_claim_without_new_fields(self):
        claim = Claim.model_validate(
            {
                "claim_type": "numerical",
                "source_type": "data",
                "field_ref": "solvency_metrics.资产负债率.2024",
                "stated_value": 40.0,
                "interpretation": "资产负债率为 40%",
            }
        )
        assert claim.metric_name is None
        assert claim.period is None

    def test_new_fields_accepted(self):
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="profitability_metrics.毛利率.2024",
            stated_value=45.2,
            interpretation="毛利率约 45.2%",
            metric_name="毛利率",
            period="2024",
        )
        assert claim.metric_name == "毛利率"
        assert claim.period == "2024"


class TestFailBuckets:
    def _state(self) -> dict:
        return {"solvency_metrics": {"资产负债率": {"2024": 40.0}}}

    def test_value_mismatch_bucket(self):
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=45.0,
            interpretation="x",
        )
        (r,) = verify_claims([claim], self._state())
        assert r.status == "FAIL"
        assert r.bucket == "value_mismatch"

    def test_path_unresolvable_bucket(self):
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.不存在.2024",
            stated_value=40.0,
            interpretation="x",
        )
        (r,) = verify_claims([claim], self._state())
        assert r.status == "FAIL"
        assert r.bucket == "path_unresolvable"

    def test_pass_has_no_bucket(self):
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=40.0,
            interpretation="x",
        )
        (r,) = verify_claims([claim], self._state())
        assert r.status == "PASS"
        assert r.bucket is None

    def test_unverifiable_has_no_bucket(self):
        claim = Claim(
            claim_type="numerical",
            source_type="llm_inference",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=40.0,
            interpretation="x",
        )
        (r,) = verify_claims([claim], self._state())
        assert r.status == "UNVERIFIABLE"
        assert r.bucket is None

    def test_comparative_wrong_direction_is_value_mismatch(self):
        state = {"profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}}}
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="profitability_metrics.ROE.2024",
            stated_value="less_than",
            interpretation="2024 年 ROE 低于 2023 年",
            field_ref_b="profitability_metrics.ROE.2023",
            stated_value_b=25.0,
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "FAIL"
        assert r.bucket == "value_mismatch"

    def test_comparative_numeric_delta_unverifiable_with_gap(self):
        """非枚举 stated_value（差值数字）→ UNVERIFIABLE + 独立桶 + 覆盖缺口。"""
        state = {"profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}}}
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="profitability_metrics.ROE.2024",
            stated_value=2.3,
            interpretation="2024 年 ROE 较 2023 年低约 2.3",
            field_ref_b="profitability_metrics.ROE.2023",
            stated_value_b=25.0,
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "UNVERIFIABLE"
        assert r.bucket == "comparative_delta_unregistered"
        assert r.coverage_gap is True

    def test_comparative_delta_not_recomputed(self):
        """差值再离谱也不判 FAIL（本变更不新增验证语义）——仍是 UNVERIFIABLE。"""
        state = {"profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}}}
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="profitability_metrics.ROE.2024",
            stated_value=999.0,
            interpretation="x",
            field_ref_b="profitability_metrics.ROE.2023",
            stated_value_b=25.0,
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "UNVERIFIABLE"
        assert r.bucket == "comparative_delta_unregistered"

    def test_comparative_enum_path_unchanged(self):
        """三枚举路径行为不变：方向正确 PASS。"""
        state = {"profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}}}
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="profitability_metrics.ROE.2024",
            stated_value="greater_than",
            interpretation="2024 年 ROE 高于 2023 年",
            field_ref_b="profitability_metrics.ROE.2023",
            stated_value_b=25.0,
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "PASS"
        assert r.bucket is None

    def test_event_not_found_is_unverifiable_text(self):
        claim = Claim(
            claim_type="temporal",
            source_type="event",
            field_ref="不存在的事件",
            stated_value="",
            interpretation="x",
        )
        (r,) = verify_claims([claim], {"key_events": []})
        # rework-citation-gate-attribution 阶段 3：文本/事件 claim 未命中判 UNVERIFIABLE(text)，
        # 不进 FAIL 分母、不计覆盖缺口（incident 026：key_events 14/14 曾恒为不可验/误 FAIL）
        assert r.status == "UNVERIFIABLE"
        assert r.bucket is None and r.coverage_gap is False
