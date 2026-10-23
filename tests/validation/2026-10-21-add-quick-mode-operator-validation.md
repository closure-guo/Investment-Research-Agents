# 人工验证报告: add-quick-mode-operator

**日期**: 2026-10-21
**关联 delta**: `openspec/changes/add-quick-mode-operator/`
**执行环境**: 独立 worktree `.worktrees/quick-mode-operator`（分支 `feat/quick-mode-operator`），E2E 后端 8006 / 前端 5179
**E2E 配置**: `tests/e2e/playwright/playwright.operator.config.ts`（`STUB_SCENARIO=operator`，DB 全指向 `test-e2e-operator-*`）

## 验证结果

| 场景 | 覆盖方式 | 预期 | 实际结果 | 通过 |
|---|---|---|---|---|
| 快速模式「分析茅台」→ 派单 | Playwright E2E（真实浏览器 + 真实后端） | 工具横幅 + 回执 + 独立 analysis 会话 | 1 passed（4.4s） | ✅ |
| 派单回执一键打开分析会话 | Playwright E2E | `agui-open-analysis` 按钮可见 | 通过 | ✅ |
| 快速模式「日批现在什么状态」→ 只读查询 | Playwright E2E | `get_daily_ops_status` 工具横幅 + 回答 | 1 passed（3.3s） | ✅ |
| 快速通道不出现管线事件 | E2E 断言 | 无 `node_start` / `node_complete` | 通过 | ✅ |
| 7 个只读工具整形/截断/缺数据披露 | 单元测试 | 池≤20、队列≤10、持仓≤20、条款≤20、因子≤20；null/样本不足保留 | 8 passed | ✅ |
| 写操作首次只预览 | 单元测试 | `confirmation_required` + token，零副作用 | 通过 | ✅ |
| 未带用户确认词拒绝执行 | 单元测试 + Agent 注入测试 | 返回错误，业务函数未被调用 | 通过 | ✅ |
| 用户确认后执行一次 / token 单次 | 单元测试 | 成功一次，重复使用拒绝 | 通过 | ✅ |
| 跨 Agent run 确认 | 单元测试 | 第二个 run 按 session+action+params 匹配 preview 并执行 | 通过 | ✅ |
| 参数篡改 / token 过期 / 错误 session | 单元测试 | 拒绝并给出原因 | 通过 | ✅ |
| 因子控制 `control_unavailable` | 单元测试 | 原样返回 409 语义，不写控制文件 | 通过 | ✅ |
| 日批补跑白名单 / 后台回执 | 单元测试 | 未知 job 拒绝；白名单任务后台执行并立即回执 | 通过 | ✅ |
| 记忆条款确认/驳回 / 未知 ID | 单元测试 | 预览含条款文本；未知 ID 明确报错 | 通过 | ✅ |
| 精选池批量分析 / 空池 | 单元测试 | 空池拒绝；确认后复用既有队列 | 通过 | ✅ |
| 工具注册单一来源 | 单元测试 | 13 个 operator 工具 + web_search；开关关闭仅 web_search | 通过 | ✅ |
| 提示词契约 | 单元测试 + Langfuse staging | 只读/写工具政策 + 确认协议 + 禁止自确认 | 通过 | ✅ |
| fast path 行为零回归 | 既有回归测试 | 全部通过 | 通过 | ✅ |

## 执行命令与读数

```bash
# 后端定向套件（P1 + P2 + P3）
uv run pytest tests/test_operator_registry.py tests/test_operator_gating.py \
  tests/test_operator_confirmation.py tests/test_operator_reads.py \
  tests/test_operator_writes.py tests/test_operator_analysis_tool.py \
  tests/test_api_operator_dispatch.py tests/test_agent_factory_quick_operator.py \
  tests/test_quick_prompt_contract.py tests/test_agent_factory.py \
  tests/test_agent_factory_testing_branch.py tests/agui/test_endpoint.py \
  tests/evals/test_tool_registry.py tests/test_api_pipeline_resume.py \
  tests/test_pipeline_anchor.py -q
# → 108 passed

uv run ruff check <changed files>   # → All checks passed
uv run mypy src/finance_agent/operator src/finance_agent/tool_registry.py  # → Success

# 前端
cd frontend && npx tsc -b && npx vitest run   # → 100 files / 889 tests passed

# E2E
cd tests/e2e/playwright && npx playwright test --config=playwright.operator.config.ts
# → 2 passed (12.5s)
```

## 写操作确认协议（实现口径）

- 第一次调用：`confirm=false` → 只返回 `status=confirmation_required`、预览摘要、一次性 token（TTL 300s）、确认指引；不执行任何业务函数。
- 第二次调用：`confirm=true` → 校验预览未过期、session/action/params 一致、**当前用户消息**含确认词（确认/确定/执行/同意/可以/好的）后才执行；成功即删除预览记录（单次使用）。
- token 可显式携带；省略时按 session+action+params 匹配最近一条预览（跨 run 时不依赖 chat_history 里的 token）。
- 所有写操作记录 `operator_write` 审计日志（session/action/params/result），并经 `_trace_tool` 进 Langfuse。

## 异常记录

- E2E 后端日志出现 `RM 结构化输出解析失败，降级为自由文本`：`STUB_SCENARIO=operator` 的固定回复不满足管线 RM JSON 契约，属于测试 stub 与管线场景不匹配，不影响派单/查询断言；生产真实模型不会走该 stub。
- **提示词发布状态（重要）**：最终 quick_mode 已作为 Langfuse **version 31**（`staging` + `latest`，2323 chars）存在；生产代码尚未合并部署（主检出仍是旧版快速模式），因此 `production` 标签保持在 **version 28**（旧内容）。代码合并部署后需把 `production` 标签移到 version 31（或重新执行 `deploy_prompts.py`）。
- 全量 `uv run pytest` 在本机运行 30+ 分钟未结束（进程长时间无 CPU 增长，疑等待外部网络/Langfuse），已主动停止；定向 108 项与 E2E 全绿。全量套件需在 CI 或带 `LANGFUSE_*` 的环境补跑，不能以定向结果替代全量结论。
- 写操作的 E2E 两轮确认未纳入本轮（需要更复杂的多轮 stub）；已由 11 项单测覆盖，真实 LLM 下的文案与体验待 owner 抽验。

## 结论

- [x] P1 + P2 + P3 + UI 回执卡片的自动化验证全部通过（定向 108 项 / E2E 2 项 / 前端 889 项 / lint / 类型）。
- [ ] 全量 `uv run pytest` 未在本环境跑完，需 CI/带 Langfuse 环境补跑。
- [ ] 人工主观验证（真实 LLM 下回执文案、写操作确认体验、因子控制回执）待 owner 在部署后抽查。
- [ ] `tasks.md` 全勾 + 全量测试结论确认后才可 archive。
