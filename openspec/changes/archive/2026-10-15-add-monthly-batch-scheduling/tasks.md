# Tasks: add-monthly-batch-scheduling

## 1. 排程粒度扩展（先写失败测试）
- [x] 1.1 `scheduler.py`：`_cron_from_spec` 分支——`day_of_month` 存在 → `CronTrigger(day=..., hour, minute, timezone)`；否则维持 `day_of_week` 分支；单测：月批 cron 字段正确、既有日批 cron 逐字不变
- [x] 1.2 前端排程文案两处月粒度：`EvalOpsPane.scheduleText`（「每月 N 日 HH:MM」）+ `DailyOpsPage` 时间线（同形态）；Vitest：月批条目渲染 + 既有日批文案不变
- [x] 1.3 `eval-ops-console` 规范 MODIFIED 已随本 delta（粒度通用化 + 当前 12 个 + 月粒度可读场景）

## 2. 记忆提炼月批（先写失败测试）
- [x] 2.1 `memory/`：月批入口 `run_memory_extract_monthly()`——取已结算决策（近 90 天窗口）→ `extract_candidate_clauses`（LLM 优先/规则兜底）→ `store_candidate_clauses` 落候选态；摘要 `{window_days, settled_count, candidates, path}`；单测：合成已结算决策 → 候选入库且 `/memory` 可见（list_pending 含之）/ 注入集合不变（未确认不注入回归）/ 空素材 `candidates=0` 不造假
- [x] 2.2 `jobs.py` 注册 `memory_extract_monthly`（10→11，标签「记忆提炼」）+ `SCHEDULES`（day_of_month=1, 09:00 Asia/Shanghai）+ scheduler 注册与 `_with_retry`；单测：注册/排程/封装

## 3. 因子提案月批（先写失败测试）
- [x] 3.1 `factors/`：月批入口 `run_factor_proposal_monthly()`——`propose_broad_batch` → 逐条 `admit_candidates` → 过闸入库/未过留事件史；摘要 `{proposed, admitted, rejected, errors}`；单测：合成提案（stub 客户端）→ 1 过 2 不过的计数与库/事件史两处断言；网关不可用 → 上抛（fail loud）；逐条失败不阻断整批
- [x] 3.2 `jobs.py` 注册 `factor_proposal_monthly`（11→12，标签「因子提案」）+ `SCHEDULES`（day_of_month=1, 10:00 Asia/Shanghai）+ scheduler 注册；单测：注册/排程

## 4. 既有测试最小更新（计数类，逐条记录）
- [x] 4.1 后端注册表/调度计数断言 10→12（test_ops_jobs×2、test_scheduler×2 按新增两任务同步）
- [x] 4.2 E2E `eval-ops-console.spec.ts` JOB_IDS +2（位置=后端顺序）+ 标题计数

## 5. 验证
- [x] 5.1 全量测试通过（后端 + 前端 + E2E stub 门禁）
- [x] 5.2 真实栈手动补跑两任务：记忆提炼（有已结算决策 → 候选入库 → `/memory` 页可见，截图）；因子提案（封禁期如实 fail loud 或零候选）；运维页两新任务卡片与「每月 1 日 HH:MM」文案
- [x] 5.3 既有测试零修改（除 §4 计数类最小更新）
- [x] 5.4 验证报告落 `tests/validation/2026-10-15-add-monthly-batch-scheduling-validation.md`
