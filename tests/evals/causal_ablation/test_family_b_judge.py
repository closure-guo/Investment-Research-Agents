"""族 B 判定腿测试（零 LLM）：prompt 契约、解析容错、成本闸门、盲评协议、校准材料。"""

from __future__ import annotations

import json

import pytest

from evals.causal_ablation import family_b_judge as fj


def _judge_returning(payload: dict | str):
    def fn(prompt: str) -> str:
        return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    return fn


class TestPrompts:
    def test_b1_prompt_carries_decision_context(self):
        prompt = fj.b1_prompt(
            {
                "risk_point": "存货跌价准备计提不充分",
                "decision_action": "hold",
                "decision_reasoning": "潜在减值未反映",
                "evidence_refs": [{"claim": "空头排列", "source": "technical"}],
                "fm_decision": "approve",
                "fm_reasoning": "同意",
            }
        )
        for token in ("存货跌价", "hold", "潜在减值", "空头排列", "approve"):
            assert token in prompt
        assert '"absorbed"' in prompt  # 输出契约在 prompt 里

    def test_b2_prompt_carries_both_sides(self):
        prompt = fj.b2_prompt({"rebutted_point": "需求崩塌", "rebuttal_text": "基数效应"})
        assert "需求崩塌" in prompt and "基数效应" in prompt

    def test_b5_prompt_asks_decision_value_not_length(self):
        prompt = fj.b5_prompt("AAA", "BBB")
        assert "决策辅助价值" in prompt and "不看篇幅" in prompt


class TestB1B2Runs:
    def _rows(self, n: int, ticker: str = "600519") -> list[dict]:
        return [
            {"unit_id": f"{ticker}::b1::{i}", "ticker": ticker, "risk_point": f"p{i}"}
            for i in range(n)
        ]

    def test_labels_and_reasons_recorded(self):
        out = fj.run_b1_judgments(
            self._rows(2), llm_fn=_judge_returning({"absorbed": True, "reason": "理由在决策里"})
        )
        assert [r["judge_label"] for r in out] == [True, True]
        assert out[0]["judge_reason"] == "理由在决策里"
        assert out[0]["judge_parse_failed"] is False

    def test_per_ticker_cap_is_deterministic(self):
        rows = self._rows(10)
        out = fj.run_b1_judgments(rows, llm_fn=_judge_returning({"absorbed": False}), per_ticker=3)
        assert [r["unit_id"] for r in out] == ["600519::b1::0", "600519::b1::1", "600519::b1::2"]

    def test_parse_failure_is_none_not_false(self):
        """解析失败不得记 false（那是「未吸收」的意思，与「没判定」是两回事）。"""
        out = fj.run_b1_judgments(self._rows(1), llm_fn=lambda _p: "不是 JSON")
        assert out[0]["judge_label"] is None
        assert out[0]["judge_parse_failed"] is True

    def test_exception_is_recorded_as_parse_failure(self):
        def boom(_p: str) -> str:
            raise RuntimeError("gateway down")

        out = fj.run_b1_judgments(self._rows(1), llm_fn=boom)
        assert out[0]["judge_parse_failed"] is True and out[0]["judge_label"] is None

    def test_b2_uses_corrected_key(self):
        out = fj.run_b2_judgments(
            [{"unit_id": "u", "ticker": "600519", "rebutted_point": "a", "rebuttal_text": "b"}],
            llm_fn=_judge_returning({"corrected": True, "reason": "让步了"}),
        )
        assert out[0]["judge_label"] is True


class TestB5Pairwise:
    def _pair(self, *, key: str = "600519") -> dict:
        return {
            "pair_key": key,
            "ticker": "600519",
            "report_a_id": "analysts",
            "report_b_id": "full",
            "report_a": "报告甲",
            "report_b": "报告乙",
        }

    def test_three_votes_majority(self):
        votes = iter([{"winner": "A"}, {"winner": "A"}, {"winner": "B"}])
        out = fj.run_b5_pairwise([self._pair()], llm_fn=lambda _p: json.dumps(next(votes)))
        assert len(out[0]["votes"]) == 3
        assert out[0]["verdict"] == out[0]["votes"][0]  # 2:1 多数

    def test_verdict_maps_back_to_real_arm(self):
        """A/B 是展示位，判定须映射回真实臂（否则位置随机化会污染结论）。"""
        out = fj.run_b5_pairwise(
            [self._pair()], llm_fn=_judge_returning({"winner": "A", "reason": "r"})
        )
        assert out[0]["votes"] == [out[0]["position_first"]] * 3
        assert out[0]["verdict"] == out[0]["position_first"]

    def test_tie_and_failed_votes(self):
        calls = {"n": 0}

        def flaky(_p: str) -> str:
            # 前两次都返回垃圾：`_ask_json` 失败会重试一次，只坏第一次会被重试救回来
            calls["n"] += 1
            return "垃圾" if calls["n"] <= 2 else json.dumps({"winner": "tie"})

        out = fj.run_b5_pairwise([self._pair()], llm_fn=flaky)
        assert out[0]["votes"] == ["tie", "tie"]  # 失败票不入票箱
        assert out[0]["judge_parse_failed"] is True  # 少一票如实记
        assert out[0]["verdict"] == "tie"

    def test_vote_reasons_and_majority_reason_are_carried(self):
        """B5 装置缺陷修复（2026-09-18）：三票理由落盘——vote_reasons 与 votes 对齐，
        judge_reason = 多数派第一票的理由；缺失时 None。校准材料须自证可读。"""
        payload = {"winner": "A", "reason": "报告A的依据可核对"}

        def fn(prompt: str) -> str:
            return json.dumps(payload, ensure_ascii=False)

        rows = fj.run_b5_pairwise(
            [
                {
                    "unit_id": "600000::b5",
                    "pair_key": "600000",
                    "ticker": "600000",
                    "report_a_id": "through_trader",
                    "report_b_id": "full",
                    "report_a": "AA",
                    "report_b": "BB",
                }
            ],
            llm_fn=fn,
            votes=2,
        )
        row = rows[0]
        first, _ = fj.pairwise_assign("through_trader", "full", pair_key="600000")
        assert row["verdict"] == first  # 评审答 A = 先展示的那份
        assert row["vote_reasons"] == ["报告A的依据可核对", "报告A的依据可核对"]
        assert row["judge_reason"] == "报告A的依据可核对"

    def test_missing_reason_is_none_not_crash(self):
        def fn(prompt: str) -> str:
            return json.dumps({"winner": "A"}, ensure_ascii=False)

        rows = fj.run_b5_pairwise(
            [
                {
                    "unit_id": "600000::b5",
                    "pair_key": "600000",
                    "ticker": "600000",
                    "report_a_id": "through_trader",
                    "report_b_id": "full",
                    "report_a": "AA",
                    "report_b": "BB",
                }
            ],
            llm_fn=fn,
            votes=1,
        )
        assert rows[0]["vote_reasons"] == [None]
        assert rows[0]["judge_reason"] is None

    def test_unit_id_is_preserved_for_caching(self):
        """判定结果要能按 unit_id 回读缓存（丢掉 id 会让已花的判定调用匹配不上）。"""
        out = fj.run_b5_pairwise(
            [dict(self._pair(), unit_id="600519::b5")],
            llm_fn=_judge_returning({"winner": "A"}),
        )
        assert out[0]["unit_id"] == "600519::b5"

    def test_position_is_deterministic_per_pair_key(self):
        first = fj.run_b5_pairwise([self._pair()], llm_fn=_judge_returning({"winner": "A"}))
        second = fj.run_b5_pairwise([self._pair()], llm_fn=_judge_returning({"winner": "A"}))
        assert first[0]["position_first"] == second[0]["position_first"]


class TestCalibrationMaterial:
    def test_rows_carry_material_and_empty_human_column(self):
        judged = [
            {
                "unit_id": "600519::b1::0",
                "ticker": "600519",
                "risk_point": "存货跌价",
                "judge_label": True,
                "judge_reason": "r",
            }
        ]
        rows = fj.calibration_rows(judged, material_key="risk_point")
        assert rows[0]["human_label(与判定一致?是/否)"] == ""
        assert rows[0]["material"] == "存货跌价"

    def test_gate_verdict_reports_facts_only(self):
        judged = [
            {"ticker": "a", "judge_label": True, "judge_parse_failed": False},
            {"ticker": "a", "judge_label": False, "judge_parse_failed": False},
            {"ticker": "b", "judge_label": None, "judge_parse_failed": True},
        ]
        verdict = fj.gate_verdict(judged, method="nli")
        assert verdict["rows"] == 3 and verdict["labeled"] == 2
        assert verdict["parse_failed"] == 1
        assert verdict["positive_rate"] == pytest.approx(0.5)
        assert "provisional" in verdict["status"]

    def test_summarize_rate_none_when_nothing_labeled(self):
        got = fj.summarize(
            [{"ticker": "a", "judge_label": None, "judge_parse_failed": True}], key="absorbed"
        )
        assert got["per_ticker"]["a"]["rate"] is None  # 不得记 0


class TestSpreadSampling:
    """成本闸门不得引入顺序偏倚：带相似度时按相似度等距抽样（覆盖两端极端样本）。"""

    def _rows(self, n: int) -> list[dict]:
        return [
            {
                "unit_id": f"u{i}",
                "ticker": "600519",
                "risk_point": f"p{i}",
                "max_similarity": i / 10,
            }
            for i in range(n)
        ]

    def test_evenly_spread_over_similarity(self):
        out = fj.run_b1_judgments(
            self._rows(10), llm_fn=lambda _p: json.dumps({"absorbed": True}), per_ticker=3
        )
        sims = [r["max_similarity"] for r in out]
        assert sims == [0.0, 0.4, 0.9]  # 两端 + 居中（min4.5→round 4），而不是前 3 个

    def test_fewer_rows_than_limit_keeps_all(self):
        out = fj.run_b1_judgments(
            self._rows(2), llm_fn=lambda _p: json.dumps({"absorbed": False}), per_ticker=4
        )
        assert len(out) == 2

    def test_without_similarity_falls_back_to_prefix(self):
        rows = [{"unit_id": f"u{i}", "ticker": "t", "risk_point": str(i)} for i in range(5)]
        out = fj.run_b2_judgments(
            [dict(r, rebutted_point="a", rebuttal_text="b") for r in rows],
            llm_fn=lambda _p: json.dumps({"corrected": True}),
            per_ticker=2,
        )
        assert [r["unit_id"] for r in out] == ["u0", "u1"]


class TestPromptV2Archived:
    """v2 归档（2026-09-18 验证未过门 0.825/0.750/0.690）：默认回 v1，v2 只作单变量迭代基线。"""

    ROW = {
        "unit_id": "600000::b1::0",
        "ticker": "600000",
        "risk_point": "机构资金持续撤离，趋势未扭转",
        "decision_action": "hold",
        "decision_reasoning": "维持观望",
        "evidence_refs": [{"claim": "空头排列", "source": "technical"}],
        "fm_decision": "approve",
        "fm_reasoning": "同意",
        "reference_points": ["营收零增长", "利润微增"],
    }

    def test_v1_is_default_rubric_and_prompt(self):
        assert fj.B1_RUBRIC == "b1-v1" and fj.B2_RUBRIC == "b2-v1"
        prompt = fj.b1_prompt(self.ROW)
        assert '"absorbed"' in prompt
        assert '"new_risk_point"' not in prompt  # v1 默认单问（v2 双问未过门不进默认）
        assert "营收零增长" not in prompt  # v1 不带参照池

    def test_b1_prompt_v2_keeps_dual_question_channel_rule_and_exemplars(self):
        prompt = fj.b1_prompt_v2(self.ROW)
        assert '"new_risk_point"' in prompt and '"absorbed"' in prompt
        assert "营收零增长" in prompt and "利润微增" in prompt
        assert "辩论下游" in prompt and "分析师通道" in prompt
        assert "权重主张" in prompt
        assert "（来源 technical）" in prompt

    def test_b2_prompt_v2_keeps_adjudicated_exemplars(self):
        prompt = fj.b2_prompt_v2({"rebutted_point": "需求崩塌", "rebuttal_text": "基数效应"})
        assert "顶撞" in prompt and "拨备" in prompt

    def test_runner_override_for_single_variant_experiments(self):
        judged = fj.run_b1_judgments(
            [self.ROW],
            llm_fn=_judge_returning({"new_risk_point": True, "absorbed": False, "reason": "x"}),
            prompt_fn=fj.b1_prompt_v2,
            rubric="b1-v2a",
        )
        assert judged[0]["judge_label"] is False
        assert judged[0]["judge_new_risk_point"] is True
        assert judged[0]["rubric"] == "b1-v2a"

    def test_b1_run_v1_payload_newness_is_none_not_crash(self):
        judged = fj.run_b1_judgments(
            [self.ROW], llm_fn=_judge_returning({"absorbed": True, "reason": "x"})
        )
        assert judged[0]["judge_new_risk_point"] is None
        assert judged[0]["rubric"] == "b1-v1"


class TestB5ConclusionJudging:
    """B5c 结论级盲评（预登记 §10，b5c-v1）：六准则 + 双防硬排除 + tie 语义 + decisive_criterion。"""

    def test_prompt_carries_six_criteria_and_anti_bias_rules(self):
        prompt = fj.b5_conclusion_prompt("600000", "甲指令", "乙指令")
        for token in ("方向自洽", "置信度校准", "可执行性", "风险边界", "可证伪性", "内部无矛盾"):
            assert token in prompt, f"缺准则：{token}"
        assert "谨慎不加分" in prompt and "决断不加分" in prompt  # 双防
        assert "tie" in prompt and "篇幅" in prompt
        assert "甲指令" in prompt and "乙指令" in prompt and "600000" in prompt
        assert '"decisive_criterion"' in prompt

    def test_run_conclusion_maps_position_and_carries_criteria(self):
        payload = {
            "winner": "A",
            "decisive_criterion": "2 置信度校准",
            "reason": "甲置信度与理由匹配，乙70%配均衡证据",
        }

        def fn(prompt: str) -> str:
            assert "甲指令" in prompt  # 材料就位
            return json.dumps(payload, ensure_ascii=False)

        rows = fj.run_b5_conclusion(
            [
                {
                    "unit_id": "600000::b5c",
                    "pair_key": "600000",
                    "ticker": "600000",
                    "instruction_a": "甲指令",
                    "instruction_b": "乙指令",
                }
            ],
            llm_fn=fn,
            votes=2,
        )
        r = rows[0]
        assert r["judge_reason"] == payload["reason"]
        assert r["vote_reasons"] == [payload["reason"]] * 2
        assert r["decisive_criteria"] == ["2 置信度校准"] * 2
        assert r["rubric"] == fj.B5C_RUBRIC
        # 位置映射：A 票映射回逻辑臂 instruction_a/b（runner 内 pairwise_assign 决定展示序）
        assert r["verdict"] in ("a", "b")

    def test_tie_vote_recorded(self):
        def fn(prompt: str) -> str:
            return json.dumps(
                {"winner": "tie", "decisive_criterion": None, "reason": "等价"}, ensure_ascii=False
            )

        rows = fj.run_b5_conclusion(
            [
                {
                    "unit_id": "x::b5c",
                    "pair_key": "x",
                    "ticker": "x",
                    "instruction_a": "同",
                    "instruction_b": "同",
                }
            ],
            llm_fn=fn,
            votes=1,
        )
        assert rows[0]["verdict"] == "tie"
        assert rows[0]["decisive_criteria"] == [None]
