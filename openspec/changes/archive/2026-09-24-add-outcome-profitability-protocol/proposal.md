# Proposal: add-outcome-profitability-protocol

## Why

「赚钱能力」（outcome 收益）侧至今没有口径、没有预登记：judge 四维与因果消融度量的是过程质量，不能换算成收益主张；生产战绩链已结算样本 <10（`track-record` 契约规定此时不展示胜率）、predictions 表刚清完测试泄漏（incident 031，2026-09-22），回测链只有 n=3 的通路验证（pilot-2023-shock，自标「无统计意义」）。两条读数腿（forward paper-trading cohort、近期 walk-forward 回测）即将启动，按项目纪律（口径变更先改 metrics.md §1 再动代码；预登记先于读数；incident 026「指标低 ≠ 能力差」），必须在第一个读数产生之前锁死主指标、结算窗口、基准、胜率定义、MDE、停止规则、泄漏控制声明与收口纪律——否则重演「读数出来后口径漂移」的老问题。

## What Changes

- `docs/evals/metrics.md` §1 新增 **§1.8「Outcome 收益指标」**：
  - 主指标 = 逐决策 **T+20 交易日相对沪深300（000300）超额收益**（均值 + 胜率）；胜率沿用 track-record 既有判定口径（±2% 中性带，resolved_win/(win+loss)），不另造定义
  - 人口划分：主结论只基于可执行决策（buy/sell → long/short）；watch/hold（neutral）以**回避正确率**作辅助指标，不进主结论
  - 辅助观测：T+5/T+10 盯市窗口（由 daily_marks 派生，只观测不判定）
  - 红线：settled 可执行样本 <10 SHALL NOT 报任何胜率类结论（track-record 展示门槛泛化为评估纪律）
  - 泄漏控制声明：回测腿读数须带泄漏探针披露；未过「干净窗口」判定的批次只能标「通路验证」
- 首个 outcome 预登记文档（沿用 `evals/ablation/preregister/` 门禁字段格式）：主指标 / MDE / 决策阈值 / 样本量依据 / 停止规则 / 成本分型 / 泄漏控制
- 收口纪律工具化（Step 3）：健康检查（结算成功率 / 不可判定率（unresolvable/settleable，与结算成功率互补、同一分母）/ 污染护栏 / integrity_check 通过）→ 异常行人工终裁 → **两句式结论**（显著方向 + CI ｜ 分辨率不足 + MDE 与扩样方向；裸「未获统计支持」非法）→ 报告生命周期 status 头 → `metrics.md` §2 时间线 + `runs.jsonl` 追加
- 双腿互证口径：forward 与 backtest 两腿 SHALL 同一预登记口径（同结算窗口 / 基准 / 入场价口径 / 胜率定义），读数并列报告；分歧超阈值先归因（泄漏 / regime 漂移 / 执行差异）后结论
- 边界（显式不做）：不作层间归因（B6 维持 suspended）；不动 Trader 决策姿态（#134 产品裁决解耦）；不改任何结算行为（行为变更在 `update-decision-settlement-contract`）

## Capabilities

### New Capabilities

（无——本 delta 是 `evaluation` 能力域内的新增需求）

### Modified Capabilities

- `evaluation`: 新增三条需求——「Outcome 收益指标口径与预登记」「Outcome 读数收口纪律」「Forward 与回测双腿互证」

## Impact

- 台账：`docs/evals/metrics.md` §1.8 新增 + §2 时间线「outcome 口径启用」切点行；`docs/evals/metrics/runs.jsonl` 新增 outcome run 类型约定（`type: outcome-forward | outcome-backtest`）
- 代码：仅 evals 侧——outcome 收口工具（健康检查脚本、两句式结论校验与报告 status 头，复用 `evals/causal_ablation/conclusion.py`、`report_status.py` 机制）；预登记文档落 `evals/ablation/preregister/`；**零生产代码变更**
- 依赖顺序：本 delta（口径）→ `update-decision-settlement-contract`（契约落地 T+20 / 回避判定）→ `add-forward-paper-trading-cohort` ∥ `add-backtest-leakage-controls`（两条读数腿）
- Owner 决策点：① 主窗口 T+20（建议值；备选 T+10 / T+60，改动只影响预登记与 Δ2 默认配置项）；② 泄漏探针降级阈值（建议 60%，见 Δ4）；③ 两条读数腿的预算（见 Δ3/Δ4 proposal）
