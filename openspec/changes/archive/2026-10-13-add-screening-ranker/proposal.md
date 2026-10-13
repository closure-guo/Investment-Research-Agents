# Proposal: add-screening-ranker

## Why

- L1 过滤后的可交易池仍有 2000-3000 只，需要一个纯计算的横截面打分把候选压缩到 LLM 可负担的规模（Top 50-100）。
- 因子库起步为空，若 ranker 依赖 LLM 因子就绪才能跑，选股漏斗整体阻塞——需要手工经典因子作为内置基线，顺带充当 LLM 因子的查重与增量对照组。
- 打分必须可解释（每个因子贡献多少分），否则前端"为什么选中它"和事后归因无数据可依。

## What Changes

- 新增 `screening/ranker.py`（纯函数）：对 L1 通过标的在快照上做横截面打分，输出 Top-N（默认 100）`Candidate` 列表。
- 打分公式：各 active 因子值截面 z-score 标准化后按其近 60 日 IC 方向与强度加权合成综合分；权重计算逻辑显式化、可测试。
- `Candidate` 契约：`{symbol, trade_date, composite_score, factor_contribs[{factor_id, zscore, weight, contribution}], rank}`。
- 内置手工因子基线包（动量 20 日、反转 5 日、换手率、20 日波动率、市值代理），作为 `category=builtin` 注入因子库，与 LLM 因子同闸不同权（手工因子免提案环节但仍过准入闸）。

非目标（Out of scope）：

- 不做 LLM 粗筛与深度分析触发（后续 delta）。
- 不做行业/市值中性化（初版直接截面 z-score，中性化留待评估证明需要后再加）。

## Capabilities

### Modified Capabilities
- `stock-screening`: 新增 L2 因子打分排序层与 Candidate 契约。
- `factor-library`: 新增 builtin 类因子的注册路径（免提案、仍过准入闸）。

## Impact

- **后端**：`src/finance_agent/screening/ranker.py`、`factors/builtin.py`（新增）。
- **测试**：打分确定性单测（同输入同输出）；factor_contribs 合计=综合分的勾稽单测；builtin 因子注册单测。
- **验证**：对 2026-09 每月首交易日产出 Top-100 名单，人工抽 10 只核对手算综合分一致，报告落 `tests/validation/`。
