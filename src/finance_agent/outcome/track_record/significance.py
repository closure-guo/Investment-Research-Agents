"""结算显著性与信号一致性统计（add-prediction-pool-integrity；口径预登记 metrics.md §1.9-v2）。

纯函数模块：IC 月度序列 / ICIR / 敞口对齐蒙特卡洛零模型。输入方（API/结算报告）负责
只喂日主口径行（duplicate_of_day 永不进入）；本模块按 direction/status 二次防御过滤。
阈值与公式以 §1.9-v2 为准，禁止在调用方另写拷贝。
"""

from __future__ import annotations

import logging
import random

logger = logging.getLogger(__name__)

IC_MIN_SAMPLE = 10  # 单期可判定样本 < 10 → 该期「样本不足」，不进 ICIR
ICIR_MIN_PERIODS = 6  # 有效期数 < 6 → ICIR 不展示
MC_DEFAULT_N = 10_000  # 零模型抽样次数


def monthly_ic(rows: list[dict], kind: str = "direction") -> list[dict]:
    """按结算月（resolved_at[:7]）聚合月度读数（§1.9-v2）。

    kind="direction"（默认）：long/short 的 resolved_win/resolved_loss；
    kind="avoidance"：neutral 的 avoidance_win/avoidance_loss（回避类单独成列，
    SHALL NOT 混入方向 IC）。回避状态取值字段 = r.get("avoidance_status") or
    r.get("status")，拍平兼容两种形状：DB 行为 status='avoidance'（生命周期终态，
    judgment.TERMINAL_AVOIDANCE_STATUS）+ 独立 avoidance_status 结果列；拍平行则
    status 直接为 avoidance_win/loss。其余状态（resolved_neutral/unresolvable/
    avoidance_neutral）与 duplicate_of_day 行不进分子分母（防御性再过滤，输入方
    仍须只喂日主行）。返回按月升序：[{month, wins, losses, sample, ic, insufficient}]。
    """
    want_status = (
        ("resolved_win", "resolved_loss")
        if kind == "direction"
        else ("avoidance_win", "avoidance_loss")
    )
    want_dirs = ("long", "short") if kind == "direction" else ("neutral",)
    win_status, loss_status = want_status
    agg: dict[str, list[int]] = {}
    for r in rows:
        if r.get("direction") not in want_dirs:
            continue
        if kind == "avoidance":
            status = r.get("avoidance_status") or r.get("status")
        else:
            status = r.get("status")
        if status not in want_status:
            continue
        month = str(r["resolved_at"])[:7]
        slot = agg.setdefault(month, [0, 0])
        slot[0 if status == win_status else 1] += 1
    out: list[dict] = []
    for month in sorted(agg):
        wins, losses = agg[month]
        sample = wins + losses
        out.append(
            {
                "month": month,
                "wins": wins,
                "losses": losses,
                "sample": sample,
                "ic": round(wins / sample, 4) if sample else None,
                "insufficient": sample < IC_MIN_SAMPLE,
            }
        )
    return out


def icir(ic_series: list[dict]) -> float | None:
    """mean(ic)/std(ic, population)；剔除 insufficient 期后期数 < ICIR_MIN_PERIODS → None。"""
    values: list[float] = [
        s["ic"] for s in ic_series if not s.get("insufficient") and s.get("ic") is not None
    ]
    if len(values) < ICIR_MIN_PERIODS:
        return None
    n = len(values)
    mu: float = sum(values) / n
    sd: float = (sum((v - mu) ** 2 for v in values) / n) ** 0.5
    if sd <= 0:
        return None
    return round(mu / sd, 4)


def _draw_group_symbols(rng: random.Random, pool: list[str], n: int) -> tuple[list[str], bool]:
    """单组（同归属日同方向）抽 n 个标的：**无放回**（对齐日主唯一性）。

    日主去重后，真实组合在同一 (归属日, 方向) 内不可能两注同股（每 symbol 每归属日
    至多一条日主观点）——零模型无放回才不产生真实组合支撑空间之外的样本。
    n > len(pool) 理论不可达（真实组合注数 ≤ 池大小），回退有放回仅为防御路径，
    返回 (symbols, fallback_used)，调用方负责在返回前 log。
    """
    if n <= len(pool):
        return rng.sample(pool, n), False
    return [rng.choice(pool) for _ in range(n)], True


def simulate_random_excess(
    universe_by_day: dict[str, list[str]],
    long_counts: dict[str, int],
    short_counts: dict[str, int],
    closes: dict[str, dict[str, float]],
    entry_map: dict[str, str],
    exit_date: str,
    benchmark_closes: dict[str, float],
    n_sims: int = MC_DEFAULT_N,
    seed: int = 20261006,
) -> list[float]:
    """敞口对齐零模型：按日池随机替换选股，保持每归属日每方向注数一致（§1.9-v2）。

    「随机替换选股」指替换标的身份，非有放回抽样：同 (归属日, 方向) 内**无放回**
    （对齐日主唯一性，见 _draw_group_symbols）；跨方向 / 跨归属日独立抽取。
    RNG 共享、种子注入、复现确定。

    per-day universe（§1.9-v2「归属日当日起作用的池」）：universe_by_day 为
    归属日 → 当日起作用标的池；每组 (归属日, 方向) 抽样前取 universe_by_day[day]
    再过滤「entry 与 exit_date 双档收盘齐全」——缺 A 日但有 B 日收盘的标的不再像
    单一全局列表那样先进 A 日抽样再被逐槽剔除、扭曲等权分母。
    **universe_by_day 缺归属日键 = 行情面板装配缺陷，抛 KeyError（fail loud：
    静默缩池会偏置零模型分布）**；某日过滤后池为空 → 该日组整体跳过并 WARN
    （per-day 语义天然容忍缺股，此为极端降级，调用方应检查行情面板完整性）。

    closes: symbol → {date: close}；entry_map: 归属日 → 实际入场交易日（与真实
    组合 settle_entry_price 派生同源）；benchmark_closes: date → 基准收盘
    （T+20 槽位入场日不同、同期基准收益不同——基准按槽位逐档取，禁组合层减单一
    标量）。每槽超额 = sign × ((exit/entry − 1) − (bench_exit/bench_entry − 1))，
    bench 取 entry 与 exit_date 两档收盘（同 judgment.resolve_prediction 的基准
    口径）；组合收益 = 各槽超额等权均值。返回模拟超额列表（语义 = 随机组合的
    平均槽位超额，与真实读数的逐槽超额均值同构可比）——行情/基准缺失注逐注剔除、
    整轮无有效注的轮次跳过，实际条数可能少于 n_sims。
    """
    rng = random.Random(seed)  # noqa: S311  蒙特卡洛零模型非密码用途；种子注入保证复现
    groups: list[tuple[str, float, int]] = []  # (归属日, sign, 注数)；组间独立
    for day, n in long_counts.items():
        if int(n) > 0:
            groups.append((day, 1.0, int(n)))
    for day, n in short_counts.items():
        if int(n) > 0:
            groups.append((day, -1.0, int(n)))
    if not groups:
        return []
    if not benchmark_closes:
        logger.warning("simulate_random_excess: benchmark_closes 为空，无基准可比——返回空列表")
        return []
    # per-day 池预构建：每组抽样前按日取池，再过滤双档收盘齐全（口径见 docstring）
    day_pools: dict[str, list[str]] = {}
    for day, _, _ in groups:
        if day in day_pools:
            continue
        if day not in universe_by_day:
            raise KeyError(
                f"universe_by_day 缺归属日 {day}：每组抽样前按日取池（§1.9-v2"
                "「归属日当日起作用的池」），缺失日键属行情面板装配缺陷，拒绝静默缩池"
            )
        entry = entry_map[day]
        pool = [
            s
            for s in universe_by_day[day]
            if s in closes and closes[s].get(entry) and closes[s].get(exit_date)
        ]
        day_pools[day] = pool
        if not pool:
            logger.warning(
                "simulate_random_excess: 归属日 %s 过滤后池为空（entry=%s 与 exit=%s "
                "双档收盘无一标的齐全），该日组整体跳过",
                day,
                entry,
                exit_date,
            )
    active_groups = [(day, sign, n) for day, sign, n in groups if day_pools[day]]
    if not active_groups:
        return []
    out: list[float] = []
    fallback_days: list[str] = []
    for _ in range(n_sims):
        plans: list[tuple[str, float]] = []  # (entry, sign)，与 picks 一一对应
        picks: list[str] = []
        for day, sign, n in active_groups:
            syms, fell_back = _draw_group_symbols(rng, day_pools[day], n)
            if fell_back and day not in fallback_days:
                fallback_days.append(day)
            plans += [(entry_map[day], sign)] * n
            picks += syms
        rets = []
        bench_exit = benchmark_closes.get(exit_date)
        for (entry, sign), sym in zip(plans, picks, strict=True):
            entry_close = closes[sym].get(entry)
            exit_close = closes[sym].get(exit_date)
            bench_entry = benchmark_closes.get(entry)
            if not entry_close or not exit_close or not bench_entry or not bench_exit:
                continue  # 行情/基准缺失注剔除（与真实读数的缺失处理一致）
            rets.append(
                sign * ((exit_close / entry_close - 1.0) - (bench_exit / bench_entry - 1.0))
            )
        if not rets:
            continue
        out.append(sum(rets) / len(rets))
    for day in fallback_days:
        logger.warning(
            "simulate_random_excess: 归属日 %s 注数(%d)超过该日池大小(%d)，同组回退"
            "有放回抽样——零模型样本含真实组合支撑空间外情形，结果不可用于显著性终裁",
            day,
            max(n for d, _, n in active_groups if d == day),
            len(day_pools[day]),
        )
    return out


def excess_quantile(real_excess: float, sim_excess: list[float]) -> dict:
    """真实读数在零模型分布中的右尾定位；p 值含真实读数本身（保守）。

    quantile = 模拟中 ≤ 真实读数的比例；p_value = (严格大于真实读数的模拟数 + 1)
    / (n_sims + 1)——自身计入分母的保守 p（真实读数打败全部模拟时 p = 1/(n+1) 而非 0）。
    """
    if not sim_excess:
        return {"quantile": None, "p_value": None, "n_sims": 0}
    ranked = sorted(sim_excess)
    less_equal = sum(1 for v in ranked if v <= real_excess)
    greater = len(ranked) - less_equal
    quantile = less_equal / len(ranked)
    p_value = (greater + 1) / (len(ranked) + 1)
    return {
        "quantile": round(quantile, 4),
        "p_value": round(p_value, 4),
        "n_sims": len(ranked),
    }
