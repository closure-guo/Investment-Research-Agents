"""add-paper-broker Task 4：合成面板回放核对（一个月，≥10 笔成交；逐笔价格/费用独立手算对照）。

用途：对 `tests/validation/2026-10-13-add-paper-broker-validation.md` 的回放核对表做
「独立手算 vs PaperBroker 实跑」逐行对照。**期望值在本脚本内独立计算**（不调用
broker/matching 的实现推导价格：滑点档位由脚本自己的分位函数按木样选取，成交价 =
``round(open × (1 ± rate), 2)``，费用 = ``round(max(最低佣金, 成交价×数量×佣金率)
(+ 卖出印花税), 2)``），broker 侧只经公开接口 ``PaperBroker.place_order`` 下单。

背景（如实记录）：2026-09 真实行情回放受东财 IP 封禁（incident 033 / issue #325）
阻断——无法拉取真实历史 bar 与全市场成交额样本；本脚本以**合成面板**（2026-09
工作日，3 标的、逐日 open/close、固定成交额木样）替代，覆盖成交价/费用/资金/持仓
的确定性算术；真实行情口径核对挂 follow-up（见验证报告异常记录）。

口径：撮合配置取真实 ``config/matching_engine.json``（``load_matching_config``，
佣金 0.00025 / 最低 5 元 / 印花税 0.0005 / 滑点三档 0.002/0.001/0.0005）；成交额
木样 ``MARKET_AMOUNTS``（10 档）决定分位档位；标的 bar 的 ``amount`` 按构造分别落
三档，覆盖全部滑点档。回放另含 1 笔一字涨停买入（挂单不成交）验证 pending 分支，
不计入成交笔数。

用法::

    uv run python tests/validation/2026-10-13-add-paper-broker-replay-check.py [--markdown]

输出：stdout 对照表（--markdown 输出 Markdown 表）+ JSON 落
``tests/validation/2026-10-13-add-paper-broker-replay-check.json``。
退出码：0 = 全部逐字段一致；1 = 存在不一致（逐行打印差异）。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from finance_agent.execution.brokers.base import (
    REASON_INSUFFICIENT_CASH,  # noqa: F401  （口径对照：本回放不触发，留作原因码速查）
    STATUS_FILLED,
    STATUS_PENDING,
)
from finance_agent.execution.brokers.paper import PaperBroker
from finance_agent.execution.config import load_matching_config
from finance_agent.execution.matching import (
    REASON_LIMIT_UP_UNFILLABLE,
    MarketBar,
    MatchingConfig,
    sealed_flags_from_ohlc,
)
from finance_agent.execution.risk_gate import OrderIntent

INITIAL_CASH = 1_000_000.0  # spec 场景：初始资金 100 万元
JSON_OUT = Path(__file__).with_suffix(".json")

# 全市场成交额木样（10 档）：分位 = count(样本 <= amount) / 10
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

# 2026-09 工作日（一个月回放窗口）
TRADING_DAYS: tuple[str, ...] = (
    "2026-09-01",
    "2026-09-02",
    "2026-09-03",
    "2026-09-04",
    "2026-09-07",
    "2026-09-08",
    "2026-09-09",
    "2026-09-10",
    "2026-09-11",
    "2026-09-14",
    "2026-09-15",
    "2026-09-16",
    "2026-09-17",
    "2026-09-18",
    "2026-09-21",
    "2026-09-22",
    "2026-09-23",
    "2026-09-24",
    "2026-09-25",
    "2026-09-28",
    "2026-09-29",
    "2026-09-30",
)

# 合成面板：symbol -> (基准 open 价, 每日步进, bar 成交额)；
# amount 100→分位 0.1（首档 0.002）/ 500→0.5（中档 0.001）/ 1000→1.0（末档 0.0005）
SYMBOLS: dict[str, tuple[float, float, float]] = {
    "600519.SH": (1600.0, 8.0, 1000.0),
    "300308.SZ": (150.0, 1.5, 500.0),
    "000858.SZ": (120.0, 0.8, 100.0),
}

# 订单日程（签名：日期 / 标的 / 方向 / 数量）——买入在前、卖出按已在仓（隔日可卖）；
# 13 笔全部可成交（非一字板、资金充足），不足 100 股非整手不出现
SCHEDULE: tuple[tuple[str, str, str, int], ...] = (
    ("2026-09-01", "600519.SH", "buy", 100),
    ("2026-09-01", "300308.SZ", "buy", 200),
    ("2026-09-01", "000858.SZ", "buy", 100),
    ("2026-09-07", "600519.SH", "buy", 100),
    ("2026-09-07", "300308.SZ", "buy", 100),
    ("2026-09-14", "600519.SH", "buy", 200),
    ("2026-09-14", "000858.SZ", "buy", 200),
    ("2026-09-21", "300308.SZ", "buy", 100),
    ("2026-09-28", "600519.SH", "sell", 200),
    ("2026-09-28", "300308.SZ", "sell", 100),
    ("2026-09-30", "600519.SH", "sell", 200),
    ("2026-09-30", "300308.SZ", "sell", 300),
    ("2026-09-30", "000858.SZ", "sell", 300),
)

# 一字涨停挂单用例：600519 于 2026-09-10 涨停封板 → 买单 pending 不成交
SEALED_CASE = ("2026-09-10", "600519.SH", "buy", 100)


def _round2(value: float) -> float:
    """两位小数（与引擎同口径的普通四舍五入；本脚本的独立性在算术本身）。"""
    return round(value, 2)


def tier_rate_for(amount: float, config: MatchingConfig) -> float:
    """独立分位选档：count(样本<=amount)/n 落 (上一档 quantile_max, quantile_max]。"""
    quantile = sum(1 for sample in MARKET_AMOUNTS if sample <= amount) / len(MARKET_AMOUNTS)
    for tier in config.slippage_tiers:
        if quantile <= tier.quantile_max:
            return tier.slippage_rate
    raise AssertionError("不可达：末档 quantile_max=1.0")


def expected_fill(
    open_price: float, side: str, qty: int, amount: float, config: MatchingConfig
) -> tuple[float, float, float]:
    """独立手算：返回 (滑点率, 成交价, 费用)。

    成交价 = round(open × (1 + rate), 2)（买）/ round(open × (1 − rate), 2)（卖）；
    费用 = round(round(max(最低佣金, 成交价×数量×佣金率), 2)
                 (+ round(成交价×数量×印花税率, 2) 卖出), 2)。
    """
    rate = tier_rate_for(amount, config)
    price = _round2(open_price * (1.0 + rate) if side == "buy" else open_price * (1.0 - rate))
    turnover = price * qty
    commission = _round2(max(config.min_commission, turnover * config.commission_rate))
    stamp = _round2(turnover * config.stamp_tax_rate) if side == "sell" else 0.0
    return rate, price, _round2(commission + stamp)


def panel_bar(symbol: str, trade_date: str, *, sealed: bool = False) -> MarketBar:
    """合成 bar：open 逐日步进；sealed=True 时构造一字涨停（up +10% 整数关口）。"""
    base, step, amount = SYMBOLS[symbol]
    day_index = TRADING_DAYS.index(trade_date)
    open_price = _round2(base + step * day_index)
    if sealed:
        # 昨收 = 今开 /1.10（涨停口径），一字板 open=high=low=close
        prev_close = _round2(open_price / 1.10)
        up, down = sealed_flags_from_ohlc(
            open_price, open_price, open_price, open_price, prev_close
        )
        assert up and not down, "sealed 构造失败"
        return MarketBar(
            trade_date=trade_date,
            open=open_price,
            high=open_price,
            low=open_price,
            close=open_price,
            prev_close=prev_close,
            amount=amount,
            limit_up_sealed=True,
            limit_down_sealed=False,
        )
    close = _round2(open_price * 1.01)
    prev_close = (
        _round2(base + step * (day_index - 1)) if day_index > 0 else _round2(open_price / 1.005)
    )
    up, down = sealed_flags_from_ohlc(
        open_price, _round2(open_price * 1.02), _round2(open_price * 0.99), close, prev_close
    )
    assert not up and not down, f"非 sealed 构造失败: {symbol} {trade_date}"
    return MarketBar(
        trade_date=trade_date,
        open=open_price,
        high=_round2(open_price * 1.02),
        low=_round2(open_price * 0.99),
        close=close,
        prev_close=prev_close,
        amount=amount,
    )


def run_replay(config: MatchingConfig) -> list[dict[str, object]]:
    """在临时库回放整月日程，返回逐笔对照行（含独立期望与实跑读数）。"""
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="paper-replay-") as tmp:
        broker = PaperBroker(
            db_path=Path(tmp) / "execution.db", config=config, initial_cash=INITIAL_CASH
        )
        cash = INITIAL_CASH
        for idx, (trade_date, symbol, side, qty) in enumerate(SCHEDULE, start=1):
            bar = panel_bar(symbol, trade_date)
            amount = SYMBOLS[symbol][2]
            rate, exp_price, exp_fee = expected_fill(bar.open, side, qty, amount, config)
            record = broker.place_order(
                OrderIntent(
                    decision_id=f"dec-2026-09-{idx:02d}",
                    signal_id=f"sig-{symbol}-{trade_date}-{side}",
                    symbol=symbol,
                    side=side,  # type: ignore[arg-type]
                    qty=qty,
                    target_weight=0.1,
                ),
                bar=bar,
                market_amounts=MARKET_AMOUNTS,
            )
            # 独立资金台账（期望侧）：买入 −(价×量+费)；卖出 +(价×量−费)
            cash = (
                _round2(cash - (exp_price * qty + exp_fee))
                if side == "buy"
                else _round2(cash + (exp_price * qty - exp_fee))
            )
            rows.append(
                {
                    "seq": idx,
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "bar_open": bar.open,
                    "amount": amount,
                    "slippage_rate": rate,
                    "expected_price": exp_price,
                    "actual_price": record.fill_price,
                    "expected_fee": exp_fee,
                    "actual_fee": record.fee,
                    "status": record.status,
                    "reason": record.reason,
                    "match": (
                        record.status == STATUS_FILLED
                        and record.fill_price == exp_price
                        and record.fee == exp_fee
                    ),
                }
            )
        # 一字涨停挂单：不成交、零持仓变动（spec 场景；不计成交笔数）
        sealed_date, sealed_symbol, sealed_side, sealed_qty = SEALED_CASE
        before = {p["symbol"]: p["qty"] for p in broker.query_positions()}
        sealed_record = broker.place_order(
            OrderIntent(
                decision_id="dec-2026-09-sealed",
                signal_id=f"sig-{sealed_symbol}-{sealed_date}-{sealed_side}",
                symbol=sealed_symbol,
                side=sealed_side,  # type: ignore[arg-type]
                qty=sealed_qty,
                target_weight=0.1,
            ),
            bar=panel_bar(sealed_symbol, sealed_date, sealed=True),
            market_amounts=MARKET_AMOUNTS,
        )
        after = {p["symbol"]: p["qty"] for p in broker.query_positions()}
        sealed_ok = (
            sealed_record.status == STATUS_PENDING
            and sealed_record.reason == REASON_LIMIT_UP_UNFILLABLE
            and before == after
        )
        actual_cash = broker.query_cash()
        positions = broker.query_positions()
        rows.append(
            {
                "seq": "sealed",
                "trade_date": sealed_date,
                "symbol": sealed_symbol,
                "side": sealed_side,
                "qty": sealed_qty,
                "status": sealed_record.status,
                "reason": sealed_record.reason,
                "positions_unchanged": before == after,
                "match": sealed_ok,
            }
        )
        rows.append(
            {
                "seq": "cash",
                "expected_cash_ledger": cash,
                "actual_cash": actual_cash,
                "match": abs(cash - actual_cash) < 1e-6,
            }
        )
        rows.append(
            {
                "seq": "positions",
                "expected_positions_empty": True,
                "actual_positions": positions,
                "match": positions == [],
            }
        )
    return rows


def _markdown_table(rows: Sequence[dict[str, object]]) -> str:
    lines = [
        "| # | 日期 | 标的 | 方向 | 数量 | bar open | 滑点档 | 期望成交价 | 实跑成交价 | 期望费用 | 实跑费用 | 判定 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        if row.get("seq") in ("cash", "positions", "sealed"):
            continue
        lines.append(
            "| {seq} | {trade_date} | {symbol} | {side} | {qty} | {bar_open} | {slippage_rate} |"
            " {expected_price} | {actual_price} | {expected_fee} | {actual_fee} | {verdict} |".format(
                verdict="✅" if row["match"] else "❌", **row
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="paper broker 合成面板回放核对")
    parser.add_argument("--markdown", action="store_true", help="stdout 输出 Markdown 对照表")
    args = parser.parse_args()

    config = load_matching_config()
    rows = run_replay(config)
    fills = [r for r in rows if r.get("side") and r.get("seq") != "sealed"]
    fills_ok = sum(1 for r in fills if r["match"])
    sealed_row = next(r for r in rows if r.get("seq") == "sealed")
    cash_row = next(r for r in rows if r.get("seq") == "cash")
    positions_row = next(r for r in rows if r.get("seq") == "positions")
    all_ok = (
        fills_ok == len(fills)
        and sealed_row["match"]
        and cash_row["match"]
        and positions_row["match"]
    )

    payload: dict[str, object] = {
        "script": Path(__file__).name,
        "date": "2026-10-13",
        "window": ["2026-09-01", "2026-09-30"],
        "data_source": "synthetic",
        "data_source_note": "2026-09 真实行情回放受东财 IP 封禁（incident 033 / issue #325）阻断，本回放为合成面板替代",
        "config": {
            "commission_rate": config.commission_rate,
            "min_commission": config.min_commission,
            "stamp_tax_rate": config.stamp_tax_rate,
            "slippage_tiers": [
                {"quantile_max": t.quantile_max, "slippage_rate": t.slippage_rate}
                for t in config.slippage_tiers
            ],
        },
        "market_amounts": list(MARKET_AMOUNTS),
        "initial_cash": INITIAL_CASH,
        "fills": len(fills),
        "fills_matched": fills_ok,
        "sealed_case": {
            "status": sealed_row["status"],
            "reason": sealed_row["reason"],
            "match": sealed_row["match"],
        },
        "cash_ledger": {
            "expected": cash_row["expected_cash_ledger"],
            "actual": cash_row["actual_cash"],
            "match": cash_row["match"],
        },
        "positions_empty": positions_row["match"],
        "rows": rows,
        "verdict": "PASS" if all_ok else "FAIL",
    }
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.markdown:
        print(_markdown_table(rows))
    else:
        header = (
            f"{'#':>2} {'日期':<10} {'标的':<9} {'方向':<4} {'数量':>4} {'open':>8}"
            f" {'档':>6} {'期望价':>9} {'实跑价':>9} {'期望费':>7} {'实跑费':>7} 判定"
        )
        print(header)
        print("-" * len(header))
        for row in fills:
            print(
                f"{row['seq']:>2} {row['trade_date']:<10} {row['symbol']:<9} {row['side']:<4}"
                f" {row['qty']:>4} {row['bar_open']:>8} {row['slippage_rate']:>6}"
                f" {row['expected_price']:>9} {row['actual_price']:>9}"
                f" {row['expected_fee']:>7} {row['actual_fee']:>7} "
                f"{'OK' if row['match'] else 'MISMATCH'}"
            )
        print(
            f"\n一字涨停挂单: status={sealed_row['status']} reason={sealed_row['reason']}"
            f" 零持仓变动={sealed_row['positions_unchanged']} -> {'OK' if sealed_row['match'] else 'MISMATCH'}"
        )
        print(
            f"资金台账: 期望 {cash_row['expected_cash_ledger']} vs 实际 {cash_row['actual_cash']}"
            f" -> {'OK' if cash_row['match'] else 'MISMATCH'}"
        )
        print(
            f"期末持仓: {positions_row['actual_positions'] or '空仓'} -> {'OK' if positions_row['match'] else 'MISMATCH'}"
        )

    print(f"\n成交逐笔核对: {fills_ok}/{len(fills)} 匹配；JSON → {JSON_OUT}")
    print(f"verdict: {payload['verdict']}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
