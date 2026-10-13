"""execution/risk_gate.py 确定性风控门禁单测（add-signal-portfolio-risk-gate Task 3 / G4）。

覆盖 delta spec trade-execution「确定性风控门禁」：

- 8 个原因码逐字正反：``ST_FLAG`` / ``SUSPENDED`` / ``LIMIT_UP_UNFILLABLE`` /
  ``LIMIT_DOWN_UNFILLABLE`` / ``SINGLE_POSITION_CAP`` / ``TOTAL_POSITION_CAP`` /
  ``T1_SELL_BLOCKED`` / ``ODD_LOT``（卖零股全卖例外同 matching 语义）；
- 首个命中即拒（拒绝即停），detail 记检查轨迹与 decision_id/signal_id 证据链；
- 约束单一来源：改 ``screening/constraints.py`` 一处即改变门禁（spec「与 L1 过滤
  使用同一约束常量」）；
- 静态扫描：AST 解析 ``risk_gate.py`` 无 llm/gateway/harness import（spec 场景）；
- ``matching.py`` 整手判定改 import ``LOT_SIZE``（单一来源）且既有撮合测试保持绿。

数值断言独立手写（不复用被测实现的计算）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from finance_agent.execution import matching, risk_gate
from finance_agent.execution.matching import MarketBar, PositionContext
from finance_agent.execution.risk_gate import (
    REASON_LIMIT_DOWN_UNFILLABLE,
    REASON_LIMIT_UP_UNFILLABLE,
    REASON_ODD_LOT,
    REASON_SINGLE_POSITION_CAP,
    REASON_ST_FLAG,
    REASON_SUSPENDED,
    REASON_T1_SELL_BLOCKED,
    REASON_TOTAL_POSITION_CAP,
    GateDecision,
    OrderIntent,
    check_order,
)
from finance_agent.screening import constraints

_RISK_GATE_PATH = Path(risk_gate.__file__)


def _order(
    side: str = "buy",
    qty: int = 100,
    target_weight: float = 0.1,
    symbol: str = "600519.SH",
) -> OrderIntent:
    return OrderIntent(
        decision_id="dec-1",
        signal_id="600519.SH:2026-10-13",
        symbol=symbol,
        side=side,  # type: ignore[arg-type]  # 测试构造，取值受用例约束
        qty=qty,
        target_weight=target_weight,
    )


def _bar(*, limit_up_sealed: bool = False, limit_down_sealed: bool = False) -> MarketBar:
    return MarketBar(
        trade_date="2026-10-13",
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        prev_close=100.0,
        amount=500.0,
        limit_up_sealed=limit_up_sealed,
        limit_down_sealed=limit_down_sealed,
    )


def _check(order: OrderIntent, **overrides: object) -> GateDecision:
    kwargs: dict = {
        "current_weight": 0.0,
        "projected_total_weight": 0.2,
        "bar": _bar(),
        "position": None,
    }
    kwargs.update(overrides)
    return check_order(order, **kwargs)  # type: ignore[arg-type]


# ── ① ST / ② 停牌 ──────────────────────────────────────────────────────────


def test_st_flag_rejected_first():
    """spec 场景：X 当日 ST → 拒绝，原因码逐字 ST_FLAG。"""
    result = _check(_order(), is_st=True)
    assert result.approved is False
    assert result.reason == REASON_ST_FLAG == "ST_FLAG"
    assert result.detail["checks"] == [
        {"check": "st_flag", "passed": False, "reason": "ST_FLAG", "is_st": True}
    ]


def test_non_st_passes_st_check():
    result = _check(_order(), is_st=False)
    assert result.approved is True
    assert result.reason is None


def test_suspended_rejected():
    result = _check(_order(), is_suspended=True)
    assert result.approved is False
    assert result.reason == REASON_SUSPENDED == "SUSPENDED"


def test_non_suspended_passes():
    assert _check(_order(), is_suspended=False).approved is True


def test_st_precedes_suspended_and_others():
    """首个命中即拒（① 先于 ②）：ST + 停牌 + 一字涨停 + 超上限同单 → ST_FLAG。"""
    result = _check(
        _order(qty=150, target_weight=0.9),
        is_st=True,
        is_suspended=True,
        bar=_bar(limit_up_sealed=True),
        projected_total_weight=0.95,
    )
    assert result.reason == "ST_FLAG"
    assert len(result.detail["checks"]) == 1  # 拒绝即停，后续检查不执行


# ── ③ 涨跌停一字板 ─────────────────────────────────────────────────────────


def test_limit_up_sealed_buy_rejected():
    result = _check(_order(side="buy"), bar=_bar(limit_up_sealed=True))
    assert result.approved is False
    assert result.reason == REASON_LIMIT_UP_UNFILLABLE == "LIMIT_UP_UNFILLABLE"


def test_limit_up_sealed_sell_allowed():
    """一字涨停不阻断卖单（有买盘可成交）——与 matching M3-② 同向。"""
    result = _check(
        _order(side="sell"),
        bar=_bar(limit_up_sealed=True),
        position=PositionContext(sellable_qty=100, total_qty=100),
    )
    assert result.approved is True


def test_limit_down_sealed_sell_rejected():
    result = _check(
        _order(side="sell"),
        bar=_bar(limit_down_sealed=True),
        position=PositionContext(sellable_qty=100, total_qty=100),
    )
    assert result.approved is False
    assert result.reason == REASON_LIMIT_DOWN_UNFILLABLE == "LIMIT_DOWN_UNFILLABLE"


def test_limit_down_sealed_buy_allowed():
    assert _check(_order(side="buy"), bar=_bar(limit_down_sealed=True)).approved is True


def test_bar_missing_skips_limit_check():
    """bar 缺失 → 跳过涨跌停判定（不编造板状态），轨迹如实标注。"""
    result = _check(_order(side="buy"), bar=None)
    assert result.approved is True
    seal_check = next(c for c in result.detail["checks"] if c["check"] == "limit_seal")
    assert seal_check["passed"] is True
    assert "跳过" in seal_check["note"]


# ── ④⑤ 单票 / 总仓上限 ─────────────────────────────────────────────────────


def test_single_position_cap_rejected_above_20pct():
    result = _check(_order(target_weight=0.21))
    assert result.approved is False
    assert result.reason == REASON_SINGLE_POSITION_CAP == "SINGLE_POSITION_CAP"


def test_single_position_cap_boundary_exactly_20pct_passes():
    """边界：恰 20% 不拒（严格大于才拒）；浮点尘埃 ≤1e-9 亦不拒。"""
    assert _check(_order(target_weight=0.2)).approved is True
    assert _check(_order(target_weight=0.2 + 1e-12)).approved is True
    assert _check(_order(target_weight=0.2 + 1e-6)).reason == "SINGLE_POSITION_CAP"


def test_total_position_cap_rejected_above_80pct():
    result = _check(_order(target_weight=0.1), projected_total_weight=0.81)
    assert result.approved is False
    assert result.reason == REASON_TOTAL_POSITION_CAP == "TOTAL_POSITION_CAP"


def test_total_position_cap_boundary_exactly_80pct_passes():
    assert _check(_order(target_weight=0.1), projected_total_weight=0.8).approved is True


def test_single_cap_precedes_total_cap():
    """④ 先于 ⑤：单票 30% + 总仓 90% 同单 → SINGLE_POSITION_CAP。"""
    result = _check(_order(target_weight=0.3), projected_total_weight=0.9)
    assert result.reason == "SINGLE_POSITION_CAP"
    check_names = [c["check"] for c in result.detail["checks"]]
    assert check_names == ["st_flag", "suspended", "limit_seal", "single_position_cap"]


# ── ⑥ T+1 ─────────────────────────────────────────────────────────────────


def test_t1_sell_blocked_when_qty_exceeds_sellable():
    result = _check(
        _order(side="sell", qty=200),
        position=PositionContext(sellable_qty=100, total_qty=200),
    )
    assert result.approved is False
    assert result.reason == REASON_T1_SELL_BLOCKED == "T1_SELL_BLOCKED"


def test_t1_sell_within_sellable_passes():
    result = _check(
        _order(side="sell", qty=100),
        position=PositionContext(sellable_qty=100, total_qty=200),
    )
    assert result.approved is True


def test_t1_sell_without_position_context_rejected():
    """position 缺省 = 可卖 0：保守拒绝无持仓卖出。"""
    result = _check(_order(side="sell", qty=100), position=None)
    assert result.reason == "T1_SELL_BLOCKED"


def test_buy_ignores_sellable():
    assert _check(_order(side="buy", qty=100), position=PositionContext(0, 0)).approved is True


def test_t1_precedes_odd_lot():
    """⑥ 先于 ⑦（本门禁顺序，与 matching M3 的撮合前因裁决不同）：非整手卖出且超可卖 → T1。"""
    result = _check(
        _order(side="sell", qty=150),
        position=PositionContext(sellable_qty=100, total_qty=1000),
    )
    assert result.reason == "T1_SELL_BLOCKED"


# ── ⑦ 整手（100 股整数倍）──────────────────────────────────────────────────


def test_odd_lot_buy_rejected():
    result = _check(_order(side="buy", qty=150))
    assert result.approved is False
    assert result.reason == REASON_ODD_LOT == "ODD_LOT"


def test_odd_lot_full_position_sell_allowed():
    """卖零股全卖例外同 matching 语义：qty == 持仓总量 且总量非整手 → 放行。"""
    result = _check(
        _order(side="sell", qty=350),
        position=PositionContext(sellable_qty=350, total_qty=350),
    )
    assert result.approved is True


def test_odd_lot_sell_not_full_position_rejected():
    result = _check(
        _order(side="sell", qty=350),
        position=PositionContext(sellable_qty=350, total_qty=1000),
    )
    assert result.reason == "ODD_LOT"


def test_qty_non_positive_is_caller_error():
    with pytest.raises(ValueError, match="委托数量"):
        _check(_order(qty=0))


# ── 检查轨迹与证据链 ───────────────────────────────────────────────────────


def test_approved_detail_records_full_check_trace_in_order():
    result = _check(_order(), projected_total_weight=0.2)
    assert result.approved is True
    assert result.reason is None
    assert [c["check"] for c in result.detail["checks"]] == [
        "st_flag",
        "suspended",
        "limit_seal",
        "single_position_cap",
        "total_position_cap",
        "t1_sellable",
        "odd_lot",
    ]
    assert all(c["passed"] is True for c in result.detail["checks"])
    assert result.detail["decision_id"] == "dec-1"
    assert result.detail["signal_id"] == "600519.SH:2026-10-13"
    assert result.detail["symbol"] == "600519.SH"
    assert result.detail["side"] == "buy"


def test_order_requires_evidence_chain_fields():
    """订单挂 decision_id/signal_id（证据链）：缺失为调用方编程错误。"""
    with pytest.raises(TypeError):
        OrderIntent(  # type: ignore[call-arg]
            symbol="600519.SH", side="buy", qty=100, target_weight=0.1
        )


def test_cap_values_in_trace_read_from_constraints():
    """轨迹里的上限值与 screening/constraints 同源（同一约束常量）。"""
    result = _check(_order())
    cap_check = next(c for c in result.detail["checks"] if c["check"] == "single_position_cap")
    assert cap_check["cap"] == constraints.MAX_SINGLE_POSITION
    total_check = next(c for c in result.detail["checks"] if c["check"] == "total_position_cap")
    assert total_check["cap"] == constraints.MAX_TOTAL_POSITION
    assert cap_check["current_weight"] == 0.0


def test_constraints_single_source_at_call_time(monkeypatch):
    """单点改 screening/constraints.py 即改变门禁行为（spec「与 L1 同一约束常量」）。"""
    assert risk_gate.constraints is constraints
    monkeypatch.setattr(constraints, "MAX_SINGLE_POSITION", 0.5)
    monkeypatch.setattr(constraints, "LOT_SIZE", 10)
    assert _check(_order(qty=150, target_weight=0.3)).approved is True
    monkeypatch.setattr(constraints, "MAX_TOTAL_POSITION", 0.9)
    # 总仓 0.95 > 新上限 0.9 → 拒（证明总上限同样调用期读取）
    assert _check(_order(qty=150, target_weight=0.3), projected_total_weight=0.95).reason == (
        "TOTAL_POSITION_CAP"
    )


# ── 静态扫描（spec 场景：门禁无 LLM 依赖）────────────────────────────────


def _imported_full_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_risk_gate_has_no_llm_gateway_harness_import():
    """spec 场景：静态扫描 risk_gate.py，无 llm/gateway/harness import。"""
    names = _imported_full_names(_RISK_GATE_PATH)
    forbidden = [
        n for n in names if any(token in n.lower() for token in ("llm", "gateway", "harness"))
    ]
    assert forbidden == []
    # 约束定义 import 自 screening/constraints.py（spec 明文；调用期读模块属性）
    assert "finance_agent.screening.constraints" in names


def test_risk_gate_has_no_dynamic_import():
    """静态扫描补充：无 __import__ / importlib.import_module 动态加载后门。"""
    tree = ast.parse(_RISK_GATE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "__import__":
                pytest.fail("risk_gate.py 不得使用 __import__ 动态导入")
            if isinstance(func, ast.Attribute) and func.attr == "import_module":
                pytest.fail("risk_gate.py 不得使用 importlib.import_module 动态导入")


def test_matching_lot_size_imported_from_constraints():
    """matching.py 整手判定改 import LOT_SIZE（单一来源，no 本地字面量副本）。"""
    assert constraints.LOT_SIZE == 100
    assert matching.LOT_SIZE == constraints.LOT_SIZE
    assert "finance_agent.screening.constraints.LOT_SIZE" in _imported_full_names(
        Path(matching.__file__)
    )
    tree = ast.parse(Path(matching.__file__).read_text(encoding="utf-8"))
    assigned = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "LOT_SIZE" not in assigned
    assert "_LOT_SIZE" not in assigned
