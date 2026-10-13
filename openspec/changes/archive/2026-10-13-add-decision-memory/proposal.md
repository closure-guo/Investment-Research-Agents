# Proposal: add-decision-memory

## Why

- 现有决策落库只是"档案"而非"记忆"：系统对同一只票反复分析时，过往决策的成败经验不会进入上下文，同一错误可能重复犯。
- A-C-A 框架对照显示记忆（Memory）是本项目架构五组件中的唯一空白；FinMem 的三层记忆（工作/情景/语义）是经过论文验证的可借鉴设计。
- 记忆是否有增量不能预设为真，按项目惯例须以消融变体裁决。

## What Changes

- 情景记忆：深度分析启动时，检索该标的及同申万三级行业标的的历史决策（含结算结果与论据摘要），Top-5 注入分析师与 Trader 上下文；注入内容标注来源与日期，纳入 citation 校验覆盖。
- 语义记忆：月度日批从已结算决策中提炼经验条款（例：「小盘股高换手信号命中率持续 < 40%」），经人工确认后入库，注入风控辩论层 prompt；未确认条款不生效。
- 记忆检索与注入计入 Langfuse trace（用途标签 `decision_memory`）；上下文预算占用受 `analyst-context-budget` 约束。
- 消融变体：「+记忆注入」vs「无记忆」，配对 bootstrap 裁决。

非目标（Out of scope）：

- 不实现记忆自动改写/遗忘算法（初版按月批全量重提炼）。
- 不让 LLM 直接写记忆库（语义记忆必须人工确认，防自我强化偏差）。

## Capabilities

### New Capabilities
- `decision-memory`: 情景记忆注入与语义记忆提炼。

### Modified Capabilities
- `agent-node-contracts`: 分析师/Trader/风控辩论的上下文输入契约扩展（记忆块）。
- `analyst-context-budget`: 记忆块纳入预算核算。
- `causal-ablation`: 新增记忆消融变体。

## Impact

- **后端**：`src/finance_agent/memory/episodic.py`、`memory/semantic.py`（新增）；分析师/Trader/风控节点上下文组装扩展。
- **测试**：检索 Top-5 相关性单测；语义记忆未确认不生效单测；预算核算单测。
- **验证**：n=10 消融批次，报告落 `docs/evals/`。
