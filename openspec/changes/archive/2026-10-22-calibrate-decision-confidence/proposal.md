# Proposal: calibrate-decision-confidence

## Why

近 14 天 84 条预测的 confidence 分布：**0.50–0.60 占 63 条（75%）**、0.60–0.70 占 20 条、<0.50 仅 1 条，**没有任何 ≥0.70 的决策**。决策层 prompt 的锚点是「≥0.7 高置信 / 0.4–0.7 中等 / <0.4 低」，示例值又是 0.75/0.5/0.6/0.55 —— 模型稳定落在「中等」中段。置信度因此失去分辨力：既不能表达强证据（永远不到 0.7），也不能表达弱证据（几乎不去 0.4 以下），下游 FM 漂移披露与战绩校准都拿不到可用信号。

这是 metrics.md §3 长期挂起的待决策 #4（「置信度锚定（RM/RJ/FM 扎堆示例值 0.50–0.60）」），此前缺一个可证伪的改法与读数口径。

## What Changes

单变量（只动 confidence 相关的 prompt 指令与示例，不动决策逻辑/价位/证据契约）：

- 三个决策节点（trader / risk_judge / fund_manager）的 confidence 段改为**可执行的分档判据 + 反例**：写清「什么证据组合必须 ≥0.7」「什么情形必须 <0.4」，并给出 0.85 / 0.55 / 0.3 三档示例，删除当前把中段当默认的示例值。
- 增加「confidence 必须与 reasoning 的证据计数一致」的机械提示（引用已申报的锚点/证据条数），不新增结构化字段。
- 观测：round 收口除 judge 分外，登记 **confidence 分布直方图**（按 action 分桶）与 `≥0.7 / <0.4` 尾部占比，写入实验报告；不新增 Scores（避免污染 judge 口径）。

## Capabilities

- **Modified Capabilities**: `agent-prompt-contracts`（决策层 confidence 校准指令）
- **Related**: `report-decision-rendering`（FM 漂移披露口径不变）

## Impact

- `src/finance_agent/prompts/trader.md`、`risk_judge.md`、`fund_manager.md` + `scripts/deploy_prompts.py` 发布
- `docs/evals/metrics.md` §1 增「confidence 分布」观测口径；runs.jsonl 增 `confidence_hist` 字段
- 验证路径：改前基线（r14 同批 9 条 deep）→ 单变量改 prompt → 同 dataset 复测 → 比较**分布形状**（尾部占比、按 action 分桶），judge 四维只作健康检查不作成败判据
- 风险：confidence 与 FM 漂移披露联动，若模型过度上移可能触发更多「漂移说明」；判据要求与 reasoning 一致，避免纯数值上移
