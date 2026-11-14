"""决策契约字段解析（enforce-decision-contract-at-execution）。

决策层（trader/risk_judge）申报的执行约束——仓位档位与 ``exit_schedule`` 首批
比例——的自由文本 → 结构化口径，供信号契约与订单规划消费（issue #593）：

- 档位锚与 ``src/finance_agent/prompts/trader.md`` 档位表对齐取下沿（保守方向，
  宁小勿大；600780 2026-11-12 方案 FM 批准文本「light 档下沿约 10%」即此口径）；
- 首批比例只认「首批 …N%」的单一明确匹配；无/多匹配或 (0,1] 之外 → ``None``
  （调用方退化为既有全额退出并留痕 ``schedule_unparsed``）。
"""

from __future__ import annotations

import re

POSITION_TIER_WEIGHTS: dict[str, float] = {
    "light": 0.10,
    "moderate": 0.30,
    "heavy": 0.50,
}

# 「首批 …N%」：首批与其后的第一个百分数之间允许任意非 % 的字符（含其他数字，
# 如「首批在现价 21.72 附近即减持有敞口的 70%」）；出现两处 → 调用方判歧义。
_FIRST_BATCH_RE = re.compile(r"首批[^%。；;]*?(\d+(?:\.\d+)?)\s*%")


def parse_position_tier(value: object) -> str | None:
    """档位名 → 归一小写键；非字符串/未知档位/百分比串 → ``None``（未申报档位）。"""
    if not isinstance(value, str):
        return None
    tier = value.strip().lower()
    return tier if tier in POSITION_TIER_WEIGHTS else None


def parse_first_batch_ratio(schedule: object) -> float | None:
    """``exit_schedule`` 自由文本 → 首批比例（(0,1]）；无/多匹配或越界 → ``None``。"""
    if not isinstance(schedule, str) or not schedule.strip():
        return None
    matches = _FIRST_BATCH_RE.findall(schedule)
    if len(matches) != 1:
        return None
    try:
        ratio = float(matches[0]) / 100.0
    except ValueError:
        return None
    return ratio if 0.0 < ratio <= 1.0 else None
