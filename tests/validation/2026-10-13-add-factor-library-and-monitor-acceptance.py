"""add-factor-library-and-monitor Task 7 对照验收脚本：5 经典因子 vs 5 噪声因子。

用法::

    uv run python tests/validation/2026-10-13-add-factor-library-and-monitor-acceptance.py
    uv run python tests/validation/2026-10-13-add-factor-library-and-monitor-acceptance.py --out <路径.json>

**合成面板（固定种子，零网络）**：迭代生成价格/成交过程，次日收益**显式依赖**
五个经典因子当日的横截面 zscore（自满足构造——生成过程显式依赖这些量，
因子因而对下一期收益有真实预测成分）::

    r_{t+1} = Σ_k β_k · z_k,t + σ · ε          （z_k,t = 因子 k 当日横截面 zscore）

- 动量 ``delta(close,20)``：过去 1 个月（20 日）涨幅（Jegadeesh-Titman 动量）；
- 反转 ``div(ts_mean(close,5),close)``：≈ 1 − (4r_t+3r_{t−1}+2r_{t−2}+r_{t−3})/5，
  近一周跌则高（周度短期反转）；
- 波动 ``div(1,ts_std(delta(close,1),10))``：10 日已实现波动倒数（低波动异象）；
- 流动性 ``div(1,ts_mean(amount,5))``：成交额水平倒数（非流动性溢价）；
- 量价协同 ``ts_corr(close,volume,10)``：10 日价量滚动相关（量价协同）。

**载荷（β_k）与窗口的选取（实测调参，非拍脑袋）**：所有经典因子对价格路径都有
反馈（因子驱动收益 → 收益进价格 → 价格类因子互为镜像），实测若五因子同载荷且
动量/反转同窗口（10 日 vs 1 日），动量~反转 IC 序列相关达 −0.76 > 0.7 会被闸④按
冗余拒收；载荷取 ``β = (动量 0.006, 反转 0.009, 波动 0.006, 流动性 0.006,
量价协同 0.006)``、σ = 0.02（低信噪比）时，五因子 IC 同号为正、两两 |IC 相关| ≤ 0.57
且逐条池增量 > 0（见 JSON artifact 的 ``classic_ic_correlations`` 与逐条闸④ detail）。

**口径注记（诚实披露）**：spec/proposal 的经典因子清单含「市值 / 换手」，但 DSL
字段集只有价量六字段（无股本 / 流通股数据），两者不可直接表达——按计划 Task 7
口径注记以「流动性（成交额水平代理，amount 的倒数）」与「量价协同」替代，并在
验证报告写明替换理由。

噪声因子 = 固定种子随机表达式，**字段限 volume**、只用变异性算子（``ts_std`` /
``ts_rank`` / ``delta`` / ``abs``）：volume 只通过「与价格的滚动相关」进入收益
过程，其水平/变异性与收益无关（实测 |ICIR| ≤ 0.12）→ 对下一期收益无预测成分。
不使用 amount 类表达式：amount 水平本身驱动收益，其水平/变异性表达式会成为
「流动性」因子的代理（走冗余拒收），那属于冗余而非噪声，不能冒充噪声对照。

脚本断言（Task 7 验收）：5 经典因子全部 ``active``、5 噪声因子全部被拒；逐条记录
被拒闸口与原因码；结果 JSON 落
``tests/validation/2026-10-13-add-factor-library-and-monitor-acceptance.json``。
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE_ROOT / "src"))

from finance_agent.factors.dsl import (  # noqa: E402
    AstNode,
    Call,
    Field,
    Number,
    is_subtree_isomorphic,
    parse,
    serialize,
)
from finance_agent.factors.eval_engine import (  # noqa: E402
    MarketPanel,
    cross_section_zscore,
    evaluate,
    forward_returns,
    rank_ic_series,
)
from finance_agent.factors.library import list_factors  # noqa: E402
from finance_agent.factors.validate import _ic_stats, admit_candidates  # noqa: E402

# 面板与随机种子（固定 → 逐位可复现）
PANEL_SEED = 20261018
PANEL_DATES = 150
PANEL_CODES = 80
RETURN_NOISE = 0.02  # σ：低信噪比（压低因子间反馈镜像强度，见模块 docstring）
WARMUP_DAYS = 22  # 最长窗口 20（动量）+ 1 天缓冲

# 五个经典因子：(标签, 表达式, 收益载荷 β_k)（载荷选取依据见模块 docstring）
CLASSIC_FACTORS: tuple[tuple[str, str, float], ...] = (
    ("动量", "delta(close,20)", 0.006),
    ("反转", "div(ts_mean(close,5),close)", 0.009),
    ("波动", "div(1,ts_std(delta(close,1),10))", 0.006),
    ("流动性", "div(1,ts_mean(amount,5))", 0.006),
    ("量价协同", "ts_corr(close,volume,10)", 0.006),
)

# 噪声因子随机表达式：字段限 volume + 变异性算子（与收益机制无关，见模块 docstring）
NOISE_SEED = 20261019
NOISE_FACTOR_COUNT = 5
_NOISE_SHAPES = 7  # 生成器的同构类数量（见 _noise_expression）

_HYPOTHESIS = "合成面板验收用假设文本：描述该因子与下一期收益的预期关系（长度达标）。"
OUT_JSON = (
    Path(__file__).resolve().with_name("2026-10-13-add-factor-library-and-monitor-acceptance.json")
)


def _noise_expression(rng: np.random.Generator) -> AstNode:
    """一条噪声表达式（volume 的变异性/形态算子；窗口 2–12，恒在复杂度上限内）。

    形态（同构类）共 7 种，足以抽 5 条互不同构；全部只用 volume 的水平无关量
    （波动、时序秩、变化、相对自身均值的偏离），与收益生成机制无关。
    """
    template = int(rng.integers(0, 7))
    field = Field("volume")
    if template == 0:
        return Call("ts_std", (field, Number(int(rng.integers(3, 13)))))
    if template == 1:
        return Call("ts_rank", (field, Number(int(rng.integers(3, 13)))))
    if template == 2:
        return Call("delta", (field, Number(int(rng.integers(3, 13)))))
    if template == 3:
        return Call("abs", (Call("delta", (field, Number(int(rng.integers(2, 9))))),))
    if template == 4:
        return Call(
            "sub",
            (field, Call("ts_mean", (field, Number(int(rng.integers(3, 13)))))),
        )
    if template == 5:
        return Call(
            "div",
            (
                Call("ts_std", (field, Number(int(rng.integers(3, 7))))),
                Call("ts_std", (field, Number(int(rng.integers(8, 13))))),
            ),
        )
    return Call(
        "ts_std",
        (Call("delta", (field, Number(1))), Number(int(rng.integers(3, 9)))),
    )


def _noise_expressions() -> list[str]:
    """固定种子生成 ``NOISE_FACTOR_COUNT`` 条互不同构且与经典因子不同构的噪声表达式。"""
    rng = np.random.default_rng(NOISE_SEED)
    classic_nodes = [parse(expression) for _, expression, _ in CLASSIC_FACTORS]
    nodes: list[AstNode] = []
    expressions: list[str] = []
    for _ in range(1000):
        if len(expressions) >= NOISE_FACTOR_COUNT:
            break
        node = _noise_expression(rng)
        if any(is_subtree_isomorphic(node, other) for other in [*nodes, *classic_nodes]):
            continue
        nodes.append(node)
        expressions.append(serialize(node))
    if len(expressions) != NOISE_FACTOR_COUNT:
        raise RuntimeError("噪声表达式生成器未产出足量互不同构样本")
    return expressions


def _zscore_last(frame: pd.DataFrame) -> pd.Series:
    """因子帧最后一行的横截面 zscore（NaN → 0，热身期不注入信号）。"""
    return cross_section_zscore(frame).iloc[-1].fillna(0.0)


def build_panel() -> tuple[MarketPanel, dict]:
    """迭代生成合成面板：次日收益显式依赖五经典因子的当日横截面 zscore。

    返回 ``(panel, diagnostics)``；diagnostics 含逐经典因子的当日 IC 均值/ICIR 与
    因子值横截面相关矩阵（验收报告证据）。
    """
    rng = np.random.default_rng(PANEL_SEED)
    codes = PANEL_CODES
    dates = PANEL_DATES
    close = np.full((dates, codes), np.nan)
    volume = np.full((dates, codes), np.nan)
    amount = np.full((dates, codes), np.nan)
    close[0] = 100.0
    shape = np.zeros(codes)  # 成交量 AR(1) 状态
    for step in range(1, dates):
        shape = 0.5 * shape + rng.normal(size=codes)
        volume[step - 1] = np.exp(0.4 * shape)
        # 流动性水平逐日独立（不做 AR(1)）：水平本身不累积进价格路径，避免
        # 「持续信号 → 价格路径代理」把动量/反转因子带成它的镜像（实测相关性 0.64）
        amount[step - 1] = 1e9 * np.exp(0.5 * rng.normal(size=codes))
        partial = MarketPanel(
            fields={
                "open": pd.DataFrame(close[:step]),
                "high": pd.DataFrame(close[:step]),
                "low": pd.DataFrame(close[:step]),
                "close": pd.DataFrame(close[:step]),
                "volume": pd.DataFrame(volume[:step]),
                "amount": pd.DataFrame(amount[:step]),
            }
        )
        signal = np.zeros(codes)
        if step >= WARMUP_DAYS:
            for _, expression, beta in CLASSIC_FACTORS:
                signal += beta * _zscore_last(evaluate(parse(expression), partial)).to_numpy()
        returns = signal + RETURN_NOISE * rng.normal(size=codes)
        close[step] = close[step - 1] * (1.0 + returns)
    volume[dates - 1] = np.exp(0.4 * shape)
    amount[dates - 1] = 1e9 * np.exp(0.5 * rng.normal(size=codes))
    labels = [(date(2026, 1, 5) + timedelta(days=offset)).isoformat() for offset in range(dates)]
    code_labels = [f"a{index:03d}" for index in range(codes)]

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=labels, columns=code_labels)

    panel = MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(volume),
            "amount": frame(amount),
        }
    )
    fwd = forward_returns(panel.fields["close"])
    diagnostics: dict = {
        "factors": {},
        "ic_correlations": {},
        "panel": {
            "dates": dates,
            "codes": codes,
            "seed": PANEL_SEED,
            "return_noise": RETURN_NOISE,
            "signal_betas": {label: beta for label, _, beta in CLASSIC_FACTORS},
        },
    }
    values: dict[str, pd.DataFrame] = {}
    for label, expression, beta in CLASSIC_FACTORS:
        frame_values = evaluate(parse(expression), panel)
        values[label] = frame_values
        mean, std, icir, n_samples = _ic_stats(rank_ic_series(frame_values, fwd))
        diagnostics["factors"][label] = {
            "expression": expression,
            "beta": beta,
            "ic_mean": None if math.isnan(mean) else round(mean, 4),
            "ic_std": None if math.isnan(std) else round(std, 4),
            "icir": round(icir, 4),
            "ic_days": n_samples,
        }
    labels_list = [label for label, _, _ in CLASSIC_FACTORS]
    for left in labels_list:
        for right in labels_list:
            if left >= right:
                continue
            pair = pd.concat(
                [
                    rank_ic_series(values[left], fwd).rename("l"),
                    rank_ic_series(values[right], fwd).rename("r"),
                ],
                axis=1,
            ).dropna()
            rho = float(pair["l"].corr(pair["r"]))
            diagnostics["ic_correlations"][f"{left}~{right}"] = round(rho, 4)
    return panel, diagnostics


def run_acceptance() -> dict:
    """跑一次验收：生成面板 → ``admit_candidates`` → 逐条结论 + 断言。"""
    panel, diagnostics = build_panel()
    noise_expressions = _noise_expressions()
    candidates = [
        {"expression": expression, "hypothesis": _HYPOTHESIS, "category": category}
        for category, (_, expression, _) in zip(
            ("动量", "反转", "波动率", "流动性", "价量"), CLASSIC_FACTORS, strict=True
        )
    ] + [
        {"expression": expression, "hypothesis": _HYPOTHESIS, "category": "价量"}
        for expression in noise_expressions
    ]
    labels = [f"经典:{label}" for label, _, _ in CLASSIC_FACTORS] + [
        f"噪声:{index + 1}" for index in range(len(noise_expressions))
    ]
    db_dir = tempfile.mkdtemp(prefix="factor_acceptance_")
    db_path = str(Path(db_dir) / "factor_library.db")
    results = admit_candidates(candidates, panel, seed=PANEL_SEED, db_path=db_path)
    fwd = forward_returns(panel.fields["close"])
    rows = []
    for label, candidate, result in zip(labels, candidates, results, strict=True):
        rejected = next((gate for gate in result.gates if not gate.passed), None)
        ic_mean, _, icir, _ = _ic_stats(
            rank_ic_series(evaluate(parse(candidate["expression"]), panel), fwd)
        )
        rows.append(
            {
                "label": label,
                "expression": candidate["expression"],
                "status": result.status,
                "factor_id": result.factor_id,
                "rejected_gate": result.rejected_gate,
                "reason": None if rejected is None else str(rejected.reason),
                "ic_mean": None if math.isnan(ic_mean) else round(ic_mean, 4),
                "icir": round(icir, 4),
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
    classic_rows = [row for row in rows if row["label"].startswith("经典:")]
    noise_rows = [row for row in rows if row["label"].startswith("噪声:")]
    verdict = {
        "classic_active": sum(1 for row in classic_rows if row["status"] == "active"),
        "classic_total": len(classic_rows),
        "noise_rejected": sum(1 for row in noise_rows if row["status"] == "rejected"),
        "noise_total": len(noise_rows),
        "passed": (
            all(row["status"] == "active" for row in classic_rows)
            and all(row["status"] == "rejected" for row in noise_rows)
        ),
    }
    return {
        "panel": diagnostics["panel"],
        "classic_factor_diagnostics": diagnostics["factors"],
        "classic_ic_correlations": diagnostics["ic_correlations"],
        "noise_expressions": noise_expressions,
        # 不落 db_path（临时目录逐次不同）：artifact 逐位可复现（同种子同输出）
        "library_rows": [
            {
                "id": row["id"],
                "expression": row["expression"],
                "status": row["status"],
                "pool_increment": row["pool_increment"],
            }
            for row in list_factors(db_path=db_path)
        ],
        "candidates": rows,
        "verdict": verdict,
    }


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
    print("\n[经典因子诊断] 表达式 / IC 均值 / ICIR / 有效日")
    for label, stats in summary["classic_factor_diagnostics"].items():
        print(
            f"  {label:<6} {stats['expression']:<34} ic_mean={stats['ic_mean']:>7}"
            f" icir={stats['icir']:>7} days={stats['ic_days']}"
        )
    print(
        "\n[经典因子 IC 序列相关] "
        + json.dumps(summary["classic_ic_correlations"], ensure_ascii=False)
    )
    print("\n[噪声因子表达式]")
    for expression in summary["noise_expressions"]:
        print(f"  {expression}")
    print("\n[准入结论] 候选 / 状态 / 被拒闸口 / 原因码 / ICIR")
    for row in summary["candidates"]:
        print(
            f"  {row['label']:<10} {row['expression']:<34} {row['status']:<8}"
            f" gate={row['rejected_gate']} reason={row['reason']} icir={row['icir']}"
        )
    print(f"\n[verdict] {summary['verdict']}")
    print(f"[artifact] {out_path}")
    return 0 if summary["verdict"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
