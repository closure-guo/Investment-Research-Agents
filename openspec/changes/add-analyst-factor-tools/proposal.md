# Proposal: add-analyst-factor-tools

## Why

- 因子库挖出的因子若没有消费方，挖掘管线就是自转空耗；Layer I 技术分析师是最自然的消费方。
- 「因子库工具对分析质量有无增量」不能预设为真——按项目惯例（辩论层消融的先例）必须做成消融变体用配对 bootstrap 裁决。

## What Changes

- 技术分析师 ReAct 工具集新增 `query_factor_library` 工具：按标的查询 active 因子的当前取值、z-score、近 60 日 IC；工具输出限结构化 JSON，禁止 LLM 自由拼接因子表达式。
- 新增消融变体：「技术分析师+因子工具」vs「裸技术分析师」，复用 `evals/ablation.py` 的配对 bootstrap 框架，judge 维度按适用性过滤。
- 因子工具调用计入节点耗时追踪与 Langfuse trace（用途标签 `analyst_factor_tool`）。

非目标（Out of scope）：

- 不改动其他三个分析师的工具集。
- 不允许分析师通过该工具写入/修改因子库（只读）。

## Capabilities

### Modified Capabilities
- `analyst-data-sources`: 技术分析师新增因子库只读工具。
- `causal-ablation`: 新增因子工具消融变体的契约。

## Impact

- **后端**：`src/finance_agent/factors/tools.py`（新增只读工具）、`agent_factory.py` 工具集注册、`evals/ablation.py` 变体定义。
- **测试**：工具只读性单测（无写路径）；输出 schema 合同单测；消融变体 wiring 单测。
- **验证**：跑 n=10 消融批次，报告落 `docs/evals/`。
