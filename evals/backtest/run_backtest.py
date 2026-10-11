"""回测编排 CLI（人工触发；LLM 消耗 ≈ 股数 × 3 次回放）。

流程：分层抽样 → 逐样本 replay_with_consistency（n=3）→ 结算收益序列 →
绩效四指标 + 基线对照 + block bootstrap Sharpe CI + 一致性披露 →
批次准入（干净窗口 / 预登记 / 探针）与四段披露 → reports/backtest/<name>.json。
一致率 < 2/3 的标的不进绩效汇总，单独披露。

用法：
    uv run python -m evals.backtest.run_backtest --codes 600519 000858 \
        --per-regime 10 --repeats 3 [--sanity-note "..."] \
        [--batch-kind pathway|formal] [--as-of 2026-09-23] [--probe]
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from evals.backtest.baselines import Strategy, baseline_positions, strategy_returns
from evals.backtest.leakage_probe import run_leakage_probe
from evals.backtest.performance import perf_metrics
from evals.backtest.replay import replay_with_consistency
from evals.backtest.report import (
    ALL_REGIMES,
    aggregate_probes,
    assert_clean_window,
    build_conclusion,
    build_disclosures,
    render_backtest_report_md,
    resolve_positioning,
)
from evals.backtest.sampling import stratified_sample
from evals.backtest.significance import block_length_sensitivity, validate_sanity
from evals.causal_ablation.preregister import (
    OUTCOME_REQUIRED_FIELDS,
    MissingPreregistrationError,
    assert_preregistered,
)
from evals.stats import paired_block_bootstrap_diff
from finance_agent.data.akshare_client import SETTLEMENT_ADJUST
from finance_agent.outcome.track_record.job import BENCHMARK_CODE

logger = logging.getLogger(__name__)

CONSISTENCY_FLOOR = 2 / 3
BASELINE_STRATEGIES: tuple[Strategy, ...] = ("buy_hold", "macd", "kdj", "rsi")
BATCH_KINDS: tuple[str, ...] = ("pathway", "formal")
PREREGISTER_DIR = Path("evals/ablation/preregister")
# 目录多实验共用：门禁必须读本实验自己的预登记文档（见 preregister.find_latest_preregister）
PREREGISTER_NAME_CONTAINS = "outcome"
# JSON 报告落盘目录（gitignored）；md 报告落 evals/backtest/results（入库）
JSON_REPORT_DIR = Path("reports/backtest")
MD_REPORT_DIR = Path("evals/backtest/results")


def _trade_daily_returns(result: dict, kline: pd.DataFrame) -> list[float]:
    """单笔回放结果（Task 9 replay 返回：settlement + entry_price + action +
    decision_date）→ 持有期日收益序列（entry=决策日收盘，exit=结算价）。

    近似：持有期内按逐日收盘 pct_change，末日修正到结算价；
    方法在报告 metadata.methodology 披露。方向符号：buy 为正、sell 为负；
    非 buy/sell（hold/watch→neutral，属回避语义）不产生方向化 system 收益——本节返回 []
    且由编排层整条排除（§1.9② 主结论仅基于可执行决策 long/short）。
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
    batch_kind: str = "pathway",
    as_of: str | None = None,
    window_days: int = 20,
    probe: dict[str, Any] | None = None,
    preregistration: Any = None,
    preregister_dir: Path = PREREGISTER_DIR,
    clean_window: dict[str, Any] | None = None,
) -> dict:
    """一批回放样本 → 聚合绩效报告（纯编排，replay_fn 可注入测试）。

    批次准入（delta add-backtest-leakage-controls）：
    - `formal` 批必须持有效预登记（`evals/ablation/preregister/`，`name_contains="outcome"`）
      且必须提供探针读数；探针不可测（`direction_hit_rate is None`）→ 结论按通路验证处理。
    - 干净窗口（决策日距 as_of ≥ window_days 交易日）未过 → 结论降为通路验证，剥离 skill 句。
    - `pathway` 批（默认）结论恒标「通路验证」，不产 skill 结论句。
    """
    if batch_kind not in BATCH_KINDS:
        raise ValueError(f"未知批次类型 {batch_kind!r}（可选 {BATCH_KINDS}）")
    if batch_kind == "formal":
        if preregistration is None:
            preregistration = assert_preregistered(
                preregister_dir,
                name_contains=PREREGISTER_NAME_CONTAINS,
                required_fields=OUTCOME_REQUIRED_FIELDS,
            )
        # D1（Task 3 审查）：库层注入的预登记也须校验有效性——只判 None 会让调用方
        # 传入 valid=False 的预登记绕过 formal 身份门禁。
        if not getattr(preregistration, "valid", False):
            issues = "; ".join(getattr(preregistration, "issues", None) or ["未提供校验细节"])
            raise MissingPreregistrationError(
                f"预登记无效（{getattr(preregistration, 'path', '<unknown>')}）：{issues}"
            )
        if probe is None:
            raise ValueError("formal 批必须提供泄漏探针读数（探针不可测时按通路验证处理）")
    decision_dates = [str(item.get("decision_date", ""))[:10] for item in sample]
    if clean_window is None and (as_of is not None or batch_kind == "formal"):
        clean_window = assert_clean_window(
            decision_dates,
            as_of=as_of or datetime.now().strftime("%Y-%m-%d"),
            window_days=window_days,
            benchmark=benchmark_kline,
        )
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
        # §1.9②：主结论仅基于可执行决策（buy/sell → long/short）；hold/watch（neutral，
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

    regime_covered = sorted({str(item.get("regime")) for item in sample if item.get("regime")})
    regime_limited = set(regime_covered) != set(ALL_REGIMES)
    disclosures = build_disclosures(
        preregistration=preregistration,
        clean_window=clean_window,
        probe=probe,
        regime_covered=regime_covered,
        regime_limited=regime_limited,
        batch_kind=batch_kind,
    )
    # 深历史执法（issue #172）：批内任一决策日早于切点 → 定位降 pathway
    decision_dates = [str(item.get("decision_date", ""))[:10] for item in sample]
    positioning = resolve_positioning(
        batch_kind=batch_kind, clean_window=clean_window, probe=probe, decision_dates=decision_dates
    )
    conclusion = build_conclusion(
        _conclude(sharpe_ci),
        batch_kind=batch_kind,
        clean_window=clean_window,
        probe=probe,
        sanity=sanity,
        regime_covered=regime_covered,
        regime_limited=regime_limited,
        decision_dates=decision_dates,
    )
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
        "conclusion": conclusion,
        "positioning": positioning,
        "sanity": sanity,
        "block_length_sensitivity": (
            block_length_sensitivity(system_returns, B=1_000, seed=42) if system_returns else None
        ),
        "perf_by_regime": by_regime,
        # 四段披露 + batch_kind（spec「历史离线回放」「知识泄漏探针」「分层市场状态抽样」
        # 「回测批次预登记与报告生命周期」）
        **disclosures,
        "methodology": {
            "entry": (
                "结算语义与生产 track-record 判定同源（horizon/超额/±2% 带；入场价由行情派生）"
            ),
            "daily_returns": "单笔结算收益摊到持有期逐日；基线为 T-1 信号 T 生效的逐日仓位收益",
            "adjust": (
                "结算/回测区间收益走后复权（hfq，SETTLEMENT_ADJUST，Δ4 Task 1 统一）；"
                "指数无复权概念，基准取原始点位"
            ),
            "system_population": (
                "system 收益仅计 buy/sell 决策（long/short）；hold/watch（neutral，回避语义）"
                "整条排除、不计方向化收益（§1.9② 主结论仅基于可执行决策），"
                "被排除的已一致标的数见 excluded_non_executable"
            ),
            "excluded_non_executable": excluded_non_executable,
            "benchmark": f"BENCHMARK_CODE 默认 {BENCHMARK_CODE}（沿 decision-outcome 默认，待 ADR 确认）",
            "probe_coverage": (
                "批内决策日可能不唯一（stratified_sample 每 regime 一个）；探针对每个 distinct "
                "决策日各执行一次并取最差态汇总（任一超阈→downgraded；任一不可测→unmeasurable），"
                "覆盖窗口见 leakage_probe.probe_window——单窗口批即该窗口，仅覆盖该窗口"
            ),
            "ci_truncation": ci_truncation,
            "batch_kind": (
                "pathway = 通路验证（不产 skill 结论句）；formal = 正式批"
                "（须有效预登记 + 干净窗口 + 探针披露）"
            ),
        },
    }


def run_batch_probe(
    codes: list[str],
    decision_dates: list[str],
    *,
    window_days: int = 20,
    client: Any = None,
) -> dict[str, Any] | None:
    """批级泄漏探针（D2）：对批内每个 distinct 决策日各探一次 → 最差态汇总。

    只探首个决策日会低估其余窗口的泄漏风险；本函数覆盖全部 distinct 窗口
    （`run_leakage_probe` 逐窗口调用，成本 = 窗口数 × 探针题量，相对回放可忽略），
    由 `aggregate_probes` 汇总（任一超阈→downgraded；任一不可测→unmeasurable）。
    无决策日 → None（不探、不冒充读数）。
    """
    windows = sorted({str(d)[:10] for d in decision_dates if str(d).strip()})
    if not windows:
        return None
    probes: list[dict[str, Any] | None] = []
    for window in windows:
        result = run_leakage_probe(codes, window, window_days=window_days, client=client)
        # 审查②：每项带上**它自己**的窗口，供 aggregate_probes 的 per_window 逐窗口披露。
        if result is not None:
            result = {**result, "probe_window": [window]}
        probes.append(result)
    return aggregate_probes(probes, windows=windows)


def main() -> None:
    parser = argparse.ArgumentParser(description="decision-backtest 离线回放")
    parser.add_argument("--codes", nargs="+", required=True, help="标的池（分层抽样输入）")
    parser.add_argument("--per-regime", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--sanity-note", default=None)
    parser.add_argument(
        "--batch-kind",
        choices=BATCH_KINDS,
        default="pathway",
        help="pathway=通路验证（默认，不产 skill 结论句）；formal=正式批（须有效预登记）",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="跑批日（干净窗口判定基准，ISO YYYY-MM-DD；默认今天）",
    )
    parser.add_argument(
        "--probe",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="是否执行泄漏探针（formal 批强制开启；--no-probe 在 formal 下被忽略）",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="报告名（md 文件名主干；默认 <batch_kind>-<时间戳>）",
    )
    parser.add_argument(
        "--out-dir",
        default=str(MD_REPORT_DIR),
        help=f"md 报告落盘目录（默认 {MD_REPORT_DIR}；JSON 恒落 {JSON_REPORT_DIR}）",
    )
    parser.add_argument(
        "--min-decision-date",
        default=None,
        help="决策日下限（ISO YYYY-MM-DD；早于该日的 regime 窗口跳过，凑不齐三 regime 抛错）",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    from finance_agent.data.akshare_client import AKShareClient

    as_of = args.as_of or datetime.now().strftime("%Y-%m-%d")
    # D4（Task 3 审查）：formal 批强制探针，显式 --no-probe 被忽略（打日志，不静默）。
    if args.batch_kind == "formal" and args.probe is False:
        logger.warning("formal 批强制探针，已忽略 --no-probe（formal 不得以未测泄漏风险下结论）")
    # formal 批前置门禁：无有效预登记拒绝正式批身份（可降级 --batch-kind pathway）
    if args.batch_kind == "formal":
        assert_preregistered(
            PREREGISTER_DIR,
            name_contains=PREREGISTER_NAME_CONTAINS,
            required_fields=OUTCOME_REQUIRED_FIELDS,
        )

    client = AKShareClient()
    index_kline = client.fetch_index_kline(BENCHMARK_CODE, days=1500)
    sample = stratified_sample(
        index_kline,
        args.codes,
        per_regime=args.per_regime,
        min_decision_date=args.min_decision_date,
    )
    klines = {
        code: client.fetch_kline(code, days=1500, adjust=SETTLEMENT_ADJUST) for code in args.codes
    }
    use_probe = args.batch_kind == "formal" or bool(args.probe)
    probe: dict[str, Any] | None = None
    if use_probe and sample:
        # D2：对批内每个 distinct 决策日各探一次（最差态汇总），不再只探首个窗口。
        probe = run_batch_probe(
            args.codes,
            [str(item.get("decision_date", "")) for item in sample],
            client=client,
        )
    report = run_backtest(
        sample,
        klines,
        benchmark_kline=index_kline,
        repeats=args.repeats,
        sanity_note=args.sanity_note,
        batch_kind=args.batch_kind,
        as_of=as_of,
        probe=probe,
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = args.name or f"{args.batch_kind}-{stamp}"
    json_dir = JSON_REPORT_DIR
    json_dir.mkdir(parents=True, exist_ok=True)
    json_path = json_dir / f"backtest-{stamp}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_dir = Path(args.out_dir)
    md_dir.mkdir(parents=True, exist_ok=True)
    md_path = md_dir / f"{name}.md"
    md_path.write_text(render_backtest_report_md(report, name=name), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"回测报告已写入 {json_path}（JSON）与 {md_path}（md）")


if __name__ == "__main__":
    main()
