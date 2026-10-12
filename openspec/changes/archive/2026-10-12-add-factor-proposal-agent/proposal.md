# Proposal: add-factor-proposal-agent

## Why

- 因子挖掘的"提案器"需要 LLM 参与，但 LLM 输出必须受合同约束（DSL 表达式 + 经济学假设），否则下游五道闸会被不可解析、无假设的垃圾提案淹没。
- 提案质量需要两个分工：广度变异（大量低成本候选）与深度改进（基于入库/拒收反馈的针对性迭代），单一模型单一口径两者都做不好且成本高。
- LLM 可能写出"动量因子配价值假设"这类货不对板的提案，需要对齐信号供下游参考。

## What Changes

- 新增 `factors/propose.py`：因子提案节点，全部 LLM 调用走 LLM Gateway（PolicyRouter 注册新用途 `factor_proposal`），结构化输出复用 `extract → validate → repair` 合同。
- 输出合同：每条提案必填 `expression`（DSL 字符串）、`hypothesis`（经济学假设，不少于 30 字）、`category`（价量/反转/动量/流动性/波动率）；DSL 解析失败视为合同违约，走修复重试（上限 2 次），仍失败则该提案丢弃并记 Langfuse trace。
- 双模型分工：广度变异提案走 quick profile（每批 ≥20 条候选）；基于因子库反馈（拒收原因、衰减因子复盘）的深度改进提案走 deep profile。
- 假设-因子对齐软评分：LLM 自评 0-1 一致性分，写入提案元数据（**仅作软信号**，不参与硬拒收）。
- prompt 采样时注入因子库分桶统计与算子先验表（历史各算子/桶的入库 IC 均值），并强制跨桶采样，防止提案收敛到单一因子族。

非目标（Out of scope）：

- 不实现五道闸与因子库（属 `add-factor-library-and-monitor`）。
- 不做 RL/微调类搜索器；不实现 MCTS（算子先验统计表为本 change 的简化替代）。
- 不消费研报 RAG 作为 idea 源（留待 v2.0 研报 RAG delta 合并）。

## Capabilities

### New Capabilities
- `factor-proposal`: LLM 因子提案的合同化生成与双模型分工。

### Modified Capabilities
- `llm-policy-router`: 注册新用途 `factor_proposal` 的能力要求与 fallback 链。

## Impact

- **后端**：`src/finance_agent/factors/propose.py`、`llm/router.py` 用途注册、`prompts/factor_proposal.md`（新增模板，Langfuse 托管）。
- **测试**：合同违约修复重试单测（stub LLM）；双 profile 路由单测；跨桶采样断言单测。
- **验证**：跑一批 50 条提案，人工核对 DSL 可解析率与假设填写完整率，报告落 `tests/validation/`。
