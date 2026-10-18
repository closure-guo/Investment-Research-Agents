"""内置手工因子基线包（add-screening-ranker Task 2；关键设计决策 R5）。

计划 docs/superpowers/plans/2026-10-13-add-screening-ranker.md Global Constraints 与
R5（实施者不得自行改口径）：

- 五个 builtin（名称逐字）：``momentum_20`` / ``reversal_5`` / ``turnover_proxy`` /
  ``volatility_20`` / ``size_proxy``；``category="builtin"``、``source="builtin"``；
- **免 LLM 提案环节，但同样通过五道闸**（``validate.admit_candidates``）方可置为
  active——阈值与 LLM 提案因子完全一致（spec 场景：builtin 反转因子 ICIR=0.12 →
  被拒于闸②）；
- 幂等：``register_builtins`` 重跑时库内已存在结构同构者 → 被闸 0 结构查重拦下
  （``STRUCTURAL_DUPLICATE``），不重复入库（``find_structural_duplicate`` 覆盖
  candidate/active/degraded，retired 可重提）。

**代理口径的诚实披露（字段集限制）**：因子 DSL 只有价量六字段
（``open/high/low/close/volume/amount``），**无股本/流通股数据**，故：

- 「换手率」不可直接表达 → ``turnover_proxy`` 以**量比**代理：
  ``div(volume, ts_mean(volume, 20))``（当日成交量 / 近 20 日均量；量比与换手率
  在关注度/活跃度维度同向，但量纲与解释不同）；
- 「市值」不可直接表达 → ``size_proxy`` 以**成交额水平**代理：
  ``ts_mean(amount, 20)``（成交额与市值正相关，但受换手率影响，非市值本身）。

两者均以上述代理表达式的名义注册与展示，报告与后续评估不得把它们当作真实换手率 /
市值使用。builtin 包同时充当 LLM 因子的结构查重对照与池级增量（闸④）基线。

其余三个因子（20 日动量 ``delta(close,20)``、5 日反转 ``div(ts_mean(close,5),close)``、
20 日波动率 ``ts_std(delta(close,1),20)``）为教科书经典形态的直译；全部五条表达式
均在 DSL 默认复杂度上限内（深度 ≤4、自由参数 ≤3、字段 ≤3，单测逐条钉死）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.library import list_factors
from finance_agent.factors.monthly import (  # noqa: PLC2701 - 面板取数/原因码口径单一来源（同 monitor 先例）
    _panel_from_snapshot,
    _rejection_reason,
)
from finance_agent.factors.validate import AdmissionResult, admit_candidates

# 注册词表（逐字；库内 category/source 列值）
BUILTIN_CATEGORY = "builtin"
BUILTIN_SOURCE = "builtin"

# 注册任务闸③置换检验的固定种子（与提案月批同款 42；同面板同候选逐位可复现）
DEFAULT_REGISTRATION_SEED = 42


@dataclass(frozen=True)
class BuiltinFactor:
    """一条内置手工因子：名称（逐字）/ DSL 表达式 / 经济学假设文本。"""

    name: str
    expression: str
    hypothesis: str


BUILTIN_FACTORS: tuple[BuiltinFactor, ...] = (
    BuiltinFactor(
        name="momentum_20",
        expression="delta(close,20)",
        hypothesis=(
            "过去 20 个交易日涨幅高的标的短期内倾向继续跑赢：信息扩散缓慢与投资者"
            "反应不足使价格趋势具有持续性（Jegadeesh-Titman 动量效应）。"
        ),
    ),
    BuiltinFactor(
        name="reversal_5",
        expression="div(ts_mean(close,5),close)",
        hypothesis=(
            "近 5 日均价相对现价偏高的标的（即近周下跌者）短期内倾向反弹：短期流动"
            "性冲击造成的超跌在随后交易日部分修正（周度反转效应）。"
        ),
    ),
    BuiltinFactor(
        name="turnover_proxy",
        expression="div(volume,ts_mean(volume,20))",
        hypothesis=(
            "量比（当日成交量 / 近 20 日均量）度量关注度与交易活跃度的相对抬升，活跃"
            "度上升的标的短期收益分布更偏向正侧。DSL 无股本数据，以量比代理换手率口径。"
        ),
    ),
    BuiltinFactor(
        name="volatility_20",
        expression="ts_std(delta(close,1),20)",
        hypothesis=(
            "近 20 日已实现波动率刻画风险暴露与情绪强度，波动率层级在截面上区分收益"
            "分布（低波动异象方向由准入管线按 IC 符号与显著性裁决，本包不预设方向）。"
        ),
    ),
    BuiltinFactor(
        name="size_proxy",
        expression="ts_mean(amount,20)",
        hypothesis=(
            "近 20 日平均成交额刻画标的规模与流动性层级（成交额与市值正相关，大市值"
            "与高流动性伴随不同的风险溢价）。DSL 无股本数据，以成交额水平代理市值口径。"
        ),
    ),
)


def register_builtins(
    *,
    panel: MarketPanel,
    seed: int,
    factors: Sequence[BuiltinFactor] | None = None,
    db_path: str | Path | None = None,
) -> list[AdmissionResult]:
    """R5：builtin 逐条走 ``admit_candidates``（免提案、同五道闸），返回同序结论。

    ``category="builtin"``、``source="builtin"``、``hypothesis`` 为经济学描述文本
    （≥30 字，与提案合同同长度纪律）；``seed`` 透传闸③置换检验（同种子可复现）。
    已存在结构同构者 → 该条在闸 0 被拒（``STRUCTURAL_DUPLICATE``，``status="rejected"``，
    ``factor_id=None``）——幂等重跑不重复入库。

    ``factors`` 缺省 = 全量 ``BUILTIN_FACTORS``；注册日批（见
    ``run_factor_builtin_register``）传入**缺失子集**，避免对已注册条目重复走闸。
    """
    items = BUILTIN_FACTORS if factors is None else tuple(factors)
    candidates = [
        {
            "expression": item.expression,
            "hypothesis": item.hypothesis,
            "category": BUILTIN_CATEGORY,
            "source": BUILTIN_SOURCE,
        }
        for item in items
    ]
    return admit_candidates(candidates, panel, seed=seed, db_path=db_path, ledger_source="builtin")


def missing_builtins(*, db_path: str | Path | None = None) -> list[BuiltinFactor]:
    """库内缺失的 builtin 条目（存在性判据 = 同表达式，**任意状态**含 retired/degraded）。

    retired 视为已注册（MUST NOT 复活——「retired 可重提」属提案路径语义，基线包
    固定表达式不参与）；结构等价但字符串不同的条目由闸 0 兜底（提交后被
    ``STRUCTURAL_DUPLICATE`` 拒收，不重复入库）。
    """
    known = {str(row["expression"]).strip() for row in list_factors(db_path=db_path)}
    return [item for item in BUILTIN_FACTORS if item.expression.strip() not in known]


def run_factor_builtin_register(
    *,
    panel: MarketPanel | None = None,
    seed: int = DEFAULT_REGISTRATION_SEED,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """注册日批入口（job 零参数调用）：存在性幂等 → 缺失子集过五道闸 → 摘要。

    摘要 ``{registered, rejected, skipped, reasons}``：``skipped`` = 已存在条目
    （任意状态，含 retired——不回填）；``reasons`` = 未过闸者逐条
    ``{expression, gate, reason}``（原因码口径同提案月批）。全量已注册 → **不构造
    面板**（快照库不可达亦不失败）；缺失条目存在而面板不可得 → ``ValueError``
    上抛（job 记 failed，MUST NOT 以空面板放行注册）。
    """
    missing = missing_builtins(db_path=db_path)
    skipped = len(BUILTIN_FACTORS) - len(missing)
    if not missing:
        return {"registered": 0, "rejected": 0, "skipped": skipped, "reasons": []}
    if panel is None:
        panel = _panel_from_snapshot(snapshot_db_path)
    results = register_builtins(panel=panel, seed=seed, factors=missing, db_path=db_path)
    registered = sum(1 for result in results if result.factor_id is not None)
    reasons = [
        {
            "expression": missing[index].expression,
            "gate": result.rejected_gate,
            "reason": _rejection_reason(result),
        }
        for index, result in enumerate(results)
        if result.factor_id is None
    ]
    return {
        "registered": registered,
        "rejected": len(results) - registered,
        "skipped": skipped,
        "reasons": reasons,
    }
