"""add-debate-argument-anchors Task 2：论点锚点存在性校验（零 LLM）。"""

import pandas as pd

from finance_agent.debate_anchors import anchor_stats, check_argument_anchors
from finance_agent.models import DebateMessage


def _msg(args: list[dict], role: str = "bull", rnd: int = 1) -> DebateMessage:
    return DebateMessage.model_validate(
        {"role": role, "round": rnd, "content": "x", "key_arguments": args}
    )


class TestAnchorChecks:
    def test_data_anchor_resolved_and_unresolved(self):
        state = {"technical_indicators": {"MA": {"5": [1.0, 2.0]}}}
        checks = check_argument_anchors(
            _msg(
                [
                    {
                        "text": "MA5",
                        "kind": "data",
                        "anchors": ["technical_indicators.MA.5.-1", "not_a_root.x"],
                    }
                ]
            ),
            state,
        )
        assert checks[0]["anchor_statuses"] == ["resolved", "unresolved"]
        assert checks[0]["anchored"] is True and checks[0]["status"] == "resolved"

    def test_dataframe_row_column_path(self):
        state = {"income_statement": pd.DataFrame([{"报告日": "20251231", "营业总收入": 1e9}])}
        checks = check_argument_anchors(
            _msg(
                [
                    {
                        "text": "营收",
                        "kind": "data",
                        "anchors": ["income_statement.20251231.营业总收入"],
                    }
                ]
            ),
            state,
        )
        assert checks[0]["anchored"] is True

    def test_event_echo_hit(self):
        state = {"news_list": [{"title": "公司公告拟回购不超过 10 亿元"}]}
        checks = check_argument_anchors(
            _msg([{"text": "回购", "kind": "event", "anchors": ["拟回购不超过 10 亿元"]}]),
            state,
        )
        assert checks[0]["anchored"] is True

    def test_data_zero_anchor_is_missing(self):
        checks = check_argument_anchors(_msg([{"text": "t", "kind": "data", "anchors": []}]), {})
        assert checks[0]["status"] == "missing" and checks[0]["anchored"] is False

    def test_inference_zero_anchor_is_none(self):
        checks = check_argument_anchors(
            _msg([{"text": "t", "kind": "inference", "anchors": []}]), {}
        )
        assert checks[0]["status"] == "none" and checks[0]["anchored"] is False

    def test_unspecified_not_checked(self):
        checks = check_argument_anchors(
            _msg([{"text": "t", "kind": "unspecified", "anchors": ["x"]}]), {}
        )
        assert checks[0]["status"] == "unspecified"

    def test_event_echo_miss_is_unresolved(self):
        state = {"news_list": [{"title": "公司公告拟回购不超过 10 亿元"}]}
        checks = check_argument_anchors(
            _msg([{"text": "t", "kind": "event", "anchors": ["不存在的新闻标题"]}]), state
        )
        assert checks[0]["anchor_statuses"] == ["unresolved"]
        assert checks[0]["status"] == "unresolved" and checks[0]["anchored"] is False

    def test_event_zero_anchor_is_missing(self):
        checks = check_argument_anchors(_msg([{"text": "t", "kind": "event", "anchors": []}]), {})
        assert checks[0]["status"] == "missing" and checks[0]["anchored"] is False

    def test_empty_stats_is_all_zero(self):
        assert anchor_stats([]) == {
            "total": 0,
            "anchored": 0,
            "unanchored_inference": 0,
            "unresolved": 0,
            "missing_required": 0,
            "unspecified": 0,
        }

    def test_stats_split(self):
        checks = [
            {"anchored": True, "status": "resolved", "kind": "data"},
            {"anchored": False, "status": "none", "kind": "inference"},
            {"anchored": False, "status": "unresolved", "kind": "data"},
            {"anchored": False, "status": "missing", "kind": "event"},
            {"anchored": False, "status": "unspecified", "kind": "unspecified"},
        ]
        assert anchor_stats(checks) == {
            "total": 5,
            "anchored": 1,
            "unanchored_inference": 1,
            "unresolved": 1,
            "missing_required": 1,
            "unspecified": 1,
        }


class TestInferenceTwoStageResolution:
    """inference 型锚点两段式解析：先 field_ref，未命中再回声回退。

    两段各自锁死——重构丢掉或颠倒第二段而其余套件仍绿，必须被这两条用例捕获。
    """

    def test_inference_anchor_resolves_via_field_ref(self):
        state = {"technical_indicators": {"MA": {"5": [1.0, 2.0]}}}
        checks = check_argument_anchors(
            _msg(
                [
                    {
                        "text": "动能",
                        "kind": "inference",
                        "anchors": ["technical_indicators.MA.5.-1"],
                    }
                ]
            ),
            state,
        )
        assert checks[0]["anchor_statuses"] == ["resolved"]
        assert checks[0]["status"] == "resolved" and checks[0]["anchored"] is True

    def test_inference_anchor_falls_back_to_event_echo(self):
        # 锚点不含 "."，field_ref 解析必然 None——只有回声段能把这条判为 resolved
        state = {"news_list": [{"title": "公司公告拟回购不超过 10 亿元"}]}
        checks = check_argument_anchors(
            _msg([{"text": "情绪", "kind": "inference", "anchors": ["拟回购不超过 10 亿元"]}]),
            state,
        )
        assert checks[0]["anchor_statuses"] == ["resolved"]
        assert checks[0]["status"] == "resolved" and checks[0]["anchored"] is True


class TestCheckRecordContract:
    """Task 3/5 消费检查记录：键集合与定位字段是接口契约，锁死。"""

    def test_check_record_keys_exact(self):
        checks = check_argument_anchors(
            _msg([{"text": "t", "kind": "event", "anchors": ["x"]}], role="aggressive", rnd=2),
            {},
        )
        assert len(checks) == 1
        assert set(checks[0]) == {
            "role",
            "round",
            "index",
            "kind",
            "anchors",
            "anchor_statuses",
            "status",
            "anchored",
        }
        assert (checks[0]["role"], checks[0]["round"], checks[0]["index"]) == ("aggressive", 2, 1)

    def test_index_is_one_based_across_arguments(self):
        checks = check_argument_anchors(
            _msg(
                [
                    {"text": "a", "kind": "inference", "anchors": []},
                    {"text": "b", "kind": "inference", "anchors": []},
                ]
            ),
            {},
        )
        assert [c["index"] for c in checks] == [1, 2]

    def test_event_echo_uses_full_shared_source_set(self):
        """event 锚点复用 citation 回声源集合（单一实现，勿在核对器内另建窄集合）。"""
        state = {
            "announcements": [{"title": "关于回购公司股份的公告"}],
            "block_trades": [{"date": "2025-09-10", "buyer": "机构专用"}],
        }
        checks = check_argument_anchors(
            _msg(
                [
                    {"text": "a", "kind": "event", "anchors": ["关于回购公司股份的公告"]},
                    {"text": "b", "kind": "event", "anchors": ["2025-09-10"]},
                ]
            ),
            state,
        )
        assert [c["anchored"] for c in checks] == [True, True]
