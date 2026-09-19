"""add-debate-argument-anchors Task 1：结构化论点模型与旧格式兼容。"""

import pytest
from pydantic import ValidationError

from finance_agent.models import DebateArgument, DebateMessage
from finance_agent.nodes.debate import _build_debate_context


class TestDebateArgumentModel:
    def test_structured_parse(self):
        msg = DebateMessage.model_validate(
            {
                "role": "bull",
                "round": 1,
                "content": "x",
                "key_arguments": [
                    {
                        "text": "MA5 上穿 MA20",
                        "kind": "data",
                        "anchors": ["technical_indicators.MA.5.-1"],
                    },
                ],
            }
        )
        arg = msg.key_arguments[0]
        assert isinstance(arg, DebateArgument)
        assert arg.kind == "data"
        assert arg.anchors == ["technical_indicators.MA.5.-1"]

    def test_legacy_string_list_downgrades_to_unspecified(self):
        msg = DebateMessage.model_validate(
            {"role": "bull", "round": 1, "content": "x", "key_arguments": ["论点1", "论点2"]}
        )
        assert [a.kind for a in msg.key_arguments] == ["unspecified", "unspecified"]
        assert [a.text for a in msg.key_arguments] == ["论点1", "论点2"]
        assert all(a.anchors == [] for a in msg.key_arguments)

    def test_invalid_kind_downgrades_not_raises(self):
        msg = DebateMessage.model_validate(
            {
                "role": "bear",
                "round": 1,
                "content": "x",
                "key_arguments": [{"text": "t", "kind": "citation", "anchors": []}],
            }
        )
        assert msg.key_arguments[0].kind == "unspecified"

    def test_missing_kind_downgrades_to_unspecified(self):
        """dict 项缺 kind 键同样降级（历史/夹具形态），anchors 原样保留。"""
        msg = DebateMessage.model_validate(
            {
                "role": "bear",
                "round": 1,
                "content": "x",
                "key_arguments": [{"text": "无 kind", "anchors": ["x"]}],
            }
        )
        assert msg.key_arguments[0].kind == "unspecified"
        assert msg.key_arguments[0].anchors == ["x"]

    def test_mixed_legacy_and_structured_items(self):
        """混合列表（裸字符串 + 结构项）逐项处理，互不影响。"""
        msg = DebateMessage.model_validate(
            {
                "role": "bull",
                "round": 1,
                "content": "x",
                "key_arguments": ["旧论点", {"text": "新论点", "kind": "event", "anchors": []}],
            }
        )
        assert [(a.text, a.kind) for a in msg.key_arguments] == [
            ("旧论点", "unspecified"),
            ("新论点", "event"),
        ]

    def test_prebuilt_instance_round_trips_unchanged(self):
        """回归：已构造的 DebateArgument 实例不得被 str() 串化降级为 unspecified。

        Task 2 的测试/调用方会直接手搓类型化论点传给 DebateMessage；
        串化会静默丢失 text/kind/anchors 三要素。
        """
        arg = DebateArgument(text="MA5 上穿 MA20", kind="data", anchors=["a.b"])
        msg = DebateMessage(role="bull", round=1, content="x", key_arguments=[arg])
        assert msg.key_arguments[0].text == "MA5 上穿 MA20"
        assert msg.key_arguments[0].kind == "data"
        assert msg.key_arguments[0].anchors == ["a.b"]

    def test_empty_text_raises(self):
        # 计划原文为 pytest.raises(Exception)，ruff B017（禁盲断言异常）不允许；
        # 收窄为 pydantic.ValidationError——语义相同且更精确。
        with pytest.raises(ValidationError):
            DebateMessage.model_validate(
                {
                    "role": "bull",
                    "round": 1,
                    "content": "x",
                    "key_arguments": [{"text": "", "kind": "data", "anchors": []}],
                }
            )

    def test_string_anchors_normalized_to_single_item_list(self):
        """P1 真跑回归：LLM 把 anchors 返回为字符串致 DebateMessage 校验崩溃；按显式降级口径归一为单元素列表。"""
        msg = DebateMessage.model_validate(
            {
                "role": "bear",
                "round": 1,
                "content": "x",
                "key_arguments": [
                    {
                        "text": "MA5 上穿 MA20",
                        "kind": "data",
                        "anchors": "technical_indicators.MA.5.-1",
                    }
                ],
            }
        )
        assert msg.key_arguments[0].anchors == ["technical_indicators.MA.5.-1"]
        # 形态修复只动 anchors，不得顺带动摇 kind（归一 ≠ 语义降级）
        assert msg.key_arguments[0].kind == "data"

    @pytest.mark.parametrize("bad_anchors", [None, {"k": "v"}, 3])
    def test_unusable_anchors_shapes_degrade_to_empty_list(self, bad_anchors):
        """无法解释为锚点列表的形态（None/dict/int）→ []：不猜内容，也不抛异常。"""
        msg = DebateMessage.model_validate(
            {
                "role": "bear",
                "round": 1,
                "content": "x",
                "key_arguments": [{"text": "t", "kind": "event", "anchors": bad_anchors}],
            }
        )
        assert msg.key_arguments[0].anchors == []

    def test_non_string_anchor_entries_dropped(self):
        """混合类型条目：保留 str，丢弃其余（维持 list[str] 契约，不字符串化杂类值）。"""
        msg = DebateMessage.model_validate(
            {
                "role": "bull",
                "round": 1,
                "content": "x",
                "key_arguments": [
                    {
                        "text": "t",
                        "kind": "data",
                        "anchors": ["technical_indicators.MA.5.-1", 3, None, {"k": "v"}, "a.b"],
                    }
                ],
            }
        )
        assert msg.key_arguments[0].anchors == ["technical_indicators.MA.5.-1", "a.b"]

    def test_tuple_anchors_normalized_to_list(self):
        msg = DebateMessage.model_validate(
            {
                "role": "bull",
                "round": 1,
                "content": "x",
                "key_arguments": [{"text": "t", "kind": "data", "anchors": ("a.b", 7, "c.d")}],
            }
        )
        assert msg.key_arguments[0].anchors == ["a.b", "c.d"]
        assert isinstance(msg.key_arguments[0].anchors, list)

    def test_anchors_shape_repair_does_not_mask_empty_text(self):
        """形态归一 SHALL NOT 吞掉真错误：text 缺失/为空仍抛（形态噪声 ≠ 内容缺失）。"""
        with pytest.raises(ValidationError):
            DebateMessage.model_validate(
                {
                    "role": "bull",
                    "round": 1,
                    "content": "x",
                    "key_arguments": [{"text": "", "kind": "data", "anchors": "a.b"}],
                }
            )

    def test_rebuttal_to_position_semantics_unchanged(self):
        msg = DebateMessage.model_validate(
            {
                "role": "bear",
                "round": 2,
                "content": "x",
                "key_arguments": [
                    {"text": "a", "kind": "inference", "anchors": []},
                    {"text": "b", "kind": "inference", "anchors": []},
                ],
                "rebuttal_to": [2],
            }
        )
        assert msg.rebuttal_to == [2] and len(msg.key_arguments) == 2


class TestDebateContextRenderingPrivacy:
    """对手可见的编号行只渲染 text，不暴露锚点（spec: agent-node-contracts）。

    锚点是「生成侧申报 + 程序判存在性」的核对材料（Task 2/5），不是给对手
    辩论方看的信息——渲染层不得泄漏 kind/anchors。
    """

    def test_opponent_visible_line_renders_text_only(self):
        msg = DebateMessage(
            role="bull",
            round=1,
            content="看多论证正文",
            key_arguments=[
                DebateArgument(
                    text="MA5 上穿 MA20",
                    kind="data",
                    anchors=["technical_indicators.MA.5.-1"],
                )
            ],
        )
        context = _build_debate_context({"debate_history": [msg]})
        assert "MA5 上穿 MA20" in context
        assert "technical_indicators.MA.5.-1" not in context
        assert "anchors" not in context
        assert "kind" not in context
