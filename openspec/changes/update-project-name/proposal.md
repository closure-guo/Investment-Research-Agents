# Proposal: update-project-name

## Why

项目对外品牌名需要从 `Finance Analysis Agent` 统一改为 `Investment Research Agents`。当前品牌名分散在前后端标题、文档、部署标识与 E2E 断言中，继续保留旧名会导致新旧混用与断言漂移。

## What Changes

- 显示名：`Finance Analysis Agent` → `Investment Research Agents`
- 发行名：`finance-agent` → `investment-research-agents`（pyproject、前端/E2E 包名、包元数据读取）；Compose 项目名保留 `finance-agent` 作为内部标识，避免 Docker Desktop 自动恢复的旧栈与新栈抢端口
- **BREAKING 风险已规避**：命名卷显式固定到 `finance-agent_langfuse_*`，即使未来改 Compose 项目名也不会创建空卷导致 Langfuse traces/prompts 看似丢失
- 保持 Python 导入包 `finance_agent` 与仓库目录名 `finance_analysis_agent` 不变（发行名与导入名解耦）

## Capabilities

- **Modified Capabilities**: `frontend`, `e2e-infrastructure`

## Impact

- 前端页面标题、FastAPI OpenAPI 标题、README/AGENTS/CONTEXT/OpenSpec 项目上下文
- `pyproject.toml`、`uv.lock`、`requirements.txt`、前端与 E2E 的 `package.json`/lock、`docker-compose.yml`
- 现有 Langfuse 数据卷（固定卷名保护）
- 不涉及数据库结构、API 契约与业务逻辑
