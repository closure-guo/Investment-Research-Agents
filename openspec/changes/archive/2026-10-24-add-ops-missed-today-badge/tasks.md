# Tasks: add-ops-missed-today-badge

- [x] `frontend/src/types.ts` 补 `OpsJobStatus.missed_today?: boolean`；`DailyOpsPage.describeJob` 消费后端字段：`true` → 「今日漏跑」告警徽章（degraded 样式 + 专属摘要文案），字段缺失回退既有客户端推断
- [x] 四态单测矩阵钉死：真漏跑（告警 + 非 failed 样式）/ 已失败（failed 徽章 + 不误报漏跑）/ 未到点（不告警，既有文案）/ 非排程日（周末与月批非排程日不告警）
- [x] TESTING 造数扩展：`/api/test/seed` 新增 `ops` 块（`now` 冻结 ops 状态时钟 + `runs` 造 `job_runs` 行 + `runs_reset` 清运行行），后端单测覆盖冻结与造行
- [x] E2E spec 覆盖核心场景（`tests/e2e/playwright/tests/ops-missed-badge.spec.ts`，复用 isolated config）：漏跑告警出现、failed 不误报为漏跑、未到点/非排程日不误报
- [x] `uv run pytest` 定向子集、`cd frontend && npm test`、`npm run build`、`ruff` 全部通过
- [x] E2E 门禁执行通过（isolated config，隔离库）
- [x] 人工验证报告落 `tests/validation/`（浏览器人工抽查四态可见表现）
