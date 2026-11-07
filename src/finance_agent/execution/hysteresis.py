"""共享方向滞回判定（add-signal-portfolio-risk-gate G5）——单一来源。

抽取自 ``nodes/risk.py::_evaluate_hysteresis``（add-decision-hysteresis 原语义）：
决策侧节点与信号生成路径（``signals.generate_signal``）共用本判定的
flip/needs_reaffirm 三目，禁止各自再写副本。

``evaluate_direction_hysteresis``（纯判定，不改方向、不落库）：

- ``flip`` = 前向存在且与当前方向不同；
- ``needs_reaffirm`` = 方向翻转且未申报证据增量（update-decision-buy-gates T3
  收窄：均衡带内执行的增量申报义务已移除——均衡带标志的效力仅要求 reasoning
  如实披露对峙点，MUST NOT 对执行动作施增量义务，首建/同向执行以价位逻辑与
  既有证据为充分依据放行；``balanced_zone`` 参数保留供判定记录落 state 观测）；
- ``applied`` 由消费侧填写（本函数恒 ``""``；决策侧回填中文处置文案，信号侧
  回填 ``keep_prior``）。

``apply_signal_hysteresis``（信号侧终态处置）：信号路径无 LLM 重申回路，判定
命中即终局——``needs_reaffirm``（即翻转且无增量）→ 维持前向
（``applied="keep_prior"``，吸收 LLM 采样抖动）；否则原样返回。
（历史值 ``HYSTERESIS_DOWNGRADE_BALANCED``：均衡带单独触发的降级已随 T3 收窄
不可达，常量保留供既有 trace 记录兼容。）
"""

from __future__ import annotations

from typing import Any

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

    ``balanced_zone`` 不再参与 ``needs_reaffirm``（T3 收窄），保留入参以维持
    调用方签名与判定记录字段兼容。
    """
    flip = prior_direction is not None and prior_direction != current_direction
    needs_reaffirm = flip and not incremental_claimed
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
    judgment["applied"] = ""
    return raw_direction, judgment
