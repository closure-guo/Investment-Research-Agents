# tests/evals/test_extract.py
"""judge 变量提取:state → 9 个字符串变量,缺失容错,结论章节提取。"""

from evals.extract import _JUDGE_MAX_BYTES  # noqa: I001
from evals.extract import extract_conclusion, extract_judge_vars

from finance_agent.citation import Claim
from finance_agent.models import TradeDecision


def _state() -> dict:
    return {
        "final_report": "# 报告\n## 财务分析\n很好。\n## 结论\n建议买入,目标价 2000。\n## 风险提示\n波动。",
        "analyst_reports": {
            "fundamental": {"summary": "基本面强劲", "claims": []},
            "technical": {"conclusion": "均线上扬"},
            "macro": {},
            "sentiment": {"summary": "情绪偏热"},
        },
        "debate_history": [
            {"role": "bull", "content": "看多理由"},
            {"role": "bear", "content": "看空理由"},
        ],
        "research_manager_conclusion": "综合看多方占优",
        "final_trade_decision": {"action": "buy", "confidence": 0.8, "reasoning": "论据充分"},
        "risk_debate_history": [{"role": "risky", "content": "激进看法"}],
        "fund_manager_decision": "approve",
    }


class TestExtractJudgeVars:
    def test_nine_keys_all_str(self):
        vars_ = extract_judge_vars(_state(), query="分析茅台")
        expected_keys = {
            "query",
            "report",
            "report_conclusion",
            "analyst_reports",
            "debate_history",
            "research_manager_decision",
            "trader_plan",
            "risk_metrics",
            "risk_debate_history",
            "trade_decision",
            "risk_judgment",
            "fund_manager_decision",
        }
        assert set(vars_.keys()) == expected_keys
        assert all(isinstance(v, str) for v in vars_.values())

    def test_trader_plan_var_from_state(self):
        """v8：trader_plan 变量 = Trader 原始方案（Layer III），非 Risk Judge 裁决——
        consistency 材料据此核对「Trader 方案 → Risk Judge 裁决」是否静默推翻。"""
        state = _state()
        state["trader_plan"] = {"action": "buy", "confidence": 0.7, "reasoning": "突破确认"}
        vars_ = extract_judge_vars(state, query="q")
        assert "buy" in vars_["trader_plan"]
        assert "突破确认" in vars_["trader_plan"]
        # 无 trader_plan（旧会话/quick 模式）→ 空串，不报错
        state2 = _state()
        state2.pop("trader_plan", None)
        assert extract_judge_vars(state2, query="q")["trader_plan"] == ""

    def test_convergence_skeleton_in_debate_variable(self):
        """v8：debate 材料骨架行——发言轮数/论点数/让步与坚持语计数，
        强制标注「程序统计，供参考」，置于原始发言之前。"""
        state = _state()
        state["debate_history"] = [
            {
                "role": "bull",
                "content": "确实基本面在改善，我方坚持看多",
                "key_arguments": ["论点甲", "论点乙"],
            },
            {
                "role": "bear",
                "content": "不同意，估值恰恰相反地偏贵",
                "key_arguments": ["反论点一"],
            },
        ]
        vars_ = extract_judge_vars(state, query="q")
        debate = vars_["debate_history"]
        assert "收敛信号(程序统计，供参考)" in debate
        assert "发言 2 轮" in debate
        assert "论点共 3 条" in debate
        assert "让步语 1 处" in debate  # 「确实」
        assert "坚持/反驳语 2 处" in debate  # 「不同意」「恰恰相反」
        # 骨架行在原始发言之前
        assert debate.index("收敛信号") < debate.index("确实基本面")

    def test_values_mapped_from_state(self):
        vars_ = extract_judge_vars(_state(), query="分析茅台")
        assert "基本面强劲" in vars_["analyst_reports"]
        assert "看多理由" in vars_["debate_history"]
        assert vars_["research_manager_decision"] == "综合看多方占优"
        assert "buy" in vars_["trade_decision"]
        assert vars_["fund_manager_decision"] == "approve"
        assert "建议买入" in vars_["report_conclusion"]
        # review 加固:risk_judgment 拼接契约 + query/report echo
        assert "buy" in vars_["risk_judgment"], "risk_judgment 应含决策 JSON"
        assert "激进看法" in vars_["risk_judgment"], "risk_judgment 应含 risk_debate 末条"
        assert vars_["query"] == "分析茅台", "query 应原样 echo"
        assert "【分析师结论】" in vars_["report"], (
            "report 已结构化拼装（3.5），不再回显 final_report 全文"
        )

    def test_analyst_reports_prefers_plain_conclusion(self):
        """add-agent-readable-conclusion：analyst_reports 优先展示普通人可读结论。"""
        state = {
            "final_report": "报告",
            "analyst_reports": {
                "fundamental": {
                    "summary": "基本面黑话摘要：ROE 32%、负债率 41%",
                    "plain_conclusion": "基本面偏多：盈利质量优秀，负债率低",
                    "claims": [],
                }
            },
        }
        vars_ = extract_judge_vars(state, query="q")
        assert "基本面偏多：盈利质量优秀" in vars_["analyst_reports"]
        assert "ROE 32%" not in vars_["analyst_reports"]  # summary 不再默认展示

    def test_analyst_reports_fallbacks_without_plain_conclusion(self):
        """旧 trace 报告对象无 plain_conclusion 时回退 summary，不报错。"""
        state = {
            "final_report": "报告",
            "analyst_reports": {"fundamental": {"summary": "旧版本摘要", "claims": []}},
        }
        vars_ = extract_judge_vars(state, query="q")
        assert "旧版本摘要" in vars_["analyst_reports"]

    def test_missing_keys_give_empty_string(self):
        vars_ = extract_judge_vars({})
        assert vars_["report"] == ""
        assert vars_["analyst_reports"] == ""
        assert vars_["fund_manager_decision"] == ""

    def test_long_values_truncated(self):
        state = _state()
        # 超过预算（_JUDGE_MAX_BYTES）才截断；预算已按用户决策放宽，正常长度不再被切
        state["research_manager_conclusion"] = "长" * (_JUDGE_MAX_BYTES // 3 * 2)
        vars_ = extract_judge_vars(state)
        assert len(vars_["research_manager_decision"].encode("utf-8")) < _JUDGE_MAX_BYTES
        assert "truncated" in vars_["research_manager_decision"]

    def test_fm_action_confidence_in_judge_var(self):
        """D1：FM 操作定性进 judge 变量——approve 批准的方向无需从理由推断。"""
        state = {
            "final_trade_decision": {"action": "watch"},
            "fund_manager_decision": "approve",
            "fund_manager_decision_reasoning": "风控可控，批准执行",
            "fund_manager_action": "watch",
            "fund_manager_confidence": 0.55,
        }
        out = extract_judge_vars(state)["fund_manager_decision"]
        assert "approve" in out
        assert "操作定性 watch" in out
        assert "置信度 0.55" in out

    def test_fm_legacy_state_without_action(self):
        """历史 state 无 action/confidence 键时保持旧行为（决策+理由）。"""
        state = {
            "fund_manager_decision": "reject",
            "fund_manager_decision_reasoning": "回撤超限",
        }
        out = extract_judge_vars(state)["fund_manager_decision"]
        assert out == "reject" + chr(10) + "理由: 回撤超限"
        assert "操作定性" not in out

    def test_rm_rating_prefixed_conclusion_passed_through(self):
        """D1（1.9）：RM conclusion（评级前置拼装）进 judge 变量为直取——
        消费端不解析正文，前置的「评级: …」行原样到达 judge。"""
        state = {
            "research_manager_conclusion": "评级: 看多（置信度 0.65）" + chr(10) + "多方论据扎实。",
        }
        out = extract_judge_vars(state)["research_manager_decision"]
        assert out.startswith("评级: 看多（置信度 0.65）")

    def test_risk_metrics_and_risk_debate_become_judge_vars(self):
        """r1 复盘：Risk Judge 裁决引用 beta/回撤与三方风险辩论，judge 材料里却没有。"""
        state = {
            "risk_metrics": {
                "max_drawdown": 0.345,
                "volatility": 0.584,
                "var_95": 0.0516,
                "beta": 1.96,
            },
            "risk_debate_history": [
                {"role": "aggressive", "content": "左侧建仓", "key_arguments": ["超卖反弹"]},
                {
                    "role": "conservative",
                    "content": "期限错配",
                    "key_arguments": ["日度VaR不能为中期回撤背书"],
                },
                {"role": "neutral", "content": "隐含PE约25倍", "key_arguments": ["估值切换未完成"]},
            ],
        }
        out = extract_judge_vars(state)
        assert "最大回撤 34.5%" in out["risk_metrics"]
        assert "beta 1.96" in out["risk_metrics"]
        assert "VaR(95%) 5.2%" in out["risk_metrics"]
        for role in ("aggressive", "conservative", "neutral"):
            assert f"【{role}】" in out["risk_debate_history"], role
        assert "隐含PE约25倍" in out["risk_debate_history"]

    def test_risk_vars_empty_when_absent(self):
        out = extract_judge_vars({})
        assert out["risk_metrics"] == ""
        assert out["risk_debate_history"] == ""

    def test_long_reasoning_decision_stays_valid_json(self):
        """r1 复盘（茅台 66009ecb）：_serialize_decision 先出 JSON 再被 _trunc 挖心 2349 字节，
        残缺 JSON 人读化解析不了，标注人与 judge 都看到原始残缺 JSON。长 reasoning 须在
        对象内截断，序列化结果保持合法 JSON。"""
        import json as _json

        state = {
            "final_trade_decision": {
                "action": "watch",
                "confidence": 0.55,
                "reasoning": "理由" * 3000,
                "evidence_refs": [{"claim": "beta 0.05", "source": "risk_metrics"}],
            }
        }
        out = extract_judge_vars(state)["trade_decision"]
        parsed = _json.loads(out)
        assert parsed["action"] == "watch"
        assert parsed["evidence_refs"][0]["source"] == "risk_metrics"
        assert len(out.encode("utf-8")) <= _JUDGE_MAX_BYTES
        assert "truncated" in parsed["reasoning"]

    def test_risk_judgment_keeps_decision_json_intact(self):
        """r2 三道关复盘：risk_judgment = 裁决 JSON + 风险辩论尾部，合计超 4096 后外层 _trunc
        挖心把 JSON 闭合括号挖掉，consistency 材料的【Risk Judge 裁决】人读化失败 9/9。
        裁决 JSON 须保持完整，辩论尾部只在剩余预算内追加。"""
        import json as _json

        state = {
            "final_trade_decision": {
                "action": "watch",
                "confidence": 0.55,
                "reasoning": "理由" * 1500,
                "evidence_refs": [
                    {
                        "claim": f"论据 {i}：ROE 3.4%、自由现金流持续为负、健康度 44 分",
                        "source": "fundamental",
                    }
                    for i in range(12)
                ],
            },
            "risk_debate_history": [
                {
                    "role": "conservative",
                    "content": "保守" * 600,
                    "key_arguments": ["保守论点：期限错配、VaR 不能为回撤背书"] * 6,
                },
                {
                    "role": "neutral",
                    "content": "中性" * 600,
                    "key_arguments": ["中性论点：隐含 PE 约 25 倍、切换未完成"] * 6,
                },
            ],
        }
        out = extract_judge_vars(state)["risk_judgment"]
        head = out.split("\n", 1)[0]
        assert _json.loads(head)["action"] == "watch"
        assert len(out.encode("utf-8")) <= _JUDGE_MAX_BYTES
        assert "【conservative】" in out or "【neutral】" in out

    def test_relaxed_budgets_keep_long_analyst_and_debate_text_intact(self):
        """用户决策（r2 三道关复盘）：分析师/辩论段截断预算一律放宽——800 字节中段截断
        让 judge 在 6/41 条理由里抱怨「无法核对」并压低置信度，还切出「…行 综合四份」
        类残句与丢期间标签的假矛盾。2500 字节量级的单段须完整可见。"""
        long_summary = "营收增长16.5%、净利润增长36.3%、ROE 3.4%、自由现金流为负；" * 40  # ≈ 2.6KB
        state = {
            "analyst_reports": {
                "fundamental": {
                    "agent_name": "fundamental",
                    "summary": long_summary,
                    "plain_conclusion": "偏空",
                    "key_findings": [],
                    "claims": [],
                    "markdown": "",
                }
            },
            "debate_history": [
                {
                    "role": "bull",
                    "round": 1,
                    "content": "多方论述：" + long_summary,
                    "key_arguments": ["论点甲"] * 12,
                    "rebuttal_to": [],
                },
                {
                    "role": "bear",
                    "round": 1,
                    "content": "空方论述：" + long_summary,
                    "key_arguments": ["论点乙"] * 12,
                    "rebuttal_to": [],
                },
            ],
        }
        out = extract_judge_vars(state)
        assert "truncated" not in out["analyst_reports"]
        assert "truncated" not in out["debate_history"]
        assert out["debate_history"].count("论点甲") == 12

    def test_rebuttal_coverage_parallel_rounds_full_engagement(self):
        """r1/r2 复盘：辩论图按轮扇出——bull_r1 与 bear_r1 并行、bull_r2 与 bear_r2 并行
        （routing.route_to_debate_r1 返回两个 Send）。同轮互不可见，R2 只能回应对方 R1，
        R2 论点无人可回应。旧实现按「上一条发言 = 对方」推断（历史顺序在扇出下不确定，
        还会把同方连续发言互相记账），分母又把不可回应的末轮论点算进去——8 条 trace 全
        「4/8」。新语义：rebuttal_to 指向对方上一轮（prompt 契约原文），分母只算「对方
        存在更后轮次」的论点。"""
        from evals.extract import _rebuttal_coverage

        def msg(role, rnd, args, rebuttal):
            return {"role": role, "round": rnd, "key_arguments": args, "rebuttal_to": rebuttal}

        history = [
            msg("bull", 1, ["a1", "a2", "a3", "a4"], []),
            msg("bear", 1, ["b1", "b2", "b3", "b4"], []),
            msg("bull", 2, ["a5", "a6", "a7", "a8"], [1, 2, 3, 4]),
            msg("bear", 2, ["b5", "b6", "b7", "b8"], [1, 2, 3, 4]),
        ]
        assert _rebuttal_coverage(history) == "bull 论点被回应 4/4；bear 论点被回应 4/4"

    def test_rebuttal_coverage_partial_and_order_independent(self):
        from evals.extract import _rebuttal_coverage

        def msg(role, rnd, args, rebuttal):
            return {"role": role, "round": rnd, "key_arguments": args, "rebuttal_to": rebuttal}

        bull1 = msg("bull", 1, ["a1", "a2", "a3", "a4"], [])
        bear1 = msg("bear", 1, ["b1", "b2", "b3"], [])
        bull2 = msg("bull", 2, ["a5"], [2])  # 只回应了 bear R1 的②
        bear2 = msg("bear", 2, ["b4", "b5"], [1, 3, 9])  # 9 越界忽略
        expected = "bull 论点被回应 2/4；bear 论点被回应 1/3"
        assert _rebuttal_coverage([bull1, bear1, bull2, bear2]) == expected
        # 扇出下 state 里的历史顺序不确定：乱序结果必须一致
        assert _rebuttal_coverage([bull1, bear1, bear2, bull2]) == expected
        assert _rebuttal_coverage([bear2, bull1, bull2, bear1]) == expected

    def test_rebuttal_coverage_round_field_missing_falls_back_to_occurrence(self):
        from evals.extract import _rebuttal_coverage

        history = [
            {"role": "bull", "key_arguments": ["a1", "a2"], "rebuttal_to": []},
            {"role": "bear", "key_arguments": ["b1"], "rebuttal_to": []},
            {"role": "bull", "key_arguments": ["a3"], "rebuttal_to": [1]},
            {"role": "bear", "key_arguments": ["b2"], "rebuttal_to": [2]},
        ]
        assert _rebuttal_coverage(history) == "bull 论点被回应 1/2；bear 论点被回应 1/1"

    def test_rebuttal_coverage_none_for_three_party_or_single_round(self):
        """三方风险辩论对手不唯一、单轮辩论无人可回应 → 不产出覆盖率行。"""
        from evals.extract import _rebuttal_coverage

        three = [
            {"role": r, "round": 1, "key_arguments": ["x"], "rebuttal_to": []}
            for r in ("aggressive", "conservative", "neutral")
        ]
        assert _rebuttal_coverage(three) is None
        single = [
            {"role": "bull", "round": 1, "key_arguments": ["a"], "rebuttal_to": []},
            {"role": "bear", "round": 1, "key_arguments": ["b"], "rebuttal_to": []},
        ]
        assert _rebuttal_coverage(single) is None

    def test_rebuttal_coverage_in_debate_variable(self):
        """D1（1.12）：交锋覆盖率进入 debate_history 变量——确定性指标（零 token），
        「对方论点被回应的比例」由各轮 rebuttal_to 并集直接计算。"""
        state = {
            "debate_history": [
                {
                    "role": "bull",
                    "round": 1,
                    "content": "开场",
                    "key_arguments": ["论点甲", "论点乙", "论点丙"],
                    "rebuttal_to": [],
                },
                {
                    "role": "bear",
                    "round": 1,
                    "content": "开场",
                    "key_arguments": ["反论点一"],
                    "rebuttal_to": [],
                },
                {
                    "role": "bull",
                    "round": 2,
                    "content": "回应反论点一",
                    "key_arguments": [],
                    "rebuttal_to": [1],
                },
                {
                    "role": "bear",
                    "round": 2,
                    "content": "回应甲和丙",
                    "key_arguments": [],
                    "rebuttal_to": [1, 3],
                },
            ],
        }
        out = extract_judge_vars(state)["debate_history"]
        assert "交锋覆盖" in out
        # bear 回应了 bull 的 1、3 → bull 侧覆盖 2/3；bull 回应了 bear 的 1 → bear 侧 1/1
        assert "bull 论点被回应 2/3" in out
        assert "bear 论点被回应 1/1" in out

    def test_report_conclusion_prefers_focus_summary(self):
        """D3（3.3）：report_conclusion 优先取 state["focus_summary"]（分析综合结论），
        回退 extract_conclusion(final_report)（审批复述——历史 trace 兼容）。"""
        # 有 focus_summary：取它
        state = {
            "focus_summary": "研究聚焦：多空均衡，建议观望。",
            "final_report": "## 六、基金经理决策" + chr(10) + "审批通过",
        }
        out = extract_judge_vars(state)["report_conclusion"]
        assert out == "研究聚焦：多空均衡，建议观望。"
        # 无 focus_summary：回退全文提取（旧行为）
        state2 = {"final_report": "## 六、基金经理决策" + chr(10) + "审批通过"}
        out2 = extract_judge_vars(state2)["report_conclusion"]
        assert "审批通过" in out2

    def test_report_var_structured_assembly_no_images(self):
        """D3（3.5）：deep 报告的 report 变量改为结构化拼装（聚焦/分析师/RM/决策/FM），
        剔除图片 markdown；全文 head/tail 截断使 judge 只见图表路径+审批章（幻觉输入，
        4f58faf7 实测） SHALL NOT 再发生。"""
        state = {
            "focus_summary": "聚焦：多空均衡。",
            "analyst_reports": {"technical": {"summary": "技术面企稳", "plain_conclusion": "偏多"}},
            "research_manager_conclusion": "评级: 中性（置信度 0.55）" + chr(10) + "证据均衡。",
            "final_trade_decision": {"action": "watch"},
            "fund_manager_decision": "approve",
            "fund_manager_action": "watch",
            "fund_manager_confidence": 0.55,
        }
        out = extract_judge_vars(state)["report"]
        assert "【研究聚焦】聚焦：多空均衡。" in out
        assert "【分析师结论】" in out
        assert "【研究经理结论】" in out
        assert "【交易方案】" in out
        assert "【基金经理决策】" in out
        assert "![重大" not in out and "![" not in out  # 图片引用剔除

    def test_report_var_falls_back_to_final_report(self):
        """旧 trace 无结构化字段时回退 final_report 全文截断（兼容）。"""
        state = {"final_report": "# 报告" + chr(10) + "正文内容", "focus_summary": ""}
        out = extract_judge_vars(state)["report"]
        assert "正文内容" in out


class TestExtractConclusion:
    def test_section_hit(self):
        report = "## 财务分析\nA\n## 结论\n买入。\n## 风险提示\nB"
        assert extract_conclusion(report) == "买入。"

    def test_fallback_starts_at_sentence_boundary(self):
        """回归（2026-09-09）：无结论性标题时不得从 report[-500:] 中间切开（半句话），
        须从最近断句边界（句号）之后开始。"""
        report = "前置句。" + "中" * 400 + "边界句。" + "尾" * 300
        conclusion = extract_conclusion(report)
        assert conclusion == "尾" * 300  # 从「边界句。」之后起，不以「中」开头

    def test_numbered_fund_manager_title(self):
        """真实报告结论标题为「## 六、基金经理决策」这类编号式，词表须匹配到。"""
        report = (
            "# 报告\n## 一、研究聚焦\n内容。\n"
            "## 六、基金经理决策\n批准买入，仓位 light，止损 1280。\n"
        )
        assert extract_conclusion(report) == "批准买入，仓位 light，止损 1280。"

    def test_last_conclusion_title_wins(self):
        """「多空辩论结论」等中间章节也含关键词时，取最后一个结论性标题。"""
        report = "## 三、多空辩论结论\n辩论内容\n## 六、基金经理决策\n最终决策内容\n"
        assert extract_conclusion(report) == "最终决策内容"

    def test_empty_report(self):
        assert extract_conclusion("") == ""


class TestPydanticStateCompat:
    """管线 state 中 debate/analyst 为 pydantic 对象时须正常提取。

    跑批实测：graph.invoke 返回的 state 里 DebateMessage 是 pydantic 实例，
    _summarize_debate 的 isinstance(dict) 静默跳过 → judge 拿到空辩论打 1 分
    （debate_quality 全 1.0 的根因，混在真实分数中不可察觉）。
    """

    def test_pydantic_debate_messages_extracted(self):
        from finance_agent.models import DebateMessage

        state = {
            "debate_history": [
                DebateMessage(
                    role="bull", round=1, content="看多：净息差改善", key_arguments=["a"]
                ),
                DebateMessage(role="bear", round=1, content="看空：不良抬头", key_arguments=["b"]),
            ],
        }
        vars_ = extract_judge_vars(state, query="q")
        assert "看多：净息差改善" in vars_["debate_history"]
        assert "看空：不良抬头" in vars_["debate_history"]

    def test_all_debate_rounds_kept_under_total_budget(self):
        """回归（2026-09-10 实测 2fd1ee6d）：多轮辩论总量超 _JUDGE_MAX_BYTES 时，
        整体 head/tail 截断把中间发言连【bear】标签一起挖掉——judge 评「逐条交锋」
        时看不到交锋过程。须按消息边界截断：每个角色标签都保留。"""
        messages = []
        for rnd, (role_a, role_b) in enumerate([("bull", "bear"), ("bull", "bear")], start=1):
            messages.append(
                {
                    "role": role_a,
                    "round": rnd,
                    "content": f"{role_a} 第{rnd}轮开场。" + "论据。" * 300,
                }
            )
            messages.append(
                {
                    "role": role_b,
                    "round": rnd,
                    "content": f"{role_b} 第{rnd}轮回应对方论点。" + "反驳。" * 300,
                }
            )
        state = {"debate_history": messages, "risk_debate_history": []}
        out = extract_judge_vars(state)["debate_history"]
        # 全部轮次的全部角色标签保留（中间发言不被整体截断挖掉）
        assert "【bull】" in out
        assert out.count("【bear】") == 2
        assert "第1轮回应对方论点" in out
        assert "第2轮回应对方论点" in out

    def test_key_arguments_prepended_to_debate_messages(self):
        """回归（2026-09-10）：DebateMessage.key_arguments 早已存在且被 LLM 填充，
        但 _summarize_debate 只拼 content 将其丢弃——judge 评「逐条交锋」时见不到
        每轮论点骨架。拼装 SHALL 把论点前置到每条发言开头（正文截断时骨架仍在）。"""
        state = {
            "debate_history": [
                {
                    "role": "bull",
                    "round": 1,
                    "key_arguments": ["估值下移空间未释放", "历史外推属归纳谬误"],
                    "content": "多头痛斥空头误读定价逻辑，展开论述。" * 50,
                },
                {
                    "role": "bear",
                    "round": 1,
                    "key_arguments": ["戴维斯双杀风险"],
                    "content": "空头反驳。",
                },
            ],
            "risk_debate_history": [],
        }
        out = extract_judge_vars(state)["debate_history"]
        # 论点骨架前置且完整（长正文被截断也不影响论点行）
        assert "论点: 估值下移空间未释放; 历史外推属归纳谬误" in out
        assert "论点: 戴维斯双杀风险" in out
        # pydantic DebateMessage 同样拼论点
        from finance_agent.models import DebateMessage

        state2 = {
            "debate_history": [
                DebateMessage(role="bull", round=1, content="看多", key_arguments=["净息差改善"])
            ]
        }
        out2 = extract_judge_vars(state2)["debate_history"]
        assert "论点: 净息差改善" in out2

    def test_pydantic_risk_debate_extracted(self):
        from finance_agent.models import DebateMessage

        state = {
            "risk_debate_history": [
                DebateMessage(role="aggressive", round=1, content="加仓", key_arguments=[])
            ]
        }
        vars_ = extract_judge_vars(state, query="q")
        assert "加仓" in vars_["risk_judgment"]


class TestSerializeDecisionEvidenceRefs:
    """_serialize_decision / trade_decision 变量含 evidence_refs。"""

    def test_judge_var_trade_decision_contains_evidence_refs(self):
        state = {
            "final_trade_decision": TradeDecision.model_validate(
                {
                    "action": "buy",
                    "confidence": 0.75,
                    "reasoning": "理由",
                    "evidence_refs": [{"claim": "ROE 3.4%", "source": "fundamental"}],
                }
            ),
            "analyst_reports": {},
            "risk_debate_history": [],
        }
        vars_ = extract_judge_vars(state)
        assert "evidence_refs" in vars_["trade_decision"]
        assert "fundamental" in vars_["trade_decision"]


class TestSummarizeAnalystReportsKeepsNumbers:
    """_summarize_analyst_reports 必须保留可核对的数值（claims 附注）。"""

    def test_claim_numbers_preserved(self):
        reports = {
            "fundamental": {
                "agent_name": "fundamental",
                "summary": "盈利能力稳健",
                "key_findings": ["ROE 提升"],
                "claims": [
                    {
                        "claim_type": "numerical",
                        "source_type": "data",
                        "field_ref": "profitability_metrics.roe.2024",
                        "stated_value": 3.4,
                        "interpretation": "ROE 处于行业中等水平",
                    }
                ],
                "markdown": "# fundamental\n正文",
            }
        }
        state = {
            "final_trade_decision": {},
            "analyst_reports": reports,
            "risk_debate_history": [],
        }
        vars_ = extract_judge_vars(state)
        assert "3.4" in vars_["analyst_reports"]
        assert "ROE 处于行业中等水平" in vars_["analyst_reports"]

    def test_all_agents_kept_under_total_budget(self):
        """回归（2026-09-09 实测）：4 个分析师结论+论据总量超 _JUDGE_MAX_BYTES(4096)
        时，整体 head/tail 截断会整段切掉中间 agent——fundamental 的 plain_conclusion
        在 judge 输入与标注材料里都不可见，consistency 评分无法核对各层结论。
        须按 agent 边界截断：每个 agent 的结论方向都保留。"""
        reports = {}
        for name, conclusion in (
            ("technical", "技术面偏多：均线多头排列，动能向上"),
            ("macro", "宏观中性：低通胀低利率，估值有支撑"),
            ("fundamental", "基本面优异但增速放缓：ROE 32.5%，营收转负"),
            ("sentiment", "舆情偏正面：渠道动作密集，资金回流"),
        ):
            reports[name] = {
                "agent_name": name,
                "plain_conclusion": conclusion,
                "claims": [
                    {
                        "claim_type": "numerical",
                        "source_type": "data",
                        "field_ref": "f",
                        "stated_value": float(i),
                        "interpretation": f"论据{name}{i}：数值核对" + "。" * 40,
                    }
                    for i in range(20)
                ],
            }
        state = {"final_trade_decision": {}, "analyst_reports": reports, "risk_debate_history": []}
        out = extract_judge_vars(state)["analyst_reports"]
        # 4 个 agent 全部保留（中间 agent 不被整体截断切掉）
        assert "【technical】" in out
        assert "【macro】" in out
        assert "【fundamental】" in out
        assert "【sentiment】" in out


class TestFormatClaims:
    """_format_claims 各分支（final review F3 加固）。"""

    def test_interpretation_and_value(self):
        from evals.extract import _format_claims

        out = _format_claims([{"interpretation": "ROE 提升", "stated_value": 3.4}])
        assert out == "ROE 提升(3.4)"

    def test_interpretation_only(self):
        from evals.extract import _format_claims

        out = _format_claims([{"interpretation": "盈利改善", "stated_value": ""}])
        assert out == "盈利改善"

    def test_value_only(self):
        from evals.extract import _format_claims

        out = _format_claims([{"interpretation": "", "stated_value": 0.05}])
        assert out == "0.05"

    def test_pydantic_claim_instance(self):
        from evals.extract import _format_claims

        c = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="x.y",
            stated_value=3.4,
            interpretation="ROE",
        )
        assert _format_claims([c]) == "ROE(3.4)"

    def test_empty_list(self):
        from evals.extract import _format_claims

        assert _format_claims([]) == ""


class TestAnchorMaterial:
    """add-debate-argument-anchors Task 5：锚点覆盖率 + judge 材料骨架行/论点前缀。"""

    def _checks(self) -> list[dict]:
        return [
            {
                "role": "bull",
                "round": 1,
                "index": 1,
                "kind": "data",
                "anchors": ["x"],
                "anchor_statuses": ["resolved"],
                "status": "resolved",
                "anchored": True,
            },
            {
                "role": "bear",
                "round": 1,
                "index": 1,
                "kind": "inference",
                "anchors": [],
                "anchor_statuses": [],
                "status": "none",
                "anchored": False,
            },
        ]

    def test_coverage_and_skeleton_line(self):
        from evals.extract import anchor_coverage, extract_judge_vars

        checks = self._checks()
        cov = anchor_coverage(checks)
        assert cov is not None
        assert cov["value"] == 0.5 and cov["unanchored_inference"] == 1

        state = {
            "debate_history": [
                {
                    "role": "bull",
                    "round": 1,
                    "content": "c",
                    "key_arguments": [{"text": "MA5 上穿", "kind": "data", "anchors": ["x"]}],
                },
            ],
            "debate_anchor_checks": checks[:1],
        }
        debate = extract_judge_vars(state, query="q")["debate_history"]
        assert "【锚点覆盖】" in debate
        assert "[data ✓]" in debate
        # 按方拆分：只有 bull 记录 → 只有 bull 方，缺席方省略（尾括号为全局拆项）
        assert "【锚点覆盖】bull 1/1（推断无锚 0，未解析 0，data/event 无锚 0，旧格式 0）" in debate
        assert "风控" not in debate

    def test_skeleton_line_per_party_counts_and_order(self):
        """骨架行按方拆分（bull / bear / 风控=三方聚合），全局拆项在尾括号；
        位于收敛信号之后、原始发言之前；缺席方省略。"""
        from evals.extract import extract_judge_vars

        def check(role, index, status, anchored):
            return {
                "role": role,
                "round": 1,
                "index": index,
                "kind": "data",
                "anchors": ["x"],
                "anchor_statuses": ["resolved"] if anchored else ["unresolved"],
                "status": status,
                "anchored": anchored,
            }

        state = {
            "debate_history": [
                {"role": "bull", "round": 1, "content": "多", "key_arguments": ["a", "b"]},
                {"role": "bear", "round": 1, "content": "空", "key_arguments": ["c", "d"]},
            ],
            "debate_anchor_checks": [
                check("bull", 1, "resolved", True),
                check("bull", 2, "none", False),
                check("bear", 1, "resolved", True),
                check("bear", 2, "resolved", True),
                check("aggressive", 1, "resolved", True),
                check("conservative", 1, "none", False),
                check("neutral", 1, "unspecified", False),
            ],
        }
        out = extract_judge_vars(state, query="q")["debate_history"]
        assert (
            "【锚点覆盖】bull 1/2｜bear 2/2｜风控 1/3"
            "（推断无锚 2，未解析 0，data/event 无锚 0，旧格式 1）"
        ) in out
        assert out.index("收敛信号") < out.index("【锚点覆盖】") < out.index("【bull】")

    def test_skeleton_line_absent_without_checks(self):
        """旧 trace（无 debate_anchor_checks 通道）→ 材料不得出现锚点骨架行。"""
        from evals.extract import extract_judge_vars

        state = {
            "debate_history": [
                {"role": "bull", "round": 1, "content": "多", "key_arguments": ["a"]}
            ]
        }
        assert "【锚点覆盖】" not in extract_judge_vars(state, query="q")["debate_history"]

    def test_coverage_none_without_checks(self):
        """无论点（含非 dict 噪声）→ None：无锚点数据的旧 trace 不得产出 0/0 假指标。"""
        from evals.extract import anchor_coverage

        assert anchor_coverage([]) is None
        assert anchor_coverage([None, "x"]) is None

    def test_mark_mapping_gates_on_status(self):
        """标记一律取 status：unspecified 的 anchor_statuses 非权威（恒 unresolved），
        unspecified→?、unresolved/missing→✗、none→○、resolved→✓。"""
        from evals.extract import extract_judge_vars

        def check(role, status, anchor_statuses):
            return {
                "role": role,
                "round": 1,
                "index": 1,
                "kind": "inference",
                "anchors": [],
                "anchor_statuses": anchor_statuses,
                "status": status,
                "anchored": status == "resolved",
            }

        state = {
            "debate_history": [
                {
                    "role": role,
                    "round": 1,
                    "content": "c",
                    "key_arguments": [{"text": f"{role} 论点", "kind": "inference"}],
                }
                for role in ("bull", "bear", "aggressive", "conservative")
            ],
            "debate_anchor_checks": [
                check("bull", "unspecified", ["unresolved"]),  # 非权威状态不得渲染成 ✗
                check("bear", "unresolved", ["unresolved"]),
                check("aggressive", "missing", []),
                check("conservative", "none", []),
            ],
        }
        out = extract_judge_vars(state, query="q")["debate_history"]
        assert "[inference ?] bull 论点" in out
        assert "[inference ✗] bear 论点" in out
        assert "[inference ✗] aggressive 论点" in out
        assert "[inference ○] conservative 论点" in out

    def test_pydantic_arguments_rendered_with_mark(self):
        """DebateArgument 实例取 text 渲染（不得 str() 成 dict-repr 噪声），
        检查记录按 (role, round, index) 命中并拼状态标记。"""
        from evals.extract import extract_judge_vars

        from finance_agent.models import DebateArgument, DebateMessage

        state = {
            "debate_history": [
                DebateMessage(
                    role="bull",
                    round=2,
                    content="看多",
                    key_arguments=[
                        DebateArgument(text="ROE 32.5%", kind="data", anchors=["f.roe"])
                    ],
                )
            ],
            "debate_anchor_checks": [
                {
                    "role": "bull",
                    "round": 2,
                    "index": 1,
                    "kind": "data",
                    "anchors": ["f.roe"],
                    "anchor_statuses": ["resolved"],
                    "status": "resolved",
                    "anchored": True,
                }
            ],
        }
        out = extract_judge_vars(state, query="q")["debate_history"]
        assert "[data ✓] ROE 32.5%" in out
        assert "kind=" not in out and "'kind'" not in out  # dict-repr 泄漏回归守卫

    def test_risk_material_without_checks_no_skeleton_line(self):
        """风控材料无检查记录时不得崩、不产出锚点骨架行，论点行仍渲染。"""
        state = {
            "risk_debate_history": [
                {"role": "aggressive", "round": 1, "content": "加仓", "key_arguments": ["x"]}
            ]
        }
        out = extract_judge_vars(state, query="q")
        assert "【锚点覆盖】" not in out["risk_debate_history"]
        assert "【锚点覆盖】" not in out["risk_judgment"]
        assert "论点: x" in out["risk_debate_history"]
