"""TESTING stub 与节点输出契约同步护栏（回归：FM approve 缺 action/confidence 致 E2E 全红）。

背景：`harden-decision-report-semantics` 给 FundManagerDecision 加了「approve 必填
action/confidence」校验，但 `_stub_pipeline_answer` 的 fund_manager 载荷未同步 →
TESTING 模式下管线在 FM 处 pydantic 报错中断、generate_report 不执行、report_ready
空载荷，整套深度管线 E2E（timeline suite）确定性红。

本文件把「stub 载荷必须能被对应节点的 pydantic 模型校验通过」固化为测试：
未来任何节点输出契约变更若忘了同步 stub，这里先红，而不是等 E2E 全红。
"""

from __future__ import annotations

import json

import pytest

from finance_agent.models import (
    AnalystReport,
    DebateMessage,
    FundManagerDecision,
    TradeDecision,
)
from finance_agent.nodes._llm_utils import _stub_pipeline_answer

# 节点 → 期望的 pydantic 模型（与各节点 parse 的模型一致）
_EXPECTED_MODEL = {
    "technical_analyst": AnalystReport,
    "macro_analyst": AnalystReport,
    "fundamental_analyst": AnalystReport,
    "sentiment_analyst": AnalystReport,
    "bull_debater": DebateMessage,
    "bear_debater": DebateMessage,
    "aggressive_debater": DebateMessage,
    "conservative_debater": DebateMessage,
    "neutral_debater": DebateMessage,
    "trader": TradeDecision,
    "risk_judge": TradeDecision,
    "fund_manager": FundManagerDecision,
}


@pytest.mark.parametrize(("node_name", "model"), sorted(_EXPECTED_MODEL.items()))
def test_stub_payload_matches_node_model(node_name: str, model: type) -> None:
    """stub 载荷必须能被对应节点的模型校验通过（契约漂移即红）。"""
    payload = json.loads(_stub_pipeline_answer(node_name))
    model.model_validate(payload)


def test_stub_watch_carries_inaction_rationale_and_triggers():
    """require-watch-hold-rationale + add-watch-trigger-tracking：stub watch 载荷
    须带结构化理由与非空触发位（E2E 报告结构化渲染 + K 线触发参考线覆盖的前提）。

    触发位方向须与 stub K 线最新收盘（180.0）相容：high > close > low——
    价位门禁方向校验（_check_structured_trigger_levels）以此为判据。
    """
    payload = json.loads(_stub_pipeline_answer("trader"))
    assert payload.get("action") == "watch"
    assert payload.get("inaction_reason")
    assert payload.get("reeval_triggers")
    assert payload.get("trigger_high") == 181.0
    assert payload.get("trigger_low") == 178.0
    assert payload["trigger_high"] > 180.0 > payload["trigger_low"]
