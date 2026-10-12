"""factors/propose.py 单测（add-factor-proposal-agent Task 1–3；stub client 零网络）。

计划 docs/superpowers/plans/2026-10-13-add-factor-proposal-agent.md P1–P5：
- 合同：expression（DSL 合法）/ hypothesis（≥30 字，中文按字符数，去首尾空白）/ category 枚举；
- repair：首轮 + 至多 2 次修复 = 3 次尝试；每次修复把解析错误消息原文作为上下文追加；
- 三次仍不可解析 → 该条进 rejected（含最后错误）+ trace metadata 记 proposal_rejected；
- 软评分 alignment_score 只写元数据、不参与任何硬拒收；
- broad 批 n ≥ 20（入参校验）；
- 双档路由 broad→quick / deep→deep（PolicyRouter，T2）；
- 跨桶采样 prompt 组装（T3）。

零真实网络：全部用例经 stub client / monkeypatch，prompt 模板走本地兜底
（autouse fixture 把 loader._get_client 钉成 None）。
"""

from __future__ import annotations

import pytest

from finance_agent.factors.propose import (
    CATEGORIES,
    ProposalBatchResult,
    ProposalResponseError,
    propose_broad_batch,
    propose_deep_improvement,
    validate_proposal_payload,
)

HYPOTHESIS_OK = "短期均值回复效应：收盘价偏离其短期均值后存在回归倾向，成交量放大时更显著。"
BAD_EXPRESSION = "unknown_op(close, 1)"


@pytest.fixture(autouse=True)
def _local_prompts_only(monkeypatch):
    """prompt 模板走本地兜底：加载器客户端钉成 None + 清 TTL 缓存（零网络）。"""
    from finance_agent.prompts import loader

    monkeypatch.setattr(loader, "_get_client", lambda: None)
    loader._clear_cache()
    yield
    loader._clear_cache()


def _payload(**overrides):
    data = {
        "expression": "ts_mean(close, 5)",
        "hypothesis": HYPOTHESIS_OK,
        "category": "价量",
    }
    data.update(overrides)
    return data


class StubClient:
    """脚本化 stub：依次返回队列中的 payload（Exception 项则抛出）；记录每次调用。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete_json(self, prompt, *, purpose, metadata):
        self.calls.append({"prompt": prompt, "purpose": purpose, "metadata": dict(metadata)})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestContractValidation:
    """P2 合同：expression / hypothesis / category；alignment_score 软信号。"""

    def test_legal_payload_returns_proposal(self):
        proposal, error = validate_proposal_payload(_payload())
        assert error is None
        assert proposal is not None
        assert proposal.expression == "ts_mean(close, 5)"
        assert proposal.hypothesis == HYPOTHESIS_OK
        assert proposal.category == "价量"
        assert proposal.alignment_score is None
        assert proposal.mode == "broad"

    def test_unknown_operator_error_carries_parse_message(self):
        proposal, error = validate_proposal_payload(_payload(expression=BAD_EXPRESSION))
        assert proposal is None
        assert error is not None
        # DSL 解析错误原文（含未知算子名与位置）
        assert "未知算子" in error
        assert "unknown_op" in error

    def test_complexity_violation_rejected_via_default_parse_path(self):
        """提案路径必须走默认复杂度硬约束（enforce_complexity=True）。"""
        expr = "add(close, add(close, add(close, add(close, close))))"
        proposal, error = validate_proposal_payload(_payload(expression=expr))
        assert proposal is None
        assert error is not None
        assert "复杂度" in error

    def test_hypothesis_length_boundary_30_chars(self):
        proposal, error = validate_proposal_payload(_payload(hypothesis="字" * 29))
        assert proposal is None
        assert error is not None
        assert "30" in error
        # 30 字整（首尾空白去除后计数）放行
        proposal, error = validate_proposal_payload(_payload(hypothesis=" " + "字" * 30 + "\n"))
        assert error is None
        assert proposal is not None

    def test_category_must_be_in_enum(self):
        proposal, error = validate_proposal_payload(_payload(category="成长"))
        assert proposal is None
        assert error is not None
        assert "成长" in error
        for category in CATEGORIES:
            p, e = validate_proposal_payload(_payload(category=category))
            assert e is None
            assert p is not None and p.category == category

    def test_missing_fields_rejected(self):
        proposal, error = validate_proposal_payload({})
        assert proposal is None and error is not None
        proposal, error = validate_proposal_payload(_payload(expression="  "))
        assert proposal is None and error is not None
        proposal, error = validate_proposal_payload(
            {"expression": "close", "hypothesis": None, "category": "价量"}
        )
        assert proposal is None and error is not None

    def test_alignment_score_is_soft_and_never_rejects(self):
        """P5：alignment_score=0 仍进入 proposals；非数值不阻塞（软信号）。"""
        proposal, error = validate_proposal_payload(_payload(alignment_score=0))
        assert error is None
        assert proposal is not None and proposal.alignment_score == 0.0
        proposal, _ = validate_proposal_payload(_payload(alignment_score=0.73))
        assert proposal is not None and proposal.alignment_score == 0.73
        # 越界收敛到 [0,1]（软信号不因分值拒收）
        proposal, error = validate_proposal_payload(_payload(alignment_score=3))
        assert error is None and proposal is not None and proposal.alignment_score == 1.0
        # 非数值 → 未评分（None），仍放行
        proposal, error = validate_proposal_payload(_payload(alignment_score="高"))
        assert error is None and proposal is not None and proposal.alignment_score is None


class TestRepairLoop:
    """P2/P3：首轮 + 2 次修复；耗尽 → rejected + trace proposal_rejected。"""

    def test_third_attempt_success_yields_one_proposal(self):
        bad = _payload(expression=BAD_EXPRESSION)
        client = StubClient([bad, bad, _payload()])
        result = propose_broad_batch(
            n=20, client=client, bucket_stats={"价量": 1}, operator_ic={"ts_mean": 0.02}
        )
        assert isinstance(result, ProposalBatchResult)
        assert [p.expression for p in result.proposals] == ["ts_mean(close, 5)"]
        assert result.proposals[0].mode == "broad"
        assert result.rejected == ()
        assert len(client.calls) == 3
        for call in client.calls:
            assert call["purpose"] == "factor_proposal"
            assert call["metadata"]["purpose_tag"] == "factor_proposal"
            assert call["metadata"]["mode"] == "broad"
        # 修复上下文：上一轮错误原文进入后续 prompt
        assert "unknown_op" in client.calls[1]["prompt"]
        assert "unknown_op" in client.calls[2]["prompt"]

    def test_three_failures_reject_with_trace_marker(self):
        bad1 = _payload(expression=BAD_EXPRESSION)
        bad2 = _payload(hypothesis="太短")
        bad3 = _payload(category="成长")
        client = StubClient([bad1, bad2, bad3])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert result.proposals == ()
        assert len(result.rejected) == 1
        assert result.rejected[0].raw == bad3
        assert "成长" in result.rejected[0].error
        meta = result.trace_metadata
        assert meta["purpose_tag"] == "factor_proposal"
        assert meta["mode"] == "broad"
        assert meta["rejected"][0]["error"] == result.rejected[0].error
        assert meta["rejected"][0]["raw"] == bad3
        assert meta["proposal_rejected"] is True

    def test_no_rejection_has_no_marker(self):
        client = StubClient([_payload()])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert result.trace_metadata["rejected"] == []
        assert "proposal_rejected" not in result.trace_metadata

    def test_partial_acceptance_repairs_only_failing_item(self):
        good = _payload()
        bad_hypothesis = "短期动量延续效应：过去数日涨幅与未来收益正相关，成交量放大时更强。"
        bad = _payload(expression=BAD_EXPRESSION, hypothesis=bad_hypothesis)
        fixed = _payload(expression="ts_std(close, 10)")
        client = StubClient([{"proposals": [good, bad]}, {"proposals": [fixed]}])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert [p.expression for p in result.proposals] == [
            "ts_mean(close, 5)",
            "ts_std(close, 10)",
        ]
        assert result.rejected == ()
        assert len(client.calls) == 2
        # 失败条目原文 + 错误进修复上下文；已通过的条目不重发
        assert "unknown_op" in client.calls[1]["prompt"]
        assert bad_hypothesis in client.calls[1]["prompt"]
        assert HYPOTHESIS_OK not in client.calls[1]["prompt"]

    def test_malformed_response_repairs_then_rejects_with_raw_text(self):
        failure = ProposalResponseError("响应不是 JSON：Expecting value", raw_text="not json")
        client = StubClient([failure, failure, failure])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert result.proposals == ()
        assert len(result.rejected) == 1
        assert result.rejected[0].raw["raw_text"] == "not json"
        assert "不是 JSON" in result.rejected[0].error
        assert result.trace_metadata["proposal_rejected"] is True

    def test_payload_without_proposals_list_is_repaired(self):
        client = StubClient([{"oops": 1}, _payload()])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert len(result.proposals) == 1
        assert result.rejected == ()
        assert "proposals" in client.calls[1]["prompt"]

    def test_deep_improvement_uses_deep_mode_and_feedback(self):
        feedback = [{"原因": "DSL 解析失败", "条数": 3}]
        client = StubClient([_payload()])
        result = propose_deep_improvement(feedback=feedback, client=client)
        assert result.proposals[0].mode == "deep"
        assert client.calls[0]["metadata"]["mode"] == "deep"
        assert "DSL 解析失败" in client.calls[0]["prompt"]

    def test_broad_batch_rejects_n_below_20_before_any_call(self):
        client = StubClient([])
        with pytest.raises(ValueError):
            propose_broad_batch(n=19, client=client, bucket_stats={}, operator_ic={})
        assert client.calls == []
