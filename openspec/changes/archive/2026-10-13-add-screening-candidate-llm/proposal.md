# Proposal: add-screening-candidate-llm

## Why

- L2 因子打分捕捉不到事件驱动风险（立案调查、大额减持、暴雷公告），纯量化候选池会混入"因子好看但有雷"的标的。
- 但 LLM 粗筛隔天抖动大（采样随机性），若无稳定性门槛，候选池日际换手剧烈，下游深度分析预算被消耗在一次性噪声标的上。

## What Changes

- 新增 `screening/candidate_llm.py`：对 L2 Top-N 候选用 quick profile 逐只粗筛，输入综合分、factor_contribs 摘要与最近事件（复用 `events/` 模块），输出合同化 `keep/drop + reason（≤50字）`。
- 在榜稳定性门槛：标的须连续 3 个交易日在 L2 Top-N 内方有资格进入精选池（门槛值可配置）。
- 粗筛调用并发执行（并发上限可配置，默认 8）；单只失败不阻断批次，该只按 keep 处理并记 trace warning（宁可多留不可错杀）。
- LLM 粗筛的 keep/drop 决策落库，供后续消融（「±L3 粗筛」）与前向命中率分层统计使用。

非目标（Out of scope）：

- 不用 deep profile 做粗筛（成本纪律）。
- 不在本 change 实现深度分析自动触发（属 `add-screening-pipeline`）。

## Capabilities

### Modified Capabilities
- `stock-screening`: 新增 L3 LLM 排雷层与在榜稳定性门槛。

## Impact

- **后端**：`src/finance_agent/screening/candidate_llm.py`（新增）、`prompts/screening_triage.md`（新增模板）。
- **测试**：输出合同单测（stub LLM）；稳定性门槛边界单测（连续 2/3/4 日在榜）；单只失败降级 keep 单测。
- **验证**：对 2026-09 一个完整周的候选跑 L3，人工抽 20 条 drop 理由核对合理性，报告落 `tests/validation/`。
