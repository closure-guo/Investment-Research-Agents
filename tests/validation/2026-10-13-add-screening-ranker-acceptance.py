"""add-screening-ranker Task 3 对照验收脚本：3 交易日 × Top-100 抽 10 只/日 手算核对。

用法::

    uv run python tests/validation/2026-10-13-add-screening-ranker-acceptance.py
    uv run python tests/validation/2026-10-13-add-screening-ranker-acceptance.py --out <路径.json>

**验收目标（对被测实现零依赖的算术对照）**：L2 打分链路的每一步都在本脚本内用
**独立手写算术**复算——① 从因子库直接读 IC 序列（``list_ic_series``）手写 IC 均值；
② 直接从面板求值引擎取因子值，手写 ddof=1 截面 z-score；③ 手写权重
``ic_mean/Σ|ic_mean|`` 与加权和；④ 与 ``rank_universe`` 产出的
``factor_contribs`` / ``composite_score`` 逐项对照（容差 1e-9，spec 勾稽容差）。
**不经 ranker 模块的内部函数**（仅把脚本手算的 ic_stats 作为接口输入传入，
以及把 ``recent_ic_stats`` 的输出与手算值交叉核对）。

**流程**（固定种子、零网络、零 LLM、无随机抽查——抽样位置固定）：

1. 合成面板 170 交易日 × 120 标的：次日收益显式依赖五个 builtin 因子当日横截面
   zscore（``r_{t+1} = Σ_k β_k z_k,t + σ·θ_i·ε_t``，自满足构造 → 因子有真实预测
   成分）；``θ_i`` 为持久异质波动（供 ``volatility_20`` 有横截面结构）；
2. ``register_builtins``（免提案、同五道闸）→ 记录逐条准入结论；
3. 模拟日批**无前视**：升序遍历监控日，每次用截断到 ≤ as_of 的面板调
   ``run_factor_monitor``（只记录 ≤ as_of−1 的 IC）——rank 日 D 只消费 D−1 及更早
   的 IC，杜绝前视；
4. 对 3 个 rank 日（index −40 / −20 / −1）各：脚本手算 ic_stats → ``rank_universe``
   （Top-100，passed_codes = 全面板标的；L1 硬过滤由 add-screening-filters 单独
   验收，本脚本聚焦 L2）；
5. 每日按 rank 等比间隔抽 10 只：逐项对照 zscore/weight/contribution/
   composite_score（≤1e-9）；并对当日全部 100 条候选核对勾稽
   ``Σ contribution == composite_score``（≤1e-9）；
6. 落 JSON artifact + stdout 报告；退出码 0 当且仅当 30/30 手算一致。

**诚实注记（live 数据不可得）**：spec/proposal 要求对 2026-09 每月首交易日产出
Top-100 并抽 10 只核对，但本机东财行情域 IP 封禁持续生效（incident 033 / issue
#325），真实 2026-09 快照数据不可得 → 以**合成面板 + 独立手算**替代；本验收证明
的是「打分/排序/勾稽机制正确」，**不构成真实市场有效性的证据**。
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE_ROOT / "src"))

from finance_agent.factors.builtin import BUILTIN_FACTORS, register_builtins  # noqa: E402
from finance_agent.factors.dsl import parse  # noqa: E402
from finance_agent.factors.eval_engine import (  # noqa: E402
    MarketPanel,
    cross_section_zscore,
    evaluate,
    forward_returns,
    rank_ic_series,
)
from finance_agent.factors.library import list_active_factors, list_ic_series  # noqa: E402
from finance_agent.factors.monitor import run_factor_monitor  # noqa: E402
from finance_agent.factors.validate import _ic_stats  # noqa: E402
from finance_agent.screening.ranker import rank_universe, recent_ic_stats  # noqa: E402

# 面板与因子载荷（固定 → 逐位可复现；载荷选取见验收报告 §1）
PANEL_SEED = 20261013
PANEL_DATES = 170
PANEL_CODES = 120
RETURN_NOISE = 0.03
VOL_HETEROGENEITY = 0.3
WARMUP_DAYS = 22
BETAS: dict[str, float] = {
    "momentum_20": 0.006,
    "reversal_5": 0.009,
    "turnover_proxy": 0.006,
    "volatility_20": 0.006,
    "size_proxy": 0.006,
}

# 验收参数：3 个 rank 日（相对面板末日的倒数偏移）、监控窗口 100 个 as_of 调用、
# 抽样位置固定（升序 rank 等比间隔 10 个）、勾稽容差（spec 明文 1e-9）
RANK_DAY_OFFSETS: tuple[int, ...] = (40, 20, 1)
MONITOR_DAYS = 100
IC_WINDOW = 60
TOP_N = 100
SAMPLES_PER_DAY = 10
ACCEPT_TOLERANCE = 1e-9

OUT_JSON = Path(__file__).resolve().with_name("2026-10-13-add-screening-ranker-acceptance.json")


# ── 合成面板（含真实预测结构；与 tests/factors/test_builtin.py 同构） ───────


def build_panel() -> MarketPanel:
    """迭代生成面板：次日收益显式依赖五 builtin 因子当日 zscore + 异质波动噪声。"""
    rng = np.random.default_rng(PANEL_SEED)
    width = PANEL_CODES
    close = np.full((PANEL_DATES, width), np.nan)
    volume = np.full((PANEL_DATES, width), np.nan)
    amount = np.full((PANEL_DATES, width), np.nan)
    close[0] = 100.0
    shape = np.zeros(width)  # 成交量 AR(1) 状态
    base_vol = np.exp(VOL_HETEROGENEITY * rng.normal(size=width))
    labels = [
        (date(2026, 1, 5) + timedelta(days=offset)).isoformat() for offset in range(PANEL_DATES)
    ]
    codes = [f"c{index:03d}" for index in range(width)]
    expressions = {item.name: item.expression for item in BUILTIN_FACTORS}

    def frame(values: np.ndarray, upto: int) -> pd.DataFrame:
        return pd.DataFrame(values[:upto], index=labels[:upto], columns=codes)

    for step in range(1, PANEL_DATES):
        shape = 0.5 * shape + rng.normal(size=width)
        volume[step - 1] = np.exp(0.4 * shape)
        amount[step - 1] = 1e9 * np.exp(0.5 * rng.normal(size=width))
        partial = MarketPanel(
            fields={
                "open": frame(close, step),
                "high": frame(close, step),
                "low": frame(close, step),
                "close": frame(close, step),
                "volume": frame(volume, step),
                "amount": frame(amount, step),
            }
        )
        signal = np.zeros(width)
        if step >= WARMUP_DAYS:
            for name, beta in BETAS.items():
                zscores = (
                    cross_section_zscore(evaluate(parse(expressions[name]), partial))
                    .iloc[-1]
                    .fillna(0.0)
                    .to_numpy()
                )
                signal += beta * zscores
        close[step] = close[step - 1] * (
            1.0 + signal + RETURN_NOISE * base_vol * rng.normal(size=width)
        )
    volume[PANEL_DATES - 1] = np.exp(0.4 * shape)
    amount[PANEL_DATES - 1] = 1e9 * np.exp(0.5 * rng.normal(size=width))
    return MarketPanel(
        fields={
            "open": frame(close, PANEL_DATES),
            "high": frame(close, PANEL_DATES),
            "low": frame(close, PANEL_DATES),
            "close": frame(close, PANEL_DATES),
            "volume": frame(volume, PANEL_DATES),
            "amount": frame(amount, PANEL_DATES),
        }
    )


def _builtin_names() -> list[str]:
    """builtin 目录名称（报告展示用）。"""
    return [item.name for item in BUILTIN_FACTORS]


# ── 独立手算：IC 统计 / 权重 / 截面 z-score / 综合分 ────────────────────────


def _hand_ic_stats(rows: Sequence[dict[str, Any]], db_path: str) -> dict[int, tuple[float, float]]:
    """手写 IC 统计：``list_ic_series`` 最近 60 条 → (均值, ICIR)，常量序列 ICIR 记 0。"""
    stats: dict[int, tuple[float, float]] = {}
    for row in rows:
        factor_id = int(row["id"])
        values = [
            float(value) for _, value in list_ic_series(factor_id, limit=IC_WINDOW, db_path=db_path)
        ]
        count = len(values)
        if count == 0:
            stats[factor_id] = (0.0, 0.0)
            continue
        mean = math.fsum(values) / count
        icir = 0.0
        if count >= 2:
            variance = math.fsum([(value - mean) ** 2 for value in values]) / (count - 1)
            std = math.sqrt(variance)
            if std != 0.0:
                icir = mean / std
        stats[factor_id] = (mean, icir)
    return stats


def _hand_weights(ic_stats: dict[int, tuple[float, float]]) -> dict[int, float]:
    """手写权重：``ic_mean_i / Σ_j |ic_mean_j|``（无常量复制，公式直译）。"""
    total = math.fsum([abs(mean) for mean, _icir in ic_stats.values()])
    if total < 1e-12:
        raise RuntimeError("手算权重失败：Σ|ic_mean| < 1e-12（面板结构异常）")
    return {factor_id: mean / total for factor_id, (mean, _icir) in ic_stats.items()}


def _hand_zscores(values: dict[str, float]) -> dict[str, float]:
    """手写截面 z-score：ddof=1 教科书公式；非有限 → 0；有效样本<2/std=0 → 全 0。"""
    finite = {code: value for code, value in values.items() if math.isfinite(value)}
    if len(finite) < 2:
        return dict.fromkeys(values, 0.0)
    count = len(finite)
    mean = math.fsum(finite.values()) / count
    variance = math.fsum([(value - mean) ** 2 for value in finite.values()]) / (count - 1)
    std = math.sqrt(variance)
    if std == 0.0:
        return dict.fromkeys(values, 0.0)
    return {
        code: 0.0 if not math.isfinite(value) else (value - mean) / std
        for code, value in values.items()
    }


def _slice_panel(panel: MarketPanel, as_of: str) -> MarketPanel:
    """截断面板至 ≤ ``as_of``（模拟当日可得数据，杜绝前视）。"""
    return MarketPanel(fields={name: frame.loc[:as_of] for name, frame in panel.fields.items()})


def _max_abs_diff(
    left: dict[int, tuple[float, float]], right: dict[int, tuple[float, float]]
) -> float:
    """两个 ``{id: (float, float)}`` 的逐项最大绝对差。"""
    return max(
        abs(a - b)
        for factor_id in left
        for a, b in zip(left[factor_id], right[factor_id], strict=True)
    )


# ── 验收主流程 ──────────────────────────────────────────────────────────────


def run_acceptance() -> dict[str, Any]:
    """生成面板 → 注册 builtin → 累计 IC → 3 日 Top-100 → 30 只手算逐项对照。"""
    panel = build_panel()
    codes = [str(code) for code in panel.fields["close"].columns]
    dates = [str(day) for day in panel.fields["close"].index]
    db_dir = tempfile.mkdtemp(prefix="ranker_acceptance_")
    db_path = str(Path(db_dir) / "factor_library.db")

    admission_results = register_builtins(panel=panel, seed=PANEL_SEED, db_path=db_path)
    names = _builtin_names()
    admission_rows: list[dict[str, Any]] = []
    for name, result in zip(names, admission_results, strict=True):
        rejected = next((gate for gate in result.gates if not gate.passed), None)
        admission_rows.append(
            {
                "name": name,
                "factor_id": result.factor_id,
                "status": result.status,
                "rejected_gate": result.rejected_gate,
                "reason": None if rejected is None else str(rejected.reason),
                "gates": [
                    {
                        "gate": gate.gate,
                        "passed": gate.passed,
                        "reason": None if gate.reason is None else str(gate.reason),
                        "detail": _jsonable(gate.detail),
                    }
                    for gate in result.gates
                ],
            }
        )
    active_rows = list_active_factors(db_path=db_path)
    if not active_rows:
        raise RuntimeError("验收前提失败：builtin 全部被拒，无 active 因子可打分")

    panel_diagnostics = _panel_diagnostics(panel, active_rows)

    # 模拟日批累计 IC（升序、无前视）：rank 日 D 只消费 ≤ D−1 的 IC
    monitor_calls = 0
    degraded: list[int] = []
    call_days = dates[-MONITOR_DAYS:]
    used_calls: set[str] = set()
    day_reports: list[dict[str, Any]] = []
    for offset in RANK_DAY_OFFSETS:
        rank_day = dates[-offset]
        active_rows = list_active_factors(db_path=db_path)
        for call_day in call_days:
            if call_day > rank_day or call_day in used_calls:
                continue
            outcome = run_factor_monitor(_slice_panel(panel, call_day), call_day, db_path=db_path)
            used_calls.add(call_day)
            monitor_calls += 1
            degraded.extend(outcome["degraded"])
        day_reports.append(_rank_one_day(panel, rank_day, codes, active_rows, db_path))

    sampled = [check for day in day_reports for check in day["checks"]]
    matched = sum(1 for check in sampled if check["matched"])
    reconciliation_max = max(day["reconciliation_max_abs_diff"] for day in day_reports)
    verdict = {
        "sampled_total": len(sampled),
        "sampled_matched": matched,
        "sampled_days": len(day_reports),
        "reconciliation_max_abs_diff": reconciliation_max,
        "active_factor_ids": [int(row["id"]) for row in active_rows],
        "passed": matched == len(sampled) and reconciliation_max <= ACCEPT_TOLERANCE,
    }
    return {
        "panel": {
            "dates": PANEL_DATES,
            "codes": PANEL_CODES,
            "seed": PANEL_SEED,
            "return_noise": RETURN_NOISE,
            "vol_heterogeneity": VOL_HETEROGENEITY,
            "warmup_days": WARMUP_DAYS,
            "signal_betas": BETAS,
            "rank_days": [dates[-offset] for offset in RANK_DAY_OFFSETS],
        },
        "live_data_blocker": (
            "spec 要求的 2026-09 真实快照核对受本机东财行情域 IP 封禁（incident 033 / "
            "issue #325）不可执行；本 artifact 为合成面板 + 独立手算的替代证据，"
            "不构成真实市场有效性证据"
        ),
        "admission": admission_rows,
        "panel_diagnostics": panel_diagnostics,
        "monitor": {"calls": monitor_calls, "degraded": sorted(set(degraded))},
        "rank_days": day_reports,
        "verdict": verdict,
    }


def _rank_one_day(
    panel: MarketPanel,
    rank_day: str,
    codes: list[str],
    active_rows: list[dict[str, Any]],
    db_path: str,
) -> dict[str, Any]:
    """单日：手算 ic_stats → rank_universe → 抽 10 只手算对照 + 全量勾稽。"""
    sliced = _slice_panel(panel, rank_day)
    hand_stats = _hand_ic_stats(active_rows, db_path)
    helper_stats = recent_ic_stats(active_rows, window=IC_WINDOW, db_path=db_path)
    stats_cross_check = _max_abs_diff(helper_stats, hand_stats)
    hand_weights = _hand_weights(hand_stats)

    top = rank_universe(
        panel=sliced,
        as_of=rank_day,
        passed_codes=codes,
        factors=active_rows,
        ic_stats=hand_stats,
        top_n=TOP_N,
    )
    if len(top) != TOP_N:
        raise RuntimeError(f"rank 日 {rank_day} 产出 {len(top)} 条候选（期望 {TOP_N}）")

    reconciliation_max = 0.0
    for candidate in top:
        total = 0.0
        for contrib in candidate.factor_contribs:
            total += contrib.contribution
        reconciliation_max = max(reconciliation_max, abs(total - candidate.composite_score))

    factor_values = {
        int(row["id"]): evaluate(parse(str(row["expression"])), sliced) for row in active_rows
    }
    hand_zscores: dict[int, dict[str, float]] = {
        int(row["id"]): _hand_zscores(
            {code: float(factor_values[int(row["id"])].at[rank_day, code]) for code in codes}
        )
        for row in active_rows
    }

    positions = [
        index * (len(top) - 1) // (SAMPLES_PER_DAY - 1) for index in range(SAMPLES_PER_DAY)
    ]
    checks: list[dict[str, Any]] = []
    for position in positions:
        candidate = top[position]
        expected_composite = 0.0
        contribution_checks = []
        for contrib in candidate.factor_contribs:
            hand_z = hand_zscores[contrib.factor_id][candidate.symbol]
            hand_weight = hand_weights[contrib.factor_id]
            hand_contribution = hand_weight * hand_z
            expected_composite += hand_contribution
            contribution_checks.append(
                {
                    "factor_id": contrib.factor_id,
                    "zscore_ranker": contrib.zscore,
                    "zscore_hand": hand_z,
                    "weight_ranker": contrib.weight,
                    "weight_hand": hand_weight,
                    "contribution_ranker": contrib.contribution,
                    "contribution_hand": hand_contribution,
                }
            )
        diffs = {
            "composite": abs(expected_composite - candidate.composite_score),
            "zscore_max": max(
                abs(c["zscore_ranker"] - c["zscore_hand"]) for c in contribution_checks
            ),
            "weight_max": max(
                abs(c["weight_ranker"] - c["weight_hand"]) for c in contribution_checks
            ),
            "contribution_max": max(
                abs(c["contribution_ranker"] - c["contribution_hand"]) for c in contribution_checks
            ),
        }
        checks.append(
            {
                "rank": candidate.rank,
                "symbol": candidate.symbol,
                "composite_ranker": candidate.composite_score,
                "composite_hand": expected_composite,
                "diffs": diffs,
                "contributions": contribution_checks,
                "matched": max(diffs.values()) <= ACCEPT_TOLERANCE,
            }
        )
    return {
        "rank_day": rank_day,
        "active_ids": [int(row["id"]) for row in active_rows],
        "ic_stats": {
            str(int(row["id"])): {
                "expression": str(row["expression"]),
                "ic_mean": hand_stats[int(row["id"])][0],
                "icir": hand_stats[int(row["id"])][1],
                "weight": hand_weights[int(row["id"])],
            }
            for row in active_rows
        },
        "recent_ic_stats_cross_check_max_abs_diff": stats_cross_check,
        "n_candidates": len(top),
        "rank_1_composite": top[0].composite_score,
        "rank_100_composite": top[-1].composite_score,
        "reconciliation_max_abs_diff": reconciliation_max,
        "checks": checks,
    }


def _panel_diagnostics(panel: MarketPanel, active_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """面板证据：active 因子的全样本 IC 均值/ICIR 与两两 IC 序列相关（闸④冗余参照）。"""
    fwd = forward_returns(panel.fields["close"])
    series: dict[int, pd.Series] = {}
    stats: dict[str, Any] = {}
    for row in active_rows:
        factor_id = int(row["id"])
        series[factor_id] = rank_ic_series(evaluate(parse(str(row["expression"])), panel), fwd)
        mean, _std, icir, samples = _ic_stats(series[factor_id])
        stats[str(factor_id)] = {
            "expression": str(row["expression"]),
            "ic_mean": None if math.isnan(mean) else round(mean, 6),
            "icir": round(icir, 6),
            "ic_days": samples,
        }
    ids = [int(row["id"]) for row in active_rows]
    correlations: dict[str, float] = {}
    for left_index, left_id in enumerate(ids):
        for right_id in ids[left_index + 1 :]:
            pair = pd.concat(
                [series[left_id].rename("l"), series[right_id].rename("r")], axis=1
            ).dropna()
            correlations[f"{left_id}~{right_id}"] = round(float(pair["l"].corr(pair["r"])), 6)
    return {"factors": stats, "ic_correlations": correlations}


def _jsonable(value: object) -> object:
    """detail 值 → JSON 友好（float 舍入；numpy 标量转 Python 标量）。"""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float):
        return None if not math.isfinite(value) else round(value, 6)
    return value


def _out_path() -> Path:
    if "--out" in sys.argv:
        index = sys.argv.index("--out")
        if index + 1 < len(sys.argv):
            return Path(sys.argv[index + 1])
    return OUT_JSON


def main() -> int:
    summary = run_acceptance()
    out_path = _out_path()
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"面板: {summary['panel']}")
    print(f"live 数据受阻声明: {summary['live_data_blocker']}")
    print("\n[builtin 准入结论] 名称 / 状态 / 被拒闸口 / 原因码")
    for row in summary["admission"]:
        print(
            f"  {row['name']:<15} {row['status']:<8} gate={row['rejected_gate']}"
            f" reason={row['reason']} factor_id={row['factor_id']}"
        )
    print(
        f"\n[监控日批] calls={summary['monitor']['calls']} degraded={summary['monitor']['degraded']}"
    )
    for day in summary["rank_days"]:
        print(
            f"\n[rank 日 {day['rank_day']}] 候选 {day['n_candidates']} 条"
            f"（rank1={day['rank_1_composite']:+.4f} / rank100={day['rank_100_composite']:+.4f}）"
        )
        print(
            "  手算 ic_stats（IC 均值 / ICIR / 权重）与 recent_ic_stats 交叉核对差: "
            f"{day['recent_ic_stats_cross_check_max_abs_diff']:.3e}"
        )
        for factor_id, stats in day["ic_stats"].items():
            print(
                f"    id={factor_id:<3} {stats['expression']:<30}"
                f" ic_mean={stats['ic_mean']:+.6f} icir={stats['icir']:+.4f}"
                f" weight={stats['weight']:+.6f}"
            )
        print("  抽样手算对照（rank / symbol / ranker 综合分 / 手算综合分 / 最大逐项差）:")
        for check in day["checks"]:
            worst = max(check["diffs"].values())
            print(
                f"    #{check['rank']:<3} {check['symbol']:<5} {check['composite_ranker']:+.12f}"
                f" {check['composite_hand']:+.12f} max_diff={worst:.3e}"
                f" {'OK' if check['matched'] else 'MISMATCH'}"
            )
        print(
            f"  全量勾稽（Σcontribution == composite）最大差:"
            f" {day['reconciliation_max_abs_diff']:.3e}"
        )
    print(f"\n[verdict] {summary['verdict']}")
    print(f"[artifact] {out_path}")
    return 0 if summary["verdict"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
