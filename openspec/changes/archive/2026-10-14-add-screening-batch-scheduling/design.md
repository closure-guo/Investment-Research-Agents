# Design: add-screening-batch-scheduling

## Context

`run_screening_batch`（`screening/pipeline.py`）实现完整：快照确认 → L1 → L2 → L3 → 精选池落库，失败不产残缺池，守卫齐全（快照库为空 / 数据日期不新鲜 → fail loud），并有完整单测。缺的只有**触发器**。

ops 注册表（`outcome/ops/jobs.py` + `scheduler.py`）是「调度 + 运行历史 + 手动补跑 + 单飞锁 + 悬挂清扫所有权」的统一通道（add-eval-ops-console 契约），且评估运维页对该通道数据驱动渲染——选股日批应复用该通道而非新造触发器。

## Decisions

- **走 ops 注册表（JOB_IDS 8→9）**：一次获得 调度 / 历史 / 手动补跑 / 单飞锁 / 所有权清扫 五件套；评估运维页零改动自动呈现。
- **排程 15:40**：晚于快照 15:30 十分钟（快照任务含重试，留足余量）；早于结算链 16:00，保持「先产出池、再结算」的日序。
- **失败语义沿用旁路铁律**：job 内 `run_screening_batch` 抛错 → 历史 `failed` + 错误串，不向 API 进程上抛（既有 `run_job` 契约）。
- **快照守卫不重复造**：守卫已在管线内（批次 failed、不产残缺池），job 只做封装与摘要透传（`trade_date/status/l1_passed/l2_top/l3_keep/pool_size/error`）。
- **规范漂移一次修净**：eval-ops-console 的「5 任务」枚举是历史漂移（8 个任务已在运行）——MODIFIED 改为「全部已注册任务（当前 9）」，场景同步改写，避免未来每次扩容都要改规范。

## Alternatives Considered

- **独立 APScheduler 触发器**：放弃——绕过 ops 通道即失去历史 / 手动补跑 / 单飞锁，运维界面不可见。
- **挂到 market_snapshot_daily 成功回调**：放弃——任务间隐式串联不可观测，违背「日批状态可查」契约。
- **排程 15:35**：放弃——快照任务重试窗口过窄（含 `_with_retry`）。

## Risks

- 快照长期不可用（东财封禁 issue #325）时该 job 每日 failed：**预期行为**（如实落历史、不产残缺池），解封后自然恢复；验证报告如实记录。
- 计数类既有测试更新（E2E 8→9）：与 5→8 同先例，断言强度不变，逐条记录。
