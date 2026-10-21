# Investment Research Agents - OpenSpec Project Context

## Project Overview

面向 A 股市场的多 Agent 投研分析系统。基于 LangGraph 5 层架构（4 分析师并行 -> Bull/Bear 辩论 -> Trader 决策 -> Risk Management 辩论 -> Fund Manager 批准），前端使用 React 18 + Vite，后端使用 FastAPI。输入股票代码或自然语言查询，自动生成交易决策分析报告。

## Architecture Summary

- **L1 前端**：React 18 + Vite（自然语言输入 + SSE 流式渲染 + 报告展示 + 文件下载 + 会话管理）
- **L2 Agent**：LangGraph（5 层架构 + 多 Agent 辩论 + Send 并行派发），由 ReAct Agent Harness 统一编排
- **L3 数据**：pandas + SQLite（AKShare 拉取 + 指标计算 + 报表持久化 + 行情缓存）
- **L4 LLM**：DeepSeek（LiteLLM 路由，deepseek-v4-pro 深度 / deepseek-chat 快速）
- **可观测性**：Langfuse 追踪 LLM 调用链路

## Key Documents

- `CONTEXT.md` - 项目级架构记忆（领域术语、架构拓扑、数据源）
- `docs/architecture.md` - 详细架构文档
- `docs/adr/0001-0017` - 架构决策记录（手动维护，只增不改）
- `docs/PRD.md` - 产品需求文档
- `docs/incidents/` - 事故记录与解决方案
- `docs/project-workflow.md` - OpenSpec + Superpowers 双框架实施文档

## Spec Domains

基线按仓库五层架构与 ADR 划分，分批构建：

| 领域 | 状态 | 来源 | 说明 |
|------|------|------|------|
| `frontend/` | ✅ 已建 | incident 010, ADR-0012/0017 | React chat UI 交互行为（事故高发区） |
| `pipeline-events/` | ✅ 已建 | ADR-0012 | 管线事件流式推送、节点生命周期 |
| `session-persistence/` | ✅ 已建 | ADR-0004/0012 | 会话持久化、跨会话恢复 |
| `e2e-infrastructure/` | ✅ 已建 | incident 010 | E2E 测试基础设施（Playwright + stub） |
| `e2e-core-specs/` | ✅ 已建 | incident 010 | E2E 核心交互场景覆盖 |
| `scoring/` | 待建 | ADR-0003, incident 005 | 双阈值评分、GARP/杜邦口径 |
| `data-pipeline/` | 待建 | ADR-0001 | 数据准备节点、AKShare 数据源约定 |
| `agents/` | 待建 | ADR-0002, ADR-0010 | pure-LLM agents、tool-use 重构 |
| `report/` | 待建 | ADR-0007 | 综合报告结构 |
| `mcp-server/` | 待建 | ADR-0008 | MCP 服务器 |
| `observability/` | 待建 | ADR-0015/0016 | Langfuse tracing 与 prompt 管理 |

## Conventions

- 基线 spec 只描述**行为契约**（给定 X 输入，系统必须 Y），不写实现细节
- 每个领域 spec 头部注明来源 ADR 编号，保持可追溯
- delta 提案是契约的唯一编辑入口；主规范库只能通过 sync 合并更新，禁止手改
- 修改任何已有行为前必须先查 `openspec/specs/` 主规范库
