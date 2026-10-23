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
  ``REDUNDANT_WITH_POOL``；池 + 候选组合 IR 增量（**验证段**，见下）≤ 0 →
  ``NO_POOL_INCREMENT``（验证段有效日不足 ``MIN_OOS_IR_SAMPLES`` → 同码拒收，
  ``detail["reason"]="insufficient_oos_dates"``）；
- **闸 5 样本外复核**：训验切分（``TRAIN_FRACTION``，切点不跨日）验证段
  ``|ICIR|`` 达标且符号一致 → 否则 ``OOS_FAILED``；训练段统计量剔除末端
  ``DEFAULT_IC_HORIZON`` 日（其远期收益跨入验证段 → 边界泄漏）。

**样本外口径（评审修正 fix 2/4）**：闸④与闸⑤共用同一切分（``train_validation_split``，
切点 ``int(len × TRAIN_FRACTION)``，按日期序不跨日）；闸④的池级增量 = 验证段
``IR(池+候选) − IR(池)``（候选与池组合同受验证段日期与同一远期收益约束；
训练段增量仅记入 ``detail`` 供审计，不参与判定）；闸⑤训练段 IC 剔除末端
horizon 日后再算。

**置换口径（评审批准 fix 3）**：闸③接口 ``check_significance(ic_series_map, *, seed)``
只接收各候选的逐日 IC 序列（编排契约逐字），无法重排横截面因子值，故以该零分布的
**条件随机化**等价形式实现：横截面重排后当日 IC 在 H0 下以 0 为中心对称（置换分布
关于原点对称），逐日独立随机化其符号即该零分布的一个**精确条件检验**实现——保留
观测幅度（对强信号保守：观测幅度越大越难被置换达到，不会放松判定）。计划 E4 的
「逐日横截面重排因子值」为同一零假说的等价表述；若后续要求真实重排横截面，需扩展
编排把因子值传入闸③（接口变更，属行为变更，须走 OpenSpec）。

实现沿革：T3 落闸①② + 编排骨架（③④⑤ 曾为占位桩），T4 替换闸③（置换 + BH-FDR），
T5 替换闸④⑤（池级增量 + 样本外）——占位桩已全部删除，五道闸均为真实统计判定。

编排契约（各任务替换实现时不得破坏）：

- ``gates`` 记录该候选**实际执行过**的闸口结果（按执行顺序，含通过项）；
  ``rejected_gate`` = 被拒闸口编号（0–5），全部通过者为 ``None``；
- ``status``：入库成功 = ``"active"``（同批保种使其立即退役则 ``"retired"``）；
  任一闸口拒收 = ``"rejected"``（``factor_id=None``）；
- 阶段 A（逐候选）：结构查重 → ``evaluate`` → 闸① → ``rank_ic_series`` → 闸②；
  阶段 B（同批次一次）：闸③（``check_significance`` 以**候选位置**为键——
  逐字重复表达式各计一条假设，不塌缩；结果按位置映射回候选）；阶段 C（逐候选）：
  池 = 调用时库内 active（本批次先入库者
  已在池内）→ 闸④ → 闸⑤ → **入库前结构复检**（批次内先前入库者与本候选同构 →
  ``rejected_gate=0``，``gates`` 末尾追加第二次 gate=0 记录）→ ``add_factor``
  （candidate）+ ``set_status(active)`` + 闸④ detail 的 ``increment`` 写
  ``pool_increment`` + ``apply_bucket_quota``（E8）；
- ``check_pool_increment`` **必须在 ``GateResult.detail`` 给出 ``increment: float``**
  （池级 IR 增量，= 验证段增量）——E8 保种排序依赖 ``factors.pool_increment``；
- 候选表达式经 ``dsl.parse``（默认复杂度硬约束）解析；解析/复杂度失败直接抛错
  （提案路径已保证合法，编排不吞错）；
- 面板须含 ``close`` 字段（``forward_returns`` 输入；面板六字段契约）。
"""

from __future__ import annotations

import enum
import logging
import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from finance_agent.factors.constraints import (
    BH_FDR_Q,
    DEFAULT_IC_HORIZON,
    MAX_IC_CORRELATION,
    MAX_MISSING_RATE,
    MIN_ABS_ICIR,
    MIN_OOS_IR_SAMPLES,
    MIN_REDUNDANCY_SAMPLES,
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

logger = logging.getLogger(__name__)

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

    置换口径（评审批准，见模块 docstring「置换口径」）：对每个候选的逐日 IC 做
    ``PERMUTATION_N`` 次逐日符号随机化——横截面重排后该日 IC 在 H0 下以 0 为中心
    对称，逐日独立随机化其符号即该零分布的精确条件检验实现（保留观测幅度 → 对强
    信号保守）；置换统计量 = 逐日置换 IC 的均值，
    ``p = (1 + #{|perm_mean| ≥ |obs_mean|}) / (1 + N)``（双侧）。
    NaN 日不参与（与闸② ``dropna`` 同口径）；全 NaN / 空序列 → ``p = 1.0``
    （fail closed）。同批次 p 值经 ``benjamini_hochberg_adjust`` 校正（K = 键数），
    ``p_adj > BH_FDR_Q`` → ``NOT_SIGNIFICANT``。

    **分辨率守卫（fix 1）**：p 值下界 ``1/(PERMUTATION_N+1) > BH_FDR_Q/K`` 时该批次
    不可能拒收任何候选（配置错误）→ 立即抛 ``ValueError``，消息给出不等式两侧
    数值，fail loud 而非静默全通过。

    ``seed`` 与候选在**排序键序**中的下标共同派生各候选独立的 ``Generator``
    （``default_rng([seed, index])``）——同种子同键集合 → 逐位相同 p 值；键集合
    变化不改变其它候选的抽样（下标按排序键序固定）。返回**每个输入键**一条结果。
    编排以候选位置为键（fix 9）：逐字重复的表达式各占一条假设（不塌缩），
    调用方按键/位置映射回候选。
    """
    if not ic_series_map:
        return {}
    keys = sorted(ic_series_map)
    total = len(keys)
    floor = 1.0 / (PERMUTATION_N + 1)
    reject_threshold = BH_FDR_Q / total
    if floor > reject_threshold:
        raise ValueError(
            f"置换检验分辨率不足：p 值下界 1/(PERMUTATION_N+1) = {floor:.6g}"
            f" > BH-FDR 可拒收阈值 BH_FDR_Q/K = {reject_threshold:.6g}"
            f"（PERMUTATION_N={PERMUTATION_N}、K={total}、BH_FDR_Q={BH_FDR_Q}）"
            "——该批次任何候选都无法通过闸③；请提高 PERMUTATION_N 或缩减同批候选数"
        )
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
            "n_hypotheses": total,
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


def _record_ledger(
    candidates: list[dict],
    outcomes: dict[int, AdmissionResult],
    *,
    source: str,
    db_path: str | Path | None,
) -> None:
    """候选检验账本单点写入（add-fdr-candidate-ledger / issue #355 使能层）。

    - 所有入口（月批/收割/builtin/外部导入）经本函数统一记账，杜绝逐入口接线遗漏；
    - 闸③ 读数（p_value/p_adj/q/n_hypotheses）从该候选实际执行过的闸③结果 detail
      提取；闸①②拒收（未做置换检验）p 值记 NULL，不计入跨批 FDR 家族；
    - 记账失败只记日志不上抛：账本属观测面，不得改变准入结论（与闸口 fail-open 同纪律）。
    """
    from finance_agent.factors.library import record_admission_ledger

    batch_id = f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    entries: list[dict[str, Any]] = []
    for position in range(len(candidates)):
        result = outcomes.get(position)
        if result is None:
            continue
        gate3_detail: dict[str, Any] = {}
        for gate in result.gates:
            if str(gate.gate) == str(GATE_SIGNIFICANCE):
                gate3_detail = dict(gate.detail or {})
                break
        entries.append(
            {
                "batch_id": batch_id,
                "source": source,
                "expression": str(candidates[position].get("expression") or ""),
                "p_value": gate3_detail.get("p_value"),
                "p_adj": gate3_detail.get("p_adj"),
                "q": gate3_detail.get("q"),
                "n_hypotheses": gate3_detail.get("n_hypotheses"),
                "admitted": result.factor_id is not None,
                "factor_id": result.factor_id,
                "rejected_gate": result.rejected_gate,
            }
        )
    try:
        record_admission_ledger(entries, db_path=db_path)
    except Exception:  # noqa: BLE001 - 观测面失败不得改变准入结论
        logger.exception("候选检验账本写入失败（忽略，不影响准入结论）")


def cumulative_fdr_preview(entries: list[dict[str, Any]], *, q: float = BH_FDR_Q) -> dict[str, Any]:
    """全局 BH 反事实预览（纯函数，issue #355 读数；MUST NOT 改变任何准入结论）。

    把账本内全部非空 p 值作为一个**跨批次**家族做一次 BH 校正，回答「若执行全局
    账本，今日读数会怎样」：family_size / significant_count / significant（行清单，
    含 batch/source/expression/factor_id）。空家族 → 全零结果。
    """
    tested = [e for e in entries if isinstance(e.get("p_value"), (int, float))]
    if not tested:
        return {"q": q, "family_size": 0, "significant_count": 0, "significant": []}
    keyed = {str(e["id"]): float(e["p_value"]) for e in tested}
    adjusted = benjamini_hochberg_adjust(keyed)
    significant = [e for e in tested if adjusted[str(e["id"])] <= q]
    return {
        "q": q,
        "family_size": len(tested),
        "significant_count": len(significant),
        "significant": [
            {
                "batch_id": e.get("batch_id"),
                "source": e.get("source"),
                "expression": e.get("expression"),
                "p_value": e.get("p_value"),
                "p_adj": adjusted[str(e["id"])],
                "factor_id": e.get("factor_id"),
            }
            for e in significant
        ],
    }


# ── 训验切分（闸④/闸⑤ 共用） ───────────────────────────────────────────────


def train_validation_split(
    dates: Iterable[Any], *, fraction: float = TRAIN_FRACTION
) -> tuple[list[str], list[str]]:
    """按日期排序切分训练/验证段（前 ``fraction`` 为训练段，切点不跨日）。

    返回 ``(train_dates, val_dates)``：两段均升序、互不相交、并集 = 输入日期集合；
    切点 ``int(len × fraction)``（两侧皆可能为空——由调用方 fail closed）。
    闸④（验证段增量）与闸⑤（样本外复核）共用本切分。
    """
    ordered = sorted(str(value) for value in dates)
    cut = int(len(ordered) * fraction)
    return ordered[:cut], ordered[cut:]


def _purge_boundary_dates(
    train_dates: list[str], *, horizon: int = DEFAULT_IC_HORIZON
) -> list[str]:
    """剔除训练段末端 ``horizon`` 日：其远期收益跨越切点进入验证段（边界泄漏）。

    训练段的最后 ``horizon`` 个交易日的 ``forward_returns`` 依赖验证段价格
    （``close_{t+horizon}``），据其计算的训练统计量会借用样本外信息；与闸⑤同一
    「只用到 ``date ≤ 切点`` 的信息」口径。剔除后为空 → 调用方 fail closed。
    """
    if horizon <= 0:
        return list(train_dates)
    return list(train_dates[:-horizon])


# ── 闸④ 池级增量与冗余（E5） ────────────────────────────────────────────────


def _aligned_pearson(left: pd.Series, right: pd.Series) -> float | None:
    """两 IC 序列按共同日期对齐后的 Pearson 相关；样本不足/不可定义 → ``None``。"""
    aligned = pd.concat([left.rename("candidate"), right.rename("pool")], axis=1).dropna()
    if int(aligned.shape[0]) < MIN_REDUNDANCY_SAMPLES:
        return None
    correlation = aligned["candidate"].corr(aligned["pool"])
    if correlation is None or not math.isfinite(float(correlation)):
        return None
    return float(correlation)


@dataclass
class _PoolEvalCache:
    """闸④池因子批内求值缓存（键 = 因子 id；``admit_candidates`` 单次调用内复用）。

    池因子在固定面板上的求值帧与现算 IC 序列**与当前候选无关**——逐候选重复
    ``evaluate``（全面板滚动算子）是闸④的主要计算浪费（60 候选 × 每池因子最多
    两次求值）。面板与表达式在单次准入调用内不变、因子 id 不复用表达式，缓存
    天然无失效；本批先入库的因子进入后续候选的池后，首遇时自然入缓存。
    ``check_pool_increment`` 独立调用方缺省 ``None`` 不缓存（行为不变）。
    """

    frames: dict[int, pd.DataFrame] = field(default_factory=dict)
    ic_series: dict[int, pd.Series] = field(default_factory=dict)


def _pool_frame(
    row: dict[str, Any], panel: MarketPanel, cache: _PoolEvalCache | None
) -> pd.DataFrame:
    """池因子求值帧（``cache`` 给定且命中 → 复用；``None`` 直算）。"""
    if cache is None:
        return evaluate(parse(str(row["expression"])), panel)
    factor_id = int(row["id"])
    if factor_id not in cache.frames:
        cache.frames[factor_id] = evaluate(parse(str(row["expression"])), panel)
    return cache.frames[factor_id]


def _pool_ic_series(
    row: dict[str, Any],
    panel: MarketPanel,
    fwd: pd.DataFrame,
    *,
    db_path: str | Path | None,
    cache: _PoolEvalCache | None = None,
) -> pd.Series:
    """池内因子的 IC 序列：优先落库序列（``factor_ic``），不足/缺失则由面板现算。

    落库序列覆盖不足 ``MIN_REDUNDANCY_SAMPLES`` 点（本批次刚入库、监控尚未跑过）
    时回退现算——同一口径的同一量（逐日 RankIC），保证冗余检查不因落库时序漏检。
    现算路径经 ``cache`` 批内复用（``admit_candidates`` 逐候选调用同池不重算）。
    """
    persisted = list_ic_series(int(row["id"]), db_path=db_path)
    if persisted:
        series = pd.Series(
            {str(trade_date): float(value) for trade_date, value in persisted}, dtype="float64"
        )
        if int(series.dropna().size) >= MIN_REDUNDANCY_SAMPLES:
            return series
    factor_id = int(row["id"])
    if cache is not None and factor_id in cache.ic_series:
        return cache.ic_series[factor_id]
    computed = rank_ic_series(_pool_frame(row, panel, cache), fwd)
    if cache is not None:
        cache.ic_series[factor_id] = computed
    return computed


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
    pool_cache: _PoolEvalCache | None = None,
) -> GateResult:
    """闸④：先冗余（|IC 相关| > ``MAX_IC_CORRELATION``）再池级 IR 增量（E5）。

    冗余：候选 IC 序列与池内任一因子 IC 序列（落库优先、不足现算，见
    ``_pool_ic_series``）在共同日期上 Pearson 相关，``|ρ| > 0.7`` →
    ``REDUNDANT_WITH_POOL``，``detail`` 记 ``duplicate_of``（相关对象 id）、
    ``correlation``（ρ）与 ``expression``；共同样本 < ``MIN_REDUNDANCY_SAMPLES``
    → 该对象跳过（2 点相关恒 ±1，不可信）。冗余比对用全样本 IC 序列（口径不变）。

    增量（**样本外，评审修正 fix 2**）：池组合 = 池各因子逐日横截面 zscore 后等权平均
    （E5 逐字），**组合内各因子（池与候选同规则）按训练段 RankIC 符号定向后参与**
    （fix-pool-increment-orientation：与生产选股 L2 的带符号 IC 权重模型对齐——原无方向
    等权组合会把稳定负向因子系统性当成「拖累」拒收，而 L2 对负方向因子自动反向加权；
    定向只用训练段、不借用验证段信息——随机因子的训练段符号来自噪声，**闸④单闸**通过率
    不变（约 50%）；**闸②→⑤整链**的噪声入库率变化见 delta proposal 的口径决策项
    （旧实现的「验证段 IR > 0」实际充当单边方向过滤，归一化后消失）；符号不可定的池因子
    不参与组合（训练段有效样本 < ``MIN_REDUNDANCY_SAMPLES`` 或 IC 均值符号 0）、符号不可定
    的候选按原向参与，由 ②⑤ 兜底；
    池因子方向与候选同规则现算（冗余检查的落库/现算 IC 与之口径独立——定向只取符号））。
    输入契约：``candidate_ic`` SHALL 为 ``rank_ic_series(candidate_values, fwd)``
    （生产调用方 ``admit_candidates`` 恒满足；IC 与值不同源时定向语义未定义）；增量 = IR(池+候选) − IR(池) 在**验证段**（与闸⑤同一
    ``train_validation_split`` 切分）上比较：候选与池组合同受验证段日期与同一远期
    收益约束，``≤ 0`` → ``NO_POOL_INCREMENT``；验证段共同有效日 <
    ``MIN_OOS_IR_SAMPLES``（IR 不稳）→ 同码拒收且 ``detail["reason"] =
    "insufficient_oos_dates"``。**空池时增量 = 验证段 IR(候选，定向后)**（=
    ``|candidate_ic 的 ICIR|``；负向候选在定向后同值取正）。训练段增量（同口径剔除末端
    horizon 日后）仅记入 ``detail["increment_train"]`` 供审计，不参与判定；
    ``detail`` 另记两段 ``ir_pool``/``ir_pool_with_candidate`` 与切分边界。
    IR = mean/std（std=0 → 0，与闸②同口径）。判定量以 ``detail["increment"]: float``
    给出——E8 保种排序依赖它（编排写入 ``pool_increment``）。
    """
    fwd = forward_returns(panel.fields["close"])
    # 与闸⑤同一时点切分（切点不跨日）；训练段剔除末端 horizon 日（边界泄漏同口径）。
    # 前移到冗余检查之前：方向归一化（下）只依赖训练段切分；冗余路径 detail 亦带定向审计。
    common_dates = candidate_values.index.intersection(fwd.index)
    train_dates, val_dates = train_validation_split(common_dates)
    train_ic_dates = _purge_boundary_dates(train_dates)
    val_index = pd.Index(val_dates)
    train_index = pd.Index(train_ic_dates)
    candidate_orientation = _sign(
        _ic_stats(candidate_ic.loc[candidate_ic.index.intersection(train_index)])[0]
    )
    redundancy_checked: list[int] = []
    redundancy_skipped: list[int] = []
    for row in pool:
        factor_id = int(row["id"])
        pool_ic = _pool_ic_series(row, panel, fwd, db_path=db_path, cache=pool_cache)
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
                    "orientation": candidate_orientation,
                    "orientation_basis": "train_segment_ic_sign",
                },
            )

    # 方向归一化（fix-pool-increment-orientation）：训练段符号定向（口径见 docstring）。
    # 池因子方向与候选**同规则**：同一面板、同一训练段现算 IC 取符号——不取落库 IC
    # （review C1：近日入库因子的落库 IC 只覆盖验证段，训练段交集为空会把它们静默踢出
    #  组合，闸④退化为单因子判定；实跑复现「同一候选随落库覆盖不同而一次拒一次收」）。
    candidate_oriented = (
        candidate_values if candidate_orientation == 0 else candidate_values * candidate_orientation
    )
    pool_orientations: list[dict[str, Any]] = []
    pool_values: list[pd.DataFrame] = []
    for row in pool:
        factor_id = int(row["id"])
        frame = _pool_frame(row, panel, pool_cache)
        train_ic = rank_ic_series(frame.loc[train_index], fwd.loc[train_index]).dropna()
        orientation = (
            _sign(_ic_stats(train_ic)[0]) if train_ic.size >= MIN_REDUNDANCY_SAMPLES else 0
        )
        pool_orientations.append({"factor_id": factor_id, "orientation": orientation})
        if orientation == 0:
            continue  # 方向不可定（训练段样本不足/IC 均值符号 0）的池因子不参与组合
        pool_values.append(frame if orientation == 1 else frame * -1)
    if pool_values:
        pool_ic_series = _composite_ic(pool_values, fwd).dropna()
        combined_ic_series = _composite_ic([*pool_values, candidate_oriented], fwd).dropna()
        shared = pool_ic_series.index.intersection(combined_ic_series.index)
        val_shared = shared.intersection(val_index)
        train_shared = shared.intersection(train_index)
        ir_pool = _ic_stats(pool_ic_series.loc[val_shared])[2]
        ir_combined = _ic_stats(combined_ic_series.loc[val_shared])[2]
        ir_pool_train = _ic_stats(pool_ic_series.loc[train_shared])[2]
        ir_combined_train = _ic_stats(combined_ic_series.loc[train_shared])[2]
    elif pool:
        # 池因子方向全部不可定 → 等价空池（审计：pool_orientations 如实记录全 0）
        combined_ic_series = _composite_ic([candidate_oriented], fwd).dropna()
        val_shared = combined_ic_series.index.intersection(val_index)
        train_shared = combined_ic_series.index.intersection(train_index)
        ir_pool = None
        ir_pool_train = None
        ir_combined = _ic_stats(combined_ic_series.loc[val_shared])[2]
        ir_combined_train = _ic_stats(combined_ic_series.loc[train_shared])[2]
    else:
        oriented_ic = candidate_ic if candidate_orientation >= 0 else candidate_ic * -1
        valid = oriented_ic.dropna()
        val_shared = valid.index.intersection(val_index)
        train_shared = valid.index.intersection(train_index)
        ir_pool = None
        ir_pool_train = None
        ir_combined = _ic_stats(oriented_ic.loc[val_shared])[2]
        ir_combined_train = _ic_stats(oriented_ic.loc[train_shared])[2]
    increment = ir_combined - (ir_pool if ir_pool is not None else 0.0)
    increment_train = ir_combined_train - (ir_pool_train if ir_pool_train is not None else 0.0)
    n_val_days = int(val_shared.size)
    n_train_days = int(train_shared.size)
    insufficient = n_val_days < MIN_OOS_IR_SAMPLES
    passed = increment > 0.0 and not insufficient
    detail: dict[str, Any] = {
        "increment": float(increment),
        "increment_train": float(increment_train),
        "orientation": candidate_orientation,
        "orientation_basis": "train_segment_ic_sign",
        "pool_orientations": pool_orientations,
        "ir_pool": ir_pool,
        "ir_pool_with_candidate": float(ir_combined),
        "ir_pool_train": ir_pool_train,
        "ir_pool_with_candidate_train": float(ir_combined_train),
        "pool_size": len(pool),
        "n_val_days": n_val_days,
        "n_train_days": n_train_days,
        "min_oos_days": MIN_OOS_IR_SAMPLES,
        "train_fraction": TRAIN_FRACTION,
        "train_end": train_dates[-1] if train_dates else None,
        "val_start": val_dates[0] if val_dates else None,
        "val_end": val_dates[-1] if val_dates else None,
        "redundancy_checked": redundancy_checked,
        "redundancy_skipped": redundancy_skipped,
    }
    if insufficient:
        detail["reason"] = "insufficient_oos_dates"
    return GateResult(
        gate=GATE_POOL_INCREMENT,
        passed=passed,
        reason=None if passed else ReasonCode.NO_POOL_INCREMENT,
        detail=detail,
    )


# ── 闸⑤ 样本外复核（E6） ───────────────────────────────────────────────────


def check_out_of_sample(factor_values: pd.DataFrame, fwd: pd.DataFrame) -> GateResult:
    """闸⑤：训验切分（``TRAIN_FRACTION``，切点不跨日）后验证段复核（E6）。

    两段各自重算 IC 序列（不跨区标准化、不做跨区信息借用）；通过条件：
    ``|ICIR_val| ≥ MIN_ABS_ICIR`` **且** 验证段 IC 均值符号与训练段一致（且非 0）。
    训练段统计量剔除末端 ``DEFAULT_IC_HORIZON`` 日（评审修正 fix 4：其远期收益
    跨越切点进入验证段，属边界泄漏；``detail["purged_train_days"]`` 记录剔除数）。
    任一段无有效样本 / 训练段均值不可定义 → ``OOS_FAILED``（fail closed）。
    ``detail`` 记两段区间、天数、ICIR 与符号（报告与审计用）。
    """
    common = factor_values.index.intersection(fwd.index)
    train_dates, val_dates = train_validation_split(common)
    train_ic_dates = _purge_boundary_dates(train_dates)
    base: dict[str, Any] = {
        "train_fraction": TRAIN_FRACTION,
        "train_days": len(train_dates),
        "val_days": len(val_dates),
        "purged_train_days": len(train_dates) - len(train_ic_dates),
        "train_start": train_dates[0] if train_dates else None,
        "train_end": train_dates[-1] if train_dates else None,
        "val_start": val_dates[0] if val_dates else None,
        "val_end": val_dates[-1] if val_dates else None,
    }
    if not train_ic_dates or not val_dates:
        return GateResult(
            GATE_OUT_OF_SAMPLE,
            False,
            ReasonCode.OOS_FAILED,
            {**base, "reason": "insufficient_dates", "min_abs_icir": MIN_ABS_ICIR},
        )
    train_mean, _, train_icir, _ = _ic_stats(
        rank_ic_series(factor_values.loc[train_ic_dates], fwd.loc[train_ic_dates])
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


def _significance_key(position: int) -> str:
    """闸③ 的候选键 = 零填充位置串（fix 9：多重性按候选位置计，不塌缩到表达式）。

    零填充保证 ``sorted`` 键序 = 入批位置序（``check_significance`` 以下标派生
    抽样种子），并让逐字重复的表达式各占一条假设。
    """
    return f"{position:04d}"


def admit_candidates(
    candidates: list[dict],
    panel: MarketPanel,
    *,
    seed: int,
    db_path: str | Path | None = None,
    ledger_source: str = "unknown",
) -> list[AdmissionResult]:
    """批量准入编排：结构查重 → 闸①② → 闸③ → 闸④ → 闸⑤ → 入库(active) → 保种。

    ``candidates`` 每条 dict：``expression`` / ``hypothesis`` / ``category``
    （必填）+ ``ast_json``（可选，缺省由表达式 ``serialize(parse())`` 派生）+
    ``source`` / ``alignment_score``（可选，透传落库）。返回与输入**同序**的
    ``AdmissionResult``；被拒候选项不落库（无 ``factors`` 行、无事件）。
    闸③ 以候选**位置**为键（逐字重复表达式各计一条假设，见 ``_significance_key``）。
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

    # 阶段 B（同批次一次）：闸③ 显著性（BH-FDR 需整批 p 值；键 = 候选位置）
    if pending:
        significance = check_significance(
            {_significance_key(item.position): item.ic for item in pending}, seed=seed
        )
        for item in pending:
            gate3 = significance[_significance_key(item.position)]
            item.gates.append(gate3)
            if not gate3.passed:
                outcomes[item.position] = AdmissionResult(
                    None, STATUS_REJECTED, tuple(item.gates), GATE_SIGNIFICANCE
                )

    # 阶段 C（逐候选）：闸④ → 闸⑤ → 入库前复检 → 入库(active) → 保种
    # 池因子批内求值缓存（同一面板上池因子的帧/IC 与候选无关；本批先入库者随后续
    # 候选入池首遇时自然进缓存——键 id 在单次调用内不复用表达式，无失效问题）
    pool_cache = _PoolEvalCache()
    for item in pending:
        if item.position in outcomes:
            continue
        pool = list_active_factors(db_path=db_path)
        gate4 = check_pool_increment(
            item.ic, item.values, pool, panel, db_path=db_path, pool_cache=pool_cache
        )
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
    _record_ledger(candidates, outcomes, source=ledger_source, db_path=db_path)
    return [outcomes[position] for position in range(len(candidates))]
