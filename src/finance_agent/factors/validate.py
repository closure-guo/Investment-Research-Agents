"""五道闸准入管线（add-factor-library-and-monitor Task 3：闸①② + 编排骨架；
Task 4：闸③ 置换检验 + BH-FDR）。

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

**T3 实现闸①②与编排骨架，T4 已把闸③占位桩替换为真实实现**；闸④⑤ 仍为显式
``TODO(T5)`` 占位桩（恒通过、逐函数独立、可直接删除替换），编排对它们只依赖
接口形状。占位桩期间只验收「编排形状 + 闸①②③判定」，真实统计判定由 T5 的
测试覆盖。

编排契约（T5 替换占位桩时不得破坏）：

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
- ``check_pool_increment`` 的真实实现**必须在 ``GateResult.detail`` 给出
  ``increment: float``**（池级 IR 增量）——E8 保种排序依赖 ``factors.pool_increment``；
- 候选表达式经 ``dsl.parse``（默认复杂度硬约束）解析；解析/复杂度失败直接抛错
  （提案路径已保证合法，编排不吞错）；
- 面板须含 ``close`` 字段（``forward_returns`` 输入；面板六字段契约）。
"""

from __future__ import annotations

import enum
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from finance_agent.factors.constraints import (
    BH_FDR_Q,
    MAX_MISSING_RATE,
    MIN_ABS_ICIR,
    PERMUTATION_N,
)
from finance_agent.factors.dsl import parse, serialize
from finance_agent.factors.eval_engine import (
    MarketPanel,
    evaluate,
    forward_returns,
    rank_ic_series,
)
from finance_agent.factors.library import (
    add_factor,
    apply_bucket_quota,
    find_structural_duplicate,
    list_active_factors,
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


def check_single_factor(ic: pd.Series) -> GateResult:
    """闸②：``|ICIR| ≥ MIN_ABS_ICIR`` 且 ``|IC 均值| > 0``（ICIR = mean/std）。

    NaN 不参与统计（逐日 NaN 即样本不足日，见 ``rank_ic_series``）；常量 IC
    （``max == min``）按口径记 std=0、ICIR=0（否则浮点残差 std≈1e-18 会算出
    5.9e15 量级的荒谬 ICIR 而过闸）；有效样本 < 2（std 不可定义）、std=0 或全
    NaN → ICIR 记 0 → ``WEAK_ICIR``。均值条件取严格非零（``|mean| > 0``，不做数值
    容差——显著性由闸③置换检验承担）。方向不约束（``|ICIR|``）：强负 IC 同样过
    闸②，方向与稳健性由闸④⑤把关。
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
    """Benjamini-Hochberg step-up 调整 p 值（q 值；键集合与输入一致）。

    调整口径：原 p 升序排序后 ``p_adj_(i) = min_{j ≥ i} (K · p_(j) / j)``
    （后缀最小，K = 候选数）——结果在排序序上**单调不减**（BH step-up 的上凸包），
    并与「拒绝所有 p_(i) ≤ (i/K)·q」的判定等价。调用方（``check_significance``）
    以 ``BH_FDR_Q`` 比较（``p_adj > q`` → ``NOT_SIGNIFICANT``）。空输入 → 空输出。
    并列 p 值按 ``(p, key)`` 稳定排序，结果确定（同输入同输出）。
    """
    if not p_values:
        return {}
    ordered = sorted(p_values, key=lambda key: (p_values[key], key))
    total = len(ordered)
    adjusted: dict[str, float] = {}
    running = math.inf
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


# ── TODO(T5) 占位桩：闸④ 池级增量与冗余 ─────────────────────────────────────


def check_pool_increment(
    candidate_ic: pd.Series,
    candidate_values: pd.DataFrame,
    pool: list[dict],
    panel: MarketPanel,
    *,
    db_path: str | Path | None = None,
) -> GateResult:
    """TODO(T5)：闸④占位桩——**恒通过**，待 T5 以冗余 + 池级 IR 增量替换。

    真实实现（E5）：先查冗余（候选与池内任一因子 IC 序列 ``|Pearson ρ| >
    MAX_IC_CORRELATION`` → ``REDUNDANT_WITH_POOL``，detail 记相关对象 id/code 与
    ρ）；再算池等权组合（各因子逐日横截面 zscore 后等权平均）IR 增量
    （``IR(池+候选) − IR(池)`` ≤ 0 → ``NO_POOL_INCREMENT``；空池时增量 = IR(候选)）。
    **须在 ``detail`` 给出 ``increment: float``**（E8 保种排序依赖）。
    ``pool`` = 调用时库内 active 因子行；``db_path`` 供读取池内因子 IC 序列。
    """
    return GateResult(
        gate=GATE_POOL_INCREMENT,
        passed=True,
        reason=None,
        detail={"stub": "TODO(T5)", "pool_size": len(pool)},
    )


# ── TODO(T5) 占位桩：闸⑤ 样本外复核 ────────────────────────────────────────


def check_out_of_sample(factor_values: pd.DataFrame, fwd: pd.DataFrame) -> GateResult:
    """TODO(T5)：闸⑤占位桩——**恒通过**，待 T5 以训验切分复核替换。

    真实实现（E6）：按日期排序前 ``TRAIN_FRACTION`` 为训练段、后段为验证段
    （切点不跨日）；验证段重算 IC 序列，``|ICIR_val| ≥ MIN_ABS_ICIR`` 且符号与
    训练段一致 → 通过，否则 ``OOS_FAILED``。
    """
    return GateResult(
        gate=GATE_OUT_OF_SAMPLE,
        passed=True,
        reason=None,
        detail={"stub": "TODO(T5)"},
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
