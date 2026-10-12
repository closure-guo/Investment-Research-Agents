"""因子库约束常量单一来源（add-factor-library-and-monitor）。

口径说明（改口径只改本文件；计划 Global Constraints 逐字）：

- ``MAX_MISSING_RATE``：闸①可计算性——候选在全样本面板上的缺失率上限
  （NaN 占比，含算子不足窗口的 NaN；按全体 cell 计）；
- ``MIN_ABS_ICIR``：闸②单因子检验——``|ICIR|`` 下限（ICIR = mean(IC)/std(IC)，
  std=0 → ICIR=0）；
- ``BH_FDR_Q``：闸③多重检验——Benjamini-Hochberg FDR 目标（``p_adj > q`` 拒收）；
- ``MAX_IC_CORRELATION``：闸④冗余——候选与池内任一因子 IC 序列 Pearson 相关
  绝对值上限（严格大于即 ``REDUNDANT_WITH_POOL``）；
- ``BUCKET_TOP_K``：分桶保种——每 category 桶内保留的 active 因子数上限
  （超出者按 ``pool_increment`` 升序退役）；
- ``DECAY_WINDOW`` / ``DECAY_NEGATIVE_DAYS`` / ``DECAY_ICIR_FLOOR``：衰减监控——
  最近 ``DECAY_WINDOW`` 个交易日滚动 IC 的均值连续 ``DECAY_NEGATIVE_DAYS`` 日为负
  且每点 ``|mean/std| < DECAY_ICIR_FLOOR`` → 置 ``degraded``；
- ``PERMUTATION_N``：闸③置换检验次数（同种子可复现）；
- ``TRAIN_FRACTION``：闸⑤样本外——按日期排序的前 70% 交易日为训练段
  （切点不跨日）；
- ``DEFAULT_IC_HORIZON``：``forward_returns`` 默认期数（1 = 下一交易日）；
- ``MIN_IC_SAMPLES``：``rank_ic_series`` 逐日有效样本下限（不足 → 该日 NaN，
  样本太少不可信；E2 逐字 30）。

消费方：``eval_engine``（``DEFAULT_IC_HORIZON``/``MIN_IC_SAMPLES``）、
``validate``（闸①–⑤）、``library``（``BUCKET_TOP_K``）、``monitor``（衰减三常量）。
其它模块禁止复制这些常量。
"""

from __future__ import annotations

# 闸①：缺失率上限（NaN 占比）
MAX_MISSING_RATE: float = 0.2
# 闸②：|ICIR| 下限
MIN_ABS_ICIR: float = 0.3
# 闸③：Benjamini-Hochberg FDR 目标
BH_FDR_Q: float = 0.1
# 闸④：与池内任一因子 IC 序列 |Pearson ρ| 上限
MAX_IC_CORRELATION: float = 0.7
# 分桶保种：桶内 active 数上限
BUCKET_TOP_K: int = 10
# 衰减监控：滚动窗口交易日数
DECAY_WINDOW: int = 60
# 衰减监控：滚动 IC 均值连续为负的交易日数
DECAY_NEGATIVE_DAYS: int = 20
# 衰减监控：滚动 |ICIR| 下限
DECAY_ICIR_FLOOR: float = 0.1
# 闸③：置换检验次数
PERMUTATION_N: int = 200
# 闸⑤：训练段占比（前 70% 交易日）
TRAIN_FRACTION: float = 0.7
# RankIC 默认远期收益期数（1 = 下一交易日）
DEFAULT_IC_HORIZON: int = 1
# RankIC 逐日有效样本下限（不足 → 该日 NaN）
MIN_IC_SAMPLES: int = 30
