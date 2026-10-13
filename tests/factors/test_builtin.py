"""factors/builtin.py 单测（add-screening-ranker Task 2）。

计划 docs/superpowers/plans/2026-10-13-add-screening-ranker.md 关键设计决策 R5 与
Global Constraints：五个内置手工因子（名称逐字 ``momentum_20`` / ``reversal_5`` /
``turnover_proxy`` / ``volatility_20`` / ``size_proxy``），``category="builtin"``、
``source="builtin"``；免 LLM 提案环节但**同样通过五道闸**方可置为 active；幂等重跑
不重复入库（结构查重命中 → ``STRUCTURAL_DUPLICATE``）。

spec 场景（factor-library delta）：「builtin 反转因子在准入评估中 ICIR = 0.12 →
被拒于闸②，与 LLM 提案因子适用同一阈值」——本文件以纯噪声面板（无反转结构，固定
种子）复现实测 ICIR ≈ 0.119，并以同一表达式、不同 source（builtin vs proposal）
两条候选验证判定一致。

强信号面板（固定种子）：次日收益显式依赖五因子当日横截面 zscore
（``r_{t+1} = Σ_k β_k z_k,t + σ ε_t``，异质持久波动供 volatility_20 有横截面结构）。
实测结论（seed 20261013、120×40、β 见脚本、σ=0.03）：momentum_20 被拒于闸②
（ICIR ≈ 0.23 < 0.3），其余四个 active——「至少一个 active」达标且闸②对 builtin
同样生效。所有断言在固定种子下逐位可复现。
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from finance_agent.factors.builtin import BUILTIN_FACTORS, BuiltinFactor, register_builtins
from finance_agent.factors.constraints import MIN_ABS_ICIR
from finance_agent.factors.dsl import (
    MAX_AST_DEPTH,
    MAX_FIELDS,
    MAX_FREE_PARAMS,
    ast_depth,
    count_fields,
    count_free_params,
    parse,
)
from finance_agent.factors.eval_engine import (
    MarketPanel,
    cross_section_zscore,
    evaluate,
)
from finance_agent.factors.library import list_factors
from finance_agent.factors.validate import (
    GATE_COMPUTABILITY,
    GATE_DEDUP,
    GATE_SINGLE_FACTOR,
    admit_candidates,
)

# 名称/表达式逐字（Global Constraints + 代理口径；momentum/reversal/volatility 为
# 语义基线，turnover/size 的两个代理表达式的来源见被测模块 docstring 的诚实披露）
_EXPECTED: tuple[tuple[str, str], ...] = (
    ("momentum_20", "delta(close,20)"),
    ("reversal_5", "div(ts_mean(close,5),close)"),
    ("turnover_proxy", "div(volume,ts_mean(volume,20))"),
    ("volatility_20", "ts_std(delta(close,1),20)"),
    ("size_proxy", "ts_mean(amount,20)"),
)
_REVERSAL_EXPRESSION = "div(ts_mean(close,5),close)"

# 强信号面板参数（固定种子；实测结论见模块 docstring）
_STRONG_SEED = 20261013
_STRONG_DATES = 120
_STRONG_CODES = 40
_RETURN_NOISE = 0.03
_VOL_HETEROGENEITY = 0.3
_WARMUP_DAYS = 22
_BETAS: tuple[tuple[str, float], ...] = (
    ("momentum_20", 0.006),
    ("reversal_5", 0.009),
    ("turnover_proxy", 0.006),
    ("volatility_20", 0.006),
    ("size_proxy", 0.006),
)

# 无反转结构（纯噪声）面板参数：固定种子的实测 ICIR ≈ 0.119（≈ spec 场景 0.12）
_NOISE_SEED = 20261045
_NOISE_DATES = 45
_NOISE_CODES = 40

_HYPOTHESIS = "测试用经济学假设文本：描述该因子与下一期收益的预期关系（长度达标）。"


# ── 合成面板构造 ────────────────────────────────────────────────────────────


def _labels(dates: int, codes: int) -> tuple[list[str], list[str]]:
    """连续日期与六位代码标签（固定起点，确定性）。"""
    days = [(date(2026, 1, 5) + timedelta(days=offset)).isoformat() for offset in range(dates)]
    return days, [f"c{index:03d}" for index in range(codes)]


def _strong_panel() -> MarketPanel:
    """强信号面板：次日收益显式依赖五 builtin 因子当日 zscore（含异质持久波动）。"""
    rng = np.random.default_rng(_STRONG_SEED)
    dates, codes = _labels(_STRONG_DATES, _STRONG_CODES)
    width = _STRONG_CODES
    close = np.full((_STRONG_DATES, width), np.nan)
    volume = np.full((_STRONG_DATES, width), np.nan)
    amount = np.full((_STRONG_DATES, width), np.nan)
    close[0] = 100.0
    shape = np.zeros(width)  # 成交量 AR(1) 状态
    base_vol = np.exp(_VOL_HETEROGENEITY * rng.normal(size=width))  # 持久异质波动
    expressions = dict(_EXPECTED)
    betas = dict(_BETAS)

    def frame(values: np.ndarray, upto: int) -> pd.DataFrame:
        return pd.DataFrame(values[:upto], index=dates[:upto], columns=codes)

    for step in range(1, _STRONG_DATES):
        shape = 0.5 * shape + rng.normal(size=width)
        volume[step - 1] = np.exp(0.4 * shape)
        amount[step - 1] = 1e9 * np.exp(0.5 * rng.normal(size=width))
        partial = MarketPanel(
            fields={
                "open": frame(close, step),
                "high": frame(close, step),
                "low": frame(close, step),
                "close": frame(close, step),
                "volume": frame(volume, step),
                "amount": frame(amount, step),
            }
        )
        signal = np.zeros(width)
        if step >= _WARMUP_DAYS:
            for name, _expression in _EXPECTED:
                zscores = (
                    cross_section_zscore(evaluate(parse(expressions[name]), partial))
                    .iloc[-1]
                    .fillna(0.0)
                    .to_numpy()
                )
                signal += betas[name] * zscores
        close[step] = close[step - 1] * (
            1.0 + signal + _RETURN_NOISE * base_vol * rng.normal(size=width)
        )
    volume[_STRONG_DATES - 1] = np.exp(0.4 * shape)
    amount[_STRONG_DATES - 1] = 1e9 * np.exp(0.5 * rng.normal(size=width))
    return MarketPanel(
        fields={
            "open": frame(close, _STRONG_DATES),
            "high": frame(close, _STRONG_DATES),
            "low": frame(close, _STRONG_DATES),
            "close": frame(close, _STRONG_DATES),
            "volume": frame(volume, _STRONG_DATES),
            "amount": frame(amount, _STRONG_DATES),
        }
    )


def _noise_panel() -> MarketPanel:
    """无结构面板：纯随机游走（次日收益与任何因子无关）——反转因子真实预测成分为 0。"""
    rng = np.random.default_rng(_NOISE_SEED)
    dates, codes = _labels(_NOISE_DATES, _NOISE_CODES)
    returns = np.zeros((_NOISE_DATES, _NOISE_CODES))
    returns[1:] = 0.02 * rng.normal(size=(_NOISE_DATES - 1, _NOISE_CODES))
    close = 100.0 * np.cumprod(1.0 + returns, axis=0)

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=dates, columns=codes)

    flat = close * 0.0 + 1_000_000.0
    return MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(flat),
            "amount": frame(flat * 100.0),
        }
    )


# ── builtin 目录（名称逐字 + DSL 默认复杂度上限） ───────────────────────────


def test_builtin_catalog_names_and_expressions_are_verbatim() -> None:
    """五个 builtin 的名称/表达式逐字；hypothesis 为经济学描述（≥30 字）。"""
    assert [item.name for item in BUILTIN_FACTORS] == [name for name, _ in _EXPECTED]
    assert [item.expression for item in BUILTIN_FACTORS] == [expr for _, expr in _EXPECTED]
    for item in BUILTIN_FACTORS:
        assert isinstance(item, BuiltinFactor)
        assert len(item.hypothesis.strip()) >= 30, item.name


@pytest.mark.parametrize("item", BUILTIN_FACTORS, ids=lambda item: item.name)
def test_builtin_expression_passes_default_complexity_caps(item: BuiltinFactor) -> None:
    """逐条验证 DSL 默认复杂度上限（depth ≤ 4、params ≤ 3、fields ≤ 3）。"""
    node = parse(item.expression)  # 默认路径：超限即 FactorComplexityError
    assert ast_depth(node) <= MAX_AST_DEPTH
    assert count_free_params(node) <= MAX_FREE_PARAMS
    assert count_fields(node) <= MAX_FIELDS


# ── 注册路径（R5） ──────────────────────────────────────────────────────────


def test_register_builtins_admits_baseline_on_strong_panel(tmp_path) -> None:
    """强信号面板：builtin 走完整五道闸，至少一个 active 且 category/source 逐字。

    实测（固定种子）：反转因子被拒于闸②（ICIR ≈ 0.23，与 LLM 因子同阈值）；
    其余四个 active。
    """
    db = str(tmp_path / "factor_library.db")
    results = register_builtins(panel=_strong_panel(), seed=_STRONG_SEED, db_path=db)

    assert len(results) == len(BUILTIN_FACTORS)
    by_name = dict(zip([item.name for item in BUILTIN_FACTORS], results, strict=True))
    assert by_name["reversal_5"].status == "rejected"
    assert by_name["reversal_5"].rejected_gate == GATE_SINGLE_FACTOR
    rejected_gate = next(gate for gate in by_name["reversal_5"].gates if not gate.passed)
    assert str(rejected_gate.reason) == "WEAK_ICIR"
    assert abs(rejected_gate.detail["icir"]) < MIN_ABS_ICIR
    for name in ("momentum_20", "turnover_proxy", "volatility_20", "size_proxy"):
        assert by_name[name].status == "active", name
    assert sum(1 for result in results if result.status == "active") >= 1

    rows = {row["id"]: row for row in list_factors(db_path=db)}
    active_ids = [result.factor_id for result in results if result.status == "active"]
    assert len(rows) == len(active_ids)  # 被拒者不落库
    for factor_id in active_ids:
        row = rows[factor_id]
        assert row["category"] == "builtin"
        assert row["source"] == "builtin"
        assert row["status"] == "active"
    active_expressions = {rows[factor_id]["expression"] for factor_id in active_ids}
    assert active_expressions == {
        expression for name, expression in _EXPECTED if by_name[name].status == "active"
    }


def test_builtin_and_proposal_get_same_gate2_verdict(tmp_path) -> None:
    """spec 场景：反转因子 ICIR ≈ 0.12 → 被拒于闸②，builtin 与 LLM 提案同阈值。

    无反转结构面板（纯噪声，固定种子）实测 ICIR ≈ 0.119；同一表达式的 builtin
    与 proposal 两条候选在闸②拿到逐位相同的统计量与阈值（``MIN_ABS_ICIR``）。
    """
    panel = _noise_panel()
    db = str(tmp_path / "factor_library.db")
    candidates = [
        {
            "expression": _REVERSAL_EXPRESSION,
            "hypothesis": _HYPOTHESIS,
            "category": "builtin",
            "source": "builtin",
        },
        {
            "expression": _REVERSAL_EXPRESSION,
            "hypothesis": _HYPOTHESIS,
            "category": "反转",
            "source": "proposal",
        },
    ]
    results = admit_candidates(candidates, panel, seed=_NOISE_SEED, db_path=db)

    assert [result.status for result in results] == ["rejected", "rejected"]
    for result in results:
        assert result.rejected_gate == GATE_SINGLE_FACTOR
        assert [gate.gate for gate in result.gates] == [
            GATE_DEDUP,
            GATE_COMPUTABILITY,
            GATE_SINGLE_FACTOR,
        ]
        verdict = result.gates[-1]
        assert str(verdict.reason) == "WEAK_ICIR"
        assert verdict.detail["min_abs_icir"] == MIN_ABS_ICIR
        assert verdict.detail["icir"] == pytest.approx(0.119, abs=1e-3)
        assert abs(verdict.detail["icir"]) < MIN_ABS_ICIR
    assert results[0].gates[-1].detail == results[1].gates[-1].detail  # builtin ≡ proposal
    assert list_factors(db_path=db) == []  # 被拒不落库


def test_register_builtins_is_idempotent(tmp_path) -> None:
    """幂等重跑：已入库者被结构查重拦下（STRUCTURAL_DUPLICATE），库内零新增。"""
    panel = _strong_panel()
    db = str(tmp_path / "factor_library.db")
    first = register_builtins(panel=panel, seed=_STRONG_SEED, db_path=db)
    rows_after_first = list_factors(db_path=db)
    assert rows_after_first  # 强信号面板上至少一个 active

    second = register_builtins(panel=panel, seed=_STRONG_SEED, db_path=db)

    assert list_factors(db_path=db) == rows_after_first  # 零新增、零状态变化
    for before, after in zip(first, second, strict=True):
        if before.status != "active":
            assert after.status == before.status  # 未入库者原样被拒（同闸同因）
            assert after.rejected_gate == before.rejected_gate
            continue
        assert after.status == "rejected"
        assert after.rejected_gate == GATE_DEDUP
        duplicate = next(gate for gate in after.gates if not gate.passed)
        assert str(duplicate.reason) == "STRUCTURAL_DUPLICATE"
        assert duplicate.detail["duplicate_of"] == before.factor_id
