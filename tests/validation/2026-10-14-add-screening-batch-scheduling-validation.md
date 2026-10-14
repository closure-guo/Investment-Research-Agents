# add-screening-batch-scheduling 实施验证报告

**日期**: 2026-10-14
**范围**: 选股日批接线（ops 注册表 8→9，工作日 15:40）+ eval-ops 规范「5 任务」漂移修正
**交付**: `outcome/ops/jobs.py`（注册表）、`outcome/scheduler.py`（`_screening_job` + 注册）、`screening/pipeline.py`（`run_screening_daily` 封装）

## 一、实施摘要

- **接线前**：`run_screening_batch` 全仓零调用方（无调度、无端点、无脚本）——「每个交易日快照更新成功后执行」的规范意图在运行态从未成立。
- **接线后**：`screening_daily` 入 ops 注册表（JOB_IDS 8→9），排程 mon-fri 15:40 Asia/Shanghai（快照 15:30 之后、因子监控 15:45 之前）；统一通道获得 调度/运行历史/手动补跑/单飞锁/悬挂清扫所有权 五件套；评估运维页数据驱动自动呈现（零前端改动）。
- **失败语义**：`run_screening_daily()` 封装——批次 `status="failed"`（快照过期等）**上抛** `RuntimeError` → `run_job` 记 `failed` 运行行（运维界面可见）；快照库为空 → `SnapshotNotFreshError` 原样透传（同样落 failed）。两层（screening_batches 批次行 + job_runs 运行行）状态一致。

## 二、TDD 证据

先写失败测试 → 7 failed（注册表 4 项 + 调度注册 + monitor 邻接 + 注册表计数）→ 实现后全绿。

| 测试 | 内容 |
|---|---|
| `test_ops_jobs.py::TestScreeningDailyRegistration`（新增 4 例） | 注册四处对齐 + 时刻序（快照<选股<因子）；排程逐字 15:40；封装对 failed 上抛（match 错误串）；封装对 ok 摘要原样透传 |
| `test_ops_jobs.py::TestRegistry` / `test_scheduler.py` / `test_factors/test_monitor.py` | 计数与邻接按新时刻序更新（见 §四清单） |

**靶向**：`pytest tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py tests/factors/test_monitor.py tests/test_ops_api.py -q` → **130 passed**
**域回归**：`pytest tests/outcome tests/factors tests/screening tests/data/test_market_snapshot_job.py -q` → **1014 passed**
**lint**：ruff check / format 全过

## 三、真实 API 验证（本地隔离实例 :8090，真调度器）

```
GET /api/v1/ops/jobs → 任务数: 9
顺序: ['market_snapshot_daily', 'screening_daily', 'factor_decay_monitor', 'decision_settle_daily', ...]
screening_daily: 选股 {'day_of_week': 'mon-fri', 'hour': 15, 'minute': 40, 'timezone': 'Asia/Shanghai'}
              | 下次触发: 2026-10-15T15:40:00+08:00
scheduler_running: True
POST /api/v1/ops/jobs/screening_daily/run（手动补跑）
→ run_failed（run_id=1）：SnapshotNotFreshError: 快照库为空：无法运行选股日批（先跑 market_snapshot_daily）
```

- 排程注册与下次触发时刻逐字符合；**手动补跑 fail loud** 且失败原因可判读（快照空 → 不产残缺池）——当前东财封禁（#325）下的预期行为。
- 运维页（设置 → 评估运维）对该任务卡片为数据驱动渲染，E2E 套件覆盖（§五）。

## 四、既有测试最小更新清单（计数/邻接类，断言强度不变）

1. `tests/outcome/test_ops_jobs.py::TestRegistry::test_ids_labels_funcs_schedules_aligned`：8 → 9（附注释）
2. `tests/outcome/test_scheduler.py::TestCohortJobRegistration::test_registers_six_jobs_including_cohort_batch`：len 8→9、id 集合 +screening_daily
3. `tests/outcome/test_scheduler.py::TestMisfireHardening::test_all_jobs_have_misfire_grace_and_coalesce`：len 8→9
4. `tests/factors/test_monitor.py::test_ops_registry_contains_factor_decay_monitor`：邻接断言按新时刻序（快照→选股→因子），断言意图（注册表按时刻升序）不变
5. `tests/e2e/playwright/tests/eval-ops-console.spec.ts`：JOB_IDS 数组 +screening_daily（仍逐 id 断言存在与顺序）

与 5→8 扩容同先例（逐条记录）；其余既有测试零修改。

## 五、E2E 门禁

`CI=1 npx playwright test --config=playwright.eval-ops.config.ts`（stub 后端，TESTING=1，独立端口 8005/5178）：

```
5 passed (48.4s)
```

含「分区渲染任务卡片（当前 9 个 job）」——逐 id 断言存在与顺序（`JOB_IDS.length` 动态 + 逐卡片三要素），断言强度不变。

## 六、遗留

1. 生产栈复核：合并后容器 `--reload` 生效，`GET /api/ops/jobs` 应现 9 任务（挂合并后部署核验）。
2. 真实日批数据（首个成功批次）待东财解封（issue #325）——本 delta 只解「触发器」，数据面不受影响。
3. eval-ops-console 规范「5 任务」漂移已随本 delta MODIFIED 修正为「全部已注册（当前 9）」，后续扩容不再需要改规范枚举。

## 七、结论

选股日批接线完成：触发器（调度 + 手动补跑 + 运行历史）三态在真实 API 上验证通过；失败语义（fail loud + 双层一致）经真实手动补跑实证；既有测试仅计数/邻接类最小更新（逐条留档）。
