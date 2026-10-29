#!/usr/bin/env python
"""cohort 评估读数（只读、零 LLM）——delta add-cohort-evaluation-program。

口径真源：docs/evals/metrics.md §1.22（口径变更先改台账再动代码）。

四轨道（SHALL NOT 合成总分）：
- A 供给成本：成功率（§1.9⑧ attempt 口径）/ tokens_per_success / 预算占用 / 落库率；
- B 过程质量：citation 三项 Langfuse Scores 日均值 + 覆盖率；
- C 方向能力：neutral 反事实 r_e（T+1 开盘→T+20 收盘 相对沪深300 超额，评估侧自算、
  零写入）+ short/long 结算胜负（挂到期闸，未达只出 preliminary）；
- D 日频价值：方向翻转率 + 保鲜期（T+k 超额符号一致率），仅描述统计。

硬约束（§1.22）：窗口切分 W1/W2/W3（段内结论、段间不混报）；熔断整批失败日剔除出
成本均值并单列；统计单位 = 交易日（先日横截面均值再跨日）；confidence 归因闸——
归因完成前只输出分布，禁止校准/分组下游字段。

用法：
    uv run python scripts/cohort_eval_readout.py \
        --sessions-db data/sessions.db --snapshot-db data/market_snapshot.db \
        --as-of 2026-10-29 [--universe-version v1] [--md-out out.md]

Langfuse Scores 仅在环境提供 LANGFUSE_HOST/LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY
且未传 --offline 时拉取；缺省离线模式在 coverage 中显式标注，不伪造覆盖数。
"""

from __future__ import annotations

import argparse
import base64
import bisect
import json
import os
import sqlite3
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 口径常量（§1.22）

W1_END = "2026-10-10"  # 分析 LLM = kimi
W2_END = "2026-10-16"  # 分析 LLM = deepseek；W2 含熔断日 10-16
FORMAL_GATE_DATE = "2026-11-15"  # short/long 结算读数到期闸（到期潮）
FORMAL_SETTLED_MIN = 30  # 或已结算方向性观点 ≥ 此数
BENCHMARK_CODE = "000300"
HORIZON_TRADING_DAYS = 20
DEFAULT_BUDGET = 3_000_000
WINDOWS = ("W1", "W2", "W3")

ScoreFetcher = Callable[[str], list[dict[str, Any]]]


def window_of(day: str) -> str:
    if day <= W1_END:
        return "W1"
    if day <= W2_END:
        return "W2"
    return "W3"


def _norm_symbol(symbol: str) -> str:
    return str(symbol).split(".")[0].strip()


def _connect_ro(path: Path) -> sqlite3.Connection:
    """只读 URI 连接——评估读数结构性零写入（spec 场景：评估零写入）。"""
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


# ---------------------------------------------------------------- 取数


def _load_success_runs(
    conn: sqlite3.Connection, universe_version: str, as_of: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT run_id, ticker, trade_date, session_id, langfuse_trace_id, tokens_total"
        " FROM cohort_runs"
        " WHERE universe_version = ? AND status = 'success' AND trade_date <= ?"
        " ORDER BY trade_date, created_at",
        (universe_version, as_of),
    ).fetchall()
    return [
        {
            "run_id": r[0],
            "ticker": str(r[1]),
            "trade_date": r[2],
            "session_id": r[3],
            "trace_id": r[4],
            "tokens_total": r[5] or 0,
        }
        for r in rows
    ]


def _load_predictions(
    conn: sqlite3.Connection, universe_version: str, as_of: str
) -> list[dict[str, Any]]:
    """cohort_runs（success）join predictions，按 session_id 关联。"""
    rows = conn.execute(
        "SELECT p.prediction_id, p.symbol, p.direction, p.confidence, p.status,"
        " p.created_at, cr.ticker, cr.trade_date, cr.langfuse_trace_id"
        " FROM cohort_runs cr JOIN predictions p ON p.session_id = cr.session_id"
        " WHERE cr.universe_version = ? AND cr.status = 'success' AND cr.trade_date <= ?"
        " ORDER BY cr.trade_date, p.created_at, p.prediction_id",
        (universe_version, as_of),
    ).fetchall()
    return [
        {
            "prediction_id": r[0],
            "symbol": _norm_symbol(r[1] or ""),
            "direction": r[2],
            "confidence": r[3],
            "status": r[4],
            "created_at": r[5] or "",
            "ticker": str(r[6]),
            "trade_date": r[7],
            "trace_id": r[8],
        }
        for r in rows
    ]


def _dedup_day_major(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同票同日取 created_at 最晚一条（§1.9-v2 日主口径）。输入须已按日排序。"""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["ticker"], r["trade_date"])
        prev = best.get(key)
        if prev is None or (r["created_at"], r["prediction_id"]) >= (
            prev["created_at"],
            prev["prediction_id"],
        ):
            best[key] = r
    return [best[k] for k in sorted(best)]


def _load_job_days(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], int]:
    """cohort_batch 的 job_runs 按日聚合（同日补跑多行合并）。

    仅消费 status='ok' 且 summary 非空的行；failed 残行（summary=None，多为被撞杀的
    历史补跑）跳过并计数返回——它们不是熔断日（当日实际交付以 cohort_runs 为准）。
    """
    rows = conn.execute(
        "SELECT started_at, status, summary FROM job_runs WHERE job_id = 'cohort_batch'"
        " ORDER BY started_at"
    ).fetchall()
    agg: dict[str, dict[str, Any]] = {}
    anomalous = 0
    for started, status, summary in rows:
        day = str(started or "")[:10]
        if status != "ok" or not summary:
            anomalous += 1
            continue
        data = json.loads(summary)
        entry = agg.setdefault(
            day,
            {
                "trade_date": day,
                "window": window_of(day),
                "success": 0,
                "failure": 0,
                "skipped": 0,
                "tokens_total": 0,
                "daily_budget": int(data.get("daily_budget") or DEFAULT_BUDGET),
            },
        )
        entry["success"] += int(data.get("success") or 0)
        entry["failure"] += int(data.get("failure") or 0)
        entry["skipped"] += int(data.get("skipped") or 0)
        entry["tokens_total"] += int(data.get("tokens_total") or 0)
    return sorted(agg.values(), key=lambda e: e["trade_date"]), anomalous


def _load_calendar(
    conn: sqlite3.Connection,
) -> tuple[list[str], dict[str, float]]:
    rows = conn.execute(
        "SELECT trade_date, close FROM index_closes WHERE index_code = ? ORDER BY trade_date",
        (BENCHMARK_CODE,),
    ).fetchall()
    return [r[0] for r in rows], {r[0]: float(r[1]) for r in rows}


def _load_bars(snapshot_db: Path) -> dict[str, dict[str, tuple[float, float]]]:
    conn = _connect_ro(snapshot_db)
    try:
        rows = conn.execute(
            "SELECT trade_date, code, open, close_raw FROM market_snapshot"
        ).fetchall()
    finally:
        conn.close()
    bars: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for d, code, o, c in rows:
        if o is not None and c is not None:
            bars[str(code)][str(d)] = (float(o), float(c))
    return dict(bars)


# ---------------------------------------------------------------- 轨道 C：反事实


def _window_excess(
    bars: dict[str, tuple[float, float]],
    index: dict[str, float],
    calendar: list[str],
    t: str,
    k: int,
) -> float | None:
    """T+1 开盘 → T+k 收盘 个股收益 − 基准收益（基准腿 close(T)→close(T+k)）。

    起止任一缺行返回 None（缺日剔除，不插值，§1.22）。
    """
    i = bisect.bisect_right(calendar, t)
    if i + k - 1 >= len(calendar):
        return None  # 窗口未满（as_of 之前到期的样本才可判读）
    start_d, end_d = calendar[i], calendar[i + k - 1]
    bar_s, bar_e = bars.get(start_d), bars.get(end_d)
    idx_s, idx_e = index.get(t), index.get(end_d)
    if bar_s is None or bar_e is None or idx_s is None or idx_e is None:
        return None
    stock_leg = bar_e[1] / bar_s[0] - 1.0
    bench_leg = idx_e / idx_s - 1.0
    return stock_leg - bench_leg


def _neutral_counterfactual(
    preds: list[dict[str, Any]],
    bars_all: dict[str, dict[str, tuple[float, float]]],
    index: dict[str, float],
    calendar: list[str],
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    missing_bars = 0
    window_incomplete = 0
    for p in preds:
        if p["direction"] != "neutral":
            continue
        excess = _window_excess(
            bars_all.get(p["symbol"], {}), index, calendar, p["trade_date"], HORIZON_TRADING_DAYS
        )
        if excess is None:
            if bisect.bisect_right(calendar, p["trade_date"]) + HORIZON_TRADING_DAYS - 1 >= len(
                calendar
            ):
                window_incomplete += 1
            else:
                missing_bars += 1
            continue
        samples.append(
            {
                "ticker": p["ticker"],
                "symbol": p["symbol"],
                "trade_date": p["trade_date"],
                "window": window_of(p["trade_date"]),
                "r_e": excess,
            }
        )
    by_day: dict[str, list[float]] = defaultdict(list)
    for s in samples:
        by_day[s["trade_date"]].append(s["r_e"])
    day_stats: list[tuple[str, float, int]] = [
        (d, sum(v) / len(v), len(v)) for d, v in sorted(by_day.items())
    ]
    daily_means = [{"trade_date": d, "mean_r_e": m, "n": n} for d, m, n in day_stats]
    by_window: dict[str, dict[str, Any]] = {}
    for w in WINDOWS:
        vals = [s["r_e"] for s in samples if s["window"] == w]
        by_window[w] = {
            "n": len(vals),
            "mean_r_e": (sum(vals) / len(vals)) if vals else None,
        }
    return {
        "n_samples": len(samples),
        "missing_bars": missing_bars,
        "window_incomplete": window_incomplete,
        "samples": samples,
        "daily_means": daily_means,
        "cross_day_mean": (sum(m for _, m, _ in day_stats) / len(day_stats) if day_stats else None),
        "n_days": len(day_stats),
        "by_window": by_window,
    }


def _directional(preds: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    rows = [p for p in preds if p["direction"] in ("long", "short")]
    settled = [
        p for p in rows if p["status"] in ("resolved_win", "resolved_loss", "resolved_neutral")
    ]
    win = sum(1 for p in settled if p["status"] == "resolved_win")
    loss = sum(1 for p in settled if p["status"] == "resolved_loss")
    formal_ready = len(settled) >= FORMAL_SETTLED_MIN or as_of >= FORMAL_GATE_DATE
    return {
        "n_total": len(rows),
        "n_open": len(rows) - len(settled),
        "settled_total": len(settled),
        "settled_win": win,
        "settled_loss": loss,
        "settled_neutral_outcome": len(settled) - win - loss,
        "formal_ready": formal_ready,
        "preliminary": not formal_ready,
    }


# ---------------------------------------------------------------- 轨道 D：日频价值


def _flips_by_ticker(day_major: list[dict[str, Any]]) -> list[dict[str, Any]]:
    per: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p in day_major:
        per[p["ticker"]].append((p["trade_date"], p["direction"]))
    out = []
    for ticker in sorted(per):
        seq = sorted(per[ticker])
        flips = sum(1 for a, b in zip(seq, seq[1:], strict=False) if a[1] != b[1])
        out.append({"ticker": ticker, "flips": flips, "analysis_days": len(seq)})
    return out


def _shelf_life(
    day_major: list[dict[str, Any]],
    bars_all: dict[str, dict[str, tuple[float, float]]],
    index: dict[str, float],
    calendar: list[str],
) -> list[dict[str, Any]]:
    """T+k 超额符号与方向符号一致率（仅 long/short；仅描述统计）。"""
    rows = [p for p in day_major if p["direction"] in ("long", "short")]
    out = []
    for k in range(1, HORIZON_TRADING_DAYS + 1):
        agree = 0
        n = 0
        for p in rows:
            excess = _window_excess(
                bars_all.get(p["symbol"], {}), index, calendar, p["trade_date"], k
            )
            if excess is None or excess == 0.0:
                continue
            sign = 1.0 if p["direction"] == "long" else -1.0
            n += 1
            if (excess > 0) == (sign > 0):
                agree += 1
        out.append({"k": k, "n": n, "agree_rate": (agree / n) if n else None})
    return out


# ---------------------------------------------------------------- 轨道 A / B


def _track_a(
    success_runs: list[dict[str, Any]], job_days: list[dict[str, Any]], job_rows_anomalous: int = 0
) -> dict[str, Any]:
    by_window: dict[str, dict[str, Any]] = {}
    for w in WINDOWS:
        runs_w = [r for r in success_runs if window_of(r["trade_date"]) == w]
        jobs_w = [j for j in job_days if j["window"] == w]
        s = sum(j["success"] for j in jobs_w)
        f = sum(j["failure"] for j in jobs_w)
        toks = sum(r["tokens_total"] for r in runs_w)
        by_window[w] = {
            "success": len(runs_w),
            "tokens_total": toks,
            "tokens_per_success": (toks / len(runs_w)) if runs_w else None,
            "attempt_success_rate": (s / (s + f)) if (s + f) else None,
        }
    cost_days = [
        {
            "trade_date": j["trade_date"],
            "window": j["window"],
            "success": j["success"],
            "tokens_total": j["tokens_total"],
            "tokens_per_success": (j["tokens_total"] / j["success"]) if j["success"] else None,
        }
        for j in job_days
        if j["success"] > 0
    ]
    circuit_days = [
        {
            "trade_date": j["trade_date"],
            "window": j["window"],
            "failure": j["failure"],
            "tokens_total": j["tokens_total"],
        }
        for j in job_days
        if j["success"] == 0
    ]
    occupancy = [
        {
            "trade_date": j["trade_date"],
            "ratio": (j["tokens_total"] / j["daily_budget"]) if j["daily_budget"] else None,
        }
        for j in job_days
    ]
    return {
        "by_window": by_window,
        "cost_days": cost_days,
        "circuit_days": circuit_days,
        "budget_occupancy": occupancy,
        "job_rows_anomalous": job_rows_anomalous,
    }


def _track_b(success_runs: list[dict[str, Any]], fetch: ScoreFetcher | None) -> dict[str, Any]:
    total = len(success_runs)
    if fetch is None:
        return {
            "coverage": {
                "scored": 0,
                "total": total,
                "rate": None,
                "note": "offline：未提供 Langfuse 拉取，覆盖率不伪造",
            },
            "daily_means": [],
        }
    per_day: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    scored = 0
    for r in success_runs:
        scores = fetch(r["trace_id"]) if r["trace_id"] else []
        if scores:
            scored += 1
        for sc in scores:
            name = sc.get("name")
            value = sc.get("value")
            # 白名单：轨道 B 只消费 citation 族——judge 四维零散断供、decision_* 是收益值
            # 而非 0-1 分数，混入会把收益均值化（§1.22 轨道 B 口径）
            if name and str(name).startswith("citation") and isinstance(value, (int, float)):
                per_day[r["trade_date"]][str(name)].append(float(value))
    daily_means = [
        {
            "trade_date": d,
            **{name: sum(vals) / len(vals) for name, vals in sorted(metrics.items())},
            "n": max(len(v) for v in metrics.values()),
        }
        for d, metrics in sorted(per_day.items())
    ]
    return {
        "coverage": {
            "scored": scored,
            "total": total,
            "rate": (scored / total) if total else None,
        },
        "daily_means": daily_means,
    }


def _confidence_gate(day_major: list[dict[str, Any]]) -> dict[str, Any]:
    dist: dict[str, int] = defaultdict(int)
    by_day_mode: list[dict[str, Any]] = []
    per_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for p in day_major:
        c = p["confidence"]
        if c is None:
            continue
        key = f"{round(float(c), 2):.2f}"
        dist[key] += 1
        per_day[p["trade_date"]][key] += 1
    for d, counts in sorted(per_day.items()):
        by_day_mode.append(
            {
                "trade_date": d,
                "mode": max(counts, key=lambda k: counts[k]),
                "n": sum(counts.values()),
            }
        )
    n_total = sum(dist.values())
    return {
        "attribution_required": True,  # 归因完成前恒为 True（§1.22 归因闸）
        "dist": dict(dist),
        "by_day_mode": by_day_mode,
        "n": n_total,
    }


# ---------------------------------------------------------------- 组装


def build_readout(
    *,
    sessions_db: Path,
    snapshot_db: Path,
    as_of: str,
    universe_version: str,
    langfuse_fetch: ScoreFetcher | None = None,
) -> dict[str, Any]:
    conn = _connect_ro(sessions_db)
    try:
        success_runs = _load_success_runs(conn, universe_version, as_of)
        preds = _load_predictions(conn, universe_version, as_of)
        job_days, job_rows_anomalous = _load_job_days(conn)
        calendar, index = _load_calendar(conn)
    finally:
        conn.close()
    bars_all = _load_bars(snapshot_db)
    day_major = _dedup_day_major(preds)

    persisted = len(day_major)
    neutral = _neutral_counterfactual(day_major, bars_all, index, calendar)
    directional = _directional(day_major, as_of)
    return {
        "meta": {
            "as_of": as_of,
            "universe_version": universe_version,
            "benchmark": BENCHMARK_CODE,
            "windows": {"W1_END": W1_END, "W2_END": W2_END},
            "horizon_trading_days": HORIZON_TRADING_DAYS,
            "n_success_runs": len(success_runs),
            "opinions_persisted": persisted,
            "persisted_rate": (persisted / len(success_runs)) if success_runs else None,
            "stats_unit": "trading_day_cluster",
            "declaration": "n<15 段仅量级描述；未达显著 ≠ 无差异（§1.22）",
        },
        "track_a": _track_a(success_runs, job_days, job_rows_anomalous),
        "track_b": _track_b(success_runs, langfuse_fetch),
        "track_c": {"neutral": neutral, "directional": directional},
        "track_d": {
            "flips_by_ticker": _flips_by_ticker(day_major),
            "shelf_life": _shelf_life(day_major, bars_all, index, calendar),
        },
        "confidence": _confidence_gate(day_major),
    }


# ---------------------------------------------------------------- Langfuse（可选）


def _make_langfuse_fetch() -> ScoreFetcher | None:
    host = os.getenv("LANGFUSE_HOST")
    pk = os.getenv("LANGFUSE_PUBLIC_KEY")
    sk = os.getenv("LANGFUSE_SECRET_KEY")
    if not (host and pk and sk):
        return None
    token = base64.b64encode(f"{pk}:{sk}".encode()).decode()

    def fetch_page(page: int) -> tuple[list[dict[str, Any]], int]:
        url = f"{host.rstrip('/')}/api/public/scores?limit=100&page={page}"
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})  # noqa: S310 - 内网 Langfuse
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - 内网 Langfuse
            body = json.load(resp)
        data = list(body.get("data", []))
        total_pages = int(body.get("meta", {}).get("totalPages") or 1)
        return data, total_pages

    return build_score_index(fetch_page)


def build_score_index(
    fetch_page: Callable[[int], tuple[list[dict[str, Any]], int]],
) -> Callable[[str], list[dict[str, Any]]]:
    """全量分页预取 scores，客户端按 score 行自身的 traceId 建索引。

    实测（2026-10-29，langfuse v3 实例）：scores API 的 ``traceId`` 查询参数不生效
    （任意 traceId 均返回同一份全局分页）——按 trace 过滤必须在客户端做，否则
    coverage / 日均值会把全局分数串到 cohort trace 上（首轮读数事故已由锚点核验拦下）。
    """
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    page = 1
    while True:
        data, total_pages = fetch_page(page)
        for s in data:
            tid = s.get("traceId")
            if tid:
                index[str(tid)].append(s)
        if page >= total_pages or not data:
            break
        page += 1

    def fetch(trace_id: str) -> list[dict[str, Any]]:
        return index.get(trace_id, [])

    return fetch


# ---------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description="cohort 评估读数（只读、零 LLM）")
    parser.add_argument("--sessions-db", default="data/sessions.db")
    parser.add_argument("--snapshot-db", default="data/market_snapshot.db")
    parser.add_argument("--as-of", required=True, help="读数截止日 YYYY-MM-DD")
    parser.add_argument("--universe-version", default="v1")
    parser.add_argument("--offline", action="store_true", help="不拉取 Langfuse Scores")
    parser.add_argument("--md-out", default=None, help="同时写 Markdown 骨架到该路径")
    args = parser.parse_args()

    fetch = None if args.offline else _make_langfuse_fetch()
    out = build_readout(
        sessions_db=Path(args.sessions_db),
        snapshot_db=Path(args.snapshot_db),
        as_of=args.as_of,
        universe_version=args.universe_version,
        langfuse_fetch=fetch,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.md_out:
        _write_md(out, Path(args.md_out))
    return 0


def _write_md(out: dict[str, Any], path: Path) -> None:
    meta = out["meta"]
    neutral = out["track_c"]["neutral"]
    directional = out["track_c"]["directional"]
    lines = [
        f"# cohort 评估读数（as-of {meta['as_of']}, universe {meta['universe_version']}）",
        "",
        "> 口径：docs/evals/metrics.md §1.22；统计单位 = 交易日聚类；未达显著 ≠ 无差异。",
        "",
        "## 轨道 A 供给成本",
        "```json",
        json.dumps(out["track_a"]["by_window"], ensure_ascii=False, indent=2),
        "```",
        f"熔断日（剔除出成本均值）：{json.dumps(out['track_a']['circuit_days'], ensure_ascii=False)}",
        "",
        "## 轨道 B 过程质量（citation）",
        f"覆盖率：{json.dumps(out['track_b']['coverage'], ensure_ascii=False)}",
        "",
        "## 轨道 C 方向能力",
        f"- neutral 反事实：n={neutral['n_samples']}, 跨日均值={neutral['cross_day_mean']}, "
        f"missing_bars={neutral['missing_bars']}, window_incomplete={neutral['window_incomplete']}",
        f"- short/long 结算：preliminary={directional['preliminary']} "
        f"(settled={directional['settled_total']}, win={directional['settled_win']}, "
        f"loss={directional['settled_loss']})",
        "",
        "## 轨道 D 日频价值（仅描述）",
        f"- 翻转率：{json.dumps(out['track_d']['flips_by_ticker'], ensure_ascii=False)}",
        "",
        "## confidence（归因闸：未归因，禁止下游使用）",
        f"分布：{json.dumps(out['confidence']['dist'], ensure_ascii=False)}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
