# Tasks: add-screening-batch-scheduling

## 1. 注册与排程（先写失败测试）
- [x] 1.1 `jobs.py`：JOB_IDS 增 `screening_daily`（8→9）+ `JOB_LABELS`（「选股」）+ `JOB_FUNCS`（封装 `run_screening_batch`，摘要透传 `trade_date/status/l1_passed/l2_top/l3_keep/pool_size/error`）+ `SCHEDULES`（mon-fri 15:40 Asia/Shanghai）
      —— 单测：注册表含 9 项且顺序稳定；排程时刻逐字；封装函数透传摘要 / 失败落 failed（stub batch 两态）
- [x] 1.2 `scheduler.py`：注册与 `_with_retry` 包装（与既有 8 任务同构）；单测：启动注册断言（含 screening_daily 与 15:40 时刻）

## 2. 规范漂移修正 + 既有测试最小更新
- [x] 2.1 后端状态接口/注册表测试计数断言 8→9（逐条记录）
- [x] 2.2 E2E `eval-ops-console.spec.ts` 计数与标题 8→9（同 5→8 先例；断言强度不变：仍逐 id 断言存在与顺序）

## 3. 验证
- [x] 3.1 全量测试通过（后端 `uv run pytest tests/ --ignore=tests/scripts -m "not live"` + 前端 vitest）
- [x] 3.2 真实栈人工验证：调度器启动后 `GET /api/ops/jobs` 含「选股」任务（15:40 排程）；手动补跑一次（当前快照缺失 → failed 如实落历史、摘要含原因）；运维页（设置 → 评估运维）可见该任务
- [x] 3.3 既有测试零修改（除 §2 计数类最小更新，逐条记录于验证报告）
- [x] 3.4 验证报告落 `tests/validation/2026-10-14-add-screening-batch-scheduling-validation.md`
