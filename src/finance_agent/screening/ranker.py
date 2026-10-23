"""L2 横截面因子打分排序纯函数（add-screening-ranker Task 1；零 IO/零随机/零 LLM）。

计划 docs/superpowers/plans/2026-10-13-add-screening-ranker.md 关键设计决策 R1–R3
（实施者不得自行改口径）：

- **R1 z-score 口径**：逐因子、在**通过 L1 的标的集合**（``passed_codes``）上做
  截面 z-score（``ddof=1``，与 ``eval_engine.cross_section_zscore`` 同口径——直接
  复用该公开别名，保证口径单一来源而非重复实现）；因子值 NaN（窗口不足/缺失）→
  该标的该因子 ``zscore=0.0``（贡献 0，且**不因缺失被剔除**——可交易性由 L1 负责）；
  截面全 NaN / std=0 / 有效样本 < 2 → 全体 zscore=0.0。非有限值（±inf）按缺失处理。
- **R2 权重口径**：``weight_i = ic_mean_i / Σ_j |ic_mean_j|``；``ic_mean_i`` = 该因子
  近 60 日 RankIC 均值，由调用方经 ``ic_stats: dict[int, tuple[float, float]]``
  传入 ``(ic_mean, icir)``（``icir`` 仅记录、不参与权重）。``ic_stats`` 缺该因子 →
  ic_mean 记 0 → 权重 0（等价于不参与，同「空序列 → (0.0, 0.0)」口径）；
  ``Σ_j |ic_mean_j| < 1e-12`` → ``ValueError("无有效 IC 信息，拒绝打分")``
  （fail loud，不做任意排序）。``contribution_i = weight_i × zscore_i``，
  ``composite_score = Σ_i contribution_i`` —— **按 ``factors`` 传入顺序累加**，
  勾稽 ``Σ contribution == composite_score``（容差 1e-9，spec 明文）由同一累加
  顺序天然成立，并被单测逐路径钉死（含全 NaN 因子 / std=0 / 负权重）。
- **R3 as_of 语义**：只用面板 ``as_of`` 当日行（DSL 算子全部过去向，无前视、
  无跨日聚合）；``as_of`` 不在面板（以 ``close`` 帧日期轴为准，六字段契约）→
  ``ValueError``（消息含可用日期范围）。

确定性（spec「打分确定性」）：同一交易日、同一因子库状态下打分完全确定——无随机数、
无 LLM、无字典序依赖；排序显式 tie-break = composite 降序 → symbol 升序。

契约注记（调用方职责与 fail-loud 边界）：

- ``factors`` 行消费 ``id``/``expression`` 两字段（``list_active_factors`` 输出；
  active 过滤由调用方完成，本模块不做二次裁决）；``factors`` 为空 →
  无 IC 信息 → ``ValueError``（无因子可打分时拒绝而非返回空榜）；
- ``passed_codes`` 须为面板内标的且互不重复（L1 输出契约）；不在面板列 / 重复 →
  ``ValueError``（fail loud：不静默造 0 分候选、不产出重复 rank）；
- ``top_n`` 默认 100、必须为正整数；截断发生在全量排序之后，rank 从 1 连续。
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import MarketPanel, cross_section_zscore, evaluate
from finance_agent.factors.library import list_ic_series

# R2：Σ|ic_mean| 下限——低于此无任何可信方向/强度信息，fail loud 而非任意排序
_MIN_ABSOLUTE_IC_TOTAL: float = 1e-12
# IC 样本门槛（add-screening-ic-bootstrap-guard）：单因子近 60 交易日 RankIC 记录
# 少于此数时不参与打分。3–5 条样本估出的 IC 均值/ICIR 由个别交易日主导，
# 权重日间摆动会让 L2 排序接近随机（生产 2026-10-21 实测 Top-100 日间重叠 1/100），
# 与精选池「连续 3 日在榜」门槛相乘即空池。10 条 ≈ 两周，是权重估计的最低可用量。
MIN_IC_SAMPLES_FOR_SCORING: int = 10
# 面板日期轴参照字段（六字段契约；as_of 合法性与横截面列集合以该帧为准）
_DATE_AXIS_FIELD = "close"


@dataclass(frozen=True)
class FactorContribution:
    """单因子对某候选综合分的贡献分解（勾稽：contribution = weight × zscore）。"""

    factor_id: int
    zscore: float
    weight: float
    contribution: float


@dataclass(frozen=True)
class Candidate:
    """L2 打分候选（契约逐字；``factor_contribs`` 顺序 = ``factors`` 传入顺序）。"""

    symbol: str
    trade_date: str
    composite_score: float
    factor_contribs: tuple[FactorContribution, ...]
    rank: int


# ── 权重与 IC 统计（R2 / R4） ───────────────────────────────────────────────


def factor_weights(
    factors: list[dict], ic_stats: dict[int, tuple[float, float]]
) -> dict[int, float]:
    """R2 权重：``ic_mean_i / Σ_j |ic_mean_j|``（结果按 ``factors`` 传入顺序）。

    ``ic_stats`` 缺该因子 → ``(0.0, 0.0)``（权重 0，等价于不参与）；提供的
    IC 均值须有限（NaN/±inf → ``ValueError``，不让坏值经分母扩散成坏权重）；
    ``Σ|ic_mean| < 1e-12``（含空因子列表）→ ``ValueError("无有效 IC 信息，拒绝打分")``。
    """
    ic_means: dict[int, float] = {}
    for row in factors:
        factor_id = int(row["id"])
        raw_mean, _icir = ic_stats.get(factor_id, (0.0, 0.0))
        ic_mean = float(raw_mean)
        if not math.isfinite(ic_mean):
            raise ValueError(f"因子 {factor_id} 的 IC 均值非有限: {raw_mean!r}")
        ic_means[factor_id] = ic_mean
    total = math.fsum([abs(ic_mean) for ic_mean in ic_means.values()])
    if total < _MIN_ABSOLUTE_IC_TOTAL:
        raise ValueError("无有效 IC 信息，拒绝打分")
    return {factor_id: ic_mean / total for factor_id, ic_mean in ic_means.items()}


def recent_ic_stats(
    factors: list[dict], *, window: int = 60, db_path: str | Path | None = None
) -> dict[int, tuple[float, float]]:
    """R4：逐因子取库内近 ``window`` 个交易日 RankIC → ``(ic_mean, icir)``。

    ICIR = mean/std（``ddof=1``）；有效样本 < 2 或常量序列（``max == min``）→ ICIR 记 0.0
    （与 ``validate._ic_stats`` 同口径，不产生 inf）；无记录 → ``(0.0, 0.0)``
    （该因子权重 0，等价于不参与——见 ``factor_weights``）。``window`` 必须为正整数。
    """
    if window < 1:
        raise ValueError(f"window 必须是正整数: {window!r}")
    stats: dict[int, tuple[float, float]] = {}
    for row in factors:
        factor_id = int(row["id"])
        values = [
            float(value)
            for _trade_date, value in list_ic_series(factor_id, limit=window, db_path=db_path)
        ]
        stats[factor_id] = _mean_icir(values)
    return stats


def _mean_icir(values: list[float]) -> tuple[float, float]:
    """``(mean, ICIR)`` 手算口径：有效样本 < 2 / 常量序列（``max == min``）→ ICIR 记 0.0。

    常量守选用 ``min == max``（与 ``validate._ic_stats`` 同口径）而非 ``std == 0.0``：
    DB 往返后的 0.1 序列其 mean 非逐位等于样本值（``fsum([0.1]*3) / 3`` 与 0.1 差
    一个 ulp），按 std 判定会漏检并算出 ~5.9e15 量级的假 ICIR。
    """
    count = len(values)
    if count == 0:
        return 0.0, 0.0
    mean = math.fsum(values) / count
    if count < 2:
        return mean, 0.0
    if min(values) == max(values):
        return mean, 0.0
    variance = math.fsum([(value - mean) ** 2 for value in values]) / (count - 1)
    std = math.sqrt(variance)
    if std == 0.0:
        return mean, 0.0
    return mean, mean / std


# ── 打分与排序（R1 / R3） ───────────────────────────────────────────────────


def rank_universe(
    *,
    panel: MarketPanel,
    as_of: str,
    passed_codes: Sequence[str],
    factors: list[dict],
    ic_stats: dict[int, tuple[float, float]],
    top_n: int = 100,
) -> list[Candidate]:
    """L2 打分入口：L1 通过标的 × 因子库因子 → Top-N ``Candidate``（R1–R3）。

    逐因子在 ``passed_codes`` 集合上取 ``as_of`` 当日值做截面 z-score（R1）→ 按
    ``factor_weights`` 加权合成（R2）→ composite 降序、symbol 升序排序（确定性
    tie-break）→ 截取 ``top_n``，rank 从 1 连续。``Σ contribution ==
    composite_score``（1e-9）在每条候选上成立（同一累加顺序）。

    fail loud：``top_n < 1`` / 面板缺 ``close`` / ``as_of`` 不在面板日期 /
    ``passed_codes`` 重复或不在面板列 / ``factors`` 为空 / ``Σ|ic_mean| < 1e-12``。
    """
    if top_n < 1:
        raise ValueError(f"top_n 必须是正整数: {top_n!r}")
    close_frame = panel.fields.get(_DATE_AXIS_FIELD)
    if close_frame is None:
        raise ValueError(f"面板缺少 {_DATE_AXIS_FIELD!r} 字段（as_of 合法性以该帧日期轴为准）")
    dates = close_frame.index
    if as_of not in dates:
        available = "（空面板）" if len(dates) == 0 else f"{dates[0]}..{dates[-1]}"
        raise ValueError(f"as_of {as_of!r} 不在面板日期内（可用范围 {available}）")
    symbols = [str(code) for code in passed_codes]
    duplicated = sorted(symbol for symbol, count in Counter(symbols).items() if count > 1)
    if duplicated:
        raise ValueError(f"passed_codes 重复: {duplicated}（每标的至多一条候选）")
    missing = [symbol for symbol in symbols if symbol not in close_frame.columns]
    if missing:
        raise ValueError(f"passed_codes 不在面板列: {sorted(missing)}")

    weights = factor_weights(factors, ic_stats)
    factor_ids: list[int] = []
    zscore_maps: list[dict[str, float]] = []
    for row in factors:
        factor_id = int(row["id"])
        frame = evaluate(parse(str(row["expression"])), panel)
        cross_section = frame.loc[as_of]
        if not isinstance(cross_section, pd.Series):  # 面板日期 index 重复 → fail loud
            raise ValueError(
                f"面板日期 {as_of!r} 非唯一行（因子 {factor_id} 的求值帧破坏面板不变量）"
            )
        factor_ids.append(factor_id)
        zscore_maps.append(_cross_section_zscores(cross_section.loc[symbols]))

    scored: list[Candidate] = []
    for symbol in symbols:
        contributions: list[FactorContribution] = []
        composite = 0.0
        for factor_id, zscore_map in zip(factor_ids, zscore_maps, strict=True):
            weight = weights[factor_id]
            zscore = zscore_map[symbol]
            contribution = weight * zscore
            contributions.append(
                FactorContribution(
                    factor_id=factor_id,
                    zscore=zscore,
                    weight=weight,
                    contribution=contribution,
                )
            )
            composite += contribution
        scored.append(
            Candidate(
                symbol=symbol,
                trade_date=as_of,
                composite_score=composite,
                factor_contribs=tuple(contributions),
                rank=0,  # 排序后回填
            )
        )
    ordered = sorted(scored, key=lambda candidate: (-candidate.composite_score, candidate.symbol))
    return [
        replace(candidate, rank=rank) for rank, candidate in enumerate(ordered[:top_n], start=1)
    ]


def _cross_section_zscores(values: pd.Series) -> dict[str, float]:
    """R1：单因子单日横截面 z-score（NaN/±inf → 0.0；全 NaN/std=0/样本<2 → 全 0）。

    口径经 ``cross_section_zscore``（eval_engine 公开别名，ddof=1）计算，再对
    NaN 结果（缺失单元、std=0、样本不足）统一映射为 0.0；非有限输入先掩码为
    NaN（坏值不扩散为 inf 分值）。
    """
    numeric = values.astype("float64")
    finite = numeric.where(np.isfinite(numeric))
    zscores = cross_section_zscore(finite.to_frame().T).iloc[0].fillna(0.0)
    return {str(symbol): float(value) for symbol, value in zscores.items()}


# ── 降级通道（add-screening-empty-library-fallback） ─────────────────────────


def has_scorable_factors(factors: Sequence[dict], ic_stats: dict[int, tuple[float, float]]) -> bool:
    """L2 是否可打分：存在因子且 ``Σ|IC 均值| ≥ 阈值``（与 ``factor_weights`` 同一拒绝条件）。

    降级通道触发谓词（pipeline 消费）：``False`` = 无 active 因子或全部因子无有效
    IC 记录——此时 ``rank_universe`` 必拒（fail loud 语义不变），调用方改走显式
    降级；非有限 IC 均值（数据错误）返回 ``True``，交回打分路径原样抛错
    （降级 MUST NOT 吞掉数据错误）。

    注意：本谓词只回答「有没有非零 IC」，**不回答「样本够不够」**；后者由
    ``ic_sample_counts`` + ``select_scorable_factors`` 在调用打分前过滤
    （add-screening-ic-bootstrap-guard）。
    """
    total = 0.0
    for row in factors:
        raw_mean, _icir = ic_stats.get(int(row["id"]), (0.0, 0.0))
        ic_mean = float(raw_mean)
        if not math.isfinite(ic_mean):
            return True  # 数据错误：交回打分路径 fail loud（不因坏值触发降级）
        total += abs(ic_mean)
    return total >= _MIN_ABSOLUTE_IC_TOTAL


def ic_sample_counts(
    factors: list[dict], *, window: int = 60, db_path: str | Path | None = None
) -> dict[int, int]:
    """逐因子近 ``window`` 个交易日的 RankIC 记录条数（无记录 → 0）。

    与 ``recent_ic_stats`` 同一取数口径（``list_ic_series``），只计数不聚合——
    供 ``select_scorable_factors`` 判断样本是否足以估计权重。
    """
    if window < 1:
        raise ValueError(f"window 必须是正整数: {window!r}")
    counts: dict[int, int] = {}
    for row in factors:
        factor_id = int(row["id"])
        counts[factor_id] = len(list(list_ic_series(factor_id, limit=window, db_path=db_path)))
    return counts


def select_scorable_factors(
    factors: list[dict],
    ic_counts: dict[int, int],
    *,
    min_samples: int = MIN_IC_SAMPLES_FOR_SCORING,
) -> tuple[list[dict], list[dict]]:
    """按 IC 样本门槛切分因子：``(可打分, 被排除)``，两表均保持 ``factors`` 传入顺序。

    ``min_samples`` 必须为正整数（``>=`` 语义：恰好等于门槛纳入）。被排除因子
    由调用方披露（日志 / 批次归因），MUST NOT 静默参与打分。
    """
    if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples < 1:
        raise ValueError(f"min_samples 必须是正整数: {min_samples!r}")
    kept: list[dict] = []
    excluded: list[dict] = []
    for row in factors:
        if int(ic_counts.get(int(row["id"]), 0)) >= min_samples:
            kept.append(row)
        else:
            excluded.append(row)
    return kept, excluded


def liquidity_fallback_rank(
    *,
    amounts_by_code: Mapping[str, Sequence[float]],
    passed_codes: Sequence[str],
    as_of: str,
    top_n: int = 100,
) -> list[Candidate]:
    """降级排序：近 N 日日均成交额降序（同额 symbol 升序）→ Top-N ``Candidate``。

    无可打分因子时的过渡排序（spec「无可打分因子降级通道」）：``composite_score``
    = 日均成交额（元，语义为过渡排序键而非因子综合分）；``factor_contribs = ()``
    （无因子参与）；窗口含非有限值/空窗口 → 剔除（fail-closed 不猜，与 L1
    ``DATA_MISSING`` 同纪律）；全确定性（两次调用逐项相等）。
    """
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
        raise ValueError(f"top_n 必须是正整数: {top_n!r}")
    counts = Counter(passed_codes)
    duplicated = sorted(code for code, count in counts.items() if count > 1)
    if duplicated:
        raise ValueError(f"passed_codes 重复: {duplicated}")
    scored: list[tuple[str, float]] = []
    for code in passed_codes:
        window = amounts_by_code.get(code)
        if not window:
            continue
        values = [float(value) for value in window]
        if not all(math.isfinite(value) for value in values):
            continue
        scored.append((code, math.fsum(values) / len(values)))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return [
        Candidate(
            symbol=code,
            trade_date=as_of,
            composite_score=score,
            factor_contribs=(),
            rank=rank,
        )
        for rank, (code, score) in enumerate(scored[:top_n], start=1)
    ]
