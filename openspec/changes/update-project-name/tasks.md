# Tasks: update-project-name

- [x] 显示名已统一为 Investment Research Agents（README/AGENTS/CONTEXT/OpenSpec/前端标签/FastAPI 标题/架构图源）
- [x] 发行标识已改为 investment-research-agents（pyproject/前端与 E2E 包名/包元数据读取）；Compose 项目名保留 finance-agent（内部标识）
- [x] Langfuse 命名卷已固定到旧卷名，现有 traces/prompts 不丢失
- [x] uv.lock、requirements.txt、npm lock 已重新生成
- [x] 后端改名相关 pytest 用例（27 passed）与 ruff 通过；前端 888 passed + production build 通过
- [ ] 全量后端 pytest 与 mypy 门禁：全量 pytest 被未标记 live 的外网用例阻塞；mypy 存量 79 项（非本次改名引入）
- [x] E2E 标题断言与主规范已同步
