# tests/evals/test_judges.py
"""LLM-as-Judge:rubric 完整性、JSON 解析容错、环境标记、降级。

judge 调用已迁移至 gateway 统一入口（purpose="judge"），mock 目标为
``finance_agent.llm.gateway.complete_text``（返回 (text, metadata) 元组）。
"""

import os
from unittest.mock import patch

import pytest
from evals.judges import JUDGE_ENV, RUBRIC_VERSIONS, RUBRICS, _judge_model, run_judge

_GATEWAY = "finance_agent.llm.gateway.complete_text"


def _mock_completion(score_json: str):
    """complete_text mock 返回值:(text, metadata)。"""
    return (score_json, {})


class TestRubricContract:
    def test_four_dimensions(self):
        assert set(RUBRICS.keys()) == {
            "report_relevance",
            "debate_quality",
            "decision_grounding",
            "consistency",
        }

    def test_rubrics_have_json_constraint_and_no_length_bias(self):
        for dim, rubric in RUBRICS.items():
            assert '{"score"' in rubric, f"{dim} rubric 缺 JSON 输出约束"
            assert "不以篇幅长短论优劣" in rubric, f"{dim} rubric 缺「不以篇幅长短论优劣」"

    def test_rubrics_require_confidence_with_material_groundedness(self):
        """round5 校准实证（4f58faf7 幻觉 5 分）：judge 输入残缺（图表路径+审批章）
        仍无任何不确定性信号、「全面覆盖」地打高分——输出缺 confidence 字段使幻觉
        无法从分数表面识别。rubric 输出契约 SHALL 含 confidence（0-1），且须定义
        其语义：对评分依据充分性的把握，材料缺失/截断/不足时 MUST 降低。"""
        for dim, rubric in RUBRICS.items():
            assert '"confidence"' in rubric, f"{dim} rubric 输出契约缺 confidence 字段"
            assert "置信" in rubric, f"{dim} rubric 缺 confidence 语义说明（依据不充分须降低）"

    def test_run_judge_returns_confidence(self):
        result = run_judge("report_relevance", {"query": "q", "report": "r"})
        assert "confidence" in result, "run_judge 返回缺 confidence"
        assert result["confidence"] is None or 0 <= result["confidence"] <= 1

    def test_run_judge_tolerates_missing_confidence(self):
        """旧格式（无 confidence）容错：score 正常解析，confidence 为 None。"""
        with patch(_GATEWAY) as mock_llm:
            mock_llm.return_value = _mock_completion('{"score": 3, "reason": "一般"}')
            result = run_judge("report_relevance", {"query": "q", "report": "r"})
        assert result == {
            "name": "report_relevance",
            "score": 3,
            "reason": "一般",
            "confidence": None,
        }

    def test_consistency_rubric_checks_fund_vs_risk(self):
        # spec consistency Scenario「特别检查 Fund Manager 与 Risk Judge 一致性」
        assert "Fund Manager" in RUBRICS["consistency"]
        assert "Risk Judge" in RUBRICS["consistency"]

    def test_consistency_rubric_defines_approve_semantics(self):
        """回归（2026-09-09 实测 round5）：judge 把「Risk watch + FM approve」系统性
        误判为冲突（11 条 consistency 中 7 条打 1-3 分，人工复核多为 4-5 分）。
        根因：rubric 未定义 approve 的批准对象——FM 的 approve/reject 针对的是
        Risk Judge 修正后的交易方案（裁决 JSON 的 action/position_size），不是
        对裁决本身投票；watch 方案被 approve 属于一致。rubric 须显式写出该语义。
        """
        rubric = RUBRICS["consistency"]
        assert "针对的是 Risk Judge 裁决后的最终交易方案" in rubric
        assert "watch(观望)而 FM approve" in rubric  # watch 方案被 approve 不算冲突
        assert "批准观望" in rubric

    def test_consistency_rubric_version_incremented(self):
        """rubric 语义修复递增版本号（v2 = approve 语义定义版，v4 = Trader 方案节 + 静默推翻核对）。"""
        assert RUBRIC_VERSIONS["consistency"] == 4

    def test_decision_grounding_rubric_mentions_evidence_refs(self):
        rubric = RUBRICS["decision_grounding"]
        assert "evidence_refs" in rubric
        assert "无引用" in rubric or "按以下原规则" in rubric


class TestRunJudge:
    @patch(_GATEWAY)
    def test_score_parsed(self, mock_llm):
        mock_llm.return_value = _mock_completion('{"score": 4, "reason": "基本切题"}')
        result = run_judge("report_relevance", {"query": "q", "report": "r"})
        assert result == {
            "name": "report_relevance",
            "score": 4,
            "reason": "基本切题",
            "confidence": None,
        }
        # 统一入口:purpose=judge + temperature=0
        _, kwargs = mock_llm.call_args
        assert kwargs["purpose"] == "judge"
        assert kwargs["temperature"] == 0.0
        # llm_config 三件套由 JUDGE_*(→LLM_*) helpers 调用时读环境拼出
        assert kwargs["llm_config"]["model"] == _judge_model()
        assert kwargs["llm_config"]["baseUrl"] == (
            os.getenv("JUDGE_BASE_URL") or os.getenv("LLM_BASE_URL", "") or ""
        )
        assert mock_llm.call_args.args[0][0]["role"] == "user"

    @patch(_GATEWAY)
    def test_parse_failure_retries_once_then_null(self, mock_llm):
        # 两次都返回非 JSON → score=None,不抛异常(spec「重试一次,仍失败记 null」)
        mock_llm.return_value = _mock_completion("这不是 JSON")
        result = run_judge("report_relevance", {"query": "q", "report": "r"})
        assert result["score"] is None
        assert result["reason"] == "judge_parse_failed"
        assert mock_llm.call_count == 2

    @patch(_GATEWAY)
    def test_llm_exception_retries_then_null(self, mock_llm):
        """gateway 抛异常(API/网络/配置错误)同样重试一次后 score=None。

        run_judge 的 try/except 覆盖 _call_judge_llm 抛出的异常,与解析失败同路径,
        不向调用方泄露异常(spec「不阻塞实验」)。
        """
        mock_llm.side_effect = RuntimeError("boom")
        result = run_judge("report_relevance", {"query": "q", "report": "r"})
        assert result["score"] is None
        assert result["reason"] == "judge_parse_failed"
        assert mock_llm.call_count == 2


class TestLazyEnvRead:
    """judge 配置必须调用时读环境（python -m evals.run 时序 bug 回归防护）。

    实况：模块 import（judges.py 固化常量）先于 main() 的 load_dotenv 执行，
    import 时 JUDGE_* 为空 → 跑批 judge 28 项全败而单测（先 dotenv 后 import）
    全通。修复：常量改函数，每次调用读环境。
    """

    @patch.dict(
        os.environ,
        {
            "JUDGE_BASE_URL": "https://judge-lazy.test/v1",
            "JUDGE_API_KEY": "sk-lazy-test",
        },
    )
    @patch(_GATEWAY)
    def test_env_set_after_import_is_effective(self, mock_llm):
        mock_llm.return_value = _mock_completion('{"score": 3, "reason": "x"}')
        result = run_judge("report_relevance", {"query": "q", "report": "r"})
        assert result["score"] == 3
        _, kwargs = mock_llm.call_args
        cfg = kwargs["llm_config"]
        assert cfg["baseUrl"] == "https://judge-lazy.test/v1"
        assert cfg["apiKey"] == "sk-lazy-test"

    @patch(_GATEWAY)
    def test_score_out_of_range_treated_as_failure(self, mock_llm):
        mock_llm.return_value = _mock_completion('{"score": 9, "reason": "x"}')
        result = run_judge("report_relevance", {"query": "q", "report": "r"})
        assert result["score"] is None

    @patch(_GATEWAY)
    def test_variables_substituted_into_prompt(self, mock_llm):
        mock_llm.return_value = _mock_completion('{"score": 5, "reason": "ok"}')
        run_judge("report_relevance", {"query": "茅台怎么样", "report": "茅台是好公司"})
        prompt = mock_llm.call_args.args[0][0]["content"]
        assert "茅台怎么样" in prompt and "茅台是好公司" in prompt
        assert "{{query}}" not in prompt

    @patch(_GATEWAY)
    def test_render_no_double_substitution(self, mock_llm):
        """变量值含 {{another_key}} 字面时不被后续迭代二次替换(单次扫描)。"""
        mock_llm.return_value = _mock_completion('{"score": 5, "reason": "x"}')
        run_judge("report_relevance", {"query": "见 {{report}}", "report": "机密"})
        prompt = mock_llm.call_args.args[0][0]["content"]
        # query 值原样保留(含字面 {{report}})或被自身键正确替换,二选一
        assert "见 {{report}}" in prompt or "见 机密" in prompt
        # report 槽位只替换一次:无论何种情况「机密」最多出现一次
        assert prompt.count("机密") == 1

    def test_render_missing_var_becomes_empty(self):
        """未提供的变量替换为空串,不残留 {{key}} 占位符（直接测 _render）。"""
        from evals.judges import _render

        prompt = _render("report_relevance", {"query": "q"})  # 缺 report
        assert "{{report}}" not in prompt

    @patch(_GATEWAY)
    def test_judge_generation_marked_with_env(self, mock_llm):
        """spec「裁判成本独立核算」:judge generation 观测带 environment 标记。

        迁移后 observation 由 gateway 统一开启，environment 审计经
        trace.metadata 传递（name="judge" + environment=JUDGE_ENV）。
        """
        mock_llm.return_value = _mock_completion('{"score": 3, "reason": "x"}')
        run_judge("report_relevance", {"query": "q", "report": "r"})
        _, kwargs = mock_llm.call_args
        trace = kwargs["trace"]
        assert trace["name"] == "judge"
        assert trace["metadata"]["environment"] == JUDGE_ENV


class TestInputMissingGuard:
    """评估链路输入合同（delta 3.4）：关键维度变量为空 → input_missing 跳过。

    实战教训（baseline r5 校准）：空辩论静默打 1 分混入真实分数，
    「自信但失真」。空输入不得出具看似正常的数字分数。
    """

    @patch(_GATEWAY)
    def test_debate_empty_marks_input_missing(self, mock_llm):
        from evals.judges import run_judge

        mock_llm.return_value = _mock_completion('{"score": 1, "reason": "x"}')
        result = run_judge("debate_quality", {"query": "q", "debate_history": "", "report": "r"})
        assert result["score"] is None
        assert result["reason"] == "input_missing:debate_history"
        mock_llm.assert_not_called()  # 关键：空输入根本不打分

    @patch(_GATEWAY)
    def test_report_empty_marks_input_missing(self, mock_llm):
        from evals.judges import run_judge

        result = run_judge("report_relevance", {"query": "q", "report": ""})
        assert result["score"] is None
        assert result["reason"] == "input_missing:report"
        mock_llm.assert_not_called()

    @patch(_GATEWAY)
    def test_non_empty_inputs_normal_scoring(self, mock_llm):
        from evals.judges import run_judge

        mock_llm.return_value = _mock_completion('{"score": 4, "reason": "ok"}')
        result = run_judge(
            "debate_quality",
            {"query": "q", "debate_history": "【bull】看多\n【bear】看空", "report": "r"},
        )
        assert result["score"] == 4
        mock_llm.assert_called_once()


class TestDecisionGroundingRubricV3:
    def test_version_incremented(self):
        """rubric 变更递增版本号（v7 = 三层判法归属 + 解读失当强制核对，v8 = 多来源归属判例）。"""
        assert RUBRIC_VERSIONS["decision_grounding"] == 8  # v8 = 多来源同判任一真实来源即合法

    def test_v6_material_covers_risk_judge_evidence_base(self):
        """r1 复盘：被评的是 Risk Judge 裁决，其理由建立在风控指标与三方风险辩论上，
        但 v5 材料只有分析师/多空辩论/RM——judge 按 rubric 只能判「无中生有」（8 条里
        5 条抱怨风控数字无出处、3 条抱怨中性方/保守方论据无出处，置信度 0.4）。"""
        rubric = RUBRICS["decision_grounding"]
        assert "【风控指标】{{risk_metrics}}" in rubric
        assert "【风险辩论记录】{{risk_debate_history}}" in rubric
        for src in ("risk_aggressive", "risk_conservative", "risk_neutral", "risk_metrics"):
            assert src in rubric, src

    def test_other_rubrics_version_pinned(self):
        assert RUBRIC_VERSIONS["report_relevance"] == 3  # v3 = confidence 契约 + 口径必读
        assert (
            RUBRIC_VERSIONS["debate_quality"] == 6
        )  # v6 = points 结构化枚举 + 程序封顶（v5 强制枚举 round10 实测仍漏判）
        assert RUBRIC_VERSIONS["consistency"] == 4  # v4 = approve 语义(v2) + Trader 方案节(v4)
        assert RUBRIC_VERSIONS["decision_grounding"] == 8  # v8 = 三层判法(v7) + 多来源归属判例

    def test_rubric_includes_semantic_check(self):
        """语义核对条款：术语/期次/方向与所引数值一致；解读失当扣分。"""
        rubric = RUBRICS["decision_grounding"]
        assert "语义一致" in rubric
        assert "期次" in rubric
        assert "行业领先" in rubric  # 反例锚点（垫底表述为领先）
        # v7（round7 终裁）：归属层判法 + 解读失当强制动作 + 组合 claim 规则
        assert "三层判法" in rubric
        assert "归属" in rubric
        assert "单向解读" in rubric
        assert "组合 claim" in rubric
        # v8（round9）：多来源同判判例——claim 在多来源均有原话时引任一真实来源即合法
        assert "判例(v8)" in rubric
        assert "任一真实来源即合法" in rubric
        # debate_quality v4 判例：论点标头纯定性论点（「历史上……」类）降 4
        debate_rubric = RUBRICS["debate_quality"]
        assert "判例(v4)" in debate_rubric
        assert "无样本" in debate_rubric
        # debate_quality v5（round10）：强制枚举动作——评分前逐条标注论点标头，
        # 任一纯定性即封顶 4（round9 审计：v4 判例 5 分档仍漏判，照搬 dg v7 强制核对模式）
        assert "强制枚举动作" in debate_rubric
        assert "逐条列出" in debate_rubric
        assert "封顶 4" in debate_rubric
        # consistency v4：Trader 方案节 + 静默推翻核对
        consistency_rubric = RUBRICS["consistency"]
        assert "【Trader 方案】{{trader_plan}}" in consistency_rubric
        assert "静默推翻" in consistency_rubric


class TestDebateEnumerationCap:
    """debate_quality v6：结构化枚举 + 程序封顶（round10 预登记 fallback）。

    round10 实测（8 行离线重判）：prompt 内强制枚举把准确率 2/8 提到 6/8，但仍
    漏判宁德、招行回归——LLM 自我枚举随机，不可依赖。v6 把判据搬到代码：judge
    只需如实列出 `points`（每条论点标头 + data/qualitative），封顶由 `run_judge`
    按枚举结果执行，与 LLM 是否自觉扣分无关。
    """

    _QUALITATIVE_JSON = (
        '{"score": 5, "confidence": 0.9, "reason": "交锋充分",'
        ' "points": [{"header": "营收同比 +12%", "type": "data"},'
        ' {"header": "周期位置有利", "type": "qualitative"}]}'
    )
    _ALL_DATA_JSON = (
        '{"score": 5, "confidence": 0.9, "reason": "交锋充分",'
        ' "points": [{"header": "营收同比 +12%", "type": "data"},'
        ' {"header": "毛利率 45.2%", "type": "data"}]}'
    )

    @patch(_GATEWAY)
    def test_qualitative_point_caps_score_to_4(self, mock_llm):
        mock_llm.return_value = _mock_completion(self._QUALITATIVE_JSON)
        result = run_judge("debate_quality", {"debate_history": "【bull】论点: 周期位置有利"})
        assert result["score"] == 4, "纯定性论点必须被程序封顶到 4（不依赖 LLM 自律）"
        assert result["cap_applied"] is True
        assert result["qualitative_points"] == 1
        assert result["enumeration_missing"] is False

    @patch(_GATEWAY)
    def test_all_data_points_keep_5(self, mock_llm):
        mock_llm.return_value = _mock_completion(self._ALL_DATA_JSON)
        result = run_judge("debate_quality", {"debate_history": "【bull】论点: 营收同比 +12%"})
        assert result["score"] == 5
        assert result["cap_applied"] is False
        assert result["qualitative_points"] == 0

    @patch(_GATEWAY)
    def test_cap_does_not_push_below_4(self, mock_llm):
        mock_llm.return_value = _mock_completion(
            '{"score": 3, "reason": "多为立场声明",'
            ' "points": [{"header": "护城河深厚", "type": "qualitative"}]}'
        )
        result = run_judge("debate_quality", {"debate_history": "【bull】论点: 护城河深厚"})
        assert result["score"] == 3, "封顶为 min(score, 4)，不得把 3 再往下压"

    @patch(_GATEWAY)
    def test_missing_enumeration_fails_open_but_flagged(self, mock_llm):
        """枚举缺失（旧格式/模型未遵契约）→ 分数不变但标记，供代裁识别机制未生效行。"""
        mock_llm.return_value = _mock_completion('{"score": 5, "reason": "交锋充分"}')
        result = run_judge("debate_quality", {"debate_history": "【bull】论点: 营收同比 +12%"})
        assert result["score"] == 5
        assert result["enumeration_missing"] is True
        assert result["cap_applied"] is False

    @patch(_GATEWAY)
    def test_malformed_points_treated_as_missing(self, mock_llm):
        # 类型非法（非 list / 缺 type / type 取值不在枚举内）→ 一律按缺失处理，不崩
        mock_llm.return_value = _mock_completion(
            '{"score": 5, "reason": "r", "points": "not-a-list"}'
        )
        result = run_judge("debate_quality", {"debate_history": "【bull】论点: x"})
        assert result["score"] == 5
        assert result["enumeration_missing"] is True

    @patch(_GATEWAY)
    def test_non_debate_dimensions_result_shape_unchanged(self, mock_llm):
        """非 debate 维度不得新增键（既有精确断言契约不变）。"""
        mock_llm.return_value = _mock_completion('{"score": 4, "reason": "基本切题"}')
        result = run_judge("report_relevance", {"query": "q", "report": "r"})
        assert set(result.keys()) == {"name", "score", "reason", "confidence"}

    def test_debate_rubric_v6_requires_points_contract(self):
        rubric = RUBRICS["debate_quality"]
        assert RUBRIC_VERSIONS["debate_quality"] == 6
        assert '"points"' in rubric, "v6 输出契约须含结构化枚举字段 points"
        assert "qualitative" in rubric and "data" in rubric
        assert "程序" in rubric or "代码" in rubric, "须写明封顶由程序执行，不依赖 LLM 自律"

    @patch(_GATEWAY)
    def test_cap_evidence_persisted_in_result(self, mock_llm):
        # 封顶证据随结果返回（落库由 eval adapter / 消融 run 记录承担）
        mock_llm.return_value = _mock_completion(self._QUALITATIVE_JSON)
        result = run_judge("debate_quality", {"debate_history": "【bull】论点: 周期位置有利"})
        assert result["points"][1]["type"] == "qualitative"
        assert result["points"][1]["header"] == "周期位置有利"


class TestRunJudgeMean:
    """消融判分协议：K 次重复取均值（round11 实测单次调用在 5/4 边界双峰翻转 ~46%）。

    同材料同 rubric 调用级方差实测（宁德 04baff5c，n=13 次调用）：4 分 7 次 /
    5 分 6 次，σ≈0.5——与消融待测层级增量（0.25-0.5）同阶。取**均值**而非中位：
    双峰 p→0.5 时中位不降翻转概率，均值无偏且方差除以 K；每次分数与极差一并
    记录（噪声保持可见，不静默平均掉）。
    """

    def _results(self, scores: list[int | None]):
        return [
            {
                "name": "debate_quality",
                "score": s,
                "reason": f"r{s}",
                "confidence": 0.8,
                "points": [],
                "qualitative_points": 1 if s == 4 else 0,
                "cap_applied": False,
                "enumeration_missing": False,
            }
            for s in scores
        ]

    def test_mean_of_repeats(self):
        from evals.judges import run_judge_mean

        with patch("evals.judges.run_judge", side_effect=self._results([5, 4, 4])):
            result = run_judge_mean("debate_quality", {"debate_history": "x"}, repeats=3)
        assert result["score"] == pytest.approx(4.333), "[5,4,4] 的均值（双峰下比中位更有效降方差）"
        assert result["judge_repeats"] == 3
        assert result["scores"] == [5, 4, 4]
        assert result["score_spread"] == 1

    def test_telemetry_from_lowest_call(self):
        """遥测取最低分那次的枚举（最保守观测）——任一次找到纯定性标头则封顶证据不丢。"""
        from evals.judges import run_judge_mean

        with patch("evals.judges.run_judge", side_effect=self._results([5, 4, 4])):
            result = run_judge_mean("debate_quality", {"debate_history": "x"}, repeats=3)
        assert result["qualitative_points"] == 1, "最低分（4）那次找到了纯定性论点"
        assert result["points"] == []

    def test_none_calls_ignored_but_counted(self):
        from evals.judges import run_judge_mean

        with patch("evals.judges.run_judge", side_effect=self._results([None, 4, 5, None])):
            result = run_judge_mean("debate_quality", {"debate_history": "x"}, repeats=4)
        assert result["score"] == pytest.approx(4.5)  # mean([4, 5])
        assert result["judge_failures"] == 2

    def test_all_failed_returns_none(self):
        from evals.judges import run_judge_mean

        with patch("evals.judges.run_judge", side_effect=self._results([None, None])):
            result = run_judge_mean("debate_quality", {"debate_history": "x"}, repeats=2)
        assert result["score"] is None
        assert result["judge_failures"] == 2

    def test_repeats_1_matches_single_call(self):
        from evals.judges import run_judge_mean

        with patch("evals.judges.run_judge", side_effect=self._results([5])):
            result = run_judge_mean("report_relevance", {"query": "q", "report": "r"}, repeats=1)
        assert result["score"] == 5
        assert result["score_spread"] == 0
