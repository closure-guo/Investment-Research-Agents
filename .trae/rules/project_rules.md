# Investment Research Agents

## Mandatory skill invocation

每个会话开始时，必须先调用 `using-superpowers` skill 激活 Superpowers 工作流，确保所有执行纪律（路由表、TDD 红线、verification 关卡）可用并正确初始化。

## Test asset locations

测试和验证产物的约定存放位置（不要在根目录创建新目录）：

| 类型 | 路径 | 说明 |
|------|------|------|
| 测试 fixtures | `tests/fixtures/` | 被测数据快照 |
| 验证脚本 | `tests/scripts/` | 手动验证脚本 |
| 验证文档 | `tests/validation/` | 人工验证报告 |
| 运行时报告输出 | `reports/` | docx/pptx 等生成文件（gitignored） |
| E2E 截图/输出 | `tests/e2e/` | 截图、HTML、report_*.md（gitignored） |

**禁止在根目录创建新目录存放测试产物。** 如果需要新的子目录，在上述位置下创建。

## E2E 测试约束

**端到端（E2E）测试禁止使用 mock 数据。** E2E 测试必须使用真实的服务、真实的依赖（如 FastAPI 后端、Vite 前端、真实文件系统）以及真实的输入数据（可来自 `tests/fixtures/`），以验证系统在真实链路下的行为。需要隔离或打桩的场景应放到单元测试或集成测试中，而不是 E2E。

**E2E 测试必须通过前端模拟用户真实输入，禁止单独测试后端 API。** E2E 测试应启动前端（Vite dev server）和后端（FastAPI），通过浏览器自动化（如 Playwright）模拟用户在页面上的真实操作（输入、点击、等待渲染），验证从前端 UI 到后端分析的完整链路。直接用 `requests`/`httpx` 调用后端接口属于集成测试，不属于 E2E。

## Project startup

当用户说"启动项目"时，使用 `docker compose up -d` 启动（在项目根目录执行）。

## Incident tracking

问题记录和解决方案维护在 `docs/incidents/`。发现系统性问题时，新建编号文档并更新 README.md 索引。

## Bug 排查

排查 bug 时不光要看后端日志，还要查看 Langfuse 的 trace（含 LLM 调用链路、输入输出、耗时与异常）。Langfuse 访问地址：http://localhost:3000

## Workflow routing

所有任务进站前先分类（详见 docs/project-workflow.md §4）：

- **新功能** -> OpenSpec delta 提案 -> Superpowers 管线 -> 验证 -> sync + archive
- **修 bug · 意图不变** -> 复现测试 + systematic-debugging，不触碰 openspec
- **修 bug · 意图变更 / 行为未定义** -> 同新功能流程，delta 先行
- **重大架构决策** -> 手动落 docs/adr/（编号递增，只增不改）
- **小改动** -> 直接改

## Verification red lines

- 没有先写失败测试的代码，删除重写
- archive 前置条件：tasks.md 全勾 + verification 通过 + 人工验证报告落 tests/validation/
- 「测试全过」不等于「行为正确」；交互行为变更必须有人工验证环节

## Agent skills

### Issue tracker

Issues tracked on GitHub. Use `gh` CLI for all operations. See `docs/agents/issue-tracker.md`.

### Triage labels

Default triage label vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout - one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`. ADR 由人手动维护，agent 不得自动新建 ADR。
