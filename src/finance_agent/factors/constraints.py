"""因子库约束常量单一来源（add-factor-library-and-monitor）。

口径说明（改口径只改本文件；计划 Global Constraints 逐字）：

- ``MAX_MISSING_RATE``：闸①可计算性——候选在全样本面板上的缺失率上限
  （NaN 占比，含算子不足窗口的 NaN；按全体 cell 计）；
- ``MIN_ABS_ICIR``：闸②单因子检验——``|ICIR|`` 下限（ICIR = mean(IC)/std(IC)，
  std=0 → ICIR=0）；
- ``MIN_DECILE_MONOTONICITY``：闸②附加判据「分层单调性」——十分位累计收益与
  分位序的 Spearman 秩相关下限，且端点差（Q10−Q1）符号须与 IC 均值一致
  （add-factor-monotonicity-gate；首月观测期不调，调整须过 metrics.md 口径修订）；
- ``BH_FDR_Q``：闸③多重检验——Benjamini-Hochberg FDR 目标（``p_adj > q`` 拒收）；
- ``MAX_IC_CORRELATION``：闸④冗余——候选与池内任一因子 IC 序列 Pearson 相关
  绝对值上限（严格大于即 ``REDUNDANT_WITH_POOL``）；
- ``BUCKET_TOP_K``：分桶保种——每 category 桶内保留的 active 因子数上限
  （超出者按 ``pool_increment`` 升序退役）；
- ``DECAY_WINDOW`` / ``DECAY_NEGATIVE_DAYS`` / ``DECAY_ICIR_FLOOR``：衰减监控——
  最近 ``DECAY_WINDOW`` 个交易日滚动 IC 的均值连续 ``DECAY_NEGATIVE_DAYS`` 日为负
  且每点 ``|mean/std| < DECAY_ICIR_FLOOR`` → 置 ``degraded``；
- ``PERMUTATION_N``：闸③置换检验次数（同种子可复现）。**分辨率约束**：p 值下界
  ``1/(PERMUTATION_N+1)`` 必须 ≤ ``BH_FDR_Q/K``（K = 同批候选数），否则 BH-FDR 在
  该批次不可能拒收任何候选（``check_significance`` 对此 fail loud）。N=999 →
  覆盖 K ≤ q·(N+1) = 100；
- ``TRAIN_FRACTION``：闸⑤样本外——按日期排序的前 70% 交易日为训练段
  （切点不跨日）；闸④的池级增量亦按同一切分的**验证段**判定（样本外）；
- ``MIN_OOS_IR_SAMPLES``：闸④验证段 IR 所需的最少共同有效交易日（不足 → IR 不稳，
  fail closed 记 ``insufficient_oos_dates``）；
- ``DEFAULT_IC_HORIZON``：``forward_returns`` 默认期数（1 = 下一交易日）；闸⑤
  据此剔除训练段末端 horizon 日（其远期收益跨入验证段 → 边界泄漏）；
- ``MIN_IC_SAMPLES``：``rank_ic_series`` 逐日有效样本下限（不足 → 该日 NaN，
  样本太少不可信；E2 逐字 30）；
- ``MIN_REDUNDANCY_SAMPLES``：闸④冗余比对的最少共同有效交易日（2 点相关恒 ±1 →
  会假阳性；5 点起才有意义）；
- ``ADMISSION_PANEL_LOOKBACK_DAYS``：准入面板深度下限（交易日）——五道闸准入
  评估（提案月批/基线注册/外部导入/收割四入口共用）所用面板的取数深度；**独立于
  衰减监控口径**（``DECAY_WINDOW+30``）。150 → 250（raise-admission-panel-depth）→
  500（raise-admission-panel-depth-500）。两条量化依据：① 闸①缺失率含热身期 NaN——
  20 日窗在 250 日面板实测 0.1540、500 日面板 0.1109；60 日窗 0.2975（250 日闸①
  结构性拒收）→ 0.1838（500 日转为可算，进闸②裁决）；阈值 0.2 不变。
  ② 闸② ICIR 估计标准误 ≈1/√n——150 日 ≈0.086（≈3.5 SE）、250 日 ≈0.063
  （≈4.8 SE）、500 日 ≈0.045（≈6.7 SE），同族因子排序噪声进一步下降
  （闸②门槛复审备忘，docs/evals/2026-10-17；500 日实测 2026-10-20）。
  改闸①口径需与该值联动（本轮重算：20 日窗 11.1%、60 日窗 18.4%，均 <20%，阈值维持 0.2）。

消费方：``eval_engine``（``DEFAULT_IC_HORIZON``/``MIN_IC_SAMPLES``）、
``validate``（闸①–⑤）、``library``（``BUCKET_TOP_K``）、``monitor``（衰减三常量）。
其它模块禁止复制这些常量。
"""

from __future__ import annotations

# 闸①：缺失率上限（NaN 占比）
MAX_MISSING_RATE: float = 0.2
# 闸②：|ICIR| 下限
MIN_ABS_ICIR: float = 0.3
# 闸②附加判据：十分位累计收益与分位序的 Spearman 下限（分层单调性）
MIN_DECILE_MONOTONICITY: float = 0.6
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
# 闸③：置换检验次数（分辨率覆盖 K ≤ BH_FDR_Q·(N+1) = 100）
PERMUTATION_N: int = 999
# 闸⑤：训练段占比（前 70% 交易日；闸④的增量判定亦用该切分的验证段）
TRAIN_FRACTION: float = 0.7
# 闸④：验证段 IR 所需的最少共同有效交易日（不足 → fail closed）
MIN_OOS_IR_SAMPLES: int = 10
# RankIC 默认远期收益期数（1 = 下一交易日）
DEFAULT_IC_HORIZON: int = 1
# RankIC 逐日有效样本下限（不足 → 该日 NaN）
MIN_IC_SAMPLES: int = 30
# 闸④：冗余比对的最少共同有效交易日（2 点相关恒 ±1）
MIN_REDUNDANCY_SAMPLES: int = 5
# 准入面板深度下限（交易日；三准入入口共用；独立于衰减监控口径——见模块 docstring）
ADMISSION_PANEL_LOOKBACK_DAYS: int = 500
# 入库后复核样本门槛（add-factor-post-admission-review）：入库后 IC 样本 ≥ 60 才判定
POST_ADMISSION_MIN_SAMPLES: int = 60
# 拒绝池复苏重评（add-rejected-factor-revival）：冷却期（自然日，自最近 created/
# re_rejected 事件起算）与单次运行提交上限。静默线复用闸② ``MIN_ABS_ICIR``、
# 窗口复用 ``tools.IC_WINDOW``——单一真相源，不另立阈值。
REVIVAL_COOLDOWN_DAYS: int = 30
REVIVAL_DAILY_CAP: int = 5
