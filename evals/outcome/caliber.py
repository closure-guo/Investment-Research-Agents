"""Outcome 口径常量（唯一权威定义 docs/evals/metrics.md §1.9；口径变更先改 §1 再动本文件）。"""

from __future__ import annotations

# 主评估窗口：唯一实现源在 track-record 判定（§1.9①「不另造定义」）
from finance_agent.outcome.track_record.judgment import (  # noqa: F401  别名再导出
    DEFAULT_HORIZON_DAYS as PRIMARY_WINDOW_DAYS,
)

# 中性带常量：唯一实现源在 track-record 判定（§1.9①「不另造定义」）。
# update-outcome-band-volatility-scaled（2026-11-28）：判定侧已改为逐行波动率自适应
# （judgment.scaled_neutral_band），本常量现为「波动不可得时的回落带」；泄漏探针等
# 诊断分桶仍消费它（诊断用途，不是结算判定）。
from finance_agent.outcome.track_record.judgment import (  # noqa: F401  别名再导出
    DEFAULT_NEUTRAL_BAND as NEUTRAL_BAND,
)

# §1.9 十样本红线：唯一真相源在 outcome/track_record/readout（C6① 归一，生产侧）
from finance_agent.outcome.track_record.readout import (  # noqa: F401  别名再导出
    MIN_SETTLED_FOR_WINRATE,
)

AUX_WINDOWS: tuple[int, ...] = (5, 10)  # 辅助观测窗（daily_marks 派生，只观测不判定）
BENCHMARK_CODE = "000300.SH"  # 沪深300
FULL_CONCLUSION_SAMPLE = 30  # 完整结论门槛（n≥30）
LEAKAGE_PROBE_THRESHOLD = 0.60  # 回测腿探针降级阈值（预登记默认）
