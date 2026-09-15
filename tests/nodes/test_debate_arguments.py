"""add-debate-argument-anchors Task 1：结构化论点模型与旧格式兼容。"""

import pytest
from pydantic import ValidationError

from finance_agent.models import DebateArgument, DebateMessage


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
