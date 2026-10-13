"""选股漏斗约束常量单一来源（add-screening-filters）。

约束说明（spec「约束单一来源」，改口径只改本文件）：

- ``MIN_TRADING_DAYS``：上市交易天数下限——上市 < 60 个交易日的次新股剔除；
- ``MIN_AVG_AMOUNT_20D``：近 20 日日均成交额下限（元）——低于即流动性不足；
- ``MIN_LIQUIDITY_HISTORY_DAYS``：流动性判定所需的成交额历史长度（交易日）——
  快照积累不足该长度时 ``DATA_MISSING``（fail-closed）。

消费方（各字段去留精确到函数，避免误解）：

- ``filter_universe`` 调用期读 ``DEFAULT_THRESHOLDS`` 的 ``min_trading_days`` /
  ``min_avg_amount_20d``（模块属性可被 monkeypatch）；它**不读**
  ``min_liquidity_history_days``——该函数只吃已算好的派生量（``CandidateRecord``）；
- ``build_candidate_record`` 调用期读 ``DEFAULT_THRESHOLDS``（或显式
  ``thresholds=`` 覆盖）的 ``min_liquidity_history_days``，经
  ``avg_amount_20d(amounts, history_days=...)`` 决定均值窗口长度；``avg_amount_20d``
  在 ``history_days=None`` 时调用期读本模块 ``MIN_LIQUIDITY_HISTORY_DAYS`` 常量；
- 交易执行层（add-signal-portfolio-risk-gate，spec 明文「约束定义 import 自
  ``screening/constraints.py``」）：``execution/portfolio.py`` 默认上限读
  ``MAX_SINGLE_POSITION`` / ``MAX_TOTAL_POSITION``；``execution/risk_gate.py``
  调用期读三常量（单票/总仓上限 + 整手）；``execution/matching.py`` 的整手判定
  import ``LOT_SIZE``。

因此修改上方常量（或测试中 monkeypatch ``DEFAULT_THRESHOLDS`` /
``MIN_LIQUIDITY_HISTORY_DAYS``）即改变过滤行为，无需改任何其他文件。后续交易执行层的
风控门禁 SHALL import 本模块复用约束定义，禁止复制常量到执行层代码。
"""

from __future__ import annotations

from dataclasses import dataclass

# 上市交易天数下限：满 60 个交易日才进入候选池（spec「上市 < 60 交易日」剔除）
MIN_TRADING_DAYS: int = 60
# 近 20 日日均成交额下限（元）：低于 5000 万视为流动性不足
MIN_AVG_AMOUNT_20D: float = 50_000_000.0
# 流动性判定所需的成交额历史长度（交易日）：快照积累不足即 DATA_MISSING
MIN_LIQUIDITY_HISTORY_DAYS: int = 20
# 单票目标仓位上限（组合聚合/风控门禁共用单一来源）
MAX_SINGLE_POSITION: float = 0.2
# 组合总仓位上限（组合聚合/风控门禁共用单一来源）
MAX_TOTAL_POSITION: float = 0.8


@dataclass(frozen=True)
class FilterThresholds:
    """过滤层阈值集合（默认值 = 上列常量口径；可显式覆盖/运维调整）。"""

    min_trading_days: int = MIN_TRADING_DAYS
    min_avg_amount_20d: float = MIN_AVG_AMOUNT_20D
    min_liquidity_history_days: int = MIN_LIQUIDITY_HISTORY_DAYS


# 默认阈值单例；过滤层调用期读本模块属性（monkeypatch 本属性即改变行为）
DEFAULT_THRESHOLDS: FilterThresholds = FilterThresholds()
