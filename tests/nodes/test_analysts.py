"""TDD tests for nodes/analysts.py — Layer I 分析师 Agent。

Agent 行为：
1. 接收 state（含 PREP 数据）
2. 构建 prompt context
3. 调用 LLM（mocked）
4. 解析 JSON 响应为 AnalystReport
5. 返回 state 更新
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pandas as pd

from finance_agent.models import AnalystReport
from finance_agent.nodes.analysts import (
    _build_fundamental_context,
    _build_macro_context,
    _build_technical_context,
    _retry_feedback_section,
    _series_semantic_header,
    technical_analyst,
)


def _mock_llm_response() -> str:
    """模拟 LLM 返回的 AnalystReport JSON。"""
    return json.dumps(
        {
            "agent_name": "technical",
            "summary": "技术面分析显示短期趋势向上",
            "plain_conclusion": "技术面偏多：短期趋势向上，MACD 金叉确认",
            "key_findings": ["MA5 上穿 MA20", "MACD 金叉"],
            "claims": [
                {
                    "claim_type": "numerical",
                    "source_type": "data",
                    "field_ref": "technical_indicators.MA.5.4",
                    "stated_value": 13.0,
                    "interpretation": "MA5 为 13.0",
                }
            ],
            "markdown": "## 技术面分析\n短期趋势向上。",
        },
        ensure_ascii=False,
    )


class TestTechnicalAnalyst:
    """Layer I 技术面分析师 Agent 测试。"""

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_produces_analyst_report(self, mock_llm):
        """技术分析师返回 AnalystReport 结构化输出。"""
        mock_llm.return_value = _mock_llm_response()
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "technical_indicators": {
                "MA": {"5": [None, None, None, None, 13.0, 14.0]},
            },
        }
        result = technical_analyst(state)

        assert "analyst_reports" in result
        report = result["analyst_reports"]["technical"]
        assert report.agent_name == "technical"
        assert len(report.claims) == 1
        assert report.claims[0].stated_value == 13.0

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_llm_response_with_code_block(self, mock_llm):
        """LLM 返回 markdown 代码块包裹的 JSON 也能正确解析。"""
        mock_llm.return_value = f"```json\n{_mock_llm_response()}\n```"
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "technical_indicators": {"MA": {"5": [None, None, None, None, 13.0]}},
        }
        result = technical_analyst(state)
        report = result["analyst_reports"]["technical"]
        assert report.agent_name == "technical"


class TestAnalystDegradationObservability:
    """解析降级与字段改写的可观测性（harden-llm-output-validation）。

    加固前降级与改写完全静默：解析失败产出 claims=[] 的报告，
    而零 claim 会使引用校验 all_passed=True（citation.py 的 failed == 0），
    即解析失败反而让校验「通过」，属隐蔽的静默失败。
    """

    _STATE = {
        "stock_name": "贵州茅台",
        "stock_code": "600519",
        "technical_indicators": {"MA": {"5": [None, None, None, None, 13.0]}},
    }

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_malformed_json_logs_warning(self, mock_llm, caplog):
        """解析失败记录 WARNING 日志，包含节点名。"""
        mock_llm.return_value = "这不是 JSON，只是一段自由文本"
        with caplog.at_level(logging.WARNING):
            technical_analyst(dict(self._STATE))
        assert any(
            r.levelno == logging.WARNING and "technical" in r.getMessage() for r in caplog.records
        ), f"未记录含节点名的 WARNING：{[r.getMessage() for r in caplog.records]}"

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_degraded_report_carries_marker(self, mock_llm):
        """降级报告携带可识别标记，使下游能区分「解析失败的零 claim」与「正常的零 claim」。"""
        mock_llm.return_value = "坏响应"
        report = technical_analyst(dict(self._STATE))["analyst_reports"]["technical"]
        assert report.parse_degraded is True
        assert report.claims == []

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_successful_parse_has_no_degraded_marker(self, mock_llm):
        """正常解析的报告不带降级标记（与降级路径可区分）。"""
        mock_llm.return_value = _mock_llm_response()
        report = technical_analyst(dict(self._STATE))["analyst_reports"]["technical"]
        assert report.parse_degraded is False

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_invalid_claim_type_logs_warning(self, mock_llm, caplog):
        """非法 claim_type 改写为 entity 时记录 WARNING（含原值与改写后值）。"""
        payload = json.loads(_mock_llm_response())
        payload["claims"][0]["claim_type"] = "textual"  # 不在 _VALID_CLAIM_TYPES 内
        mock_llm.return_value = json.dumps(payload, ensure_ascii=False)

        with caplog.at_level(logging.WARNING):
            report = technical_analyst(dict(self._STATE))["analyst_reports"]["technical"]

        assert report.claims[0].claim_type == "entity"  # 保留既有改写行为
        messages = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
        assert "textual" in messages and "entity" in messages, (
            f"WARNING 未含原值与改写值：{messages}"
        )

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_invalid_source_type_logs_warning(self, mock_llm, caplog):
        """非法 source_type 改写为 data 时记录 WARNING（含原值与改写后值）。"""
        payload = json.loads(_mock_llm_response())
        payload["claims"][0]["source_type"] = "hearsay"  # 不在 _VALID_SOURCE_TYPES 内
        mock_llm.return_value = json.dumps(payload, ensure_ascii=False)

        with caplog.at_level(logging.WARNING):
            report = technical_analyst(dict(self._STATE))["analyst_reports"]["technical"]

        assert report.claims[0].source_type == "data"  # 保留既有改写行为
        messages = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
        assert "hearsay" in messages and "data" in messages, f"WARNING 未含原值与改写值：{messages}"


class TestAnalystSpanMetadata:
    """解析降级 / 枚举改写路径的 span metadata（trace-observability Task 6）。

    降级与改写此前只记日志，trace 不可见；本组测试锁定 update_current_span
    写入的 metadata 与 level，使静默修复在 Langfuse trace 可见。
    """

    def test_parse_degraded_marks_span(self, monkeypatch):
        """JSON 解析失败降级时，span metadata 记 parse_degraded，level=WARNING。"""
        from finance_agent.nodes.analysts import _parse_analyst_report

        captured = {}

        def _fake_update(metadata=None, level=None):
            captured["metadata"] = metadata
            captured["level"] = level

        monkeypatch.setattr("finance_agent.nodes.analysts.update_current_span", _fake_update)

        # 喂非法 JSON 触发降级
        report = _parse_analyst_report("not a json {{{", "technical")
        assert report.parse_degraded is True
        md = captured["metadata"]
        assert md["degradation"] == "parse_degraded"
        assert captured["level"] == "WARNING"
        # raw_excerpt 必须存在且含原文（否则降级原因不可审计——「观测数据会撒谎」同族）
        assert md.get("raw_excerpt"), f"raw_excerpt 缺失/为空: {md}"
        assert "not a json" in md["raw_excerpt"]
        # 命名空间键：同 span 多次降级不互相覆盖（agent/轮次可区分）
        assert md["agent"] == "technical"
        assert md["degradation.technical.r0"] == "parse_degraded"

    def test_sanitize_claims_marks_span(self, monkeypatch):
        """非法枚举被改写时，span metadata 记 sanitize_claims。"""
        from finance_agent.nodes.analysts import _sanitize_claims

        captured = []

        def _fake_update(metadata=None, level=None):
            captured.append({"metadata": metadata, "level": level})

        monkeypatch.setattr("finance_agent.nodes.analysts.update_current_span", _fake_update)

        _sanitize_claims(
            {"claims": [{"claim_type": "非法类型", "source_type": "data"}]}, "technical"
        )
        # 断言至少一次 sanitize_claims 记录
        rec = next(
            (
                c
                for c in captured
                if c["metadata"] and c["metadata"].get("degradation") == "sanitize_claims"
            ),
            None,
        )
        assert rec is not None, f"未捕获 sanitize_claims 记录: {captured}"
        # 锁完整 metadata 值：键名漂移或 raw/fixed 写反都会让看板误读降级事实
        md = rec["metadata"]
        assert md["field"] == "claim_type"
        assert md["raw"] == "非法类型"
        assert md["fixed"] == "entity"
        assert md["agent"] == "technical"
        assert md["degradation.technical.r0.sanitize.claim_type"] == "非法类型->entity"
        assert rec["level"] == "WARNING"
        assert all(c["level"] == "WARNING" for c in captured if c["metadata"])

    def test_trace_errors_do_not_block_degradation(self):
        """Langfuse client 抛异常时，解析降级流程仍正常完成（降级不阻断业务）。"""
        from finance_agent.nodes.analysts import _parse_analyst_report

        mockClient = MagicMock()
        mockClient.update_current_span.side_effect = RuntimeError("langfuse down")
        with patch("finance_agent.langfuse_tracing.get_langfuse", return_value=mockClient):
            report = _parse_analyst_report("坏响应", "technical")
        # 埋点失败被 update_current_span 吞掉，业务照常降级产出报告
        assert report.parse_degraded is True
        assert report.claims == []


class TestTechnicalContextBudget:
    """analyst-context-budget delta：技术指标序列裁剪为最近 60 期。

    线上事故（601700 深研）：250 期全窗口指标 JSON 进 prompt，
    technical_analyst 单次 LLM 调用 11.5~14 分钟。
    """

    def _indicators(self, n: int) -> dict:
        full = [None] * 59 + [float(i) for i in range(n)]  # 前 59 期预热 null
        series = full[:n]
        return {
            "MA": {"5": list(series), "10": list(series)},
            "MACD": {"DIF": list(series), "DEA": list(series), "histogram": list(series)},
            "RSI": {"14": list(series)},
        }

    def test_full_window_series_trimmed_to_60(self):
        """250 期序列裁剪为最近 60 期，context 含窗口说明。"""
        from finance_agent.nodes.analysts import _build_technical_context

        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "technical_indicators": self._indicators(250),
        }
        ctx = _build_technical_context(state)

        assert "更早历史已省略" in ctx, "context 必须携带窗口说明，避免 LLM 误认截断窗口为全部历史"
        payload = ctx.split("技术指标数据", 1)[1].split(":\n", 1)[1]
        data = json.loads(payload)
        assert len(data["MA"]["5"]) == 60
        assert len(data["MACD"]["DIF"]) == 60
        # 序列值规则：index i（>=59）的值为 i-59；最近 60 期首元素为 index 190 → 131.0
        assert data["MA"]["5"][0] == 131.0

    def test_short_window_series_kept_intact(self):
        """不足 60 期的窗口保持完整。"""
        from finance_agent.nodes.analysts import _build_technical_context

        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "technical_indicators": self._indicators(45),
        }
        ctx = _build_technical_context(state)
        payload = ctx.split("技术指标数据", 1)[1].split(":\n", 1)[1]
        data = json.loads(payload)
        assert len(data["MA"]["5"]) == 45
        assert len(data["RSI"]["14"]) == 45

    def test_missing_indicators_keeps_fallback(self):
        """无 technical_indicators 时 context 不含指标段（既有兜底）。"""
        from finance_agent.nodes.analysts import _build_technical_context

        ctx = _build_technical_context({"stock_name": "X", "stock_code": "1"})
        assert "技术指标数据" not in ctx


class TestTechnicalContextArrayOrder:
    def test_context_declares_ascending_order_tail_latest(self):
        """序列数组为时间正序（旧→新），末尾为最新一期（incident 022 第四类疾病）。"""
        from finance_agent.nodes.analysts import _build_technical_context

        ctx = _build_technical_context(
            {
                "stock_name": "中际旭创",
                "stock_code": "300308",
                "technical_indicators": {
                    "MA": {"5": [1211.36, 858.318]},
                    "MACD": {"DIF": [96.64, -44.09]},
                    "RSI": {"14": [62.63, 41.56]},
                    "BOLL": {
                        "upper": [1282.05, 1016.9],
                        "middle": [1100.09, 915.36],
                        "lower": [918.14, 813.82],
                    },
                    "KDJ": {"K": [70.3, 19.57], "D": [78.28, 27.11], "J": [54.35, 4.49]},
                },
            }
        )
        assert "时间正序" in ctx and "末尾" in ctx and "最新" in ctx


class TestRetryFeedbackSection:
    def test_no_feedback_returns_empty(self):
        assert _retry_feedback_section({}, "fundamental") == ""

    def test_feedback_renders_failed_claims(self):
        state = {
            "citation_retry_feedback": {
                "fundamental": [
                    {
                        "field_ref": "solvency_metrics.资产负债率.2023",
                        "stated_value": 99.0,
                        "ground_truth": 38.0,
                        "delta": 61.0,
                        "interpretation": "2023 年资产负债率 99%",
                    }
                ]
            }
        }
        section = _retry_feedback_section(state, "fundamental")
        assert "上轮引用校验失败" in section
        assert "solvency_metrics.资产负债率.2023" in section
        assert "38.0" in section  # ground_truth 必须随反馈给出（与旧盲目重跑的关键区别）
        assert "99.0" in section

    def test_feedback_scoped_to_agent(self):
        state = {
            "citation_retry_feedback": {
                "macro": [
                    {
                        "field_ref": "x",
                        "stated_value": 1,
                        "ground_truth": 2,
                        "delta": 1,
                        "interpretation": "y",
                    }
                ]
            }
        }
        assert _retry_feedback_section(state, "fundamental") == ""


class TestSeriesSemanticHeader:
    def test_header_format(self):
        h = _series_semantic_header("时间正序(旧→新)", "index -1 = 最新交易日(2026-08-28)", 60)
        assert h == "# 序列语义: 时间正序(旧→新), index -1 = 最新交易日(2026-08-28), 共60期"

    def test_technical_header_declares_direction_and_latest_date(self):
        """incident 022 修复：语义头机生，期次与 state 实际数据一致。"""
        state = {
            "stock_name": "中际旭创",
            "stock_code": "300308",
            "kline": pd.DataFrame(
                {"日期": [f"2026-08-{d:02d}" for d in range(1, 29)], "收盘": [100.0] * 28}
            ),
            "technical_indicators": {"MA": {"5": [100.0 + i for i in range(28)]}},
        }
        ctx = _build_technical_context(state)
        assert "# 序列语义: 时间正序(旧→新)" in ctx
        assert "index -1 = 最新交易日(2026-08-28)" in ctx
        assert "共28期" in ctx
        # 与校验语义一致：列表末尾为最新一期（既有负索引声明仍在）
        assert "-1=最新一期" in ctx

    def test_technical_header_without_kline_degrades(self):
        """kline 缺失 → 方向声明保留，日期省略（不编造期次）。"""
        state = {"technical_indicators": {"MA": {"5": [1.0, 2.0]}}}
        ctx = _build_technical_context(state)
        assert "时间正序(旧→新)" in ctx
        assert "最新交易日" not in ctx

    def test_macro_header_declares_descending_and_latest_month(self):
        state = {
            "stock_name": "x",
            "stock_code": "x",
            "macro_indicators": {
                "cpi": {
                    "records": [
                        {"月份": "2026年07月份", "全国-同比增长": 0.4},
                        {"月份": "2026年06月份", "全国-同比增长": 0.5},
                    ],
                    "freshness": "fresh",
                }
            },
        }
        ctx = _build_macro_context(state)
        assert "# 序列语义: 时间降序(新→旧)" in ctx
        assert "index 0 = 最新一期(2026年07月份)" in ctx
        assert "共2期" in ctx

    def test_fundamental_headers(self):
        """报表段声明降序 + 首行最新期；季度趋势段声明 index 0 最新。"""
        df = pd.DataFrame(
            {"报告日": ["20251231", "20241231", "20231231"], "营业总收入": [1.0, 2.0, 3.0]}
        )
        state = {
            "stock_name": "x",
            "stock_code": "x",
            "balance_sheet": df,
            "income_statement": df,
            "cash_flow_statement": df,
            "quarterly_trend": {
                "quarters": ["2025Q4", "2025Q3"],
                "net_profit": [1.0, 2.0],
                "qoq": [1.0, 2.0],
                "yoy": [1.0, 2.0],
                "warnings": [],
            },
        }
        ctx = _build_fundamental_context(state)
        assert "行按报告期降序(新→旧), 首行 = 最新报告期(20251231)" in ctx
        assert "# 序列语义: 时间降序(新→旧), index 0 = 最新季度(2025Q4), 共2期" in ctx


class TestTechnicalHeaderDatetime:
    def test_kline_datetime64_header_iso_date(self):
        """机生语义头对 datetime64 日期列渲染 ISO 日期（与校验器解析口径一致）。"""
        import pandas as pd

        from finance_agent.nodes.analysts import _build_technical_context

        ctx = _build_technical_context(
            {
                "stock_name": "测试",
                "stock_code": "000001",
                "kline": pd.DataFrame({"日期": pd.to_datetime(["2026-08-27", "2026-08-28"])}),
                "technical_indicators": {"RSI": {"14": [62.63, 41.56]}},
            }
        )
        assert "最新交易日(2026-08-28)" in ctx


class TestCoverageGapFeedbackRender:
    """D6：覆盖率缺口反馈渲染（补建 claim 或删除正文数字）。"""

    def test_coverage_gap_line_rendered(self):
        from finance_agent.nodes.analysts import _retry_feedback_section

        state = {
            "citation_retry_feedback": {
                "fundamental": [{"kind": "coverage_gap", "raw": "1400亿", "value": 1.4e11}]
            }
        }
        section = _retry_feedback_section(state, "fundamental")
        assert "1400亿" in section
        assert "补建对应 claim" in section
        assert "删除该正文数字" in section

    def test_value_mismatch_feedback_still_renders(self):
        from finance_agent.nodes.analysts import _retry_feedback_section

        state = {
            "citation_retry_feedback": {
                "fundamental": [
                    {
                        "field_ref": "x",
                        "stated_value": 1.0,
                        "ground_truth": 2.0,
                        "delta": 1.0,
                        "interpretation": "i",
                    }
                ]
            }
        }
        section = _retry_feedback_section(state, "fundamental")
        assert "field_ref=x" in section
        assert "真实值 2.0" in section


class TestDegradedReportHonesty:
    """r1 实验实证：解析失败的兜底文案「基本面数据缺失」是谎言——数据没缺，是 JSON
    解析挂了；judge 据此判「fundamental 无数据」，最终报告也向用户展示假结论。
    兜底须如实说「解析失败」，且能从原始文本尽力打捞 summary/plain_conclusion。
    """

    _STATE = TestAnalystDegradationObservability._STATE

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_fallback_conclusion_says_parse_failed_not_data_missing(self, mock_llm):
        mock_llm.return_value = "坏响应"
        report = technical_analyst(dict(self._STATE))["analyst_reports"]["technical"]
        assert report.parse_degraded is True
        assert "数据缺失" not in report.plain_conclusion
        assert "解析" in report.plain_conclusion

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_salvages_summary_and_conclusion_from_truncated_json(self, mock_llm):
        """JSON 截断无法修复，但已闭合的 summary/plain_conclusion 字段应被打捞。"""
        mock_llm.return_value = (
            "```json\n{\n"
            '  "agent_name": "technical",\n'
            '  "summary": "均线空头排列，MACD 死叉",\n'
            '  "plain_conclusion": "技术面偏空：短期趋势向下",\n'
            '  "key_findings": ["MA5 下穿'
        )
        report = technical_analyst(dict(self._STATE))["analyst_reports"]["technical"]
        assert report.parse_degraded is True
        assert report.summary == "均线空头排列，MACD 死叉"
        assert report.plain_conclusion == "技术面偏空：短期趋势向下"


class TestRerunKeepsValidReport:
    """引用重试重跑分析师时，降级结果不得覆盖已有的正常报告（r1 中芯实证：
    第 3 代解析失败的兜底覆盖了前两代好报告，辩手与最终报告看到的版本不一致）。
    """

    _STATE = TestAnalystDegradationObservability._STATE

    def _good(self) -> AnalystReport:
        return AnalystReport(
            agent_name="technical",
            summary="好版本：短期趋势向上",
            plain_conclusion="技术面偏多",
            key_findings=["MA5 上穿 MA20"],
            claims=[],
            markdown="## 技术面\n好版本",
        )

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_degraded_rerun_keeps_existing_valid_report(self, mock_llm):
        good = self._good()
        state = {**self._STATE, "analyst_reports": {"technical": good}}
        mock_llm.return_value = "坏响应"
        out = technical_analyst(state)["analyst_reports"]["technical"]
        assert out.parse_degraded is False
        assert out.summary == "好版本：短期趋势向上"

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_degraded_rerun_keeps_existing_valid_dict_report(self, mock_llm):
        """state 里的既有报告可能已是 dict 序列化形态。"""
        state = {**self._STATE, "analyst_reports": {"technical": self._good().model_dump()}}
        mock_llm.return_value = "坏响应"
        out = technical_analyst(state)["analyst_reports"]["technical"]
        assert (
            getattr(
                out, "parse_degraded", out.get("parse_degraded") if isinstance(out, dict) else None
            )
            is False
        )

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_valid_rerun_replaces_existing(self, mock_llm):
        state = {**self._STATE, "analyst_reports": {"technical": self._good()}}
        mock_llm.return_value = _mock_llm_response()
        out = technical_analyst(state)["analyst_reports"]["technical"]
        assert out.summary == "技术面分析显示短期趋势向上"

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_first_run_degraded_still_returns_degraded(self, mock_llm):
        """没有既有报告时，降级报告照常返回（不改变首轮行为）。"""
        mock_llm.return_value = "坏响应"
        out = technical_analyst(dict(self._STATE))["analyst_reports"]["technical"]
        assert out.parse_degraded is True


class TestDegradationKeysNamespaced:
    """r1 实证：分析师节点没有自己的 span，update_current_span 全落到根 span，
    `degradation` 键被后来的降级覆盖——中芯 ≥3 次降级只剩最后一个、agent=None，
    规范「降级 SHALL 可被发现」形同虚设。键须带 agent + 重试轮次，互不覆盖。
    """

    def _capture(self, monkeypatch):
        captured: list[dict] = []
        monkeypatch.setattr(
            "finance_agent.nodes.analysts.update_current_span",
            lambda metadata=None, level=None: captured.append(
                {"metadata": metadata, "level": level}
            ),
        )
        return captured

    def test_parse_degraded_key_carries_agent_and_round(self, monkeypatch):
        from finance_agent.nodes.analysts import _parse_analyst_report

        captured = self._capture(monkeypatch)
        _parse_analyst_report("not a json {{{", "fundamental", round_no=2)
        md = captured[-1]["metadata"]
        assert md["degradation.fundamental.r2"] == "parse_degraded"
        assert md["agent"] == "fundamental"
        assert "raw_excerpt.fundamental.r2" in md

    def test_two_agents_same_round_do_not_collide(self, monkeypatch):
        from finance_agent.nodes.analysts import _parse_analyst_report

        captured = self._capture(monkeypatch)
        _parse_analyst_report("bad", "fundamental", round_no=0)
        _parse_analyst_report("bad", "macro", round_no=0)
        keys = {k for c in captured for k in (c["metadata"] or {})}
        assert {"degradation.fundamental.r0", "degradation.macro.r0"} <= keys

    def test_sanitize_key_carries_agent_round_and_field(self, monkeypatch):
        from finance_agent.nodes.analysts import _sanitize_claims

        captured = self._capture(monkeypatch)
        _sanitize_claims(
            {"claims": [{"claim_type": "非法类型", "source_type": "data"}]}, "technical", round_no=1
        )
        md = next(c["metadata"] for c in captured if c["metadata"])
        assert md["degradation.technical.r1.sanitize.claim_type"] == "非法类型->entity"

    @patch("finance_agent.nodes.analysts.call_llm_streaming_with_fallback")
    def test_node_passes_iteration_count_as_round(self, mock_llm, monkeypatch):
        """节点把 state.iteration_count（引用重试轮次）作为轮次传入。"""
        captured = self._capture(monkeypatch)
        mock_llm.return_value = "坏响应"
        state = {**TestAnalystDegradationObservability._STATE, "iteration_count": 2}
        technical_analyst(state)
        assert any("degradation.technical.r2" in (c["metadata"] or {}) for c in captured)


class TestCoverageSourcesContext:
    """新信源上下文装配（add-analyst-data-coverage Task 5，TDD 先行）。"""

    def test_fundamental_context_includes_announcements_and_reports(self):
        from finance_agent.nodes.analysts import _build_fundamental_context

        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "announcements": [
                {
                    "title": "2026年半年度报告",
                    "date": "2026-08-15",
                    "category": "财务报告",
                    "url": "http://a/1",
                },
                {
                    "title": "关于回购股份的公告",
                    "date": "2026-07-01",
                    "category": "股份回购",
                    "url": "",
                },
            ],
            "research_reports": [
                {
                    "title": "中报点评",
                    "org": "浙商证券",
                    "rating": "买入",
                    "publish_date": "2026-08-20",
                }
            ],
            "analyst_consensus": {
                "status": "covered",
                "report_count": 12,
                "rating_buy": 9,
                "rating_add": 3,
                "rating_neutral": 0,
                "rating_reduce": 0,
                "rating_sell": 0,
                "eps_y1": 2.10,
                "eps_y1_year": 2026,
            },
        }
        ctx = _build_fundamental_context(state)
        assert "公告" in ctx and "2026年半年度报告" in ctx
        assert "研报" in ctx and "买入" in ctx and "浙商证券" in ctx and "2026-08-20" in ctx
        assert "一致预期" in ctx and "近6个月研报数 12" in ctx and "2026E EPS 2.1" in ctx
        assert "不得" in ctx  # 防锚定条款

    def test_fundamental_context_no_coverage_is_explicit(self):
        from finance_agent.nodes.analysts import _build_fundamental_context

        ctx = _build_fundamental_context(
            {
                "stock_name": "x",
                "stock_code": "y",
                "analyst_consensus": {"status": "no_coverage"},
            }
        )
        assert "无分析师覆盖" in ctx
        consensus_line = ctx.split("一致预期")[1].split("\n")[0]
        assert "0" not in consensus_line

    def test_fundamental_context_consensus_missing_is_explicit(self):
        from finance_agent.nodes.analysts import _build_fundamental_context

        ctx = _build_fundamental_context({"stock_name": "x", "stock_code": "y"})
        assert "一致预期数据缺失" in ctx

    def test_fundamental_context_includes_manual_report_digest(self):
        from finance_agent.nodes.analysts import _build_fundamental_context

        ctx = _build_fundamental_context(
            {
                "stock_name": "x",
                "stock_code": "y",
                "manual_report_digest": {
                    "report_count": 2,
                    "brokers": ["国信证券", "开源证券"],
                    "latest_report_date": "2026-10-19",
                    "ratings": {"买入": 1, "增持": 1},
                    "target_price": {"min": 30.0, "median": 35.0, "max": 40.0, "count": 2},
                    "eps_forecasts": {"2026": {"min": 0.5, "median": 0.6, "max": 0.7, "count": 2}},
                    "core_logic": ["IDC回暖"],
                    "catalysts": ["新订单"],
                    "risks": ["竞争加剧"],
                },
            }
        )
        assert "手工投喂研报合并" in ctx
        assert "国信证券" in ctx and "开源证券" in ctx
        assert "2026E 0.50~0.70" in ctx
        assert "核心逻辑" in ctx and "IDC回暖" in ctx
        assert "不得直接作为你的结论依据" in ctx

    def test_fundamental_context_empty_sources(self):
        from finance_agent.nodes.analysts import _build_fundamental_context

        ctx = _build_fundamental_context({"stock_name": "x", "stock_code": "y"})
        assert "暂无" in ctx  # 空态显式声明，不静默消失

    def test_sentiment_context_includes_events(self):
        from finance_agent.nodes.analysts import _build_sentiment_context

        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "share_unlock": [
                {"date": "2026-10-09", "shares": 120000000, "market_value": 14300000000}
            ],
            "block_trades": [{"date": "2026-09-10", "price": 1270.0, "premium": -0.5}],
        }
        ctx = _build_sentiment_context(state)
        assert "解禁" in ctx and "2026-10-09" in ctx
        assert "大宗" in ctx and "1270" in ctx


class TestFundamentalPeerComparisonInjection:
    def test_peer_comparison_string_injected_verbatim(self):
        from finance_agent.nodes.analysts import _build_fundamental_context

        peer_text = "同业对比（主标的首行）：\n| 名称 | 代码 |\n|---|---|"
        state = {"peer_comparison": peer_text}
        ctx = _build_fundamental_context(state)
        # 差异化断言：精确拼接锁定字符串直通。
        # 旧路径 json.dumps(str, ensure_ascii=False) 会加首尾引号并把 \n 转义成字面两字符，
        # 本断言在旧实现下必然失败，新实现（isinstance str 直通）下通过。
        assert f"同业对比（state 键 peer_comparison）:\n{peer_text}" in ctx

    def test_legacy_dict_still_rendered(self):
        """防御：存量标志位 dict 形态不炸（向后兼容）。"""
        from finance_agent.nodes.analysts import _build_fundamental_context

        ctx = _build_fundamental_context({"peer_comparison": {"available": True}})
        assert "peer_comparison" in ctx

    def test_no_peer_comparison_no_section(self):
        from finance_agent.nodes.analysts import _build_fundamental_context

        ctx = _build_fundamental_context({})
        assert "peer_comparison" not in ctx
