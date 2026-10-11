"""结算报告（issue #250）：§1.9-v2 预登记口径的消费端。

IC/ICIR 与蒙特卡洛零模型的真读数在此拼装为对外结算报告：
collect（读库取结算日主行）→ build_report_data（纯函数组装读数，红线先判）
→ render_marketing_report（markdown 渲染）。本模块只读，不写库。

红线（§1.9-v2）：可判定日主样本 < 10 → 零模型 SHALL NOT 产出（首批 T+20 结算
样本可能 <10，这是最常见路径而非异常，报告必须优雅降级）；「跑赢/跑输」类结论
必须附分位读数，单独出现的点估计 = 口径违规。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from finance_agent.outcome.track_record.judgment import (
    day_master_ids,
    derive_attribution_date,
)
from finance_agent.outcome.track_record.model import list_predictions
from finance_agent.outcome.track_record.significance import (
    MC_DEFAULT_N,
    excess_quantile,
    icir,
    monthly_ic,
    simulate_random_excess,
)

logger = logging.getLogger(__name__)

SETTLED_MC_MIN_SAMPLE = 10  # §1.9-v2：可判定日主样本 < 10 → 零模型 SHALL NOT 产出


def collect_settled_day_masters(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """读取已结算日主行（§1.9-v2 样本口径）。

    list_predictions 全量读取（上限 100_000，见 model.list_predictions P1 注记）
    后防御性过滤：resolution_rule 为空（未结算 open 行）或 'duplicate_of_day'
    （同日重复关闭行）不进报告——结算行本应只含日主，防御照做。返回行不做状态级
    过滤（unresolvable/avoidance 等由下游按读数可用性处理）。
    """
    rows = list_predictions(limit=100_000, db_path=db_path)
    return [r for r in rows if r.get("resolution_rule") not in (None, "", "duplicate_of_day")]


def build_report_data(
    settled: list[dict[str, Any]],
    universe_by_day: dict[str, list[str]],
    closes: dict[str, dict[str, float]],
    benchmark_closes: dict[str, float],
    exit_date: str,
    n_sims: int = MC_DEFAULT_N,
    seed: int = 20261006,
) -> dict[str, Any]:
    """组装结算报告读数（纯函数；口径 §1.9-v2，顶层 caliber 引用预登记版本）。

    settled: collect_settled_day_masters 的输出（已结算日主行）；入参会重算
    day_master_ids 做第三道防御——非日主行（如同日 superseded 旧行）剔除后再进
    任何统计；universe_by_day/closes/benchmark_closes/exit_date: 零模型行情面板
    （语义见 simulate_random_excess）；n_sims/seed: 零模型抽样次数与种子（预登记
    默认 10_000 / 20261006）。

    IC/ICIR：方向 IC（long/short 的 resolved_win/loss）与回避序列（neutral）
    分别成列；icir 剔除样本不足期后期数 <6 → None。
    蒙特卡洛（红线先判）：可判定日主样本 = 日主中 long/short 且 excess_return
    非空的行数（unresolvable 等无读数行不是「可判定」样本）——< 10 → 零模型段
    available=False + reason（引用 §1.9-v2 与样本数），不产出任何零模型读数。
    ≥ 10 执行：真实读数 = 可判定行 excess_return 等权均值（与零模型的「平均槽位
    超额」同构可比——两侧都是逐槽 T+20 超额均值）；posture = 可判定行按归属日
    （derive_attribution_date(created_at, calendar)，calendar 取基准交易日升序
    列表 = sorted(benchmark_closes)）分组计数；零模型 entry_map = {归属日: 归属日}
    （归属日已按日历吸附为交易日，与 settle_entry_price 派生同源）。全部槽共用
    单 exit_date（CLI 取最晚归属日 + DEFAULT_HORIZON_DAYS 个基准交易日）——
    跨月槽位混合前须预登记修订为逐日 exit。真实槽 symbol（归一化去后缀）不在
    归属日池内 → logger.warning（池外选股使零模型不同框，观测项不阻断）。
    零模型无有效模拟轮次（行情面板不可得）→ available=False，无分位不出现点估计。

    返回 dict：caliber / ic_series / avoidance_series / icir / sample / monte_carlo
    （available=True 时含 real_excess / quantile / p_value / n_sims / seed /
    months_covered；available=False 时仅 available + reason）。
    """
    calendar = sorted(benchmark_closes)
    # ⚠1 第三道防御：对入参重算日主（§1.9-v2）并剔除非日主行——collect 只滤
    # duplicate_of_day，同日 superseded 旧行（盘中翻向）会漏入，此处兜底。
    masters = day_master_ids(settled, calendar)
    rows = [r for r in settled if str(r.get("prediction_id")) in masters]
    ic_series = monthly_ic(rows, kind="direction")
    avoidance_series = monthly_ic(rows, kind="avoidance")
    slots = [
        r
        for r in rows
        if r.get("direction") in ("long", "short") and r.get("excess_return") is not None
    ]
    sample = len(slots)
    data: dict[str, Any] = {
        "caliber": "§1.9-v2",
        "ic_series": ic_series,
        "avoidance_series": avoidance_series,
        "icir": icir(ic_series),
        "sample": sample,
    }
    if sample < SETTLED_MC_MIN_SAMPLE:
        data["monte_carlo"] = {
            "available": False,
            "reason": (f"可判定日主样本 {sample}<10（§1.9-v2 settled<10 红线），不产出零模型读数"),
        }
        return data
    real_excess = sum(float(r["excess_return"]) for r in slots) / sample
    long_counts: dict[str, int] = {}
    short_counts: dict[str, int] = {}
    for r in slots:
        day = derive_attribution_date(str(r["created_at"]), calendar)
        # ⚠2 观测：真实槽不在归属日池内（归一化去 .SH/.SZ 后缀，同 job._code 口径）
        # → 池外选股使零模型不同框；首批可忽略但须有观测，不阻断。
        if str(r.get("symbol", "")).split(".")[0] not in universe_by_day.get(day, []):
            logger.warning(
                "build_report_data: 真实槽 %s symbol=%s 不在归属日 %s 的日池内——"
                "池外选股使零模型不同框",
                r.get("prediction_id"),
                r.get("symbol"),
                day,
            )
        counts = long_counts if r["direction"] == "long" else short_counts
        counts[day] = counts.get(day, 0) + 1
    sims = simulate_random_excess(
        universe_by_day,
        long_counts,
        short_counts,
        closes,
        {day: day for day in long_counts | short_counts},
        exit_date,
        benchmark_closes,
        n_sims=n_sims,
        seed=seed,
    )
    q = excess_quantile(real_excess, sims)
    if q["quantile"] is None:
        # M1：零模型无有效模拟轮次（行情面板整体不可得/全部槽被剔除）——不得以
        # available=True + quantile=None 逃逸（render/CLI 格式化会崩）；无分位
        # 不出现点估计，real_excess 一并不落盘。
        logger.warning(
            "build_report_data: 零模型无有效模拟轮次（行情面板不可得或槽位全部缺失），不产出读数"
        )
        data["monte_carlo"] = {
            "available": False,
            "reason": (
                "行情面板不可得/零模型无有效模拟轮次，读数不产出（§1.9-v2：无分位不出现点估计）"
            ),
        }
        return data
    data["monte_carlo"] = {
        "available": True,
        "real_excess": round(real_excess, 6),
        "quantile": q["quantile"],
        "p_value": q["p_value"],
        "n_sims": q["n_sims"],
        "seed": seed,
        "months_covered": len({str(r["resolved_at"])[:7] for r in slots}),
    }
    return data


def render_marketing_report(data: dict[str, Any]) -> str:
    """渲染 markdown 结算报告（纯字符串拼装，可精确断言）。

    契约（§1.9-v2）：点估计与分位必须同段出现——零模型可用时真实超额、分位、p 值
    同行并排（含「跑赢」字样的行必附分位/p 值读数）；红线触发时只渲染降级说明，
    绝不单独出现点估计。诚实边界段逐条列出触发的门槛。
    data: build_report_data 的输出 + 可选 as_of（报告日期，由 CLI 注入）。
    """
    as_of = str(data.get("as_of") or "")
    caliber = str(data.get("caliber") or "")
    ic_series: list[dict[str, Any]] = data.get("ic_series") or []
    avoidance_series: list[dict[str, Any]] = data.get("avoidance_series") or []
    mc: dict[str, Any] = data.get("monte_carlo") or {}

    lines: list[str] = [f"# T+20 结算报告（{as_of} · 口径 {caliber}）", ""]
    lines.append("IC/ICIR 与零模型读数仅消费日主口径（duplicate_of_day 永不进入）。")
    lines.append("")

    lines.append("## 方向 IC（月度，long/short 日主）")
    lines.append("")
    if ic_series:
        lines.append("| 月份 | 胜 | 负 | 样本 | IC | 备注 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for s in ic_series:
            note = "样本不足（<10，不进 ICIR）" if s.get("insufficient") else ""
            ic_text = f"{s['ic']:.4f}" if s.get("ic") is not None else "—"
            lines.append(
                f"| {s['month']} | {s['wins']} | {s['losses']} | {s['sample']} | {ic_text} | {note} |"
            )
    else:
        lines.append("（暂无方向 IC 数据）")
    lines.append("")

    lines.append("## ICIR")
    lines.append("")
    icir_value = data.get("icir")
    lines.append(
        f"ICIR：{icir_value:.4f}"
        if icir_value is not None
        else "ICIR：期数不足不展示（有效期数 <6）"
    )
    lines.append("")

    lines.append("## 回避序列（neutral，单独成列）")
    lines.append("")
    if avoidance_series:
        lines.append("| 月份 | 回避正确 | 错失上涨 | 样本 | IC | 备注 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for s in avoidance_series:
            note = "样本不足（<10）" if s.get("insufficient") else ""
            ic_text = f"{s['ic']:.4f}" if s.get("ic") is not None else "—"
            lines.append(
                f"| {s['month']} | {s['wins']} | {s['losses']} | {s['sample']} | {ic_text} | {note} |"
            )
    else:
        lines.append("（暂无回避判定样本）")
    lines.append("")

    lines.append("## 蒙特卡洛零模型（敞口对齐）")
    lines.append("")
    if mc.get("available"):
        # 口径契约：点估计与分位同一行——「跑赢」结论必须同行附分位与 p 值
        lines.append(
            f"真实组合平均槽位超额 {mc['real_excess']:.4f}，位于零模型分位 "
            f"{mc['quantile']:.4f}（p 值 {mc['p_value']:.4f}）——跑赢 "
            f"{float(mc['quantile']) * 100:.1f}% 的随机组合"
            f"（n_sims={mc['n_sims']}，seed={mc['seed']}，覆盖 {mc['months_covered']} 个结算月）。"
        )
    else:
        lines.append(f"零模型读数不产出：{mc.get('reason', '原因未提供')}")
    lines.append("")

    gates: list[str] = []
    for s in ic_series:
        if s.get("insufficient"):
            gates.append(f"方向 IC {s['month']} 月样本 {s['sample']}（<10），该期不进 ICIR")
    for s in avoidance_series:
        if s.get("insufficient"):
            gates.append(f"回避序列 {s['month']} 月样本 {s['sample']}（<10）")
    if icir_value is None:
        gates.append("有效期数 <6，ICIR 不展示")
    if mc.get("available"):
        if not mc.get("n_sims"):
            gates.append("零模型无有效模拟轮次（行情面板可能整体缺失）")
    else:
        gates.append(f"零模型未产出：{mc.get('reason', '')}")
    lines.append("## 诚实边界（触发的门槛）")
    lines.append("")
    if gates:
        lines.extend(f"- {g}" for g in gates)
    else:
        lines.append("- 无（本期无触发门槛）")
    lines.append("")
    return "\n".join(lines)


MARKET_SLEEP_SECONDS = 0.5  # AKShare 逐股拉取间隔（限流是已知坑）
POOL_JSON_PATH = Path("data/cohort/universe-v1.pool.json")


def _load_pool_tickers() -> list[str]:
    """读取预登记抽样框（universe-v1.pool.json，实测 300 条沪深300 成分）。"""
    import json

    if not POOL_JSON_PATH.exists():
        raise SystemExit(f"池文件不存在：{POOL_JSON_PATH}（需在仓库根目录运行）")
    entries: list[dict[str, Any]] = json.loads(POOL_JSON_PATH.read_text(encoding="utf-8"))
    return [str(e["ticker"]) for e in entries]


def _fetch_benchmark_closes(days: int = 250) -> dict[str, float]:
    """基准指数日 K → date→close（BENCHMARK_CODE 与判定 job 同源）。"""
    from finance_agent.data.akshare_client import AKShareClient
    from finance_agent.outcome.track_record.job import BENCHMARK_CODE

    client = AKShareClient()
    df = client.fetch_index_kline(BENCHMARK_CODE, days=days)
    if df is None or df.empty:
        raise SystemExit("基准指数日 K 拉取失败（返回空），无法确定交易日历——中止")
    return {str(r["日期"])[:10]: float(r["收盘"]) for r in df.to_dict("records")}


def _fetch_stock_closes(
    tickers: list[str], universe_by_day: dict[str, list[str]], days: int = 400
) -> dict[str, dict[str, float]]:
    """逐股拉取收盘面板（网络 IO，不做单测——函数层已全测）。

    AKShare 逐股拉取有限流已知坑：逐股间隔 0.5s、失败重试 1 次；仍失败 → WARN
    并从当日池剔除——per-day universe 语义天然容忍缺股（见
    simulate_random_excess docstring），但调用方应关注 WARN 量级。
    """
    import logging
    import time

    from finance_agent.data.akshare_client import SETTLEMENT_ADJUST, AKShareClient

    logger = logging.getLogger(__name__)
    client = AKShareClient()
    closes: dict[str, dict[str, float]] = {}
    dropped: list[str] = []
    for i, ticker in enumerate(tickers):
        if i:
            time.sleep(MARKET_SLEEP_SECONDS)
        df = None
        try:
            df = client.fetch_kline(ticker, days=days, adjust=SETTLEMENT_ADJUST)
        except Exception as exc:  # 网络异常不中断整批，该股走降级路径
            logger.warning("report CLI: %s 行情拉取异常（%s）", ticker, exc)
        if df is None or df.empty:
            time.sleep(MARKET_SLEEP_SECONDS)
            try:  # 失败重试 1 次
                df = client.fetch_kline(ticker, days=days, adjust=SETTLEMENT_ADJUST)
            except Exception as exc:
                logger.warning("report CLI: %s 重试仍异常（%s）", ticker, exc)
        if df is None or df.empty:
            logger.warning(
                "report CLI: %s 行情拉取失败（重试 1 次后仍失败），从当日池剔除"
                "（per-day universe 语义天然容忍缺股）",
                ticker,
            )
            dropped.append(ticker)
            continue
        closes[ticker] = {str(r["日期"])[:10]: float(r["收盘"]) for r in df.to_dict("records")}
    if dropped:
        drop = set(dropped)
        for day, pool in universe_by_day.items():
            universe_by_day[day] = [t for t in pool if t not in drop]
    return closes


def _exit_date_for(calendar: list[str], attribution_days: list[str]) -> str:
    """exit_date = 最晚归属日后第 DEFAULT_HORIZON_DAYS 个基准交易日（越界取末日并 WARN）。"""
    import logging

    from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS

    logger = logging.getLogger(__name__)
    last_day = max(attribution_days)
    if last_day in calendar:
        idx = calendar.index(last_day)
    else:
        logger.warning("report CLI: 归属日 %s 不在基准日历内，以日历末日为基点", last_day)
        idx = len(calendar) - 1
    idx += DEFAULT_HORIZON_DAYS
    if idx >= len(calendar):
        logger.warning(
            "report CLI: 基准日历不足最晚归属日 + %d 个交易日，exit_date 取日历末日 %s",
            DEFAULT_HORIZON_DAYS,
            calendar[-1],
        )
        idx = len(calendar) - 1
    return calendar[idx]


def main(argv: list[str] | None = None) -> int:
    """薄 CLI：行情面板组装（AKShare）→ build_report_data → 渲染落盘。

    --n-sims/--seed 默认即预登记值（不建 env 配置管线，issue #250 裁决）；
    --db/--out 缺省走 SESSIONS_DB_PATH 与 reports/settlement/<今日>-settlement-report.md。
    """
    import argparse
    from datetime import date

    parser = argparse.ArgumentParser(
        prog="python -m finance_agent.outcome.track_record.report",
        description="生成 T+20 结算报告（口径 §1.9-v2；只读，不写库）",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="sessions DB 路径（缺省 SESSIONS_DB_PATH，其次 data/sessions.db）",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="输出 md 路径（缺省 reports/settlement/<今日>-settlement-report.md）",
    )
    parser.add_argument(
        "--n-sims",
        type=int,
        default=MC_DEFAULT_N,
        help="零模型抽样次数（预登记默认 10_000，不建 env 配置管线）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20261006,
        help="零模型种子（预登记默认 20261006）",
    )
    args = parser.parse_args(argv)

    from finance_agent.outcome.track_record.judgment import derive_attribution_date

    settled = collect_settled_day_masters(db_path=args.db)
    benchmark_closes = _fetch_benchmark_closes()
    calendar = sorted(benchmark_closes)
    attribution_days = sorted(
        {
            derive_attribution_date(str(r["created_at"]), calendar)
            for r in settled
            if r.get("direction") in ("long", "short")
        }
    )
    universe_by_day = {day: list(_load_pool_tickers()) for day in attribution_days}
    pool_tickers = next(iter(universe_by_day.values()), [])
    closes = _fetch_stock_closes(pool_tickers, universe_by_day) if attribution_days else {}
    exit_date = _exit_date_for(calendar, attribution_days) if attribution_days else calendar[-1]
    data = build_report_data(
        settled,
        universe_by_day,
        closes,
        benchmark_closes,
        exit_date,
        n_sims=args.n_sims,
        seed=args.seed,
    )
    data["as_of"] = date.today().isoformat()
    out_path = Path(
        args.out or f"reports/settlement/{date.today().isoformat()}-settlement-report.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_marketing_report(data), encoding="utf-8")
    mc = data["monte_carlo"]
    print(f"结算报告已写入：{out_path}")
    print(
        f"可判定日主样本 {data['sample']}；零模型："
        + (
            f"产出（quantile={mc['quantile']:.4f}, p={mc['p_value']:.4f}）"
            if mc["available"]
            else "不产出（样本红线或行情面板降级，详见报告）"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
