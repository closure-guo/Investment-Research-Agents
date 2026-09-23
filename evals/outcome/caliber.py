"""Outcome 口径常量（唯一权威定义 docs/evals/metrics.md §1.8；口径变更先改 §1 再动本文件）。"""

from __future__ import annotations

PRIMARY_WINDOW_DAYS = 20  # 主评估窗口（交易日）；与结算默认 horizon 一致
AUX_WINDOWS: tuple[int, ...] = (5, 10)  # 辅助观测窗（daily_marks 派生，只观测不判定）
BENCHMARK_CODE = "000300.SH"  # 沪深300
NEUTRAL_BAND = 0.02  # ±2% 中性带（胜率与回避判定共用）
MIN_SETTLED_FOR_WINRATE = 10  # settled < 10 不报胜率（红线）
FULL_CONCLUSION_SAMPLE = 30  # 完整结论门槛（n≥30）
LEAKAGE_PROBE_THRESHOLD = 0.60  # 回测腿探针降级阈值（预登记默认）
