# Proposal: add-screening-ic-bootstrap-guard

## Why

2026-10-21 链路评估发现精选池连续 5 个交易日为 0（10-19/10-21 为 0，10-20 仅 1 只），选股链事实上空转。归因：因子库 10-16 才开始写 IC，生产只有 3 个交易日的 RankIC 样本，而 L2 权重 `ic_mean_i / Σ|ic_mean_j|` 直接由这 3 个样本估计——权重日间剧烈摆动，L2 Top-100 日间重叠仅 1/100；再乘「连续 3 个交易日处于 Top-N」的精选池门槛，交集必然为空。这不是缺数据，是**用不足的样本冒充了可打分证据**：名义上走 `factor` 路径，实际排序接近噪声。

现有降级通道（delta `add-screening-empty-library-fallback`）只在「无 active 因子 / 全部因子无 IC 记录」时触发，未覆盖「有 IC 但样本不足以估计权重」。

## What Changes

- 新增单因子 IC 样本门槛：近 60 个交易日 RankIC 记录 < **10** 条的单因子不参与打分（权重 0），并在批次日志披露被排除的因子 id 与样本数。
- 全部 active 因子都样本不足（或无有效 IC）时，L2 走**既有显式降级通道**（流动性过渡排序），批次 `selection_mode=fallback`，三处披露口径不变。
- 不改变精选池「连续 3 个交易日」门槛，也不改变 IC 窗口（60 日）——样本随日批自然累积，达到 10 条后自动回到 `factor` 路径。

## Capabilities

- **Modified Capabilities**: `stock-screening`（Requirement「无可打分因子降级通道」的触发条件扩充）

## Impact

- `src/finance_agent/screening/ranker.py`（新增 `MIN_IC_SAMPLES_FOR_SCORING` / `ic_sample_counts` / `select_scorable_factors`）
- `src/finance_agent/screening/pipeline.py`（L2 分支改用样本过滤后的因子集；降级日志带归因）
- 测试：`tests/screening/test_ranker.py`、`tests/screening/test_pipeline.py`
- 生产影响：未来约 2 周（IC 样本从 3 条累积到 10 条）精选池将由流动性过渡排序产出并明确标注 `fallback`，而非空池；样本达标后自动切回 factor。**不下单**，仅影响选股与自动分析入口。
