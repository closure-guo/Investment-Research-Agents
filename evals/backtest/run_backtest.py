"""回测编排 CLI（人工触发；LLM 消耗 ≈ 股数 × 3 次回放）。

流程：分层抽样 → 逐样本 replay_with_consistency（n=3）→ 结算收益序列 →
绩效四指标 + 基线对照 + block bootstrap Sharpe CI + 一致性披露 →
reports/backtest/<name>.json。一致率 < 2/3 的标的不进绩效汇总，单独披露。

用法：
    uv run python -m evals.backtest.run_backtest --codes 600519 000858 \
        --per-regime 10 --repeats 3 [--sanity-note "..."]
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from evals.backtest.baselines import Strategy, baseline_positions, strategy_returns
from evals.backtest.performance import perf_metrics
from evals.backtest.replay import replay_with_consistency
from evals.backtest.sampling import stratified_sample
from evals.backtest.significance import block_length_sensitivity, validate_sanity
from evals.stats import paired_block_bootstrap_diff
from finance_agent.data.akshare_client import SETTLEMENT_ADJUST
from finance_agent.outcome.settle import BENCHMARK_CODE

CONSISTENCY_FLOOR = 2 / 3
BASELINE_STRATEGIES: tuple[Strategy, ...] = ("buy_hold", "macd", "kdj", "rsi")


def _trade_daily_returns(result: dict, kline: pd.DataFrame) -> list[float]:
    """单笔回放结果（Task 9 replay 返回：settlement + entry_price + action +
    decision_date）→ 持有期日收益序列（entry=决策日收盘，exit=结算价）。

    近似：持有期内按逐日收盘 pct_change，末日修正到结算价；
    方法在报告 metadata.methodology 披露。方向符号：buy 为正、sell 为负；
    非 buy/sell（hold/watch→neutral，属回避语义）不产生方向化 system 收益——本节返回 []
    且由编排层整条排除（§1.8② 主结论仅基于可执行决策 long/short）。
    """
    settlement = result.get("settlement") or {}
    entry = float(result.get("entry_price") or 0.0)
    if not settlement or entry <= 0:
        return []
    if str(result.get("action") or "") not in ("buy", "sell"):
        return []
    dates = kline["日期"].astype(str).str[:10]
    start = str(result.get("decision_date", ""))[:10]
    end = str(settlement.get("settle_date", ""))[:10]
    window = kline[(dates > start) & (dates <= end)]
    if window.empty:
        return []
    closes = [entry, *window["收盘"].astype(float).tolist()]
    if settlement.get("settle_price") is not None:
        closes[-1] = float(settlement["settle_price"])
    sign = 1.0 if result.get("action") == "buy" else -1.0
    return [sign * (closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]


def _baseline_window_returns(
    kline: pd.DataFrame, strat: Strategy, start: str, end: str
) -> list[float]:
    """基线在系统持有窗口 (start, end] 内的日收益序列（horizon 对齐）。

    原实现基线用全窗口（1500 日）与系统单笔持有期并列，两个 Sharpe 口径不可比
    （record doc 2026-09-02：基线与系统 horizon 不可比）。此处把基线限定到
    与系统相同的持有窗口：信号取窗口前一日（T-1 生成 T 生效），收益只取窗口内。
    """
    dates = kline["日期"].astype(str).str[:10]
    hist = kline[dates <= end].reset_index(drop=True)
    hist_dates = hist["日期"].astype(str).str[:10].reset_index(drop=True)
    positions = baseline_positions(hist, strat)
    ret = strategy_returns(hist, positions)  # ret[j-1] 对应 hist 行 j
    out: list[float] = []
    for j in range(1, len(hist_dates)):
        if start < hist_dates[j] <= end:
            out.append(ret[j - 1])
    return out


def _conclude(sharpe_ci: tuple[float, float] | None) -> str:
    """按超额 Sharpe CI 下结论：CI 含 0 → 无显著差异（spec 措辞约束）。"""
    if sharpe_ci is None:
        return "样本不足，无法判定"
    lo, hi = sharpe_ci
    if lo <= 0 <= hi:
        return "无显著差异"
    if lo > 0:
        return "显著优于基线"
    return "显著劣于基线"


def run_backtest(
    sample: list[dict],
    klines: dict[str, pd.DataFrame],
    benchmark_kline: pd.DataFrame | None = None,
    *,
    repeats: int = 3,
    sanity_note: str | None = None,
    replay_fn: Callable[..., dict] = replay_with_consistency,
) -> dict:
    """一批回放样本 → 聚合绩效报告（纯编排，replay_fn 可注入测试）。"""
    results: list[dict] = []
    for item in sample:
        code, decision_date = item["code"], item["decision_date"]
        outcome = replay_fn(
            code,
            decision_date,
            n=repeats,
            full_kline=klines.get(code),
            full_benchmark=benchmark_kline,
        )
        results.append({**item, **outcome})
    consistent = [r for r in results if r["agreement"] >= CONSISTENCY_FLOOR]
    # spec「一致率报告」：逐只披露方向一致率（含被剔除标的），excluded 为其低一致率子集
    per_symbol: list[dict[str, Any]] = [
        {
            "code": r["code"],
            "regime": r["regime"],
            "agreement": r["agreement"],
            "actions": r["actions"],
        }
        for r in results
    ]
    excluded = [p for p in per_symbol if p["agreement"] < CONSISTENCY_FLOOR]
    system_returns: list[float] = []
    baseline_returns: dict[Strategy, list[float]] = {s: [] for s in BASELINE_STRATEGIES}
    regime_returns: dict[str, list[float]] = {}
    excluded_non_executable = 0
    for r in consistent:
        kline = klines.get(r["code"])
        if kline is None:
            continue
        # §1.8②：主结论仅基于可执行决策（buy/sell → long/short）；hold/watch（neutral，
        # 回避语义）整条排除——system 与基线两侧同除，保持两条序列对齐。
        if str(r.get("action") or "") not in ("buy", "sell"):
            excluded_non_executable += 1
            continue
        trade_returns = _trade_daily_returns(r, kline)
        system_returns.extend(trade_returns)
        regime_returns.setdefault(r["regime"], []).extend(trade_returns)
        # horizon 对齐：基线只算系统持有窗口 (decision_date, settle_date] 内的收益，
        # 不再用全窗口（避免 1500 日基线 vs 单笔持有期并列误导）
        start = str(r.get("decision_date", ""))[:10]
        end = str((r.get("settlement") or {}).get("settle_date", ""))[:10]
        for strat in baseline_returns:
            baseline_returns[strat].extend(_baseline_window_returns(kline, strat, start, end))
    system_perf = perf_metrics(system_returns)
    sanity = validate_sanity(system_perf["Sharpe"], sanity_note)

    table: dict[str, Any] = {"system": system_perf}
    for strat, rets in baseline_returns.items():
        table[strat] = perf_metrics(rets)
    best_baseline = max(
        BASELINE_STRATEGIES,
        key=lambda s: table[s]["Sharpe"],
    )
    base_returns = baseline_returns[best_baseline]
    sharpe_ci: tuple[float, float] | None = None
    ci_truncation: dict[str, int] | None = None
    if system_returns and base_returns:
        # 两条序列按前 min 长度截齐（起点对齐：均为样本期首日起的日收益序列）；
        # 截断长度记录在 methodology.ci_truncation，不静默截断。
        m = min(len(system_returns), len(base_returns))
        sharpe_ci = paired_block_bootstrap_diff(
            system_returns[:m], base_returns[:m], B=1_000, seed=42
        )
        ci_truncation = {
            "system_len": len(system_returns),
            "baseline_len": len(base_returns),
            "used": m,
        }
    by_regime: dict[str, Any] = {
        regime: perf_metrics(rets) for regime, rets in regime_returns.items()
    }

    conclusion = _conclude(sharpe_ci)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_sample": len(sample),
        "n_consistent": len(consistent),
        "consistency": {
            "mean_agreement": (
                round(sum(r["agreement"] for r in results) / len(results), 4) if results else None
            ),
            "per_symbol": per_symbol,
            "excluded_low_consistency": excluded,
        },
        "perf_table": table,
        "best_baseline": best_baseline,
        "sharpe_excess_ci": (
            (round(sharpe_ci[0], 4), round(sharpe_ci[1], 4)) if sharpe_ci else None
        ),
        "conclusion": conclusion
        if sanity == "valid"
        else "invalid: Sharpe>3 未附 sanity check 说明",
        "sanity": sanity,
        "block_length_sensitivity": (
            block_length_sensitivity(system_returns, B=1_000, seed=42) if system_returns else None
        ),
        "perf_by_regime": by_regime,
        "methodology": {
            "entry": (
                "结算语义与生产 track-record 判定同源（horizon/超额/±2% 带；入场价由行情派生）"
            ),
            "daily_returns": "单笔结算收益摊到持有期逐日；基线为 T-1 信号 T 生效的逐日仓位收益",
            "system_population": (
                "system 收益仅计 buy/sell 决策（long/short）；hold/watch（neutral，回避语义）"
                "整条排除、不计方向化收益（§1.8② 主结论仅基于可执行决策），"
                "被排除的已一致标的数见 excluded_non_executable"
            ),
            "excluded_non_executable": excluded_non_executable,
            "benchmark": f"BENCHMARK_CODE 默认 {BENCHMARK_CODE}（沿 decision-outcome 默认，待 ADR 确认）",
            "ci_truncation": ci_truncation,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="decision-backtest 离线回放")
    parser.add_argument("--codes", nargs="+", required=True, help="标的池（分层抽样输入）")
    parser.add_argument("--per-regime", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--sanity-note", default=None)
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    from finance_agent.data.akshare_client import AKShareClient

    client = AKShareClient()
    index_kline = client.fetch_index_kline(BENCHMARK_CODE, days=1500)
    sample = stratified_sample(index_kline, args.codes, per_regime=args.per_regime)
    klines = {
        code: client.fetch_kline(code, days=1500, adjust=SETTLEMENT_ADJUST) for code in args.codes
    }
    report = run_backtest(
        sample,
        klines,
        benchmark_kline=index_kline,
        repeats=args.repeats,
        sanity_note=args.sanity_note,
    )
    out_dir = Path("reports/backtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"backtest-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"回测报告已写入 {path}")


if __name__ == "__main__":
    main()
