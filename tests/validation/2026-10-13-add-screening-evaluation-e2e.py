"""add-screening-evaluation Task 4：合成数据端到端（30 池日 → 全链路 → 样本报告）。

用途：以**合成历史**（固定种子，75 个交易日 × 12 只标的）驱动本 change 全链路——
候选池前向跟踪（5/10/20 日）→ 消融三变体（因子打分 / 固定种子随机 / 等权指数）→
±L3 粗筛（keep/drop）→ 因子来源分层（builtin vs LLM 挖掘）→ 报告渲染，产出：

- JSON artifact：``tests/validation/2026-10-13-add-screening-evaluation-e2e.json``；
- 样本报告：``docs/evals/2026-10-13-screening-forward-sample.md``（**合成数据，明示**）。

边界（如实记录，不冒充真实读数）：真实数据报告与「每 20 交易日」live cadence 为后续项，
受快照链路阻塞（issue #325：实网数据源 IP 封禁未解除；见验证报告 §后续项）。
本脚本零网络、零 LLM，确定性可复现（同种子两次运行产物逐位一致，除文件路径外无壁钟依赖）。

用法::

    uv run python tests/validation/2026-10-13-add-screening-evaluation-e2e.py

退出码：0 = 全链路产出通过；1 = 存在空组/缺证据。
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from evals.screening import ablation, l3, stratify  # noqa: E402
from evals.screening import forward as fwd  # noqa: E402
from evals.screening import report as screening_report  # noqa: E402
from finance_agent.execution.config import default_matching_config  # noqa: E402
from finance_agent.execution.matching import REASON_LIMIT_UP_UNFILLABLE  # noqa: E402
from finance_agent.factors.eval_engine import MarketPanel  # noqa: E402
from finance_agent.factors.library import add_factor, list_active_factors, record_ic  # noqa: E402
from finance_agent.screening.candidate_llm import TriageDecision  # noqa: E402
from finance_agent.screening.ranker import rank_universe, recent_ic_stats  # noqa: E402
from finance_agent.screening.store import (  # noqa: E402
    init_screening_db,
    record_l2_candidates,
    record_screening_batch,
    record_triage_decisions,
)

# ── 合成历史参数（固定种子；全部确定性） ────────────────────────────────────
SEED = 20261013
SYMBOLS: tuple[str, ...] = tuple(f"6000{index:02d}" for index in range(1, 13))
N_TRADING_DAYS = 75
N_POOL_DATES = 30
POOL_START_INDEX = 10
L2_TOP_N = 10  # 合成 L2 榜（12 只中取 10）
ARM_TOP_N = 6  # 消融/分层的 Top-N 臂
HORIZONS = (5, 10, 20)
ABLATION_HORIZON = 20
# 合成 L3 规则：剔除榜末 2 只（keep 8 / drop 2）；island 用**固定规则**而非 LLM（零 token）
L3_KEEP_RANKS = 8

JSON_OUT = Path(__file__).with_suffix(".json")
REPORT_OUT = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "evals"
    / "2026-10-13-screening-forward-sample.md"
)

# 因子库（合成）：2 个 builtin 动量因子 + 2 个 LLM 挖掘因子（含反转动量）
BUILTIN_FACTORS: tuple[tuple[str, float], ...] = (
    ("delta(close,5)", 0.06),
    ("delta(close,10)", 0.05),
)
LLM_FACTORS: tuple[tuple[str, float], ...] = (
    ("ts_rank(close,5)", 0.03),
    ("sub(0, delta(close,3))", 0.02),
)


def _trading_days(count: int) -> list[str]:
    """工作日序列（合成交易日轴；起始 2026-01-05，跳过周末，不含节假日）。"""
    days: list[str] = []
    current = date(2026, 1, 5)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _build_panel(dates: list[str]) -> MarketPanel:
    """合成面板：**逐标的持久漂移**（正控制：动量因子应可捕捉）+ 弱 AR(1) + 随机成交额。

    漂移 spread ±0.2%/日（20 日约 ±8%）高于 1% 日噪声的可检出下限——样本报告用于
    演示「链路能检出被植入的信号」，非市场结论（合成数据声明随报告渲染）。
    """
    # 固定种子合成历史（可复现性优先，非密码学用途）
    rng = random.Random(SEED)  # noqa: S311
    closes: dict[str, list[float]] = {}
    amounts: dict[str, list[float]] = {}
    for symbol in SYMBOLS:
        price = rng.uniform(8.0, 60.0)
        drift = rng.uniform(-0.002, 0.003)
        amount_base = rng.uniform(2e7, 8e8)
        prices = [round(price, 2)]
        previous = 0.0
        for _ in range(len(dates) - 1):
            ret = drift + 0.15 * previous + rng.gauss(0.0, 0.010)  # 持久漂移 + 弱 AR(1)
            ret = max(-0.095, min(0.095, ret))
            previous = ret
            price = round(price * (1.0 + ret), 2)
            prices.append(price)
        closes[symbol] = prices
        amounts[symbol] = [round(amount_base * (0.8 + 0.4 * rng.random()), 0) for _ in dates]
    close_frame = pd.DataFrame(closes, index=dates).sort_index().sort_index(axis=1)
    previous_close = close_frame.shift(1)
    open_frame = (previous_close + (close_frame - previous_close) * 0.3).round(2)
    open_frame = open_frame.where(previous_close.notna(), close_frame)
    high_frame = pd.DataFrame(
        np.maximum(open_frame.to_numpy(), close_frame.to_numpy()) * 1.004,
        index=close_frame.index,
        columns=close_frame.columns,
    ).round(2)
    low_frame = pd.DataFrame(
        np.minimum(open_frame.to_numpy(), close_frame.to_numpy()) * 0.996,
        index=close_frame.index,
        columns=close_frame.columns,
    ).round(2)
    return MarketPanel(
        fields={
            "open": open_frame,
            "high": high_frame,
            "low": low_frame,
            "close": close_frame,
            "amount": pd.DataFrame(amounts, index=dates).sort_index().sort_index(axis=1),
        }
    )


def _override_frame(
    panel: MarketPanel, field: str, trade_date: str, symbol: str, value: float
) -> pd.DataFrame:
    frame = panel.fields[field].copy()
    frame.at[trade_date, symbol] = value
    return frame


def _seal_entry_bar(panel: MarketPanel, symbol: str, entry_date: str) -> MarketPanel:
    """把 ``(symbol, entry_date)`` 改成**一字涨停**（open=high=low=close=昨收×1.10）。

    用于在合成链路中演示「一字涨停买单不成交 → 记原因码剔除」的撮合口径（零网络）。
    """
    close_frame = panel.fields["close"]
    position = list(close_frame.index).index(entry_date)
    prev_close = float(close_frame.iloc[position - 1][symbol])
    sealed_price = round(prev_close * 1.10, 2)
    fields = dict(panel.fields)
    for field in ("open", "high", "low", "close"):
        fields[field] = _override_frame(panel, field, entry_date, symbol, sealed_price)
    return MarketPanel(fields=fields)


def _seed_library(library_db: Path, dates: list[str]) -> list[dict]:
    """建合成因子库：builtin / LLM 各 2 个 active 因子 + 近 20 日 IC 序列。"""
    for expression, ic_value in BUILTIN_FACTORS:
        factor_id = add_factor(
            expression=expression,
            hypothesis=f"合成 builtin 动量因子 {expression}",
            category="builtin",
            status="active",
            source="builtin",
            db_path=library_db,
        )
        for trade_date in dates[:20]:
            record_ic(factor_id, trade_date, ic_value, db_path=library_db)
    for expression, ic_value in LLM_FACTORS:
        factor_id = add_factor(
            expression=expression,
            hypothesis=f"合成 LLM 挖掘因子 {expression}",
            category="动量",
            status="active",
            source="proposal",
            db_path=library_db,
        )
        for trade_date in dates[:20]:
            record_ic(factor_id, trade_date, ic_value, db_path=library_db)
    return list_active_factors(db_path=library_db)


def _seed_screening_history(
    panel: MarketPanel,
    dates: list[str],
    *,
    factors: list[dict],
    ic_stats: dict[int, tuple[float, float]],
    db_path: Path,
) -> dict[str, list[list[str]]]:
    """合成日批历史：逐日 L2 榜（rank_universe）→ L3 规则决策 → 精选池（keep ∩ 连续 ≥3）。"""
    init_screening_db(db_path)
    streak: dict[str, int] = dict.fromkeys(SYMBOLS, 0)
    boards: dict[str, list[str]] = {}
    pools: dict[str, list[str]] = {}
    for trade_date in dates:
        candidates = rank_universe(
            panel=panel,
            as_of=trade_date,
            passed_codes=list(SYMBOLS),
            factors=factors,
            ic_stats=ic_stats,
            top_n=L2_TOP_N,
        )
        record_l2_candidates(trade_date, candidates, db_path=db_path)
        board = [candidate.symbol for candidate in candidates]
        boards[trade_date] = board
        for symbol in SYMBOLS:
            streak[symbol] = streak[symbol] + 1 if symbol in board else 0
        decisions = [
            TriageDecision(
                symbol=candidate.symbol,
                verdict="keep" if candidate.rank <= L3_KEEP_RANKS else "drop",
                reason="合成 L3 规则：剔除榜末",
            )
            for candidate in candidates
        ]
        record_triage_decisions(trade_date, decisions, db_path=db_path)
        by_symbol = {candidate.symbol: candidate for candidate in candidates}
        pool_rows = [
            {
                "symbol": decision.symbol,
                "composite_score": by_symbol[decision.symbol].composite_score,
                "rank": by_symbol[decision.symbol].rank,
                "consecutive_days": streak[decision.symbol],
                "l3_verdict": "keep",
                "l3_reason": decision.reason,
                "factor_contribs": [],
            }
            for decision in decisions
            if decision.verdict == "keep" and streak[decision.symbol] >= 3
        ]
        pool_size = record_screening_batch(
            trade_date,
            status="ok",
            started_at=f"{trade_date}T15:00:00",
            finished_at=f"{trade_date}T15:01:00",
            l1_passed=len(SYMBOLS),
            l2_top=len(candidates),
            l3_keep=sum(1 for d in decisions if d.verdict == "keep"),
            pool_rows=pool_rows,
            db_path=db_path,
        )
        if pool_size:
            pools[trade_date] = [row["symbol"] for row in pool_rows]
    return {"boards": boards, "pools": pools}


def main() -> int:
    dates = _trading_days(N_TRADING_DAYS)
    pool_dates = dates[POOL_START_INDEX : POOL_START_INDEX + N_POOL_DATES]
    as_of = dates[-1]
    config = default_matching_config()

    with tempfile.TemporaryDirectory(prefix="screening-e2e-") as tmp:
        tmp_path = Path(tmp)
        library_db = tmp_path / "library.db"
        screening_db = tmp_path / "screening.db"
        factors = _seed_library(library_db, dates)
        ic_stats = recent_ic_stats(factors, db_path=library_db)
        panel = _build_panel(dates)
        history = _seed_screening_history(
            panel, dates, factors=factors, ic_stats=ic_stats, db_path=screening_db
        )
        # 一字涨停演示：对第 6 个池日的池内首只标的，在其次一交易日（入场日）造一字板
        sealed_case: dict[str, object] = {"applied": False}
        demo_pool_date = pool_dates[5]
        demo_symbols = history["pools"].get(demo_pool_date, [])
        if demo_symbols:
            entry_index = dates.index(demo_pool_date) + 1
            entry_date = dates[entry_index]
            panel = _seal_entry_bar(panel, demo_symbols[0], entry_date)
            sealed_case = {
                "applied": True,
                "pool_date": demo_pool_date,
                "symbol": demo_symbols[0],
                "entry_date": entry_date,
            }

        forward_payload = fwd.forward_pool_returns(
            pool_dates=pool_dates,
            panel=panel,
            db_path=screening_db,
            horizons=HORIZONS,
            as_of=as_of,
            config=config,
        )
        ablation_payload = ablation.compare_ablation_variants(
            variants=ablation.build_ablation_variants(
                pool_dates, db_path=screening_db, seed=42, top_n=ARM_TOP_N
            ),
            panel=panel,
            horizon=ABLATION_HORIZON,
            as_of=as_of,
            config=config,
        )
        l3_payload = l3.l3_keep_drop_compare(
            pool_dates=pool_dates,
            panel=panel,
            db_path=screening_db,
            horizon=ABLATION_HORIZON,
            as_of=as_of,
            config=config,
        )
        stratify_payload = stratify.stratify_by_factor_source(
            pool_dates=pool_dates,
            panel=panel,
            passed_codes_by_date={day: list(SYMBOLS) for day in pool_dates},
            library_db_path=library_db,
            top_n=ARM_TOP_N,
            horizon=ABLATION_HORIZON,
            as_of=as_of,
            config=config,
        )

    payload: dict[str, object] = {
        "title": "选股前向跟踪与消融报告（合成数据样本）",
        "data_source": "synthetic",
        "generated_at": "2026-10-13",
        "as_of": as_of,
        "pool_dates": {"count": len(pool_dates), "first": pool_dates[0], "last": pool_dates[-1]},
        "synthetic_parameters": {
            "seed": SEED,
            "symbols": list(SYMBOLS),
            "trading_days": N_TRADING_DAYS,
            "pool_dates": N_POOL_DATES,
            "l2_top_n": L2_TOP_N,
            "arm_top_n": ARM_TOP_N,
            "l3_rule": f"合成固定规则：剔除榜末 {L2_TOP_N - L3_KEEP_RANKS} 只",
            "pool_rule": "keep ∩ 连续在榜 ≥ 3 个交易日",
            "factors": [
                {"id": row["id"], "expression": row["expression"], "source": row["source"]}
                for row in factors
            ],
            "sealed_entry_demo": sealed_case,
        },
        "forward": forward_payload,
        "ablation": ablation_payload,
        "l3": l3_payload,
        "stratify": stratify_payload,
    }

    markdown = screening_report.render_screening_report_md(payload)
    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.write_text(markdown, encoding="utf-8")

    h20 = forward_payload["horizons"][ABLATION_HORIZON]
    unfilled = h20["unfilled_counts"]
    checks: dict[str, bool] = {
        "forward_selected_n": h20["selected"]["n"] > 0 and h20["selected"]["days"] >= 2,
        "forward_rejected_n": h20["rejected"]["n"] > 0,
        "forward_paired_ci": h20["diff"]["ci95"] is not None,
        "unfilled_recorded": unfilled.get(REASON_LIMIT_UP_UNFILLABLE, 0) >= 1,
        "ablation_three_variants": all(
            ablation_payload["variants"][name]["n"] > 0 for name in ablation.ABLATION_VARIANTS
        ),
        "ablation_paired_ci": all(
            diff["ci95"] is not None for diff in ablation_payload["diffs"].values()
        ),
        "l3_both_groups": l3_payload["keep"]["n"] > 0 and l3_payload["drop"]["n"] > 0,
        "l3_paired_ci": l3_payload["diff"]["ci95"] is not None,
        "stratify_both_strata": stratify_payload["strata"]["builtin"]["n"] > 0
        and stratify_payload["strata"]["llm"]["n"] > 0,
        "stratify_paired_ci": stratify_payload["diff"]["ci95"] is not None,
        "report_written": REPORT_OUT.exists() and len(markdown) > 500,
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"
    payload["checks"] = checks
    payload["verdict"] = verdict
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("== 合成端到端（30 池日）==")
    print(
        f"面板：{N_TRADING_DAYS} 交易日 × {len(SYMBOLS)} 标的（seed={SEED}）；池日 {pool_dates[0]}..{pool_dates[-1]}"
    )
    print(
        f"前向(h20)：入选 n={h20['selected']['n']}（均值 {h20['selected']['mean']}）"
        f" 落选 n={h20['rejected']['n']}（均值 {h20['rejected']['mean']}）"
        f" 配对 CI={h20['diff']['ci95']}"
    )
    print(
        f"未成交（撮合原因码）：{unfilled or '（无）'}；一字涨停演示：{sealed_case.get('applied')}"
    )
    print(f"消融：{ {k: v['mean'] for k, v in ablation_payload['variants'].items()} }")
    print(
        "消融增量："
        + "；".join(
            f"{key} → {diff['conclusion']}" for key, diff in ablation_payload["diffs"].items()
        )
    )
    print(
        f"±L3：keep 均值 {l3_payload['keep']['mean']}（踩雷率 {l3_payload['keep']['blowup_rate']}）"
        f" drop 均值 {l3_payload['drop']['mean']}（踩雷率 {l3_payload['drop']['blowup_rate']}）"
        f" → {l3_payload['conclusion']}"
    )
    print(
        f"分层：builtin 均值 {stratify_payload['strata']['builtin']['mean']}"
        f" / LLM 均值 {stratify_payload['strata']['llm']['mean']} → {stratify_payload['conclusion']}"
    )
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    print(f"\nJSON artifact → {JSON_OUT}")
    print(f"样本报告 → {REPORT_OUT}")
    print(f"verdict: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
