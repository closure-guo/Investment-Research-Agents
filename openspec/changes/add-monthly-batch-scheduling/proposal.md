# Proposal: add-monthly-batch-scheduling

## Why

2026-10-15 休眠链路审计发现**两条「实现完整但零生产调用方」的链路**（与前三条同类）：

- **语义记忆月批提炼**：`extract_candidate_clauses` / `store_candidate_clauses` 全仓唯一调用方是 TESTING 种子——「按月从已结算决策提炼候选经验条款」（decision-memory 规范既有要求）从未运行过。后果：`/memory` 确认页永远空态（issue #327 已登记「月批接线」）。
- **因子提案 agent**：`propose_broad_batch` / `propose_deep_improvement` 在 `src/` 与 `scripts/` 中零调用方——「LLM 提案 → 五道闸准入 → 因子库」闭环缺触发器，因子库只能靠 builtin 注册（factor-proposal 规范意图未在运行态成立）。

两条链路的共同缺口是**月批排程**：ops 注册表（`SCHEDULES` + `_cron_from_spec`）当前**只支持周粒度**（`day_of_week` 硬编码），前端两处排程文案（评估运维 `scheduleText`、日批运维时间线）同为周粒度——月批任务无处安放。

## What Changes

- **排程粒度扩展**：`SCHEDULES` 支持可选 `day_of_month`（存在 → `CronTrigger(day=..., hour, minute, timezone)`；否则维持 `day_of_week` 分支）；前端两处排程文案支持月粒度（「每月 1 日 09:00」）；`eval-ops-console` 规范「排程（工作日 + 时刻 + 时区）」MODIFIED 为通用粒度（日/周/月）+ 任务枚举更新（当前 12）。
- **新任务 ①`memory_extract_monthly`**（每月 1 日 09:00 Asia/Shanghai）：取已结算决策（近窗口）→ `extract_candidate_clauses`（LLM 优先、规则兜底）→ `store_candidate_clauses` 落**候选态**；注入契约不变（未确认不注入，经 `/memory` 人工确认后生效）；任务走统一通道（调度/运行历史/手动补跑/单飞锁）。
- **新任务 ②`factor_proposal_monthly`**（每月 1 日 10:00）：`propose_broad_batch`（跨桶采样）→ 逐条经五道闸 `admit_candidates` → 过闸者入因子库、未过者留事件史；摘要含 proposed/admitted/rejected 计数；LLM 失败 → job failed 如实（月批可重试）。
- 既有测试最小更新（计数类 10→12 + 排程文案断言），逐条记录。

非目标（Out of scope）：

- 不改确认/注入契约（未确认不注入、确认入口均维持现状）。
- 不做提案的 LLM 成本开关（月批量小；cohort 式开关留后续）。
- 不做季批粒度（月批起步，观察后可调）；不做「月批失败自动告警」之外的新告警通道。
- 不改 `/memory`、`/factors` 页面（数据由本 delta 点亮）。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `decision-memory`: 新增「月批提炼排程」要求。
- `factor-proposal`: 新增「提案月批排程」要求。
- `eval-ops-console`: 「日批运行状态与历史可观测」MODIFIED——排程粒度通用化（日/周/月）+ 任务枚举（当前 12）。

## Impact

- **后端**：`outcome/ops/jobs.py`（注册表 10→12）、`outcome/scheduler.py`（`_cron_from_spec` 粒度分支 + 两任务注册）、`memory/`（月批入口函数）、`factors/`（提案批入口函数）。
- **前端**：`EvalOpsPane.tsx`（`scheduleText` 月粒度）、`pages/ops/DailyOpsPage.tsx`（时间线文案月粒度）+ Vitest。
- **测试**：两任务单测（合成已结算决策/合成面板）+ 注册表/排程单测 + 前端文案测试 + E2E 计数更新。
