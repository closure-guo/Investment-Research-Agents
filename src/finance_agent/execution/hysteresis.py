"""共享方向滞回判定（add-signal-portfolio-risk-gate G5）——单一来源。

抽取自 ``nodes/risk.py::_evaluate_hysteresis``（add-decision-hysteresis 原语义，
行为保持）：决策侧节点与信号生成路径（``signals.generate_signal``）共用本判定的
flip/needs_reaffirm 三目，禁止各自再写副本。

``evaluate_direction_hysteresis``（纯判定，不改方向、不落库）：

- ``flip`` = 前向存在且与当前方向不同；
- ``needs_reaffirm`` =（均衡带内执行动作 或 方向翻转）且未申报证据增量；
  「执行动作」以方向符号判定：``long``/``short`` 为执行方向（决策语义 buy/sell
  的 direction 形式），``neutral``/``flat`` 非执行；
- ``applied`` 由消费侧填写（本函数恒 ``""``；决策侧回填中文处置文案，信号侧回填
  ``keep_prior``/``downgrade_balanced``）。

``apply_signal_hysteresis``（信号侧终态处置）：信号路径无 LLM 重申回路，判定
命中即终局——``needs_reaffirm 且 flip`` → 维持前向（``applied="keep_prior"``，
吸收 LLM 采样抖动）；均衡带内执行无增量 → 降为 ``flat``
（``applied="downgrade_balanced"``，与决策侧降级观望同语义）；否则原样返回。
"""

from __future__ import annotations

from typing import Any

# 执行方向（决策语义 buy/sell 的 direction 形式；A 股信号路径无做空，short 仅决策侧出现）
_ACTIONABLE_DIRECTIONS: tuple[str, ...] = ("long", "short")

HYSTERESIS_KEEP_PRIOR = "keep_prior"
HYSTERESIS_DOWNGRADE_BALANCED = "downgrade_balanced"


def evaluate_direction_hysteresis(
    prior_direction: str | None,
    current_direction: str,
    *,
    balanced_zone: bool,
    incremental_claimed: bool,
) -> dict[str, Any]:
    """判定方向翻转与是否需重申（纯函数；语义见模块 docstring）。

    与决策侧旧实现逐位一致（G5 行为保持抽取）；``applied`` 恒空串，由消费侧填写。
    """
    flip = prior_direction is not None and prior_direction != current_direction
    actionable = current_direction in _ACTIONABLE_DIRECTIONS
    needs_reaffirm = (balanced_zone and actionable and not incremental_claimed) or (
        flip and not incremental_claimed
    )
    return {"flip": flip, "needs_reaffirm": needs_reaffirm, "applied": ""}


def apply_signal_hysteresis(
    raw_direction: str,
    prior_direction: str | None,
    *,
    balanced_zone: bool,
    incremental_claimed: bool,
) -> tuple[str, dict[str, Any]]:
    """信号侧滞回终态：返回 ``(最终方向, 判定记录)``（处置语义见模块 docstring）。"""
    judgment = evaluate_direction_hysteresis(
        prior_direction,
        raw_direction,
        balanced_zone=balanced_zone,
        incremental_claimed=incremental_claimed,
    )
    if judgment["needs_reaffirm"] and judgment["flip"]:
        judgment["applied"] = HYSTERESIS_KEEP_PRIOR
        return str(prior_direction), judgment
    if judgment["needs_reaffirm"]:
        judgment["applied"] = HYSTERESIS_DOWNGRADE_BALANCED
        return "flat", judgment
    judgment["applied"] = ""
    return raw_direction, judgment
