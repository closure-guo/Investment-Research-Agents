"""regime 判定与陈旧度（确定性纯函数，MUST NOT 调用 LLM；T1）。

规则（spec crowding-regime「regime 判定与总仓位系数」/ design「判定 v1」）：

- **触发指标** = 小市值组成交额占比分位、全市场成交额分位；任一 ≥ ``danger``
  阈值 → danger；否则 ≥ ``elevated`` 阈值 → elevated；否则 normal；
- **Breadth 类**（涨跌家数/涨停跌停家数）不参与 v1 触发，但读数进 ``reasons``
  供展示（design 明文）；
- 分位为 null（历史样本不足）不触发，``reasons`` 注明
  ``insufficient_history:<指标>``；
- 行缺失 → ``normal`` + 1.0 + ``["no_regime_data"]``（缺失在执行摘要另行披露）；
- 同输入同输出（无时间/随机依赖）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from finance_agent.outcome.regime.config import RegimeConfig

# 触发指标分位键（v1 触发规则唯一口径；顺序 = reasons 输出顺序）
TRIGGER_PCTILE_KEYS: tuple[str, ...] = ("small_cap_share_pctile", "total_amount_pctile")
# Breadth 读数键（进 reasons/展示，不参与触发）
BREADTH_READING_KEYS: tuple[str, ...] = (
    "up_count",
    "down_count",
    "limit_up_count",
    "limit_down_count",
)


def _fmt(value: float) -> str:
    """读数格式化（str 最短往返形态）：0.93 → "0.93"、0.9 → "0.9"。"""
    return str(value)


def judge(row: Mapping[str, Any] | None, config: RegimeConfig) -> dict[str, Any]:
    """最新指标行 + 配置 → ``{"state","multiplier","reasons"}``（口径见模块 docstring）。"""
    if row is None:
        return {"state": "normal", "multiplier": 1.0, "reasons": ["no_regime_data"]}

    reasons: list[str] = []
    danger_hit = False
    elevated_hit = False
    for key in TRIGGER_PCTILE_KEYS:
        raw = row.get(key)
        if raw is None:
            reasons.append(f"insufficient_history:{key}")
            continue
        pctile = float(raw)
        if pctile >= config.danger_threshold:
            reasons.append(f"{key}={_fmt(pctile)}>={_fmt(config.danger_threshold)}")
            danger_hit = True
        elif pctile >= config.elevated_threshold:
            reasons.append(f"{key}={_fmt(pctile)}>={_fmt(config.elevated_threshold)}")
            elevated_hit = True
    for key in BREADTH_READING_KEYS:
        value = row.get(key)
        if value is not None:
            reasons.append(f"{key}={value}")

    if danger_hit:
        return {"state": "danger", "multiplier": config.multiplier_danger, "reasons": reasons}
    if elevated_hit:
        return {
            "state": "elevated",
            "multiplier": config.multiplier_elevated,
            "reasons": reasons,
        }
    return {"state": "normal", "multiplier": config.multiplier_normal, "reasons": reasons}


def staleness(trade_date: str, *, now: date | None = None, stale_days: int = 7) -> tuple[int, bool]:
    """数据日期距判定时刻的自然日数与陈旧标记。

    口径（spec crowding-regime：``stale`` = ``age_days > 7``，即超过 7 个自然日
    才判陈旧；边界 ``age_days == stale_days`` 不陈旧）。``trade_date`` 非规范
    ``YYYY-MM-DD`` → ``ValueError``（调用方按 corrupt 处置，不静默按 0 天）。
    """
    age_days = ((now or date.today()) - date.fromisoformat(str(trade_date))).days
    return age_days, age_days > stale_days
