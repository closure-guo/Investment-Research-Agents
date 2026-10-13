"""add-matching-engine Task 7：10 组涨跌停/滑点案例的独立核对脚本（fixture + 手算期望）。

用途：对 `tests/validation/2026-10-13-add-matching-engine-validation.md` 的人工核对表做
「手算 vs 引擎」逐行对照。**手算期望（EXPECTED）由人独立计算后写死在此**（计算依据见每例
`hand_note`），不调用引擎推导；脚本只做一件事——把相同 fixture 与参数喂给
`finance_agent.execution.matching`，打印两列并报告差异。

背景（如实记录）：本机东财 IP 封禁（incident 033 / issue #325）导致无法拉取真实历史行情
挑选 10 个真实涨跌停交易日，本表为 fixture 构造 + 手算期望的替代证据；实盘数据核对挂
follow-up（见验证报告）。

用法：
    uv run python tests/validation/2026-10-13-add-matching-engine-cases.py
退出码：0 = 全部逐字段一致；1 = 存在不一致（逐行打印差异）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from finance_agent.execution.matching import (
    MarketBar,
    MatchingConfig,
    Order,
    PositionContext,
    SlippageTier,
    match_order,
    sealed_flags_from_ohlc,
)

# 与 config/matching_engine.json 逐字一致的默认口径（comm 0.00025 / 最低 5 元 / 印花税 0.0005）
CONFIG = MatchingConfig(
    commission_rate=0.00025,
    min_commission=5.0,
    stamp_tax_rate=0.0005,
    slippage_tiers=(
        SlippageTier(quantile_max=0.2, slippage_rate=0.002),
        SlippageTier(quantile_max=0.6, slippage_rate=0.001),
        SlippageTier(quantile_max=1.0, slippage_rate=0.0005),
    ),
)

# 全市场成交额样本（10 档木）：分位 = count(样本 <= amount) / 10
MARKET_AMOUNTS: tuple[float, ...] = (
    100.0,
    200.0,
    300.0,
    400.0,
    500.0,
    600.0,
    700.0,
    800.0,
    900.0,
    1000.0,
)


@dataclass(frozen=True)
class Expected:
    """手算期望（独立于引擎；数值与报告表格逐字一致）。"""

    limit_up_sealed: bool
    limit_down_sealed: bool
    filled_qty: int
    fill_price: float | None
    fee: float
    reason: str | None
    slippage_rate: float


@dataclass(frozen=True)
class Case:
    idx: int
    name: str
    code: str
    side: str
    qty: int
    prev_close: float
    open_: float
    high: float
    low: float
    close: float
    amount: float | None
    use_market_amounts: bool
    position: PositionContext | None
    expected: Expected
    hand_note: str


_FULL_POS = PositionContext(sellable_qty=100, total_qty=100)
_ST_HELD = PositionContext(sellable_qty=100, total_qty=100)
_ODD_LOT_POS = PositionContext(sellable_qty=350, total_qty=350)

CASES: tuple[Case, ...] = (
    Case(
        idx=1,
        name="主板 10% 一字涨停买单",
        code="600519",
        side="buy",
        qty=100,
        prev_close=100.0,
        open_=110.0,
        high=110.0,
        low=110.0,
        close=110.0,
        amount=None,
        use_market_amounts=False,
        position=None,
        expected=Expected(
            limit_up_sealed=True,
            limit_down_sealed=False,
            filled_qty=0,
            fill_price=None,
            fee=0.0,
            reason="LIMIT_UP_UNFILLABLE",
            slippage_rate=0.0,
        ),
        hand_note="110/100−1=+10% ≥ 4.8% 且 o=h=l=c → 涨停一字；买单被拒（fail-closed）",
    ),
    Case(
        idx=2,
        name="创业板 20% 一字涨停买单",
        code="300750",
        side="buy",
        qty=100,
        prev_close=100.0,
        open_=120.0,
        high=120.0,
        low=120.0,
        close=120.0,
        amount=None,
        use_market_amounts=False,
        position=None,
        expected=Expected(
            limit_up_sealed=True,
            limit_down_sealed=False,
            filled_qty=0,
            fill_price=None,
            fee=0.0,
            reason="LIMIT_UP_UNFILLABLE",
            slippage_rate=0.0,
        ),
        hand_note="120/100−1=+20% ≥ 4.8% 且 o=h=l=c → 涨停一字；买单被拒",
    ),
    Case(
        idx=3,
        name="ST 5% 一字涨停买单",
        code="600001",
        side="buy",
        qty=100,
        prev_close=10.0,
        open_=10.5,
        high=10.5,
        low=10.5,
        close=10.5,
        amount=None,
        use_market_amounts=False,
        position=None,
        expected=Expected(
            limit_up_sealed=True,
            limit_down_sealed=False,
            filled_qty=0,
            fill_price=None,
            fee=0.0,
            reason="LIMIT_UP_UNFILLABLE",
            slippage_rate=0.0,
        ),
        hand_note="10.5/10−1=+5% ≥ 4.8%（ST 档被 4.8 阈值覆盖）→ 涨停一字；买单被拒",
    ),
    Case(
        idx=4,
        name="ST 5% 一字涨停卖单（有买盘可成交）",
        code="600001",
        side="sell",
        qty=100,
        prev_close=10.0,
        open_=10.5,
        high=10.5,
        low=10.5,
        close=10.5,
        amount=None,
        use_market_amounts=False,
        position=_ST_HELD,
        expected=Expected(
            limit_up_sealed=True,
            limit_down_sealed=False,
            filled_qty=100,
            fill_price=10.48,
            fee=5.52,
            reason=None,
            slippage_rate=0.002,
        ),
        hand_note="卖方成交价=10.5×(1−0.002)=10.479→10.48；佣金 max(5, 1048×0.00025)=5；印花税 1048×0.0005=0.52；费 5.52",
    ),
    Case(
        idx=5,
        name="一字跌停卖单",
        code="600519",
        side="sell",
        qty=100,
        prev_close=100.0,
        open_=90.0,
        high=90.0,
        low=90.0,
        close=90.0,
        amount=None,
        use_market_amounts=False,
        position=_FULL_POS,
        expected=Expected(
            limit_up_sealed=False,
            limit_down_sealed=True,
            filled_qty=0,
            fill_price=None,
            fee=0.0,
            reason="LIMIT_DOWN_UNFILLABLE",
            slippage_rate=0.0,
        ),
        hand_note="90/100−1=−10% ≤ −4.8% 且 o=h=l=c → 跌停一字；卖单被拒",
    ),
    Case(
        idx=6,
        name="一字跌停买单（卖盘充足可成交）",
        code="600519",
        side="buy",
        qty=100,
        prev_close=100.0,
        open_=90.0,
        high=90.0,
        low=90.0,
        close=90.0,
        amount=500.0,
        use_market_amounts=True,
        position=None,
        expected=Expected(
            limit_up_sealed=False,
            limit_down_sealed=True,
            filled_qty=100,
            fill_price=90.09,
            fee=5.0,
            reason=None,
            slippage_rate=0.001,
        ),
        hand_note="分位=5/10=0.5→中档 0.001；买方价=90×1.001=90.09；佣金 max(5, 9009×0.00025)=5；买入无印花税",
    ),
    Case(
        idx=7,
        name="正常日 × 低流动性档（q=0.2 恰在切点）",
        code="600519",
        side="buy",
        qty=800,
        prev_close=100.0,
        open_=101.0,
        high=102.5,
        low=100.5,
        close=102.0,
        amount=200.0,
        use_market_amounts=True,
        position=None,
        expected=Expected(
            limit_up_sealed=False,
            limit_down_sealed=False,
            filled_qty=800,
            fill_price=101.2,
            fee=20.24,
            reason=None,
            slippage_rate=0.002,
        ),
        hand_note="o≠h → 非一字；分位=2/10=0.2 ≤ 0.2 → 档 0.002；价=101×1.002=101.2；佣金 max(5, 80960×0.00025=20.24)",
    ),
    Case(
        idx=8,
        name="正常日 × 中档滑点",
        code="600519",
        side="buy",
        qty=800,
        prev_close=100.0,
        open_=101.0,
        high=102.5,
        low=100.5,
        close=102.0,
        amount=500.0,
        use_market_amounts=True,
        position=None,
        expected=Expected(
            limit_up_sealed=False,
            limit_down_sealed=False,
            filled_qty=800,
            fill_price=101.1,
            fee=20.22,
            reason=None,
            slippage_rate=0.001,
        ),
        hand_note="分位=5/10=0.5 ≤ 0.6 → 档 0.001；价=101×1.001=101.1；佣金 max(5, 80880×0.00025=20.22)",
    ),
    Case(
        idx=9,
        name="正常日 × 高流动性档",
        code="600519",
        side="buy",
        qty=800,
        prev_close=100.0,
        open_=101.0,
        high=102.5,
        low=100.5,
        close=102.0,
        amount=900.0,
        use_market_amounts=True,
        position=None,
        expected=Expected(
            limit_up_sealed=False,
            limit_down_sealed=False,
            filled_qty=800,
            fill_price=101.05,
            fee=20.21,
            reason=None,
            slippage_rate=0.0005,
        ),
        hand_note="分位=9/10=0.9 > 0.6 → 档 0.0005；价=101×1.0005=101.05；佣金 max(5, 80840×0.00025=20.21)",
    ),
    Case(
        idx=10,
        name="零股卖出例外（350 股 = 持仓总量）",
        code="600519",
        side="sell",
        qty=350,
        prev_close=20.0,
        open_=20.5,
        high=21.0,
        low=20.2,
        close=20.8,
        amount=900.0,
        use_market_amounts=True,
        position=_ODD_LOT_POS,
        expected=Expected(
            limit_up_sealed=False,
            limit_down_sealed=False,
            filled_qty=350,
            fill_price=20.49,
            fee=8.59,
            reason=None,
            slippage_rate=0.0005,
        ),
        hand_note="350 非整手但=总持仓 → 允许全卖；档 0.0005；价=20.5×0.9995=20.49；佣金 max(5, 7171.5×0.00025)=5；印花税 3.59；费 8.59",
    ),
)


def _fmt(value: object) -> str:
    if value is None:
        return "None"
    return str(value)


def _fields(
    filled: int, price: float | None, fee: float, reason: str | None, rate: float
) -> tuple[int, float | None, float, str | None, float]:
    return (filled, price, fee, reason, rate)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台编码防御（中文输出）
    mismatches: list[str] = []
    print("==" * 40)
    print("add-matching-engine Task 7 十案例对照（手算期望 vs 引擎输出）")
    print("==" * 40)
    for case in CASES:
        sealed = sealed_flags_from_ohlc(
            case.open_, case.high, case.low, case.close, case.prev_close
        )
        bar = MarketBar(
            trade_date="2026-10-12",
            open=case.open_,
            high=case.high,
            low=case.low,
            close=case.close,
            prev_close=case.prev_close,
            amount=case.amount,
            limit_up_sealed=sealed[0],
            limit_down_sealed=sealed[1],
        )
        result = match_order(
            Order(code=case.code, side=case.side, qty=case.qty),  # type: ignore[arg-type]
            bar,
            config=CONFIG,
            position=case.position,
            market_amounts=MARKET_AMOUNTS if case.use_market_amounts else None,
        )
        sealed_expected = (case.expected.limit_up_sealed, case.expected.limit_down_sealed)
        actual = _fields(
            result.filled_qty, result.fill_price, result.fee, result.reason, result.slippage_rate
        )
        expected = _fields(
            case.expected.filled_qty,
            case.expected.fill_price,
            case.expected.fee,
            case.expected.reason,
            case.expected.slippage_rate,
        )
        ok = sealed == sealed_expected and actual == expected
        if not ok:
            mismatches.append(
                f"[{case.idx}] {case.name}: 手算 sealed={sealed_expected} "
                f"实际 sealed={sealed}；手算 fill={expected} 实际 fill={actual}"
            )
        print(
            f"[{case.idx:>2}] {case.name}\n"
            f"     手算  sealed={(sealed_expected[0], sealed_expected[1])}  "
            f"filled={expected[0]} price={_fmt(expected[1])} fee={_fmt(expected[2])} "
            f"reason={_fmt(expected[3])} slippage={expected[4]}\n"
            f"     引擎  sealed={sealed}  "
            f"filled={actual[0]} price={_fmt(actual[1])} fee={_fmt(actual[2])} "
            f"reason={_fmt(actual[3])} slippage={actual[4]}\n"
            f"     依据  {case.hand_note}\n"
            f"     核对  {'一致' if ok else '不一致'}"
        )
    print("==" * 40)
    if mismatches:
        print(f"结果：{len(mismatches)}/{len(CASES)} 例不一致（如实记录为异常）：")
        for line in mismatches:
            print("  " + line)
        return 1
    print(
        f"结果：10/{len(CASES)} 例逐字段一致（filled_qty / fill_price / fee / reason / slippage / sealed）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
