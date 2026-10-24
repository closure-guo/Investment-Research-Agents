# 人工验证报告: add-composer-stat-pills

**日期**: 2026-10-23（自动验证）；2026-10-24（浏览器人工抽查，真实 provider）
**验证人**: AI（自动化验证 + 浏览器人工抽查）
**关联 delta**: `openspec/changes/add-composer-stat-pills/`
**E2E 门禁**: 已执行专项套件 → **3 passed**（配置 `tests/e2e/playwright/playwright.composer.config.ts`，隔离端口 8010/5182 + 独立测试库；未触碰本机 docker compose 的 8000/5173 真实数据）

## 验证结果

| Scenario | 自动覆盖 | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 三项数据齐备 / Token / 上下文各自隐藏 | `frontend/src/test/components/composerStats.test.tsx` | 有数据显示三控件；缺 token 只留两项；缺峰值隐藏环形 | 9/9 通过 | ✅ |
| 弹层互斥 / Escape 关闭 | 同上 | 同时只开一个；Escape 收起 | 通过 | ✅ |
| 输入栏不再显示免责声明 | `frontend/src/test/components/chatInputStats.test.tsx` | 显示「N 轮 M 步」，无免责声明文案 | 2/2 通过 | ✅ |
| 报告页脚免责声明保留 | 既有 `ReportEntryBanners` / report 渲染测试 | 报告底部仍有合规声明 | 回归套件通过 | ✅ |
| 会话 stats 端点真值 / 404 | `tests/test_api_session_stats.py` | 返回 turns/steps/tokens/context_peak；不存在 404 | 2/2 通过 | ✅ |
| usage 落库（线程内 contextvar） | `tests/test_deep_analysis_usage_accounting.py` | 假图在 stream 内读到收集器并注入 usage，会话记录得 1600/80/1680/900 | 通过 | ✅ |
| quick 模式 Agent 层累计 usage | `tests/test_agent_usage_accounting.py` | 两次调用 100/10 + 500/50 → 600/60/660，峰值 500 | 通过 | ✅ |
| quick 运行收尾落库 | `tests/agui/test_endpoint.py::test_quick_run_persists_provider_usage` | AG-UI 收尾后会话 tokens=1600、峰值=900 | 通过 | ✅ |
| quick 会话 steps 回退 | `tests/test_session_usage_stats.py::test_chat_session_steps_fall_back_to_assistant_messages` | chat 会话 2 条 assistant → steps=2、turns=2 | 通过 | ✅ |
| 峰值累加语义 | `tests/test_session_usage_stats.py` | token 累加、峰值取 max、无 usage 保持 0 | 4/4 通过 | ✅ |
| probe 返回 max_output | `tests/test_api_llm_config.py::test_test_llm_config_success` | capability.max_output=8192 | 通过 | ✅ |

## 验证命令与证据

```
# 浏览器端到端（真实前后端 + 独立测试库；LLM 走 TESTING stub）
cd tests/e2e/playwright
npx playwright test --config=playwright.composer.config.ts
→ 3 passed (composer-stat-pills.spec.ts ×1, llm-settings-pane.spec.ts ×2)

# 证据截图（test-results/，未入库）
# composer-stat-pills-…/composer-stats.png：2 轮 2 步 / 12.8万 / 23% + 上下文面板
# llm-settings-pane-…/llm-settings.png：连接/模型/高级三段 + 「已保存（不回显）」

uv run pytest tests/test_session_store.py tests/test_session_events.py \
  tests/test_api_session_stats.py tests/test_api_llm_config.py \
  tests/test_deep_analysis_tool.py tests/test_deep_analysis_usage_accounting.py \
  tests/nodes/test_llm_usage_collector.py tests/llm/test_budget_governance.py -q
→ 88 passed

uv run pytest tests/test_agent_usage_accounting.py tests/test_session_usage_stats.py \
  tests/agui/test_endpoint.py tests/test_react_loop.py tests/test_api_session_stats.py \
  tests/test_deep_analysis_usage_accounting.py -q
→ 41 passed

cd frontend && npx vitest run
→ Test Files 105 passed, Tests 941 passed

cd frontend && npm run build
→ ✓ 4635 modules transformed, built in 7.73s

uv run ruff check <changed files> → All checks passed
```

## 浏览器人工抽查（2026-10-24，真实 provider）

**环境**：工作区代码直起 uvicorn（端口 8010，隔离测试库同 E2E 专项配置，**无 `TESTING=1`**，LLM 走 `.env` 真实 provider = deepseek-v4.1-flash@opencode）+ vite dev（端口 5182）。

| 检查项 | 预期 | 实际（deep 真实分析 600519，管线 7m16s） | 通过 |
|---|---|---|---|
| 管线运行中计量条隐藏 | usage 未落库不渲染 | `stats visible = false` | ✅ |
| 管线结束后三控件齐现 | 有数据显示三控件 | 「1 轮 25 步」「21万」「6%」 | ✅ |
| 会话统计面板 | 轮次/步骤/管线用时 | 1 / 25 / 7m16s | ✅ |
| Token 用量面板 | 输入/输出/合计精确值 | 84,795 / 125,332 / 210,127（合计=两项之和） | ✅ |
| 上下文已用面板 | 环形 + `~峰值 / 窗口` | 6%，~1.6万 / 25.6万（16,449 / 256,000=profile contextLength） | ✅ |
| stats 端点真值 | 与 UI 一致 | `GET /api/sessions/f4cc9ed3-477/stats` = {turns:1, steps:25, duration_ms:435678, tokens_prompt:84795, tokens_completion:125332, tokens_total:210127, context_peak_tokens:16449}，与面板逐值一致 | ✅ |
| 输入栏免责声明移除 | composer 区无该文案 | 抽查脚本在聊天页全文搜「AI 生成仅供参考，不构成投资建议」0 命中 | ✅ |
| 报告页脚免责声明保留 | 报告卡片页脚有合规文案 | ReportCard 页脚「本报告由 AI 系统基于公开数据自动生成，仅供参考研究，不构成投资建议。投资有风险，入市需谨慎。」可见（截图 report-footer-disclaimer.png） | ✅ |
| 窗口回退（顺带） | 无 profile 时回退 128000 | 同一会话无 profile 打开 → 环形 13%（16,449/128,000），控件不消失 | ✅ |

证据截图：`tmp/spot-check-20261024/`（settings-1-layout / settings-2-real-probe / pills-1-activity / pills-2-usage / pills-3-context / report-footer-disclaimer，未入库）。抽查脚本为一次性文件，验证后已删除。

## 异常记录

- 首次全量前端套件在 `selectSession.test.tsx` 出现 3 个失败：`/api/sessions/{id}/stats` 请求被测试桩计入「会话详情调用序」，并触发 2s 轮询计数漂移。处置：① 去掉计量条轮询（usage 只在管线结束时落库，运行中轮询本来读不到新值）；② 测试桩为 `/stats` 单独分流且不消耗 detailCallCount。修复后 941 全绿。
- 浏览器抽查脚本初版把报告页脚免责声明的断言文案写成输入栏旧文案「AI 生成仅供参考，不构成投资建议」，在聊天页全文搜索 0 命中而报错——报告卡片页脚实际为加长合规文案（App.tsx ReportCard 内）。「输入栏无旧文案」与「报告页脚有新文案」两项各自独立验证通过；文案差异是预期行为（spec：免责声明只在报告页脚保留），非缺陷。

## 结论

- [x] 自动化验证全部通过
- [x] 浏览器人工抽查（2026-10-24，真实 provider）通过：三控件真值、面板明细、stats 端点逐值一致、免责声明迁移到位
- [x] 全部通过，可 archive
