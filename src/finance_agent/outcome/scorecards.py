"""策略族成绩单（delta add-strategy-family-scorecards）：注册表驱动 + 四槽计算。

口径权威：``docs/evals/metrics.md`` §1.13（2026-10-18 登记）与变更 design.md——
口径变更先改登记再动本模块。分层：四槽计算为纯函数（无业务 IO，可单测）；
族数据读取与组装见同模块下半部（调用期读 env，只读不写任何状态）。
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any, NamedTuple

from finance_agent.execution.config import load_matching_config
from finance_agent.execution.matching import MatchingConfig
from finance_agent.execution.store import list_paper_equity, list_positions

logger = logging.getLogger(__name__)

# ── 口径常量（design.md §已知边界：具名常量集中定义；族级门槛/阈值随注册表声明）──
SCORECARD_NOTIONAL = 100_000.0  # 成本估算名义仓位（与撮合配置参数同源换算）
CAPACITY_PARTICIPATION = 0.01  # 参与率上限 1%
CAPACITY_SYMBOL_LIMIT = 20  # 容量口径取样标的数（最近 N 个）
CAPACITY_LOOKBACK_DAYS = 40  # 快照读取窗口（自然日；覆盖 20 交易日）
CAPACITY_ADV_DAYS = 20  # 日均成交额窗口（交易日）
MIN_SAMPLES = 10  # 期望值样本门槛（各族注册项 min_samples 的默认值出处）
MIN_HISTORY_DAYS = 20  # 最大回撤结算日门槛
PORTFOLIO_TICKER = "<portfolio>"  # 执行线样本标的（组合级，无单票）

# 槽位缺数据原因码（spec：null + 原因码，MUST NOT 折算 0 或静默省略）
REASON_NO_SAMPLES = "no_samples"
REASON_INSUFFICIENT_SAMPLE = "insufficient_sample"
REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_NO_LIQUIDITY_DATA = "no_liquidity_data"
REASON_NO_LOADER = "no_loader"  # 族声明的数据源未接线装载器（空壳语义，不抛错）


class FamilySpec(NamedTuple):
    """族注册项：逐族声明 spec 要求的全部口径（数据源/费后处理/门槛/阈值/注释）。

    ``data_source`` 经 ``_LOADERS`` 映射到装载器；``cost_bearing=True`` 表示虚拟
    结算族（净 = 毛 − 往返成本估算率），``False`` 表示毛=净（已含费，不二次扣减）。
    """

    family_id: str
    name: str
    data_source: str
    cost_bearing: bool
    min_samples: int
    extreme_threshold: float
    caliber_note: str


class Sample(NamedTuple):
    """族样本：标的、结算日（前 10 字符即日期）、收益（该族口径）。"""

    ticker: str
    date: str
    ret: float


class FamilyContract(NamedTuple):
    """族契约：展示用胜率（数据层按各族口径算好）+ 随行口径注释。"""

    win_rate: float | None
    caliber_note: str


class FamilyReadings(NamedTuple):
    """数据源装载结果：样本 + 容量取数标的 + 展示胜率。"""

    samples: list[Sample]
    capacity_symbols: list[str]
    win_rate: float | None


class FamilyDbPaths(NamedTuple):
    """族装载入参：会话/执行/事件库路径（缺省由数据读取层调用期读 env）。"""

    sessions_db_path: str | Path | None = None
    execution_db_path: str | Path | None = None
    events_db_path: str | Path | None = None


FAMILY_REGISTRY: tuple[FamilySpec, ...] = (
    FamilySpec(
        "predictions",
        "观点线",
        data_source="predictions",
        cost_bearing=True,  # 虚拟结算：净 = 毛 − 往返成本估算率（§1.13）
        min_samples=MIN_SAMPLES,
        extreme_threshold=-0.15,
        caliber_note=(
            "毛=已判定观点 raw_return 均值；净=毛−往返成本估算率（参数同源撮合配置，见 §1.13）；"
            f"回撤=结算日等权复利；容量=最近 {CAPACITY_SYMBOL_LIMIT} 个样本标的近 "
            f"{CAPACITY_ADV_DAYS} 交易日日均成交额×参与率 {CAPACITY_PARTICIPATION:.0%}；"
            "极端样本阈值 -15%；胜率仅展示（同 prediction_stats 分母）"
        ),
    ),
    FamilySpec(
        "execution",
        "执行线",
        data_source="paper_equity",
        cost_bearing=False,  # 净值已含撮合费：毛=净，不二次扣减
        min_samples=MIN_SAMPLES,
        extreme_threshold=-0.05,
        caliber_note=(
            "毛=净=组合级日收益均值（paper_equity 净值派生，已含撮合费，不二次扣减）；"
            f"回撤=净值日收益同一 MDD 函数；容量=当前持仓近 {CAPACITY_ADV_DAYS} 交易日日均成交额"
            f"×参与率 {CAPACITY_PARTICIPATION:.0%}；极端样本阈值 -5%（组合级）；胜率不适用"
        ),
    ),
    # add-event-driven-lane：事件线第三族（独立于观点线 T+20 窗口，家族隔离见口径注释）
    FamilySpec(
        "event",
        "事件线",
        data_source="events",
        cost_bearing=True,  # 虚拟结算族：净 = 毛 − 往返成本估算率（同 predictions 口径）
        min_samples=MIN_SAMPLES,
        extreme_threshold=-0.15,
        caliber_note=(
            "毛=已结算事件记账 raw_return 均值（入场=公告日次一交易日开盘 hfq，"
            "horizon 默认 5 交易日，结算=结算日收盘）；净=毛−往返成本估算率（见 §1.13）；"
            f"回撤=结算日等权复利；容量=最近 {CAPACITY_SYMBOL_LIMIT} 个样本标的近 "
            f"{CAPACITY_ADV_DAYS} 交易日日均成交额×参与率 {CAPACITY_PARTICIPATION:.0%}；"
            "极端样本阈值 -15%；胜率不适用（事件线无胜率口径）；"
            "家族隔离：样本只取事件库已结算记账行，独立于观点线 T+20 统计"
        ),
    ),
)


def estimate_round_trip_cost_rate(config: MatchingConfig | None = None) -> float:
    """往返成本估算率（design.md §口径细则）：佣金双边 + 印花税 + 中档滑点双边。

    参数同源 ``config/matching_engine.json``（缺文件回落内置默认），禁止复制常量；
    默认参数下 ≈ 0.30%（0.0005 + 0.0005 + 0.002）。
    """
    cfg = config if config is not None else load_matching_config()
    commission = max(cfg.commission_rate * SCORECARD_NOTIONAL, cfg.min_commission) / (
        SCORECARD_NOTIONAL
    )
    tiers = sorted(cfg.slippage_tiers, key=lambda tier: tier.quantile_max)
    slippage_mid = tiers[len(tiers) // 2].slippage_rate  # 三档配置下 index 1 = 0.001
    return commission * 2 + cfg.stamp_tax_rate + slippage_mid * 2


def max_drawdown_from_returns(daily_returns: Sequence[float]) -> float:
    """复利净值最大回撤：``MDD = min(nav/峰值 − 1)``（等价 design 的 ``1 − max(nav/峰值)``）。

    输出 ≤ 0（无回撤 = 0.0）；取符号形式而非 ``1 − nav/峰值``——后者恒 ≥ 0，
    再与 0 取 min 恒得 0（brief 骨架的注释方向与测试的 −0.412 冲突，以测试为准）。
    """
    nav = 1.0
    peak = 1.0
    mdd = 0.0
    for ret in daily_returns:
        nav *= 1.0 + ret
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1.0)
    return mdd


def aggregate_daily_returns(samples: Sequence[Sample]) -> list[float]:
    """按日聚合样本等权收益（日期取前 10 字符），返回升序日收益序列。"""
    by_date: dict[str, list[float]] = {}
    for sample in samples:
        by_date.setdefault(sample.date[:10], []).append(sample.ret)
    return [sum(rets) / len(rets) for _, rets in sorted(by_date.items())]


def _capacity_slot(capacity_inputs: dict[str, float | None] | None) -> dict[str, Any] | None:
    """容量槽主体：最小值 × 参与率 = 建议资金上限；全缺 → None（原因码由调用方给）。"""
    if not capacity_inputs:
        return None
    usable = [adv for adv in capacity_inputs.values() if adv is not None and adv > 0]
    if not usable:
        return None
    min_adv = min(usable)
    excluded = len(capacity_inputs) - len(usable)
    return {
        "suggested_capital": round(min_adv * CAPACITY_PARTICIPATION, 2),
        "min_adv20": min_adv,
        "excluded": excluded,
    }


def build_family_payload(
    spec: FamilySpec,
    samples: Sequence[Sample],
    *,
    contract: FamilyContract,
    capacity_inputs: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """单族四槽载荷（纯函数）：四槽 → 胜率 → 口径注释，键序即 spec 展示位次序。

    ``capacity_inputs``：请求标的 → 近 20 交易日日均成交额；值 ``None``/非正表示该
    标的数据缺失（计入 ``capacity["excluded"]``）。``None`` 或空 dict＝无任何流动性
    证据 → 容量槽 null + ``no_liquidity_data``（全缺不折算 0）。样本门槛与费后处理
    均取自注册项（``spec.min_samples`` / ``spec.cost_bearing``）。
    """
    payload: dict[str, Any] = {}

    # ① 期望值：毛/净双值 + 样本量；< 门槛一律 null + 原因码
    if not samples:
        payload["expectancy"] = None
        payload["expectancy_reason"] = REASON_NO_SAMPLES
    elif len(samples) < spec.min_samples:
        payload["expectancy"] = None
        payload["expectancy_reason"] = REASON_INSUFFICIENT_SAMPLE
    else:
        gross = sum(sample.ret for sample in samples) / len(samples)
        cost_rate = estimate_round_trip_cost_rate() if spec.cost_bearing else 0.0
        payload["expectancy"] = {
            "gross": round(gross, 4),
            "net": round(gross - cost_rate, 4),
            "n": len(samples),
            "insufficient": False,
        }

    # ② 最大回撤：族级日度等权序列 → 同一 MDD 函数；结算日 < 门槛 → null + 原因码
    daily_returns = aggregate_daily_returns(samples)
    if len(daily_returns) < MIN_HISTORY_DAYS:
        payload["max_drawdown"] = None
        payload["max_drawdown_reason"] = REASON_INSUFFICIENT_HISTORY
    else:
        payload["max_drawdown"] = {
            "value": max_drawdown_from_returns(daily_returns),
            "n_days": len(daily_returns),
        }

    # ③ 容量提示：缺数据标的剔除计数，全缺 → null + 原因码
    capacity = _capacity_slot(capacity_inputs)
    if capacity is None:
        payload["capacity"] = None
        payload["capacity_reason"] = REASON_NO_LIQUIDITY_DATA
    else:
        payload["capacity"] = capacity

    # ④ 极端样本明细（族阈值可配置）
    payload["extreme_samples"] = [
        {"ticker": sample.ticker, "date": sample.date[:10], "ret": sample.ret}
        for sample in samples
        if sample.ret <= spec.extreme_threshold
    ]

    # ⑤ 胜率：展示位（四槽之后）；MUST NOT 进入自动处置/门禁/告警
    payload["win_rate"] = contract.win_rate
    payload["caliber_note"] = contract.caliber_note
    return payload


# ── 族数据读取（只读；路径入参优先，缺省调用期读 env）──


def load_prediction_samples(*, db_path: str | Path | None = None) -> list[Sample]:
    """观点线样本：已判定且 ``raw_return`` 非空的行（与 ``prediction_stats`` 同分母）。

    ``avoidance``/``unresolvable``/``duplicate_of_day``/``open`` 不进样本；结算日取
    ``resolved_at`` 前 10 字符；升序（同日按 symbol 稳定）。空库/老库无表 → 空样本
    如实返回，不抛错（API 只读面语义，缺数据由槽位 null + 原因码呈现）。
    """
    from finance_agent.outcome.track_record.model import _connect, _default_db_path

    path = Path(db_path) if db_path else _default_db_path()
    if not path.exists():
        return []
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT symbol, resolved_at, raw_return FROM predictions"
            " WHERE status IN ('resolved_win','resolved_loss','resolved_neutral')"
            " AND raw_return IS NOT NULL AND resolved_at IS NOT NULL"
            " ORDER BY resolved_at ASC, symbol ASC"
        ).fetchall()
    except sqlite3.OperationalError:  # 老库无 predictions 表：按无样本如实返回
        logger.warning("predictions 表不可读，观点线样本按空处理: %s", path)
        return []
    finally:
        conn.close()
    return [
        Sample(str(row["symbol"]), str(row["resolved_at"])[:10], float(row["raw_return"]))
        for row in rows
    ]


def load_execution_samples(*, db_path: str | Path | None = None) -> list[Sample]:
    """执行线样本：``paper_equity`` 相邻净值点的组合级日收益，ticker 记 ``"<portfolio>"``。

    首点无前值不成样本；``equity`` 非正/不可解析的行跳过并计数，同时重设基准——缺口
    后的一天不跨缺口连乘（避免把多日收益记成单日收益）。
    """
    rows = list_paper_equity(db_path)
    samples: list[Sample] = []
    previous: float | None = None
    skipped = 0
    for row in rows:
        equity = _finite_number(row.get("equity"))
        if equity is None or equity <= 0:
            skipped += 1
            previous = None
            continue
        if previous is not None:
            samples.append(
                Sample(PORTFOLIO_TICKER, str(row["trade_date"])[:10], equity / previous - 1.0)
            )
        previous = equity
    if skipped:
        logger.warning("paper_equity 净值非正行已跳过: %d 行（不参与日收益计算）", skipped)
    return samples


def _prediction_win_rate(db_path: str | Path | None) -> float | None:
    """观点线展示胜率：复用 ``prediction_stats`` 口径（long/short 已判定分母），仅展示。

    MUST NOT 进入自动处置/门禁/告警（spec 胜率定位）；空库/无表 → None。
    """
    from finance_agent.outcome.track_record.model import _default_db_path, prediction_stats

    path = Path(db_path) if db_path else _default_db_path()
    if not path.exists():
        return None
    try:
        value = prediction_stats(db_path=path).get("win_rate")
    except sqlite3.OperationalError:  # 老库无 predictions 表
        logger.warning("predictions 表不可读，观点线胜率按 null 返回: %s", path)
        return None
    return float(value) if value is not None else None


def _finite_number(value: Any) -> float | None:
    """数值有效性：None/不可转/非有限 → None（缺失，MUST NOT 折算 0）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _snapshot_code(symbol: str) -> str:
    """``'600519.SH'`` → ``'600519'``（快照 code 为 6 位裸码；同 paper._snapshot_code 口径）。"""
    return str(symbol).split(".")[0]


def _recent_symbols(samples: Sequence[Sample], limit: int = CAPACITY_SYMBOL_LIMIT) -> list[str]:
    """最近判定样本的标的（去重、最近优先，截断到 ``limit`` 个）。"""
    symbols: list[str] = []
    for sample in reversed(samples):  # 样本次序为升序，反转即最近优先
        if sample.ticker not in symbols:
            symbols.append(sample.ticker)
            if len(symbols) >= limit:
                break
    return symbols


def _capacity_inputs(
    symbols: Sequence[str], *, snapshot_db_path: str | Path | None = None
) -> dict[str, float | None]:
    """容量槽输入：标的 → 近 20 交易日日均成交额（快照 ``amount`` 尾部均值）。

    取数整体捕获：快照库空/不新鲜/损坏（``SnapshotError`` / ``sqlite3.Error`` /
    ``OSError``）或区间无行一律按缺数据处理——容量是提示槽，缺数据由载荷以
    null + 原因码呈现，MUST NOT 抛给调用方。不可用标的（无行/成交额非正）记
    ``None``，由 ``build_family_payload`` 计入 ``excluded``。
    """
    from finance_agent.data.snapshot import SnapshotError, latest_snapshot_date, read_bars

    requested = list(dict.fromkeys(symbols))  # 去重保序
    if not requested:
        return {}
    try:
        latest = latest_snapshot_date(snapshot_db_path)
        if latest is None:
            return dict.fromkeys(requested)
        start = (date.fromisoformat(latest) - timedelta(days=CAPACITY_LOOKBACK_DAYS)).isoformat()
        codes = sorted({_snapshot_code(symbol) for symbol in requested})
        frame = read_bars(codes, start, latest, as_of=latest, db_path=snapshot_db_path)
    except (SnapshotError, sqlite3.Error, OSError) as exc:  # 含 SnapshotEmptyError/NotFreshError
        logger.warning("快照读取失败，容量槽按缺数据处理: %s", exc)
        return dict.fromkeys(requested)
    buckets: dict[str, list[float]] = {}
    for record in frame.to_dict("records"):
        amount = _finite_number(record.get("amount"))
        if amount is None or amount <= 0:
            continue
        buckets.setdefault(str(record["code"]), []).append(amount)
    advances: dict[str, float] = {}
    for code, values in buckets.items():
        tail = values[-CAPACITY_ADV_DAYS:]
        advances[code] = sum(tail) / len(tail)
    return {symbol: advances.get(_snapshot_code(symbol)) for symbol in requested}


def _position_symbols(db_path: str | Path | None) -> list[str]:
    """当前持仓标的（执行线容量口径；symbol 升序，截断到 ``CAPACITY_SYMBOL_LIMIT``）。"""
    rows = list_positions(db_path)
    return [str(row["symbol"]) for row in rows[:CAPACITY_SYMBOL_LIMIT]]


def _read_predictions_family(paths: FamilyDbPaths) -> FamilyReadings:
    """观点线装载（数据源 ``predictions``）：样本 + 最近样本标的 + 展示胜率。"""
    samples = load_prediction_samples(db_path=paths.sessions_db_path)
    return FamilyReadings(
        samples, _recent_symbols(samples), _prediction_win_rate(paths.sessions_db_path)
    )


def _read_execution_family(paths: FamilyDbPaths) -> FamilyReadings:
    """执行线装载（数据源 ``paper_equity``）：净值派生样本 + 当前持仓；无胜率口径。"""
    return FamilyReadings(
        load_execution_samples(db_path=paths.execution_db_path),
        _position_symbols(paths.execution_db_path),
        None,
    )


def load_event_samples(*, db_path: str | Path | None = None) -> list[Sample]:
    """事件线样本：已结算 ``event_journal`` 行（``raw_return`` 非空、``settle_date`` 非空）。

    与观点线样本正交（家族隔离）：只读事件库，不触 ``predictions``；空库/库不存在 →
    空样本如实返回（缺数据由槽位 null + 原因码呈现，不抛错）。

    **排序契约**：SQL 侧降序截断 = 取「近期」集合（超读取上限丢最旧），返回前按
    ``settle_date`` 升序恢复下游约定——``_recent_symbols`` 依赖升序样本取最近标的
    （容量槽「最近 N 个样本标的」口径），降序直传会让容量槽取到**最旧**标的。
    """
    from finance_agent.outcome.events.model import _default_db_path as events_default_path
    from finance_agent.outcome.events.model import list_settled_journal

    path = Path(db_path) if db_path else events_default_path()
    if not path.exists():
        return []
    samples: list[Sample] = []
    for row in list_settled_journal(db_path=path):
        ret = _finite_number(row.get("raw_return"))
        settle_date = str(row.get("settle_date") or "")
        if ret is None or not settle_date:
            continue  # 未结算/读数缺失行不进样本（不折算 0）
        samples.append(Sample(str(row.get("symbol") or ""), settle_date[:10], ret))
    samples.sort(key=lambda sample: sample.date)
    return samples


def _read_event_family(paths: FamilyDbPaths) -> FamilyReadings:
    """事件线装载（数据源 ``events``）：已结算样本 + 最近样本标的；无胜率口径。"""
    samples = load_event_samples(db_path=paths.events_db_path)
    return FamilyReadings(samples, _recent_symbols(samples), None)


# 数据源 → 装载器（族经 spec.data_source 接线；未接线 → 四槽 no_loader，不抛错）
_LOADERS: dict[str, Callable[[FamilyDbPaths], FamilyReadings]] = {
    "predictions": _read_predictions_family,
    "paper_equity": _read_execution_family,
    "events": _read_event_family,
}


def get_scorecards(
    *,
    sessions_db_path: str | Path | None = None,
    execution_db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
    events_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """注册表全族四槽成绩单（只读；族序 = 注册表序，未注册数据源不出现在输出中）。

    遍历 ``FAMILY_REGISTRY``，每族按 ``spec.data_source`` 经 ``_LOADERS`` 取数；
    未接线数据源 → 该族四槽 null + ``no_loader``（空壳语义，不抛错）。各库路径缺省
    由数据读取层调用期读 env（``SESSIONS_DB_PATH`` / ``EXECUTION_DB_PATH`` /
    ``SNAPSHOT_DB_PATH`` / ``EVENTS_DB_PATH``）。任一槽缺数据以 null + 原因码如实返回，
    MUST NOT 整体报错。
    """
    reading_paths = FamilyDbPaths(sessions_db_path, execution_db_path, events_db_path)
    families: list[dict[str, Any]] = []
    for spec in FAMILY_REGISTRY:
        loader = _LOADERS.get(spec.data_source)
        if loader is None:
            logger.warning(
                "族 %s 数据源 %s 无装载器，四槽以 %s 如实返回",
                spec.family_id,
                spec.data_source,
                REASON_NO_LOADER,
            )
            readings = FamilyReadings(samples=[], capacity_symbols=[], win_rate=None)
        else:
            readings = loader(reading_paths)
        payload = build_family_payload(
            spec,
            readings.samples,
            contract=FamilyContract(win_rate=readings.win_rate, caliber_note=spec.caliber_note),
            capacity_inputs=_capacity_inputs(
                readings.capacity_symbols, snapshot_db_path=snapshot_db_path
            ),
        )
        if loader is None:  # 未接线数据源：在空壳之上把槽位原因码改写为 no_loader
            for reason_key in ("expectancy_reason", "max_drawdown_reason", "capacity_reason"):
                payload[reason_key] = REASON_NO_LOADER
        families.append(
            {
                "family_id": spec.family_id,
                "name": spec.name,
                "as_of": max((sample.date for sample in readings.samples), default=None),
                **payload,
            }
        )
    return {"families": families}
