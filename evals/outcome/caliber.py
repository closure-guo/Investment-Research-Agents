"""Outcome 口径常量（唯一权威定义 docs/evals/metrics.md §1.8；口径变更先改 §1 再动本文件）。"""

from __future__ import annotations

# 主评估窗口：唯一实现源在 track-record 判定（§1.8①「不另造定义」）
from finance_agent.outcome.track_record.judgment import (  # noqa: F401  别名再导出
    DEFAULT_HORIZON_DAYS as PRIMARY_WINDOW_DAYS,
)

# ±2% 中性带：唯一实现源在 track-record 判定（§1.8①「不另造定义」）
from finance_agent.outcome.track_record.judgment import (  # noqa: F401  别名再导出
    DEFAULT_NEUTRAL_BAND as NEUTRAL_BAND,
)

AUX_WINDOWS: tuple[int, ...] = (5, 10)  # 辅助观测窗（daily_marks 派生，只观测不判定）
BENCHMARK_CODE = "000300.SH"  # 沪深300
MIN_SETTLED_FOR_WINRATE = 10  # settled < 10 不报胜率（红线）
FULL_CONCLUSION_SAMPLE = 30  # 完整结论门槛（n≥30）
LEAKAGE_PROBE_THRESHOLD = 0.60  # 回测腿探针降级阈值（预登记默认）
