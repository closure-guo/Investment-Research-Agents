# Tasks: add-auto-pool-analysis-daily-brief

- [x] OpenSpec delta `validate --strict` 通过
- [x] `auto_pool_analysis_daily`（16:22）注册为日批任务：仅取当日成功池、rank 前 N（默认 10）、
      `(trade_date, symbol)` 幂等、时效性移除、空池/过期池显式跳过
- [x] 自动分析复用 `AnalyzeQueue` 与 SSE/journal，人工批次语义不变
- [x] 自动分析产出的观点由既有 `paper_execute_daily` 次日开盘消费；`watch/hold/sell` 不建仓
- [x] `daily_brief_daily`（20:30）注册为日批任务：事实汇总 → `purpose=daily_brief` LLM → 落
      `daily_briefs` + `reports/daily-brief-YYYY-MM-DD.md`
- [x] LLM 失败写确定性降级摘要并标记 failed（不空报告、不冒充成功）
- [x] `daily_brief` purpose 解析：`DAILY_BRIEF_*` 逐字段覆盖、默认回退 `LLM_*`（opencode deepseek-v4.1-flash）
- [x] `GET /api/v1/ops/daily-brief`（date/latest）与 `/ops` 每日汇报区（空态/失败态/正常态）
- [x] 单元/集成测试：池分析编排、幂等、上限、时效性、日报成功/失败/幂等、resolver、注册表 26
- [x] 前端 Vitest + Playwright 日批页用例更新（26 job + 汇报区）
- [ ] `uv run pytest` / `ruff` / `mypy` / 前端测试通过（定向 338 项 + 前端 887 项 + E2E 6 项已过；
      全量 pytest 无失败跑到 53% 后卡在依赖 Langfuse/网络的长测试，已停止；ruff 全过；
      mypy 新模块全过，仓库存量 api.py/evals 有既有报错）
- [x] 人工验证报告落 `tests/validation/`（交互类变更）
