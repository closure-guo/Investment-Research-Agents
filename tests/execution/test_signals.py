"""execution/signals.py + execution/store.py 合同/落库单测（add-signal-portfolio-risk-gate Task 1）。

覆盖计划 G1（决策→Signal 映射）与 G2（落库）：

- direction 枚举 long/flat：buy→long；hold/watch→flat；sell(exit/未申报)→flat；
  sell(short) 与显式 direction=short → ``SignalContractError``，消息逐字
  「direction 非法值: short」（delta spec trade-execution 场景）；
- 仅 ``fund_manager_verdict == "approve"``（或调用方显式 ``approved=True``）才可转换，
  未审批 → 合同错误「未审批通过的决策不得生成信号」；
- 缺 symbol/decision_id/action/confidence → 合同错误；strength 越界（0-1）→ 合同错误；
- ``strength = confidence``、``horizon_days = DEFAULT_HORIZON_DAYS``（同源 import）；
- signal_id 确定性（symbol:trade_date）→ 同日重跑幂等 upsert 不翻倍；
- ``list_signals`` 按 trade_date/symbol 过滤；
- delta spec decision-outcome 场景「仅审批通过生成信号」（同日 approve + reject）。

数值断言独立手写（不复用被测实现的计算）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from finance_agent.execution.signals import (
    Signal,
    SignalContractError,
    generate_signal,
    signal_from_decision,
)
from finance_agent.execution.store import list_signals, record_signal
from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS


def _decision(**overrides: object) -> dict:
    """审批通过的合法决策基座（未覆盖字段逐字）。"""
    base: dict = {
        "action": "buy",
        "confidence": 0.7,
        "reasoning": "基本面改善，增配",
        "symbol": "600519.SH",
        "fund_manager_verdict": "approve",
    }
    base.update(overrides)
    return base


def _convert(decision: dict, *, decision_id: str = "dec-1", trade_date: str = "2026-10-13"):
    return signal_from_decision(
        decision, decision_id=decision_id, source="risk_judge", trade_date=trade_date
    )


# ── G1：action/sell_type → direction 映射 ───────────────────────────────────


def test_buy_maps_long_with_full_contract_fields():
    """buy → long；strength/horizon_days/source/decision_id/created_at 逐字落契约。"""
    sig = _convert(_decision())
    assert isinstance(sig, Signal)
    assert sig.direction == "long"
    assert sig.symbol == "600519.SH"
    assert sig.strength == 0.7
    assert sig.horizon_days == DEFAULT_HORIZON_DAYS
    assert sig.source == "risk_judge"
    assert sig.decision_id == "dec-1"
    assert sig.created_at == "2026-10-13"
    assert sig.signal_id == "600519.SH:2026-10-13"


def test_hold_maps_flat():
    assert _convert(_decision(action="hold")).direction == "flat"


def test_watch_maps_flat():
    assert _convert(_decision(action="watch")).direction == "flat"


def test_sell_exit_maps_flat():
    """sell_type=exit（持有者减仓/退出敞口）→ flat。"""
    assert _convert(_decision(action="sell", sell_type="exit")).direction == "flat"


def test_sell_undeclared_type_maps_flat():
    """sell 未申报 sell_type → 按退出语义 flat（非 short 建仓）。"""
    assert _convert(_decision(action="sell")).direction == "flat"


def test_sell_short_rejected_with_verbatim_message():
    """direction 枚举拒绝 short：消息逐字「direction 非法值: short」。"""
    with pytest.raises(SignalContractError) as exc:
        _convert(_decision(action="sell", sell_type="short"))
    assert str(exc.value) == "direction 非法值: short"


def test_explicit_short_direction_input_rejected():
    """spec 场景逐字：一条 direction 为 short 的转换输入 → 拒收（同一消息）。"""
    with pytest.raises(SignalContractError, match="direction 非法值: short"):
        _convert(_decision(direction="short"))


def test_explicit_direction_in_enum_accepted():
    """显式 direction（枚举内）声明优先于 action 推导，与 action 不矛盾时不报错。"""
    sig = _convert(_decision(action="buy", direction="long"))
    assert sig.direction == "long"
    assert _convert(_decision(action="hold", direction="flat")).direction == "flat"


@pytest.mark.parametrize("declared", [123, ""])
def test_non_string_or_blank_declared_direction_rejected(declared):
    """声明了 direction 但不是合法枚举字符串 → 拒收（不得静默回退 action 映射）。

    ``None``（未声明）才走 action 映射；``123``/``""`` 是显式申报的非法值。
    """
    with pytest.raises(SignalContractError, match="direction 非法值"):
        _convert(_decision(direction=declared))


# ── G1：审批门禁（仅 approve 生成）─────────────────────────────────────────


@pytest.mark.parametrize("verdict", ["reject", "return", None, "approve_pending"])
def test_unapproved_decision_rejected(verdict):
    """非 approve（含缺失/未知值）→ 合同错误「未审批通过的决策不得生成信号」。"""
    decision = _decision()
    if verdict is None:
        decision.pop("fund_manager_verdict")
    else:
        decision["fund_manager_verdict"] = verdict
    with pytest.raises(SignalContractError) as exc:
        _convert(decision)
    assert str(exc.value) == "未审批通过的决策不得生成信号"


def test_explicit_approved_true_bypasses_verdict_key():
    """调用方显式 approved=True（已审批事实）可无 fund_manager_verdict 键转换。"""
    decision = _decision()
    decision.pop("fund_manager_verdict")
    sig = signal_from_decision(
        decision,
        decision_id="dec-2",
        source="pipeline",
        trade_date="2026-10-13",
        approved=True,
    )
    assert sig.direction == "long"
    assert sig.decision_id == "dec-2"


def test_explicit_approved_false_rejected():
    with pytest.raises(SignalContractError):
        signal_from_decision(
            _decision(fund_manager_verdict="approve"),
            decision_id="dec-2",
            source="pipeline",
            trade_date="2026-10-13",
            approved=False,
        )


# ── G1：缺字段 / strength 越界 ──────────────────────────────────────────────


def test_missing_symbol_rejected():
    decision = _decision()
    decision.pop("symbol")
    with pytest.raises(SignalContractError, match="symbol"):
        _convert(decision)


def test_blank_symbol_rejected():
    with pytest.raises(SignalContractError, match="symbol"):
        _convert(_decision(symbol="   "))


def test_blank_decision_id_rejected():
    with pytest.raises(SignalContractError, match="decision_id"):
        _convert(_decision(), decision_id="")


def test_missing_action_rejected():
    decision = _decision()
    decision.pop("action")
    with pytest.raises(SignalContractError, match="action"):
        _convert(decision)


@pytest.mark.parametrize("confidence", [1.5, -0.1, "high", None, True])
def test_bad_strength_rejected(confidence):
    """strength=confidence 须 0-1 数值；越界/非数值/bool → 合同错误。"""
    with pytest.raises(SignalContractError, match="strength"):
        _convert(_decision(confidence=confidence))


def test_strong_boundaries_accepted():
    assert _convert(_decision(confidence=0.0)).strength == 0.0
    assert _convert(_decision(confidence=1.0)).strength == 1.0


def test_created_at_override():
    sig = signal_from_decision(
        _decision(),
        decision_id="dec-1",
        source="risk_judge",
        trade_date="2026-10-13",
        created_at="2026-10-13 15:05:00",
    )
    assert sig.created_at == "2026-10-13 15:05:00"


def test_non_mapping_input_rejected():
    with pytest.raises(SignalContractError, match="决策"):
        signal_from_decision(
            "not-a-decision",  # type: ignore[arg-type]
            decision_id="dec-1",
            source="risk_judge",
            trade_date="2026-10-13",
        )


# ── G2：落库纪律（幂等 upsert / 过滤）──────────────────────────────────────


def _db(tmp_path: Path) -> Path:
    return tmp_path / "execution.db"


def test_record_signal_idempotent_upsert(tmp_path):
    """同 signal_id 重写不翻倍，字段取最新写入（决策重跑幂等）。"""
    db = _db(tmp_path)
    sig = _convert(_decision())
    record_signal(sig, db_path=db)
    record_signal(sig, db_path=db)
    rows = list_signals(db_path=db)
    assert len(rows) == 1
    assert rows[0] == sig

    sig2 = replace(sig, decision_id="dec-2", strength=0.8, created_at="2026-10-13 15:30:00")
    record_signal(sig2, db_path=db)
    rows = list_signals(db_path=db)
    assert len(rows) == 1
    assert rows[0] == sig2


def test_list_signals_filters_by_trade_date_and_symbol(tmp_path):
    db = _db(tmp_path)
    a = _convert(_decision(), decision_id="dec-a")
    b = _convert(_decision(symbol="000001.SZ"), decision_id="dec-b")
    c = _convert(_decision(), decision_id="dec-c", trade_date="2026-10-12")
    for sig in (a, b, c):
        record_signal(sig, db_path=db)

    today = list_signals(trade_date="2026-10-13", db_path=db)
    assert [s.symbol for s in today] == ["000001.SZ", "600519.SH"]  # symbol 升序
    assert [s.decision_id for s in today] == ["dec-b", "dec-a"]
    same_symbol = list_signals(symbol="600519.SH", db_path=db)
    assert [s.decision_id for s in same_symbol] == ["dec-c", "dec-a"]  # created_at 升序
    assert list_signals(trade_date="2026-10-01", db_path=db) == []
    unfiltered = list_signals(db_path=db)
    assert len(unfiltered) == 3
    assert [s.decision_id for s in unfiltered] == ["dec-c", "dec-b", "dec-a"]


def test_created_at_with_time_filtered_by_date(tmp_path):
    """created_at 带时间部分时按日期前缀过滤（substr 前 10 位）。"""
    db = _db(tmp_path)
    sig = signal_from_decision(
        _decision(),
        decision_id="dec-1",
        source="risk_judge",
        trade_date="2026-10-13",
        created_at="2026-10-13 15:05:00",
    )
    record_signal(sig, db_path=db)
    assert [s.signal_id for s in list_signals(trade_date="2026-10-13", db_path=db)] == [
        sig.signal_id
    ]


def test_generate_signal_converts_and_records(tmp_path):
    db = _db(tmp_path)
    sig = generate_signal(
        _decision(),
        decision_id="dec-1",
        source="risk_judge",
        trade_date="2026-10-13",
        db_path=db,
    )
    assert list_signals(db_path=db) == [sig]


def test_only_approved_decision_generates_signal(tmp_path):
    """delta spec 场景：同日一条 approve + 一条 reject → 仅 approve 落库且 decision_id 指向它。"""
    db = _db(tmp_path)
    approved = _decision(fund_manager_verdict="approve")
    rejected = _decision(fund_manager_verdict="reject")
    generated: list[Signal] = []
    for decision, decision_id in ((approved, "dec-approve"), (rejected, "dec-reject")):
        try:
            generated.append(
                generate_signal(
                    decision,
                    decision_id=decision_id,
                    source="risk_judge",
                    trade_date="2026-10-13",
                    db_path=db,
                )
            )
        except SignalContractError:
            continue
    assert len(generated) == 1
    rows = list_signals(trade_date="2026-10-13", db_path=db)
    assert len(rows) == 1
    assert rows[0].decision_id == "dec-approve"
    assert rows[0].symbol == "600519.SH"
