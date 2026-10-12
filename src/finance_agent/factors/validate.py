"""五道闸准入管线（add-factor-library-and-monitor T3–T5；本模块已全部实装）。

闸口定义（逐字，Global Constraints）：

- **闸 0（前置，非五道闸）结构查重**：``find_structural_duplicate``（E7；
  ``candidate``/``active``/``degraded`` 参与，``retired`` 可重提）；
  命中 → 原因码 ``STRUCTURAL_DUPLICATE``；
- **闸 1 可计算性**：全样本面板缺失率 ≤ ``MAX_MISSING_RATE`` → 否则 ``LOW_COVERAGE``；
- **闸 2 单因子检验**：``|ICIR| ≥ MIN_ABS_ICIR`` 且 ``|IC 均值| > 0``（E3；
  显著性由闸 3 承担）→ 否则 ``WEAK_ICIR``；
- **闸 3 显著性**：同批次候选置换检验 + BH-FDR（q=``BH_FDR_Q``，``seed`` 可复现）
  → 否则 ``NOT_SIGNIFICANT``；
- **闸 4 池级增量**：与池内任一因子 ``|IC 相关| > MAX_IC_CORRELATION`` →
  ``REDUNDANT_WITH_POOL``；池 + 候选组合 IR 增量 ≤ 0 → ``NO_POOL_INCREMENT``；
- **闸 5 样本外复核**：训验切分（``TRAIN_FRACTION``，切点不跨日）验证段
  ``|ICIR|`` 达标且符号一致 → 否则 ``OOS_FAILED``。

实现沿革：T3 落闸①② + 编排骨架（③④⑤ 曾为占位桩），T4 替换闸③（置换 + BH-FDR），
T5 替换闸④⑤（池级增量 + 样本外）——占位桩已全部删除，五道闸均为真实统计判定。

编排契约（各任务替换实现时不得破坏）：

- ``gates`` 记录该候选**实际执行过**的闸口结果（按执行顺序，含通过项）；
  ``rejected_gate`` = 被拒闸口编号（0–5），全部通过者为 ``None``；
- ``status``：入库成功 = ``"active"``（同批保种使其立即退役则 ``"retired"``）；
  任一闸口拒收 = ``"rejected"``（``factor_id=None``）；
- 阶段 A（逐候选）：结构查重 → ``evaluate`` → 闸① → ``rank_ic_series`` → 闸②；
  阶段 B（同批次一次）：闸③（``check_significance`` 按下标 = 候选表达式为
  存活候选返回结果）；阶段 C（逐候选）：池 = 调用时库内 active（本批次先入库者
  已在池内）→ 闸④ → 闸⑤ → **入库前结构复检**（批次内先前入库者与本候选同构 →
  ``rejected_gate=0``，``gates`` 末尾追加第二次 gate=0 记录）→ ``add_factor``
  （candidate）+ ``set_status(active)`` + 闸④ detail 的 ``increment`` 写
  ``pool_increment`` + ``apply_bucket_quota``（E8）；
- ``check_pool_increment`` **必须在 ``GateResult.detail`` 给出 ``increment: float``**
  （池级 IR 增量）——E8 保种排序依赖 ``factors.pool_increment``；
- 候选表达式经 ``dsl.parse``（默认复杂度硬约束）解析；解析/复杂度失败直接抛错
  （提案路径已保证合法，编排不吞错）；
- 面板须含 ``close`` 字段（``forward_returns`` 输入；面板六字段契约）。
"""

from __future__ import annotations

import enum
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from finance_agent.factors.constraints import (
    BH_FDR_Q,
    MAX_IC_CORRELATION,
    MAX_MISSING_RATE,
    MIN_ABS_ICIR,
    PERMUTATION_N,
    TRAIN_FRACTION,
)
from finance_agent.factors.dsl import parse, serialize
from finance_agent.factors.eval_engine import (
    MarketPanel,
    cross_section_zscore,
    evaluate,
    forward_returns,
    rank_ic_series,
)
from finance_agent.factors.library import (
    add_factor,
    apply_bucket_quota,
    find_structural_duplicate,
    list_active_factors,
    list_ic_series,
    set_pool_increment,
    set_status,
)

# 闸编号（编排契约；0 = 结构查重前置闸，1–5 = 五道闸）
GATE_DEDUP = 0
GATE_COMPUTABILITY = 1
GATE_SINGLE_FACTOR = 2
GATE_SIGNIFICANCE = 3
GATE_POOL_INCREMENT = 4
GATE_OUT_OF_SAMPLE = 5

# 编排结果状态（逐字；与库状态机词汇一致处复用同表）
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"
STATUS_REJECTED = "rejected"


class ReasonCode(enum.StrEnum):
    """拒收原因码（结构化契约，值与 Global Constraints 逐字一致，供报告与前端展示）。"""

    LOW_COVERAGE = "LOW_COVERAGE"
    WEAK_ICIR = "WEAK_ICIR"
    NOT_SIGNIFICANT = "NOT_SIGNIFICANT"
    REDUNDANT_WITH_POOL = "REDUNDANT_WITH_POOL"
    NO_POOL_INCREMENT = "NO_POOL_INCREMENT"
    OOS_FAILED = "OOS_FAILED"
    STRUCTURAL_DUPLICATE = "STRUCTURAL_DUPLICATE"


@dataclass(frozen=True)
class GateResult:
    """单闸口判定结果；``detail`` 携带判定依据（阈值、统计量、相关对象 id 等）。"""

    gate: int
    passed: bool
    reason: str | None
    detail: dict[str, Any]


@dataclass(frozen=True)
class AdmissionResult:
    """单候选编排结论（``gates`` = 实际执行过的闸口结果，含通过项）。"""

    factor_id: int | None
    status: str
    gates: tuple[GateResult, ...]
    rejected_gate: int | None


# ── 闸① 可计算性 ───────────────────────────────────────────────────────────


def check_computability(factor_values: pd.DataFrame) -> GateResult:
    """闸①：全样本面板缺失率 ≤ ``MAX_MISSING_RATE``（E3；含算子不足窗口的 NaN）。

    缺失率 = NaN cell 数 / 全体 cell 数；空面板（0 cell）视为不可计算（缺失率
    记 1.0，fail-closed，不做 0/0 除法）。
    """
    total = int(factor_values.size)
    missing = int(factor_values.isna().to_numpy().sum())
    missing_rate = missing / total if total else 1.0
    passed = missing_rate <= MAX_MISSING_RATE
    return GateResult(
        gate=GATE_COMPUTABILITY,
        passed=passed,
        reason=None if passed else ReasonCode.LOW_COVERAGE,
        detail={
            "missing_rate": missing_rate,
            "missing_cells": missing,
            "total_cells": total,
            "threshold": MAX_MISSING_RATE,
        },
    )


# ── 闸② 单因子检验（E3） ───────────────────────────────────────────────────


def _ic_stats(ic: pd.Series) -> tuple[float, float, float, int]:
    """IC 序列统计量 ``(mean, std, icir, n_samples)``——闸②/闸④/闸⑤ 共用口径。

    NaN 不参与统计（逐日 NaN 即样本不足日，见 ``rank_ic_series``）；常量 IC
    （``max == min``）按口径记 std=0、ICIR=0（否则浮点残差 std≈1e-18 会算出
    5.9e15 量级的荒谬 ICIR 而过闸）；有效样本 < 2（std 不可定义）、std=0 或全
    NaN → ICIR 记 0。ICIR = mean/std（std=0 → 0）。
    """
    valid = ic.dropna()
    n_samples = int(valid.size)
    mean = float(valid.mean()) if n_samples else math.nan
    if n_samples < 2:
        std = math.nan
        icir = 0.0
    elif float(valid.max()) == float(valid.min()):
        std = 0.0
        icir = 0.0
    else:
        std = float(valid.std(ddof=1))
        icir = mean / std if std != 0.0 else 0.0
    return mean, std, icir, n_samples


def _sign(value: float) -> int:
    """符号（±1）；NaN/0/非有限 → 0（调用方按「无符号可比」fail closed）。"""
    if not math.isfinite(value) or value == 0.0:
        return 0
    return 1 if value > 0.0 else -1


def check_single_factor(ic: pd.Series) -> GateResult:
    """闸②：``|ICIR| ≥ MIN_ABS_ICIR`` 且 ``|IC 均值| > 0``（ICIR = mean/std）。

    统计口径见 ``_ic_stats``（NaN 不计、常量 IC → ICIR 0、样本 < 2 → ICIR 0）。
    均值条件取严格非零（``|mean| > 0``，不做数值容差——显著性由闸③置换检验
    承担）。方向不约束（``|ICIR|``）：强负 IC 同样过闸②，方向与稳健性由闸④⑤把关。
    """
    mean, std, icir, n_samples = _ic_stats(ic)
    passed = math.isfinite(mean) and abs(mean) > 0.0 and abs(icir) >= MIN_ABS_ICIR
    return GateResult(
        gate=GATE_SINGLE_FACTOR,
        passed=passed,
        reason=None if passed else ReasonCode.WEAK_ICIR,
        detail={
            "mean": mean,
            "std": std,
            "icir": icir,
            "n_samples": n_samples,
            "min_abs_icir": MIN_ABS_ICIR,
        },
    )


# ── 闸③ 置换检验 + BH-FDR（E4） ─────────────────────────────────────────────


def benjamini_hochberg_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    """Benjamini-Hochberg step-up 调整 p 值（q 值；键集合与输入一致，上限截到 1.0）。

    调整口径：原 p 升序排序后 ``p_adj_(i) = min(1, min_{j ≥ i} (K · p_(j) / j))``
    （后缀最小 + 截到 1，K = 候选数）——结果在排序序上**单调不减**（BH step-up 的
    上凸包），并与「拒绝所有 p_(i) ≤ (i/K)·q」的判定等价。调用方
    （``check_significance``）以 ``BH_FDR_Q`` 比较（``p_adj > q`` → ``NOT_SIGNIFICANT``）；
    截顶不影响判定（原值 > 1 时本就 > q），只为报告/审计给出规范 q 值。
    空输入 → 空输出。并列 p 值按 ``(p, key)`` 稳定排序，结果确定（同输入同输出）。
    """
    if not p_values:
        return {}
    ordered = sorted(p_values, key=lambda key: (p_values[key], key))
    total = len(ordered)
    adjusted: dict[str, float] = {}
    running = 1.0
    for rank in range(total, 0, -1):
        key = ordered[rank - 1]
        running = min(running, p_values[key] * total / rank)
        adjusted[key] = running
    return adjusted


def check_significance(ic_series_map: dict[str, pd.Series], *, seed: int) -> dict[str, GateResult]:
    """闸③：逐候选置换检验 + 同批次 BH-FDR（E4），``seed`` 固定时逐位可复现。

    置换口径（接口只给 IC 序列——闸③ 的输入是各候选的逐日 IC，见模块 docstring
    编排契约）：对每个候选的逐日 IC 做 ``PERMUTATION_N`` 次「逐日随机重排配对」的
    条件随机化——横截面重排因子值后该日 IC 在零假设下以 0 为中心对称，逐日独立
    随机化其符号即该零分布的置换实现（保留观测幅度 → 对强信号保守：观测幅度越大
    越难被置换达到，不会放松判定）；置换统计量 = 逐日置换 IC 的均值，
    ``p = (1 + #{|perm_mean| ≥ |obs_mean|}) / (1 + N)``（双侧）。
    NaN 日不参与（与闸② ``dropna`` 同口径）；全 NaN / 空序列 → ``p = 1.0``
    （fail closed）。同批次 p 值经 ``benjamini_hochberg_adjust`` 校正，
    ``p_adj > BH_FDR_Q`` → ``NOT_SIGNIFICANT``。

    ``seed`` 与候选在**排序键序**中的下标共同派生各候选独立的 ``Generator``
    （``default_rng([seed, index])``）——同种子同键集合 → 逐位相同 p 值；键集合
    变化不改变其它候选的抽样（下标按排序键序固定）。返回**每个输入键**一条结果。
    """
    if not ic_series_map:
        return {}
    keys = sorted(ic_series_map)
    p_values: dict[str, float] = {}
    details: dict[str, dict[str, Any]] = {}
    for index, key in enumerate(keys):
        valid = ic_series_map[key].dropna()
        values = valid.to_numpy(dtype="float64")
        n_samples = int(values.size)
        observed_mean = float(values.mean()) if n_samples else math.nan
        if n_samples == 0 or not math.isfinite(observed_mean):
            count = PERMUTATION_N  # 无有效样本：恒不显著（fail closed）
        else:
            rng = np.random.default_rng([seed, index])
            signs = (
                rng.integers(0, 2, size=(PERMUTATION_N, n_samples)).astype("float64") * 2.0 - 1.0
            )
            perm_means = np.abs((signs * values).mean(axis=1))
            count = int(np.count_nonzero(perm_means >= abs(observed_mean)))
        p_values[key] = (1.0 + count) / (1.0 + PERMUTATION_N)
        details[key] = {
            "observed_mean": observed_mean,
            "n_samples": n_samples,
            "n_permutations": PERMUTATION_N,
            "exceedances": count,
            "seed": seed,
            "method": "per_day_pairing_sign_randomization",
        }
    adjusted = benjamini_hochberg_adjust(p_values)
    results: dict[str, GateResult] = {}
    for key in keys:
        p_adj = adjusted[key]
        passed = p_adj <= BH_FDR_Q
        results[key] = GateResult(
            gate=GATE_SIGNIFICANCE,
            passed=passed,
            reason=None if passed else ReasonCode.NOT_SIGNIFICANT,
            detail={**details[key], "p_value": p_values[key], "p_adj": p_adj, "q": BH_FDR_Q},
        )
    return results


# ── 闸④ 池级增量与冗余（E5） ────────────────────────────────────────────────

# 冗余比对所需的最少共同有效交易日（2 点相关恒 ±1 → 会假阳性；5 点起才有意义）
_MIN_REDUNDANCY_SAMPLES = 5


def _aligned_pearson(left: pd.Series, right: pd.Series) -> float | None:
    """两 IC 序列按共同日期对齐后的 Pearson 相关；样本不足/不可定义 → ``None``。"""
    aligned = pd.concat([left.rename("candidate"), right.rename("pool")], axis=1).dropna()
    if int(aligned.shape[0]) < _MIN_REDUNDANCY_SAMPLES:
        return None
    correlation = aligned["candidate"].corr(aligned["pool"])
    if correlation is None or not math.isfinite(float(correlation)):
        return None
    return float(correlation)


def _pool_ic_series(
    row: dict[str, Any], panel: MarketPanel, fwd: pd.DataFrame, *, db_path: str | Path | None
) -> pd.Series:
    """池内因子的 IC 序列：优先落库序列（``factor_ic``），不足/缺失则由面板现算。

    落库序列覆盖不足 ``_MIN_REDUNDANCY_SAMPLES`` 点（本批次刚入库、监控尚未跑过）
    时回退现算——同一口径的同一量（逐日 RankIC），保证冗余检查不因落库时序漏检。
    """
    persisted = list_ic_series(int(row["id"]), db_path=db_path)
    if persisted:
        series = pd.Series(
            {str(trade_date): float(value) for trade_date, value in persisted}, dtype="float64"
        )
        if int(series.dropna().size) >= _MIN_REDUNDANCY_SAMPLES:
            return series
    return rank_ic_series(evaluate(parse(str(row["expression"])), panel), fwd)


def _composite_ic(values: list[pd.DataFrame], fwd: pd.DataFrame) -> pd.Series:
    """池等权组合的 IC 序列：各因子逐日横截面 zscore 后等权平均 → 与远期收益 RankIC。

    某因子某日 NaN（窗口不足等）不拖垮整个组合：当日组合 = 其余可用因子的等权均值
    （逐格 NaN 仅当全池该格皆 NaN）。
    """
    stacked = pd.concat([cross_section_zscore(frame) for frame in values])
    composite = stacked.groupby(level=0).mean()
    return rank_ic_series(composite, fwd)


def check_pool_increment(
    candidate_ic: pd.Series,
    candidate_values: pd.DataFrame,
    pool: list[dict],
    panel: MarketPanel,
    *,
    db_path: str | Path | None = None,
) -> GateResult:
    """闸④：先冗余（|IC 相关| > ``MAX_IC_CORRELATION``）再池级 IR 增量（E5）。

    冗余：候选 IC 序列与池内任一因子 IC 序列（落库优先、不足现算，见
    ``_pool_ic_series``）在共同日期上 Pearson 相关，``|ρ| > 0.7`` →
    ``REDUNDANT_WITH_POOL``，``detail`` 记 ``duplicate_of``（相关对象 id）、
    ``correlation``（ρ）与 ``expression``；共同样本 < ``_MIN_REDUNDANCY_SAMPLES``
    → 该对象跳过（2 点相关恒 ±1，不可信）。

    增量：池等权组合 = 池各因子逐日横截面 zscore 后等权平均（E5 逐字）；
    增量 = IR(池+候选) − IR(池) ≤ 0 → ``NO_POOL_INCREMENT``；**空池时增量 =
    IR(候选)**（= 传入 ``candidate_ic`` 的 ICIR，与闸②重复但保留判定）。
    两侧 IR 在**共同有效日期**上比较（口径一致，不拿不同样本量比高低）；
    IR = mean/std（std=0 → 0，与闸②同口径）。``detail`` **必含**
    ``increment: float``——E8 保种排序依赖它（编排写入 ``pool_increment``）。
    """
    fwd = forward_returns(panel.fields["close"])
    redundancy_checked: list[int] = []
    redundancy_skipped: list[int] = []
    for row in pool:
        factor_id = int(row["id"])
        pool_ic = _pool_ic_series(row, panel, fwd, db_path=db_path)
        correlation = _aligned_pearson(candidate_ic, pool_ic)
        if correlation is None:
            redundancy_skipped.append(factor_id)
            continue
        redundancy_checked.append(factor_id)
        if abs(correlation) > MAX_IC_CORRELATION:
            return GateResult(
                gate=GATE_POOL_INCREMENT,
                passed=False,
                reason=ReasonCode.REDUNDANT_WITH_POOL,
                detail={
                    "duplicate_of": factor_id,
                    "expression": str(row.get("expression", "")),
                    "correlation": correlation,
                    "threshold": MAX_IC_CORRELATION,
                    "pool_size": len(pool),
                    "redundancy_checked": redundancy_checked,
                    "redundancy_skipped": redundancy_skipped,
                },
            )

    if pool:
        pool_values = [evaluate(parse(str(row["expression"])), panel) for row in pool]
        pool_ic_series = _composite_ic(pool_values, fwd)
        combined_ic_series = _composite_ic([*pool_values, candidate_values], fwd)
        common = pool_ic_series.dropna().index.intersection(combined_ic_series.dropna().index)
        ir_pool: float | None = _ic_stats(pool_ic_series.loc[common])[2]
        ir_combined = _ic_stats(combined_ic_series.loc[common])[2]
        n_common_days = int(common.size)
    else:
        ir_pool = None
        ir_combined = _ic_stats(candidate_ic)[2]
        n_common_days = int(candidate_ic.dropna().size)
    increment = ir_combined - (ir_pool if ir_pool is not None else 0.0)
    passed = increment > 0.0
    return GateResult(
        gate=GATE_POOL_INCREMENT,
        passed=passed,
        reason=None if passed else ReasonCode.NO_POOL_INCREMENT,
        detail={
            "increment": float(increment),
            "ir_pool": ir_pool,
            "ir_pool_with_candidate": float(ir_combined),
            "pool_size": len(pool),
            "n_common_days": n_common_days,
            "redundancy_checked": redundancy_checked,
            "redundancy_skipped": redundancy_skipped,
        },
    )


# ── 闸⑤ 样本外复核（E6） ───────────────────────────────────────────────────


def train_validation_split(
    dates: Iterable[Any], *, fraction: float = TRAIN_FRACTION
) -> tuple[list[str], list[str]]:
    """按日期排序切分训练/验证段（前 ``fraction`` 为训练段，切点不跨日）。

    返回 ``(train_dates, val_dates)``：两段均升序、互不相交、并集 = 输入日期集合；
    切点 ``int(len × fraction)``（两侧皆可能为空——由调用方 fail closed）。
    """
    ordered = sorted(str(value) for value in dates)
    cut = int(len(ordered) * fraction)
    return ordered[:cut], ordered[cut:]


def check_out_of_sample(factor_values: pd.DataFrame, fwd: pd.DataFrame) -> GateResult:
    """闸⑤：训验切分（``TRAIN_FRACTION``，切点不跨日）后验证段复核（E6）。

    两段各自重算 IC 序列（不跨区标准化、不做跨区信息借用）；通过条件：
    ``|ICIR_val| ≥ MIN_ABS_ICIR`` **且** 验证段 IC 均值符号与训练段一致（且非 0）。
    任一段无有效样本 / 训练段均值不可定义 → ``OOS_FAILED``（fail closed）。
    ``detail`` 记两段区间、天数、ICIR 与符号（报告与审计用）。
    """
    common = factor_values.index.intersection(fwd.index)
    train_dates, val_dates = train_validation_split(common)
    base: dict[str, Any] = {
        "train_fraction": TRAIN_FRACTION,
        "train_days": len(train_dates),
        "val_days": len(val_dates),
        "train_start": train_dates[0] if train_dates else None,
        "train_end": train_dates[-1] if train_dates else None,
        "val_start": val_dates[0] if val_dates else None,
        "val_end": val_dates[-1] if val_dates else None,
    }
    if not train_dates or not val_dates:
        return GateResult(
            GATE_OUT_OF_SAMPLE,
            False,
            ReasonCode.OOS_FAILED,
            {**base, "reason": "insufficient_dates", "min_abs_icir": MIN_ABS_ICIR},
        )
    train_mean, _, train_icir, _ = _ic_stats(
        rank_ic_series(factor_values.loc[train_dates], fwd.loc[train_dates])
    )
    val_mean, _, val_icir, _ = _ic_stats(
        rank_ic_series(factor_values.loc[val_dates], fwd.loc[val_dates])
    )
    sign_train = _sign(train_mean)
    sign_val = _sign(val_mean)
    passed = (
        math.isfinite(val_icir)
        and abs(val_icir) >= MIN_ABS_ICIR
        and sign_val != 0
        and sign_val == sign_train
    )
    return GateResult(
        gate=GATE_OUT_OF_SAMPLE,
        passed=passed,
        reason=None if passed else ReasonCode.OOS_FAILED,
        detail={
            **base,
            "mean_train": train_mean,
            "mean_val": val_mean,
            "icir_train": train_icir,
            "icir_val": val_icir,
            "sign_train": sign_train,
            "sign_val": sign_val,
            "min_abs_icir": MIN_ABS_ICIR,
        },
    )


# ── 编排 ───────────────────────────────────────────────────────────────────


@dataclass
class _PendingCandidate:
    """编排阶段间携带的候选状态（内部结构，非公开契约）。"""

    position: int
    expression: str
    hypothesis: str
    category: str
    ast_json: str
    source: str
    alignment_score: float | None
    values: pd.DataFrame
    ic: pd.Series
    gates: list[GateResult] = field(default_factory=list)


def _dedup_gate(ast_json: str, *, db_path: str | Path | None) -> GateResult:
    """闸 0 结构查重结果（命中记 ``duplicate_of`` = 同构对象 id）。"""
    duplicate_of = find_structural_duplicate(ast_json, db_path=db_path)
    if duplicate_of is None:
        return GateResult(GATE_DEDUP, True, None, {})
    return GateResult(
        GATE_DEDUP,
        False,
        ReasonCode.STRUCTURAL_DUPLICATE,
        {"duplicate_of": duplicate_of},
    )


def admit_candidates(
    candidates: list[dict],
    panel: MarketPanel,
    *,
    seed: int,
    db_path: str | Path | None = None,
) -> list[AdmissionResult]:
    """批量准入编排：结构查重 → 闸①② → 闸③ → 闸④ → 闸⑤ → 入库(active) → 保种。

    ``candidates`` 每条 dict：``expression`` / ``hypothesis`` / ``category``
    （必填）+ ``ast_json``（可选，缺省由表达式 ``serialize(parse())`` 派生）+
    ``source`` / ``alignment_score``（可选，透传落库）。返回与输入**同序**的
    ``AdmissionResult``；被拒候选项不落库（无 ``factors`` 行、无事件）。
    编排契约与闸编号见模块 docstring。
    """
    if not candidates:
        return []
    fwd = forward_returns(panel.fields["close"])
    outcomes: dict[int, AdmissionResult] = {}
    pending: list[_PendingCandidate] = []

    # 阶段 A（逐候选）：结构查重 → 闸① → 闸②
    for position, candidate in enumerate(candidates):
        expression = str(candidate["expression"])
        node = parse(expression)
        ast_json = str(candidate.get("ast_json") or serialize(node))
        dedup = _dedup_gate(ast_json, db_path=db_path)
        if not dedup.passed:
            outcomes[position] = AdmissionResult(None, STATUS_REJECTED, (dedup,), GATE_DEDUP)
            continue
        values = evaluate(node, panel)
        gate1 = check_computability(values)
        if not gate1.passed:
            outcomes[position] = AdmissionResult(
                None, STATUS_REJECTED, (dedup, gate1), GATE_COMPUTABILITY
            )
            continue
        ic = rank_ic_series(values, fwd)
        gate2 = check_single_factor(ic)
        if not gate2.passed:
            outcomes[position] = AdmissionResult(
                None, STATUS_REJECTED, (dedup, gate1, gate2), GATE_SINGLE_FACTOR
            )
            continue
        pending.append(
            _PendingCandidate(
                position=position,
                expression=expression,
                hypothesis=str(candidate["hypothesis"]),
                category=str(candidate["category"]),
                ast_json=ast_json,
                source=str(candidate.get("source", "proposal")),
                alignment_score=candidate.get("alignment_score"),
                values=values,
                ic=ic,
                gates=[dedup, gate1, gate2],
            )
        )

    # 阶段 B（同批次一次）：闸③ 显著性（BH-FDR 需整批 p 值）
    if pending:
        significance = check_significance({item.expression: item.ic for item in pending}, seed=seed)
        for item in pending:
            gate3 = significance[item.expression]
            item.gates.append(gate3)
            if not gate3.passed:
                outcomes[item.position] = AdmissionResult(
                    None, STATUS_REJECTED, tuple(item.gates), GATE_SIGNIFICANCE
                )

    # 阶段 C（逐候选）：闸④ → 闸⑤ → 入库前复检 → 入库(active) → 保种
    for item in pending:
        if item.position in outcomes:
            continue
        pool = list_active_factors(db_path=db_path)
        gate4 = check_pool_increment(item.ic, item.values, pool, panel, db_path=db_path)
        item.gates.append(gate4)
        if not gate4.passed:
            outcomes[item.position] = AdmissionResult(
                None, STATUS_REJECTED, tuple(item.gates), GATE_POOL_INCREMENT
            )
            continue
        gate5 = check_out_of_sample(item.values, fwd)
        item.gates.append(gate5)
        if not gate5.passed:
            outcomes[item.position] = AdmissionResult(
                None, STATUS_REJECTED, tuple(item.gates), GATE_OUT_OF_SAMPLE
            )
            continue
        dedup_again = _dedup_gate(item.ast_json, db_path=db_path)
        if not dedup_again.passed:
            outcomes[item.position] = AdmissionResult(
                None, STATUS_REJECTED, (*item.gates, dedup_again), GATE_DEDUP
            )
            continue
        factor_id = add_factor(
            expression=item.expression,
            hypothesis=item.hypothesis,
            category=item.category,
            status="candidate",
            ast_json=item.ast_json,
            source=item.source,
            alignment_score=item.alignment_score,
            db_path=db_path,
        )
        set_status(factor_id, STATUS_ACTIVE, detail={"reason": "ADMITTED"}, db_path=db_path)
        increment = gate4.detail.get("increment")
        if isinstance(increment, (int, float)) and not isinstance(increment, bool):
            set_pool_increment(factor_id, float(increment), db_path=db_path)
        retired = apply_bucket_quota(item.category, db_path=db_path)
        status = STATUS_RETIRED if factor_id in retired else STATUS_ACTIVE
        outcomes[item.position] = AdmissionResult(factor_id, status, tuple(item.gates), None)

    if len(outcomes) != len(candidates):
        missing = len(candidates) - len(outcomes)
        raise RuntimeError(f"编排缺陷：{missing} 条候选未产出准入结论")
    return [outcomes[position] for position in range(len(candidates))]
