"""A 股单一撮合引擎（add-matching-engine，纯函数无 IO）。

系统内所有成交判定（回测回放、仿真撮合、未来实盘回报解析）都必须经本模块
``match_order``，禁止在别处重新实现成交价格或成交可行性逻辑（spec「单一撮合
实现」）。关键设计决策 M1–M5（计划 docs/superpowers/plans/2026-10-13-add-matching-engine.md，
实施者不得自行改口径）：

- M1 成交价基准：买单成交价 = 当日 bar ``open × (1 + slippage_rate)``；卖单 =
  ``open × (1 − slippage_rate)``，``round(…, 2)``。回放语义：决策在 D 收盘后
  产出 → 最早可执行价是 D+1 开盘；D+1 一字涨停买单不成交。
- M2 一字板判定：``sealed_flags_from_ohlc`` —— 开=高=低=收 且涨跌幅达 ±阈值；
  阈值 0.048 覆盖 ST 5%（与 ``data/snapshot.py`` D3 的 4.8 规则同源；快照侧
  仍用交易所 pct 字段，不强行合并实现）。
- M3 校验顺序（同单多因的裁决）：① ``qty`` 非 100 整数倍 → ``ODD_LOT``
  （例外：卖单 ``qty == position.total_qty`` 且尾数非整手时允许零股全卖）
  → ② 涨跌停 → ③ T+1。``qty <= 0`` 与非法 side 是调用方编程错误 →
  ``ValueError``（不是业务原因码）。
- M4 滑点分档：``slippage_rate_for_amount`` 按成交额在全市场分位取档；
  ``market_amounts`` 缺失/空 或 ``amount`` 为 None → 最保守档。
- M5 费用：佣金 = ``max(最低佣金, 成交价×数量×佣金率)``（买卖双向）+ 印花税
  （``成交价×数量×印花税率``，仅卖出），各分项 round 2 后求和再 round 2。

原因码（逐字，跨模块契约）：``LIMIT_UP_UNFILLABLE`` / ``LIMIT_DOWN_UNFILLABLE``
/ ``T1_SELL_BLOCKED`` / ``ODD_LOT``；``FillResult.reason is None`` 表示成交。

口径护栏：回测引擎输入 bar 一律取自 hfq 全量 K 线（与结算同源）；paper 侧取
``market_snapshot`` 真实盘面价。两者只共享本引擎，不共享价格序列。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from finance_agent.screening.constraints import LOT_SIZE

# 原因码常量（spec 逐字；消费方比较时不得改写）
REASON_LIMIT_UP_UNFILLABLE = "LIMIT_UP_UNFILLABLE"
REASON_LIMIT_DOWN_UNFILLABLE = "LIMIT_DOWN_UNFILLABLE"
REASON_T1_SELL_BLOCKED = "T1_SELL_BLOCKED"
REASON_ODD_LOT = "ODD_LOT"

# A 股整手（买入须为整数倍；卖出零股例外见 match_order）——单一来源
# screening/constraints.py LOT_SIZE（add-signal-portfolio-risk-gate：风控门禁
# risk_gate.py 共用同一常量；本模块 import 期取值，撮合口径进程内不漂移）

# 一字板判定默认阈值（M2：覆盖 ST 5% 档；与 snapshot D3 的 4.8 同源）
_SEALED_THRESHOLD = 0.048


@dataclass(frozen=True)
class Order:
    """撮合订单（纯数据）：方向限 buy/sell（Literal 由类型检查钉住）。"""

    code: str
    side: Literal["buy", "sell"]
    qty: int


@dataclass(frozen=True)
class MarketBar:
    """撮合输入 bar：当日 OHLC + 昨收 + 成交额 + 一字板状态位。

    ``prev_close``/``amount`` 可为 None（数据缺失不编造）；``limit_up_sealed``/
    ``limit_down_sealed`` 由调用方按 M2（``sealed_flags_from_ohlc``）填好。
    """

    trade_date: str
    open: float
    high: float
    low: float
    close: float
    prev_close: float | None = None
    amount: float | None = None
    limit_up_sealed: bool = False
    limit_down_sealed: bool = False


@dataclass(frozen=True)
class PositionContext:
    """持仓上下文：当日可卖数量与总持仓（T+1 约束的输入）。

    回放场景（单笔决策级、不建持仓账本）卖单传 ``sellable_qty=total_qty=qty``
    视作已有隔日仓位（M6）；买单传 None。
    """

    sellable_qty: int = 0
    total_qty: int = 0


@dataclass(frozen=True)
class FillResult:
    """撮合结果：``reason is None`` 表示成交；未成交时量/价/费为 0/None/0。

    ``slippage_rate`` 记录实际取用档位（未成交为 0.0——不适用滑点）。
    """

    filled_qty: int
    fill_price: float | None
    fee: float
    reason: str | None
    slippage_rate: float


@dataclass(frozen=True)
class SlippageTier:
    """滑点档：成交额分位落在 ``(上一档 quantile_max, quantile_max]`` 内取该档费率。"""

    quantile_max: float
    slippage_rate: float


@dataclass(frozen=True)
class MatchingConfig:
    """撮合成本/滑点参数（由 ``execution/config.py`` 从 JSON 加载，不得硬编码于函数体）。

    ``slippage_tiers`` 按 ``quantile_max`` 严格升序、末档必须为 1.0（校验在加载处）。
    """

    commission_rate: float
    min_commission: float
    stamp_tax_rate: float
    slippage_tiers: tuple[SlippageTier, ...]


def sealed_flags_from_ohlc(
    open: float,
    high: float,
    low: float,
    close: float,
    prev_close: float | None,
    *,
    threshold: float = _SEALED_THRESHOLD,
) -> tuple[bool, bool]:
    """按 M2 判一字板，返回 ``(limit_up_sealed, limit_down_sealed)``。

    条件：``open == high == low == close`` 且 ``pct_chg = close / prev_close − 1``
    达 ``±threshold``（含边界，与 ``data/snapshot.py`` D3 的 ``>= ±4.8`` 同源口径）。
    ``prev_close`` 缺失或 <= 0 → ``(False, False)``（无法计算涨跌幅，不编造板状态）。
    """
    if prev_close is None or prev_close <= 0:
        return (False, False)
    if not (open == high == low == close):
        return (False, False)
    pct_chg = close / prev_close - 1.0
    if pct_chg >= threshold:
        return (True, False)
    if pct_chg <= -threshold:
        return (False, True)
    return (False, False)


def slippage_rate_for_amount(
    amount: float | None,
    market_amounts: Sequence[float] | None,
    config: MatchingConfig,
) -> float:
    """按 M4 取滑点费率：``amount`` 在全市场成交额样本中的分位落档。

    - 分位定义：``count(样本 <= amount) / len(样本)``；分位恰在切点时归更保守
      （更大滑点）的一档（``quantile <= quantile_max`` 比较）。
    - ``amount`` 为 None、``market_amounts`` 缺失/为空 → 取最保守档：全部档位中
      ``slippage_rate`` 最大者（默认配置 = 首档 0.002）。计划 M4 的括号「末档 =
      最大 slippage」在逐字配置（末档 quantile_max=1.0、滑点 0.0005）下不自洽，
      本实现以「最保守 = 最大 slippage」语义为准（回测宁可高估成本、不美化收益）。
      空样本判定用 ``is None``/``len(...) == 0``（不依赖真值）：numpy 数组样本
      不得因真值歧义被当作缺失（评审修正 A3）。
    """
    if amount is None or market_amounts is None or len(market_amounts) == 0:
        return max(tier.slippage_rate for tier in config.slippage_tiers)
    quantile = sum(1 for sample in market_amounts if sample <= amount) / len(market_amounts)
    for tier in config.slippage_tiers:
        if quantile <= tier.quantile_max:
            return tier.slippage_rate
    # 防御：配置校验（执行/加载处）已保证末档 quantile_max == 1.0，正常不可达
    return config.slippage_tiers[-1].slippage_rate


def _rejected(reason: str) -> FillResult:
    """构造未成交结果：量 0、价 None、费 0、滑点 0（未成交不适用滑点）。"""
    return FillResult(filled_qty=0, fill_price=None, fee=0.0, reason=reason, slippage_rate=0.0)


def _compute_fee(
    fill_price: float,
    qty: int,
    side: Literal["buy", "sell"],
    config: MatchingConfig,
) -> float:
    """按 M5 计算费用（两个分项各 round 2 后求和再 round 2）。

    佣金买卖双向收取：``max(min_commission, fill_price × qty × commission_rate)``；
    印花税仅卖出：``fill_price × qty × stamp_tax_rate``。
    """
    turnover = fill_price * qty
    commission = round(max(config.min_commission, turnover * config.commission_rate), 2)
    stamp_tax = round(turnover * config.stamp_tax_rate, 2) if side == "sell" else 0.0
    return round(commission + stamp_tax, 2)


def match_order(
    order: Order,
    bar: MarketBar,
    *,
    config: MatchingConfig,
    position: PositionContext | None = None,
    market_amounts: Sequence[float] | None = None,
) -> FillResult:
    """A 股撮合唯一实现：校验约束（M3）→ 定价（M1/M4）→ 计费（M5）。

    校验顺序（同单多因裁前因）：
    ① ``qty`` 非 100 整数倍 → ``ODD_LOT``；例外：卖单 ``qty == position.total_qty``
       且 ``total_qty % 100 != 0`` 时允许零股全卖；
    ② 买单一字涨停 → ``LIMIT_UP_UNFILLABLE``；卖单一字跌停 → ``LIMIT_DOWN_UNFILLABLE``；
    ③ 卖单 ``qty > position.sellable_qty``（``position`` 缺省视作 0，保守拒绝
       无持仓卖出）→ ``T1_SELL_BLOCKED``。
    ``qty <= 0`` 或 side 非 buy/sell → ``ValueError``（调用方编程错误，非原因码）。
    通过全部约束后：成交价 = ``round(open × (1 ± slippage_rate), 2)``（M1），
    ``slippage_rate`` 由 ``bar.amount`` 经 ``slippage_rate_for_amount`` 取档（M4），
    费用按 M5 计算并把 ``fee`` 记入结果（回放收益序列暂不折算费用，报告披露）。
    """
    if order.qty <= 0:
        raise ValueError(f"委托数量必须为正整数: {order.qty!r}")
    if order.side not in ("buy", "sell"):
        raise ValueError(f"委托方向必须为 buy/sell: {order.side!r}")

    # ① 整手约束（卖零股全卖例外）
    if order.qty % LOT_SIZE != 0:
        full_odd_lot_sell = (
            order.side == "sell"
            and position is not None
            and order.qty == position.total_qty
            and position.total_qty % LOT_SIZE != 0
        )
        if not full_odd_lot_sell:
            return _rejected(REASON_ODD_LOT)

    # ② 涨跌停约束（买不进一字涨停 / 卖不出一字跌停）
    if order.side == "buy" and bar.limit_up_sealed:
        return _rejected(REASON_LIMIT_UP_UNFILLABLE)
    if order.side == "sell" and bar.limit_down_sealed:
        return _rejected(REASON_LIMIT_DOWN_UNFILLABLE)

    # ③ T+1 约束（position 缺省 = 无可卖持仓）
    if order.side == "sell":
        sellable_qty = position.sellable_qty if position is not None else 0
        if order.qty > sellable_qty:
            return _rejected(REASON_T1_SELL_BLOCKED)

    # 成交价（M1）+ 滑点取档（M4）
    slippage_rate = slippage_rate_for_amount(bar.amount, market_amounts, config)
    direction = 1.0 if order.side == "buy" else -1.0
    fill_price = round(bar.open * (1.0 + direction * slippage_rate), 2)
    fee = _compute_fee(fill_price, order.qty, order.side, config)
    return FillResult(
        filled_qty=order.qty,
        fill_price=fill_price,
        fee=fee,
        reason=None,
        slippage_rate=slippage_rate,
    )
