# Design: add-monthly-batch-scheduling

## Context

ops 注册表（`outcome/ops/jobs.py` + `scheduler.py`）是「调度 + 运行历史 + 手动补跑 + 单飞锁 + 悬挂清扫所有权」的统一通道；`_cron_from_spec` 目前把 `day_of_week` 写死，前端两处排程文案（`EvalOpsPane.scheduleText`、`DailyOpsPage` 时间线）也只认周粒度。两条待接线链路的入口函数（`extract_candidate_clauses` / `propose_broad_batch`）均已实现并有完整单测，缺的只有触发器与批量编排。

## Decisions

- **一个 delta 覆盖两条链路**：它们共享同一处机制扩展（排程粒度）与同一批前端文案改动，拆成两个 delta 会在同文件冲突。
- **排程粒度：可选 `day_of_month`**：`SCHEDULES` 条目含 `day_of_month` → `CronTrigger(day=..., hour, minute, timezone)`（月批）；否则维持 `day_of_week` 分支（日批）——**同一形态两分支**，不引入新调度器/新注册表。
- **时间错开**：记忆提炼 09:00、因子提案 10:00（月初 1 日，盘前；互不竞争 LLM 网关，且都在日批链（15:30 起）之外）。
- **记忆提炼任务**：源 = 已结算决策近窗口（默认 90 天，避免全量重跑）；产出落**候选态**（`store_candidate_clauses`），注入契约不变（未确认不注入）；LLM 失败自动走规则兜底（模块既有行为），两路都空 → 摘要如实 `candidates=0`（不造假候选）。
- **提案任务**：`propose_broad_batch`（跨桶采样）→ 逐条 `admit_candidates` 五道闸 → 过闸入库、未过留事件史；逐条裁决（单条失败不阻断整批）；摘要 `{proposed, admitted, rejected, errors}`；LLM 网关不可用 → job failed 如实（月批可重试，不静默）。
- **幂等**：记忆提炼以「同窗口已结算决策集 → 条款文本」去重（store 侧既有去重语义为准，实施时读实现确认并测）；提案批以「同批次同候选集不重复入库」（五道闸双层查重既有语义）。
- **规范漂移一并修净**：`eval-ops-console` 的「排程（工作日 + 时刻 + 时区）」与「当前 9 个」枚举在 D2 扩容（10）后已陈旧——本次 MODIFIED 一次修到「通用粒度（日/周/月）」+「全部已注册任务（当前 12）」，并保全既有三场景。

## Alternatives Considered

- **给月批单开一个 APScheduler 触发器**：放弃——绕过 ops 通道即失去历史/手动补跑/单飞锁，运维界面不可见（同 D1 先例）。
- **拆成两个 delta**：放弃——同文件冲突面（注册表/cron/前端文案/spec）大于收益。
- **季批（提案）**：暂缓——月批起步观察成本；`day_of_month` 形态支持后续改季度（`month=` 扩展留到需要时）。
- **月批加 cohort 式开关**：放弃（非目标）——月批量小；如需成本闸，后续 delta 引入。

## Risks

- 东财封禁期：提案批依赖因子评估面板（需快照）→ 可能 fail loud（如实落历史）；记忆提炼只依赖**已结算决策**（历史数据）→ **可在封禁期真实运行**（本 delta 的可验证面）。
- 月批错峰在 1 日盘前：与 cohort（18:00 日批）不冲突；若 1 日恰逢非交易日，job 照跑（数据窗口按自然月，不依赖交易日历）。
