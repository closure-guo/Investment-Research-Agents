"""track-record 读模型门控（C6①：docs/architecture-debt/c6-small-items.md 小件①）。

§1.9「10 样本红线」（openspec settlement-significance-stats 单一总纲）的生产侧
唯一常量与统一门控。此前 5 处各自为政（api.py 两处字面复制 + report/
significance/segments 各写一遍），真相源在 evals/outcome/caliber——生产不可
import evals，纪律以抄写传播。本模块立生产侧真相源；evals 侧反向 re-export
（先例：caliber 对 judgment NEUTRAL_BAND 的别名再导出）。

语义站点（同一总纲「日主可判定样本 < 10」）：
- 胜率门控（overview / 回避正确率）
- IC 单期样本（significance.monthly_ic「样本不足」标注）
- 蒙特卡洛零模型（report 零模型 SHALL NOT 产出）
- 分桶样本不足（segments.insufficient）
"""

from __future__ import annotations

MIN_SETTLED_FOR_WINRATE = 10  # §1.9 红线：settled < 10 不报胜率/不产出零模型


def gate_rate(rate: float | None, settled: int | None) -> float | None:
    """按 §1.9 红线门控比率读数：settled < MIN_SETTLED_FOR_WINRATE → None。

    rate 本身 None（全 neutral 等无读数形态）原样返回 None。
    """
    if rate is None:
        return None
    if settled is None or settled < MIN_SETTLED_FOR_WINRATE:
        return None
    return rate
