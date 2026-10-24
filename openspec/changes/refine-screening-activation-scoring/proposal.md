## Why

因子模式在 10-19~10-21 短暂上线三天即被 bootstrap guard 按回 fallback（10-21 那次运行的
`l3_keep=100 → pool_size=0` 是实弹教训：1–4 个 IC 点估的权重让 L2 排序逐日换脸，与「3 日在榜」
门槛相乘后池子清零）。guard 解决了「样本 <10 的因子混入打分」，但留下两个缺口：

1. **幅度噪声入口仍在**。`factor_weights` 现式 `w_i = ic_mean_i / Σ|ic_mean_j|`——方向由 IC
   均值符号承载（这部分稳健），但幅度正比于 |IC 均值|。单样本 IC 标准差 ≈0.09，n=10–20 时
   因子间 |IC 均值| 的差异大部分是抽签噪声；等因子攒够 10 条 IC（预计 10-30 前后）因子模式
   重新激活时，排序仍会被这种噪声幅度放大单个因子的误差。
2. **激活质量不可观测**。因子榜与流动性榜（fallback 排序键）的偏离度没有读数——10-21 的
   因子榜被当晚 fallback 重跑原子替换后，事后想验证「因子模式到底带来多少新信息」已无从
   对证。若激活后因子榜 ≈ 成交额榜（秩相关极高），说明因子库没有带来增量信息，这个判断
   应该当天可见，而不是又一次事后丢失。

## What Changes

- **低样本等幅权重**：可打分因子的权重 = 方向 × 幅度。方向 = IC 均值符号（不变）；幅度在
  该因子近 60 交易日 IC 样本数 ≥ 30（新常量 `MIN_SAMPLES_FOR_PROPORTIONAL_WEIGHTS`）时维持
  |IC 均值| 比例，样本 < 30 时取等幅（消除噪声幅度，保留全部方向信息）。归一化分母改为
  幅度之和（原为 Σ|IC 均值|）。
- **因子榜/流动性榜秩相关披露**：`selection_mode=factor` 的批次在批次 summary 中新增
  `factor_liquidity_rank_corr`（对同一 L1 通过集，因子综合分排序 vs 近 20 日日均成交额排序
  的 Spearman 秩相关，手写秩变换+Pearson 实现——本仓无 scipy 依赖，沿闸②单调性判据先例）
  与 `rank_corr_samples`（参与计算的标的数）；fallback 批次不携带该字段。经批次历史接口
  照常可读，日报 LLM 读 summary 时自然可见。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `stock-screening`：L2 因子打分排序的权重构成规则（方向 × 幅度，低样本等幅）；新增
  「因子榜与流动性榜秩相关披露」Requirement（批次 summary 字段与口径）。

## Impact

- 改动面：`src/finance_agent/screening/ranker.py`（`factor_weights` 加 `ic_counts` 参数与
  等幅分支、新增 Spearman 纯函数）、`src/finance_agent/screening/pipeline.py`（factor 分支
  传 `ic_counts`、全量排序后截断、计算并写入两个 summary 字段）。
- 兼容性：贡献分解勾稽不变（Σ contribution == composite，1e-9）；确定性不变（无随机源）；
  fallback 通道语义零改动（10 条排除门槛照旧）；`rank_universe` 的 `top_n` 契约不变
  （pipeline 以全量排序后自行截断，store 入参仍是 Top-N）。
- 不改 prompt、不改前端、不改调度。
