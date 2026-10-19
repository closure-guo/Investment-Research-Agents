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

import logging
from pathlib import Path

import pytest

from finance_agent.factors import propose
from finance_agent.factors.propose import (
    CATEGORIES,
    GatewayProposalClient,
    ProposalBatchResult,
    ProposalResponseError,
    build_broad_prompt,
    build_deep_prompt,
    propose_broad_batch,
    propose_deep_improvement,
    route_proposal_call,
    validate_proposal_payload,
)
from finance_agent.llm.errors import UnsupportedCapabilityError
from finance_agent.llm.types import Capability, ModelProfile

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
        expr = (
            "add(close, add(close, add(close, add(close, "
            "add(close, add(close, add(close, add(close, close))))))))"
        )
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
        # 第二响应是首轮不足 n 触发的补充轮（评审修正 5）；空列表终止补充。
        client = StubClient([_payload(), {"proposals": []}])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert result.trace_metadata["rejected"] == []
        assert "proposal_rejected" not in result.trace_metadata
        assert len(client.calls) == 2

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


def _stub_capability(*, json_schema: str = "json_mode", max_output: int = 8192) -> Capability:
    return Capability(
        tools="single",
        tool_choice_required=False,
        streaming=True,
        streaming_tool_calls=True,
        json_schema=json_schema,  # type: ignore[arg-type]
        supports_system_role=True,
        reasoning_field=None,
        reasoning_must_echo_on_tool=False,
        reasoning_forced=False,
        max_context=128000,
        max_output=max_output,
        extra_body_allowed=True,
    )


def _stub_profile(
    name: str, *, json_schema: str = "json_mode", base_url: str | None = None
) -> ModelProfile:
    return ModelProfile(
        name=name,
        provider="openai",
        model=f"openai/{name}",
        base_url=base_url,
        api_key=None,
        capability=_stub_capability(json_schema=json_schema),
    )


class TestTierRouting:
    """P1：broad→quick / deep→deep 的双档路由（PolicyRouter 调用点）。"""

    def test_route_broad_prefers_quick_tier(self, monkeypatch):
        seen = []

        def fake_resolve(*, purpose, llm_config=None):
            seen.append(purpose)
            return _stub_profile(f"env:{purpose}")

        monkeypatch.setattr(propose, "resolve_profile", fake_resolve)
        result = route_proposal_call("broad")
        assert result.primary.name == "env:quick"
        assert [p.name for p in result.fallback_chain] == ["env:deep"]
        assert seen == ["quick", "deep"]

    def test_route_deep_prefers_deep_tier(self, monkeypatch):
        seen = []

        def fake_resolve(*, purpose, llm_config=None):
            seen.append(purpose)
            return _stub_profile(f"env:{purpose}")

        monkeypatch.setattr(propose, "resolve_profile", fake_resolve)
        result = route_proposal_call("deep")
        assert result.primary.name == "env:deep"
        assert [p.name for p in result.fallback_chain] == ["env:quick"]
        assert seen == ["deep", "quick"]

    def test_route_passes_request_config_to_both_tiers(self, monkeypatch):
        seen = []
        config = {"model": "m", "baseUrl": "http://llm.invalid"}

        def fake_resolve(*, purpose, llm_config=None):
            seen.append((purpose, llm_config))
            return _stub_profile(f"env:{purpose}")

        monkeypatch.setattr(propose, "resolve_profile", fake_resolve)
        route_proposal_call("deep", llm_config=config)
        assert seen == [("deep", config), ("quick", config)]

    def test_route_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            route_proposal_call("sideways")

    def test_route_falls_to_deep_tier_when_quick_lacks_json(self, monkeypatch):
        """能力不满足时切换至满足能力的候选（REQUIRED_CAPS 生效）。"""

        def fake_resolve(*, purpose, llm_config=None):
            if purpose == "quick":
                return _stub_profile("quick-plain", json_schema="none")
            return _stub_profile("deep-json")

        monkeypatch.setattr(propose, "resolve_profile", fake_resolve)
        result = route_proposal_call("broad")
        assert result.primary.name == "deep-json"

    def test_route_raises_typed_error_when_no_tier_satisfies_json(self, monkeypatch):
        monkeypatch.setattr(
            propose,
            "resolve_profile",
            lambda **kw: _stub_profile("no-json", json_schema="none"),
        )
        with pytest.raises(UnsupportedCapabilityError):
            route_proposal_call("broad")

    def test_batch_trace_metadata_carries_router_decision(self, monkeypatch):
        monkeypatch.setattr(
            propose, "resolve_profile", lambda **kw: _stub_profile("env:openai/main")
        )
        client = StubClient([_payload(), {"proposals": []}])  # 补充轮终止响应（修正 5）
        result = propose_broad_batch(
            n=20,
            client=client,
            llm_config={"model": "m", "baseUrl": "http://llm.invalid"},
            bucket_stats={},
            operator_ic={},
        )
        router = result.trace_metadata["router"]
        assert router["profile"] == "env:openai/main"
        assert router["capability"]["json_schema"] == "json_mode"


class TestGatewayClient:
    """默认客户端的 gateway 调用契约（monkeypatch，零网络）。"""

    def _patch(self, monkeypatch, profile):
        monkeypatch.setattr(propose, "resolve_profile", lambda **kw: profile)
        captured: dict = {}

        def fake_complete_text(messages, **kwargs):
            captured["messages"] = messages
            captured.update(kwargs)
            return captured.get("text", '{"proposals": []}'), {}

        monkeypatch.setattr(propose, "complete_text", fake_complete_text)
        return captured

    def test_pins_gateway_call_purpose_and_trace_metadata(self, monkeypatch):
        profile = _stub_profile("env:openai/quick-x", base_url="http://llm.invalid")
        captured = self._patch(monkeypatch, profile)
        client = GatewayProposalClient(mode="broad")
        payload = client.complete_json(
            "PROMPT-文本", purpose="factor_proposal", metadata={"mode": "broad"}
        )
        assert payload == {"proposals": []}
        assert captured["messages"] == [{"role": "user", "content": "PROMPT-文本"}]
        assert captured["purpose"] == "factor_proposal"
        # 路由选中的 profile 原样钉死进本次调用（评审修正 1：不给 gateway 重解析的机会）
        assert captured["profile"] is profile
        assert captured.get("llm_config") is None
        assert captured.get("preset") is None
        meta = captured["trace"]["metadata"]
        assert meta["purpose_tag"] == "factor_proposal"
        assert meta["mode"] == "broad"
        assert meta["router"]["profile"] == profile.name

    def test_routed_profile_pinned_against_purpose_resolution_drift(self, monkeypatch):
        """评审修正 1 回归（review repro）：env 双档解析不同模型且无 LLM_BASE_URL 时，
        实际调用必须钉死路由选中的 quick profile（deepseek-chat），不得回落到 LLM_MODEL
        （deepseek-reasoner）——此前的 `{}` 选择参数会让 gateway 按 purpose 重解析。"""
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("LLM_QUICK_MODEL", "deepseek/deepseek-chat")
        monkeypatch.setenv("LLM_MODEL", "deepseek/deepseek-reasoner")
        captured: dict = {}

        def fake_complete_text(messages, **kwargs):
            captured["messages"] = messages
            captured.update(kwargs)
            return '{"proposals": []}', {}

        monkeypatch.setattr(propose, "complete_text", fake_complete_text)
        route = route_proposal_call("broad")
        assert route.primary.model == "deepseek/deepseek-chat"
        client = GatewayProposalClient(mode="broad")
        client.complete_json("p", purpose="factor_proposal", metadata={})
        pinned = captured["profile"]
        assert pinned.name == route.primary.name
        assert pinned.model == "deepseek/deepseek-chat"
        assert pinned.model != "deepseek/deepseek-reasoner"

    def test_preset_profile_pinned_via_profile_override(self, monkeypatch):
        profile = _stub_profile("deepseek-official", base_url=None)
        captured = self._patch(monkeypatch, profile)
        client = GatewayProposalClient(mode="deep")
        client.complete_json("p", purpose="factor_proposal", metadata={})
        assert captured["profile"] is profile
        assert captured.get("preset") is None
        assert captured.get("llm_config") is None

    def test_unparsable_gateway_text_raises_response_error(self, monkeypatch):
        profile = _stub_profile("env:openai/quick-x", base_url="http://llm.invalid")
        captured = self._patch(monkeypatch, profile)
        captured["text"] = "这不是 JSON"
        client = GatewayProposalClient(mode="broad")
        with pytest.raises(ProposalResponseError) as exc_info:
            client.complete_json("p", purpose="factor_proposal", metadata={})
        assert exc_info.value.raw_text == "这不是 JSON"

    def test_unknown_mode_rejected_at_construction(self):
        with pytest.raises(ValueError):
            GatewayProposalClient(mode="sideways")


class TestPromptAssembly:
    """P4：跨桶采样 prompt 组装（分桶占比 + 算子先验 + 跨桶指令；拒收/衰减反馈注入）。"""

    def test_template_file_exists_with_placeholders(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "finance_agent"
            / "prompts"
            / "factor_proposal.md"
        )
        text = path.read_text(encoding="utf-8")
        assert text.strip()
        assert "{{sampling_context}}" in text
        assert "{{extra_instruction}}" in text
        assert "{{categories}}" in text

    def test_prompt_template_is_loaded_and_fully_rendered(self, monkeypatch):
        seen = []

        def fake_load(name):
            seen.append(name)
            return "M {{categories}} | S {{sampling_context}} | E {{extra_instruction}}"

        monkeypatch.setattr(propose, "load_prompt", fake_load)
        prompt = build_broad_prompt(bucket_stats={}, operator_ic={}, n=20)
        assert seen == ["factor_proposal"]
        assert "{{" not in prompt  # 占位符全部替换，无残留
        assert "价量/反转/动量/流动性/波动率" in prompt

    def test_broad_prompt_contains_bucket_shares_and_operator_ic(self):
        prompt = build_broad_prompt(
            bucket_stats={"动量": 7, "价量": 3}, operator_ic={"ts_mean": 0.021}, n=25
        )
        # 分桶占比数字
        assert "动量 7 条（70%）" in prompt
        assert "价量 3 条（30%）" in prompt
        # 算子历史入库 IC 表
        assert "ts_mean: 0.0210" in prompt
        # 候选数要求
        assert "不少于 25 条" in prompt
        # 动量占比 70% > 50% → 显式跨桶指令（含占比与「其他桶」枚举）
        assert "70%" in prompt
        assert "偏向其他桶（价量/反转/流动性/波动率）" in prompt

    def test_dominance_instruction_lists_other_buckets_by_category_order(self):
        prompt = build_broad_prompt(bucket_stats={"反转": 6, "价量": 4}, operator_ic={}, n=20)
        assert "偏向其他桶（价量/动量/流动性/波动率）" in prompt
        assert "反转 桶存量占比 60%" in prompt

    def test_broad_prompt_without_dominant_bucket_has_no_instruction(self):
        prompt = build_broad_prompt(bucket_stats={"动量": 5, "价量": 5}, operator_ic={}, n=20)
        assert "50%" in prompt  # 占比仍照实渲染
        assert "偏向其他桶" not in prompt
        assert "单桶占优" not in prompt

    def test_broad_prompt_empty_library(self):
        prompt = build_broad_prompt(bucket_stats={}, operator_ic={}, n=20)
        assert "（因子库为空）" in prompt
        assert "（暂无入库 IC 统计）" in prompt
        assert "偏向其他桶" not in prompt

    def test_deep_prompt_contains_feedback_distribution_and_decay_review(self):
        feedback = [
            {"原因": "DSL 解析失败", "条数": 12},
            {"因子": "ts_mean(close,5)", "复盘": "近 3 月 IC 由 0.04 衰减至 0.005"},
        ]
        prompt = build_deep_prompt(feedback=feedback)
        assert "原因=DSL 解析失败" in prompt
        assert "条数=12" in prompt
        assert "ts_mean(close,5)" in prompt
        assert "近 3 月 IC 由 0.04 衰减至 0.005" in prompt

    def test_deep_prompt_without_feedback_is_explicit(self):
        assert "（暂无拒收/衰减记录）" in build_deep_prompt(feedback=[])

    def test_batch_passes_alignment_score_through_without_rejection(self):
        """P5 端到端：alignment_score=0 的提案照常进入 proposals。"""
        client = StubClient([_payload(alignment_score=0), {"proposals": []}])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert result.rejected == ()
        assert result.proposals[0].alignment_score == 0.0


def _valid_payloads(count: int, *, start: int = 2) -> list[dict]:
    """生成 count 条互异且合同合法的 payload（expression 用不同窗口参数区分）。"""
    return [_payload(expression=f"ts_mean(close, {start + i})") for i in range(count)]


class TestDroppedDraftsAlwaysMarked:
    """评审修正 2：修复轮空响应 / 未应答条目不得静默消失——耗尽后必须进 rejected + 标记。"""

    def test_empty_repair_response_rejects_carried_draft(self):
        """review repro：`[坏, 空回复]` 此前 0 proposals / 0 rejected / 无标记。"""
        bad = _payload(expression=BAD_EXPRESSION)
        client = StubClient([bad, {"proposals": []}])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert result.proposals == ()
        assert len(result.rejected) == 1
        assert result.rejected[0].raw == bad
        assert "unknown_op" in result.rejected[0].error
        assert result.trace_metadata["proposal_rejected"] is True
        # 空响应即终止信号，不再空转第三轮
        assert len(client.calls) == 2

    def test_repair_response_shorter_than_pending_carries_tail(self):
        """修复轮只应答了一部分：未应答的尾部条目持续待处理，最终进 rejected。"""
        bad1 = _payload(expression=BAD_EXPRESSION)
        bad2 = _payload(hypothesis="太短")
        fixed = _payload(expression="ts_std(close, 10)")
        client = StubClient(
            [{"proposals": [bad1, bad2]}, {"proposals": [fixed]}, {"proposals": []}]
        )
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert [p.expression for p in result.proposals] == ["ts_std(close, 10)"]
        assert len(result.rejected) == 1
        assert result.rejected[0].raw == bad2  # 尾部未被应答的条目保留（含其最后错误）
        assert "30" in result.rejected[0].error
        assert result.trace_metadata["proposal_rejected"] is True
        assert len(client.calls) == 3


class TestBroadTopUp:
    """评审修正 5：broad 批次不足 n 时补一轮（同 prompt + 缺口说明），shortfall 落 trace。"""

    def test_shortfall_triggers_one_topup_round_and_records_metadata(self):
        second = [_payload(expression=f"ts_std(close, {2 + i})") for i in range(5)]
        client = StubClient([{"proposals": _valid_payloads(18)}, {"proposals": second}])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert len(result.proposals) == 23
        assert result.rejected == ()
        assert len(client.calls) == 2
        assert client.calls[1]["metadata"]["top_up"] is True
        assert "补充轮" in client.calls[1]["prompt"]
        assert "还差 2 条" in client.calls[1]["prompt"]
        assert result.trace_metadata["shortfall"] == {"initial": 2, "final": 0, "top_up": True}

    def test_no_topup_call_when_batch_meets_n(self):
        client = StubClient([{"proposals": _valid_payloads(20)}])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert len(result.proposals) == 20
        assert len(client.calls) == 1
        assert "shortfall" not in result.trace_metadata

    def test_topup_failures_go_to_rejected_and_final_deficit_recorded(self):
        bad = _payload(expression=BAD_EXPRESSION)
        client = StubClient([{"proposals": _valid_payloads(18)}, {"proposals": [bad]}])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert len(result.proposals) == 18
        assert len(result.rejected) == 1
        assert result.rejected[0].raw == bad
        assert result.trace_metadata["shortfall"] == {"initial": 2, "final": 2, "top_up": True}
        assert result.trace_metadata["proposal_rejected"] is True


class TestRoutingCandidateDedupe:
    """评审修正 4：路由候选按 (name, model, base_url) 去重，fallback 链不得列 primary 自己。"""

    def test_identical_tier_profiles_deduped_before_select(self, monkeypatch):
        profile = _stub_profile("env:openai/k3-256k")
        seen = []

        def fake_resolve(*, purpose, llm_config=None):
            seen.append(purpose)
            return profile

        monkeypatch.setattr(propose, "resolve_profile", fake_resolve)
        result = route_proposal_call("broad")
        assert seen == ["quick", "deep"]  # 两档都解析后才可判定重复
        assert result.primary is profile
        assert result.fallback_chain == []
        assert result.trace["fallback_chain"] == []

    def test_same_model_but_distinct_names_kept_in_chain(self, monkeypatch):
        """去重键含 name：同名同模型才算重复，命名不同的档位照常保留在链中。"""
        quick = _stub_profile("env:openai/k3-256k")
        deep = _stub_profile("preset:k3-256k")

        def fake_resolve(*, purpose, llm_config=None):
            return quick if purpose == "quick" else deep

        monkeypatch.setattr(propose, "resolve_profile", fake_resolve)
        result = route_proposal_call("broad")
        assert [p.name for p in result.fallback_chain] == ["preset:k3-256k"]


class TestTraceEmission:
    """评审修正 3：批次 trace_metadata（含 proposal_rejected）best-effort 发射到 Langfuse。"""

    def test_rejection_batch_emits_batch_metadata(self, monkeypatch):
        emitted: list[dict] = []
        monkeypatch.setattr(propose, "get_langfuse", lambda: object())
        monkeypatch.setattr(
            propose,
            "update_current_span",
            lambda metadata=None: emitted.append(metadata),
        )
        bad = _payload(expression=BAD_EXPRESSION)
        client = StubClient([bad, bad, bad])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert len(emitted) == 1
        payload = emitted[0]["proposal_batch"]
        assert payload == result.trace_metadata
        assert payload["proposal_rejected"] is True
        assert payload["rejected"][0]["raw"] == bad

    def test_emission_noop_with_debug_when_langfuse_absent(self, monkeypatch, caplog):
        monkeypatch.setattr(propose, "get_langfuse", lambda: None)

        def _must_not_be_called(**kwargs):
            raise AssertionError("Langfuse 未配置不得调用 update_current_span")

        monkeypatch.setattr(propose, "update_current_span", _must_not_be_called)
        with caplog.at_level(logging.DEBUG, logger="finance_agent.factors.propose"):
            client = StubClient([_payload(), {"proposals": []}])  # 补充轮终止响应（修正 5）
            result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert result.rejected == ()
        assert any("Langfuse" in record.getMessage() for record in caplog.records)

    def test_emission_failure_never_breaks_batch(self, monkeypatch):
        monkeypatch.setattr(propose, "get_langfuse", lambda: object())

        def _boom(**kwargs):
            raise RuntimeError("langfuse down")

        monkeypatch.setattr(propose, "update_current_span", _boom)
        bad = _payload(expression=BAD_EXPRESSION)
        client = StubClient([bad, bad, bad])
        result = propose_broad_batch(n=20, client=client, bucket_stats={}, operator_ic={})
        assert result.trace_metadata["proposal_rejected"] is True
