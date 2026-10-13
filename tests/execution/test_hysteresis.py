"""execution/hysteresis.py 共享滞回判定单测（add-signal-portfolio-risk-gate Task 4 / G5）。

覆盖计划 G5 与 delta spec decision-hysteresis「滞回机制适用路径」：

- 抽取为共享纯函数后与 ``nodes/risk.py::_evaluate_hysteresis`` 旧行为逐位一致
  （对既有 tests/nodes/test_risk.py hysteresis 用例覆盖的输入组合逐一断言）——
  ``nodes/risk.py`` 既有测试**零修改**通过（红线），本文件只做等价性与挂点断言；
- 信号侧应用 ``apply_signal_hysteresis``：抖动被吸收（维持前向）/ 有增量放行 /
  均衡带内执行无增量降为 flat（与决策侧降级观望同语义）；
- spec 场景：前日 long、今日裸输出 flat 且均衡带内 → 今日信号维持 long 且
  滞回判定记录落库（hysteresis_events）。

数值/枚举断言独立手写（不复用被测实现的计算）。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from finance_agent.execution.hysteresis import (
    HYSTERESIS_DOWNGRADE_BALANCED,
    HYSTERESIS_KEEP_PRIOR,
    apply_signal_hysteresis,
    evaluate_direction_hysteresis,
)
from finance_agent.execution.signals import SignalContractError, generate_signal
from finance_agent.execution.store import list_hysteresis_events, list_signals
from finance_agent.models import TradeDecision


def _decision(**overrides: object) -> dict:
    base: dict = {
        "action": "buy",
        "confidence": 0.7,
        "reasoning": "基本面改善，增配",
        "symbol": "600519.SH",
        "fund_manager_verdict": "approve",
    }
    base.update(overrides)
    return base


# ── 共享判定真值表（含既有 risk 用例覆盖的输入组合）──────────────────────


@pytest.mark.parametrize(
    ("prior", "current", "balanced", "incremental", "expect_flip", "expect_needs"),
    [
        # 翻转 + 无增量 → 需重申（看空→buy「超跌反弹」/ short→long）
        ("short", "long", False, False, True, True),
        # 翻转 + 有增量 → 放行（三季报披露）
        ("short", "long", False, True, True, False),
        # 均衡带内执行 + 无增量 → 需重申（中性→sell）
        (None, "short", True, False, False, True),
        # 均衡带内执行 + 有增量（放量破位确认）→ 放行
        (None, "short", True, True, False, False),
        # 非均衡带 + 无历史 → 零增量 no-op（看多→sell）
        (None, "short", False, False, False, False),
        # 均衡带内同向执行：无翻转但有执行动作 → 仍需增量申报
        ("short", "short", True, False, False, True),
        ("short", "short", True, True, False, False),
        # 前向 long → hold（neutral）：方向翻转 → 需重申（维持前判路径）
        ("long", "neutral", False, False, True, True),
        # 无翻转且非均衡带 → no-op
        ("long", "long", False, False, False, False),
        # 无历史且观望（非执行）→ 零增量；均衡带观望同样不降级
        (None, "neutral", False, False, False, False),
        (None, "neutral", True, False, False, False),
    ],
)
def test_shared_predicate_truth_table(
    prior, current, balanced, incremental, expect_flip, expect_needs
):
    judgment = evaluate_direction_hysteresis(
        prior, current, balanced_zone=balanced, incremental_claimed=incremental
    )
    assert judgment == {
        "flip": expect_flip,
        "needs_reaffirm": expect_needs,
        "applied": "",
    }


# ── 行为保持：risk 节点判定与共享函数逐位一致 ─────────────────────────────


_RISK_CASES: tuple[tuple[str, list[dict], str, str, str | None, str, bool, bool], ...] = (
    # (rating, history, action, reasoning, prior, current, balanced, incremental)
    ("中性", [], "sell", "趋势走弱，减仓规避回撤", None, "short", True, False),
    ("中性", [], "sell", "今日放量跌破 MA60，技术形态破位确认", None, "short", True, True),
    (
        "看空",
        [{"date": "2026-10-01", "direction": "short", "confidence": 0.6}],
        "buy",
        "超跌反弹",
        "short",
        "long",
        False,
        False,
    ),
    (
        "看空",
        [{"date": "2026-10-01", "direction": "short", "confidence": 0.6}],
        "buy",
        "三季报披露后毛利率拐点确认，估值消化",
        "short",
        "long",
        False,
        True,
    ),
    ("看多", [], "sell", "趋势走弱，减仓规避回撤", None, "short", False, False),
    ("中性", [], "hold", "等待右侧信号", None, "neutral", True, False),
    (
        "看空",
        [{"date": "2026-10-01", "direction": "long", "confidence": 0.6}],
        "buy",
        "反弹动能仍在",
        "long",
        "long",
        False,
        False,
    ),
)


@pytest.mark.parametrize(
    ("rating", "history", "action", "reasoning", "prior", "current", "balanced", "incremental"),
    _RISK_CASES,
)
def test_risk_node_judgment_matches_shared_predicate(
    rating, history, action, reasoning, prior, current, balanced, incremental
):
    """等价性：risk 节点适配出的判定结果 == 共享纯函数对同一输入的判定。"""
    from finance_agent.nodes.risk import _evaluate_hysteresis

    decision = TradeDecision(action=action, confidence=0.6, reasoning=reasoning)
    with patch("finance_agent.nodes.risk.recent_directions", return_value=history):
        result = _evaluate_hysteresis(
            {"stock_code": "688072", "research_manager_rating": rating}, decision
        )
    shared = evaluate_direction_hysteresis(
        prior, current, balanced_zone=balanced, incremental_claimed=incremental
    )
    assert result["flip"] is shared["flip"]
    assert result["needs_reaffirm"] is shared["needs_reaffirm"]
    assert result["balanced_zone"] is balanced
    assert result["prior_direction"] == prior
    assert result["incremental_claimed"] is incremental
    assert result["applied"] == ""
    assert result["prior_date"] == (history[-1]["date"] if history else None)


def test_risk_node_delegates_to_shared_predicate():
    """挂点证明：risk 节点经共享纯函数判定（不再自持 flip/needs_reaffirm 三目）。"""
    from finance_agent.nodes import risk as risk_node

    real = risk_node.evaluate_direction_hysteresis
    calls: list[tuple] = []

    def _spy(*args: object, **kwargs: object) -> dict:
        calls.append((args, kwargs))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    with (
        patch("finance_agent.nodes.risk.evaluate_direction_hysteresis", side_effect=_spy),
        patch("finance_agent.nodes.risk.recent_directions", return_value=[]),
    ):
        risk_node._evaluate_hysteresis(
            {"stock_code": "688072", "research_manager_rating": "中性"},
            TradeDecision(action="sell", confidence=0.5, reasoning="趋势走弱"),
        )
    assert len(calls) == 1
    assert calls[0][0] == (None, "short")
    assert calls[0][1] == {"balanced_zone": True, "incremental_claimed": False}


# ── 信号侧应用语义 ─────────────────────────────────────────────────────────


def test_apply_signal_hysteresis_absorbs_flip_without_increment():
    """抖动吸收：前日 long、今日裸 flat 且均衡带内（无增量）→ 维持 long。"""
    direction, judgment = apply_signal_hysteresis(
        "flat", "long", balanced_zone=True, incremental_claimed=False
    )
    assert direction == "long"
    assert judgment == {"flip": True, "needs_reaffirm": True, "applied": HYSTERESIS_KEEP_PRIOR}
    assert HYSTERESIS_KEEP_PRIOR == "keep_prior"


def test_apply_signal_hysteresis_flip_with_increment_not_absorbed():
    """非均衡带且申报增量 → 放行翻转（原样返回）。"""
    direction, judgment = apply_signal_hysteresis(
        "flat", "long", balanced_zone=False, incremental_claimed=True
    )
    assert direction == "flat"
    assert judgment == {"flip": True, "needs_reaffirm": False, "applied": ""}


def test_apply_signal_hysteresis_balanced_exec_without_increment_downgrades():
    """均衡带内执行（long）无增量 → 降为 flat（与决策侧降级观望同语义）。"""
    direction, judgment = apply_signal_hysteresis(
        "long", None, balanced_zone=True, incremental_claimed=False
    )
    assert direction == "flat"
    assert judgment["applied"] == HYSTERESIS_DOWNGRADE_BALANCED == "downgrade_balanced"


def test_apply_signal_hysteresis_balanced_exec_with_increment_kept():
    direction, judgment = apply_signal_hysteresis(
        "long", None, balanced_zone=True, incremental_claimed=True
    )
    assert direction == "long"
    assert judgment["applied"] == ""


def test_apply_signal_hysteresis_no_history_not_balanced_noop():
    assert apply_signal_hysteresis(
        "long", None, balanced_zone=False, incremental_claimed=False
    ) == ("long", {"flip": False, "needs_reaffirm": False, "applied": ""})


def test_apply_signal_hysteresis_raw_flat_not_redundantly_downgraded():
    """裸 flat 且无翻转：已无敞口，不再记降级（applied 空）。"""
    direction, judgment = apply_signal_hysteresis(
        "flat", None, balanced_zone=True, incremental_claimed=False
    )
    assert direction == "flat"
    assert judgment["applied"] == ""


# ── 信号生成路径集成（spec 场景：抖动被吸收 + 判定落库）──────────────────


def test_signal_path_absorbs_jitter_and_records_judgment(tmp_path):
    """spec 场景逐字：前日信号 long，今日裸输出 flat 且均衡带内 → 维持 long + 落库。"""
    db = tmp_path / "execution.db"
    day1 = generate_signal(
        _decision(),
        decision_id="dec-1",
        source="risk_judge",
        trade_date="2026-10-12",
        db_path=db,
    )
    assert day1.direction == "long"
    prior = list_signals(trade_date="2026-10-12", symbol="600519.SH", db_path=db)[-1]
    assert prior.direction == "long"

    day2 = generate_signal(
        _decision(action="hold"),  # 裸输出翻转 → flat
        decision_id="dec-2",
        source="risk_judge",
        trade_date="2026-10-13",
        prior_direction=prior.direction,
        balanced_zone=True,
        incremental_claimed=False,
        db_path=db,
    )
    assert day2.direction == "long"  # 抖动被吸收，维持前向
    assert [s.direction for s in list_signals(trade_date="2026-10-13", db_path=db)] == ["long"]

    events = list_hysteresis_events(signal_id=day2.signal_id, db_path=db)
    assert len(events) == 1
    assert events[0]["signal_id"] == "600519.SH:2026-10-13"
    assert events[0]["created_at"] == "2026-10-13"
    detail = json.loads(events[0]["detail_json"])
    assert detail["applied"] == "keep_prior"
    assert detail["flip"] is True
    assert detail["needs_reaffirm"] is True
    assert detail["raw_direction"] == "flat"  # 判定前裸输出可审计
    assert detail["source"] == "risk_judge"
    assert detail["decision_id"] == "dec-2"


def test_signal_path_flip_with_increment_not_absorbed_no_event(tmp_path):
    db = tmp_path / "execution.db"
    generate_signal(
        _decision(), decision_id="dec-1", source="risk_judge", trade_date="2026-10-12", db_path=db
    )
    day2 = generate_signal(
        _decision(action="hold"),
        decision_id="dec-2",
        source="risk_judge",
        trade_date="2026-10-13",
        prior_direction="long",
        balanced_zone=False,
        incremental_claimed=True,
        db_path=db,
    )
    assert day2.direction == "flat"  # 申报增量 → 放行翻转
    assert list_hysteresis_events(db_path=db) == []


def test_signal_path_balanced_exec_without_increment_downgrades(tmp_path):
    db = tmp_path / "execution.db"
    signal = generate_signal(
        _decision(action="buy"),
        decision_id="dec-1",
        source="risk_judge",
        trade_date="2026-10-13",
        balanced_zone=True,
        incremental_claimed=False,
        db_path=db,
    )
    assert signal.direction == "flat"  # 均衡带内执行无增量 → 降级
    events = list_hysteresis_events(signal_id=signal.signal_id, db_path=db)
    assert len(events) == 1
    assert json.loads(events[0]["detail_json"])["applied"] == "downgrade_balanced"


def test_signal_path_noop_records_no_event(tmp_path):
    """无历史且非均衡带 → 判定零增量 no-op，不落判定记录（保持事件表有信息量）。"""
    db = tmp_path / "execution.db"
    signal = generate_signal(
        _decision(),
        decision_id="dec-1",
        source="risk_judge",
        trade_date="2026-10-13",
        db_path=db,
    )
    assert signal.direction == "long"
    assert list_hysteresis_events(db_path=db) == []


def test_signal_path_illegal_prior_direction_rejected(tmp_path):
    """评审修正：prior_direction 非 {long, flat}（如决策侧 short）→ 拒收，不落库。

    keep-prior 分支会原样返回 prior；若 prior 是决策侧方向（short/neutral），
    落库即产生枚举外 signal。契约边界（信号路径只认 long/flat）必须在此拦截。
    """
    db = tmp_path / "execution.db"
    generate_signal(
        _decision(), decision_id="dec-1", source="risk_judge", trade_date="2026-10-12", db_path=db
    )
    with pytest.raises(SignalContractError) as exc:
        generate_signal(
            _decision(),  # 裸输出 long
            decision_id="dec-2",
            source="risk_judge",
            trade_date="2026-10-13",
            prior_direction="short",  # 决策侧历史方向，信号路径非法
            balanced_zone=False,
            incremental_claimed=False,
            db_path=db,
        )
    assert str(exc.value) == "prior_direction 非法值: short"
    # 拒收即不落库：无 10-13 signal、无判定事件
    assert list_signals(trade_date="2026-10-13", db_path=db) == []
    assert list_hysteresis_events(db_path=db) == []


def test_signal_path_rerun_does_not_duplicate_hysteresis_event(tmp_path):
    """评审修正：同日同判定的重跑 → hysteresis_events 幂等（UNIQUE + DO NOTHING）。"""
    db = tmp_path / "execution.db"
    kwargs: dict = {
        "decision": _decision(action="hold"),
        "decision_id": "dec-2",
        "source": "risk_judge",
        "trade_date": "2026-10-13",
        "prior_direction": "long",
        "balanced_zone": True,
        "incremental_claimed": False,
        "db_path": db,
    }
    first = generate_signal(**kwargs)
    second = generate_signal(**kwargs)
    assert first == second
    assert list_signals(db_path=db) == [first]
    assert len(list_hysteresis_events(signal_id=first.signal_id, db_path=db)) == 1
