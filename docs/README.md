# 项目文档索引

本页是 `docs/` 及根目录文档的一张图。入口级阅读顺序建议：[README](../README.md) → [architecture.md](architecture.md) → [project-workflow.md](project-workflow.md)。

> 权威源约定（与 AGENTS.md 红线一致）：
> - **系统行为真相** = `openspec/specs/`（28 个 capability，只经 delta 编辑 + sync 合并，禁止手改）
> - **prompt 权威源** = `src/finance_agent/prompts/*.md`（git 跟踪；Langfuse 为部署产物快照）
> - **架构决策** = `docs/adr/`（编号递增；agent 可新建/编辑，改既有结论须追加修订记录）
> - **运行时产物**（`reports/`、`tmp/`、`.superpowers/`）不入库，本索引不收录

## 顶层文档

| 文件 | 说明 |
|---|---|
| [README.md](../README.md) | 项目总览：架构、功能、评估体系、文档入口 |
| [CONTEXT.md](../CONTEXT.md) | 领域上下文：术语表、Agent 定义、Langfuse 语义 |
| [PRD.md](PRD.md) | 产品需求文档（初始设计稿，后续演进以 ADR + OpenSpec 为准） |
| [architecture.md](architecture.md) | 系统架构详细设计 |
| [project-workflow.md](project-workflow.md) | 开发工作流 SOP 地图（OpenSpec delta + Superpowers 双框架） |

## 子目录

### `adr/` — 架构决策记录（0001-0020，只增不改）
决策性质文档，编号递增。专题清单见 [README.md 文档节](../README.md#文档)。

### `agents/` — Agent 协作规范
- [triage-labels.md](agents/triage-labels.md) — GitHub issue 标签规范
- [issue-tracker.md](agents/issue-tracker.md) — issue 跟踪约定

### `audit/` — 盘点与交接记录
- [2026-08-28-openspec-archive-audit.md](audit/2026-08-28-openspec-archive-audit.md) — OpenSpec 归档盘点
- [2026-08-28-openspec-archive-handoff.md](audit/2026-08-28-openspec-archive-handoff.md) — 归档交接
- [2026-09-08-docs-tidy-audit.md](audit/2026-09-08-docs-tidy-audit.md) — 文档全量盘点（本索引的由来）

### `design/` — 专项设计档案（决策前/伴随的技术方案）
| 文件 | 主题 |
|---|---|
| [LLM Provider Gateway 完整架构设计.md](<design/LLM Provider Gateway 完整架构设计.md>) | LLM 网关防腐层 |
| [E2E测试实现方案-finance_analysis_agent.md](design/E2E测试实现方案-finance_analysis_agent.md) | E2E 门禁基础设施完整实现 |
| [Langfuse评估体系设计文档.md](design/Langfuse评估体系设计文档.md) | 评估体系（judge/对比/消融）设计 |
| [Agent输出截断治理设计方案.md](design/Agent输出截断治理设计方案.md) | 输出截断治理 |
| [claim-verification-research.md](design/claim-verification-research.md) | 引用校验调研 |
| [finground_verification_node_design.md](design/finground_verification_node_design.md) | 验证节点设计 |
| [frontend-stream-state-arch.md](design/frontend-stream-state-arch.md) | 前端流状态架构 |
| [resume-stream-on-session-switch-设计档案.md](design/resume-stream-on-session-switch-设计档案.md) | 会话切换流式续传设计档案 |
| [流状态层重构方案.md](design/流状态层重构方案.md) | 流状态层重构方案（与上列流状态文件存在内容重叠，待人工裁决） |
| 前端流状态架构.drawio / .png | 流状态架构图（drawio 源 + 渲染） |

### `evals/` — 评估体系运行记录
- [dataset-baseline.md](evals/dataset-baseline.md) — 数据集基线
- [hosted-evaluator-template.md](evals/hosted-evaluator-template.md) — 托管评估器模板
- [2026-09-02-评估体系开机与coverage-v3记录.md](evals/2026-09-02-评估体系开机与coverage-v3记录.md) — 开机与 coverage-v3 记录
- [2026-09-03-消融n10权威结果.md](evals/2026-09-03-消融n10权威结果.md) — 消融 n10 权威结果
- [perf-baseline.json](evals/perf-baseline.json) — 性能基线数据

### `incidents/` — 系统性问题档案（001-025）
每条一个编号文档，`incidents/README.md` 为分类索引。排查 bug 时先查此处是否有同源问题。

### `superpowers/` — Superpowers 工作流产物
- `plans/`（33 篇）— 各功能实现计划。**任务级耗材**：按 workflow §5.3 做完即弃，但仓库保留历史（如不再需要可整体归档，勿单篇删除）
- `specs/` — 伴随设计的 delta 设计文档（2026-07-26 e2e、2026-08-01 resume-stream）
- `research/` — 研究性产出

## 根目录其他

- `openspec/` — 行为规范库：`openspec/specs/`（唯一真相）+ `openspec/changes/`（活跃 delta 5 个）+ `openspec/changes/archive/`（86 个已归档）
- `tests/validation/` — 人工验证报告（交互类变更存档）
- `evals/` — 评估框架代码与数据

## 维护约定

- 新增文档：先判断归属（决策 → `adr/`；方案 → `design/`；运行记录 → `evals/`/`incidents/`；计划 → `superpowers/plans/`），再更新本索引
- 删除/移动文档：同步更新本索引与根 README「文档」节
- 链接使用相对路径；中文文件名直接在链接中写中文（git 已按 UTF-8 存储）