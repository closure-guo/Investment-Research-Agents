# Tasks: add-ops-missed-run-catchup

## 1. 补偿机制

- [x] 1.1 `ops/model.py`：`job_run_statuses_on(job_id, day)` 按天取运行状态（missed_today 与补跑规划共用同一读数）
- [x] 1.2 `ops/catchup.py`：`scheduled_fire_time` / `plan_catch_up`（白名单 + 6h 窗 + 上限 3 + 按计划时刻升序 + ok/running 跳过）
- [x] 1.3 `ops/catchup.py`：`run_catch_up`（顺序执行、非交易日链中止、异常不阻断、`source=catchup` 留痕）
- [x] 1.4 `scheduler.py`：`start_scheduler` 注册一次性 `date` job（启动后 5s）拉起补跑；`OPS_CATCHUP_ON_STARTUP=0` 关闭

## 2. 可见性

- [x] 2.1 `ops_api.py`：`_job_rows` 每行新增 `missed_today`（计划时刻已过 + 当天无任何运行行；非排程日恒 false）

## 3. 测试（先红后绿）

- [x] 3.1 `tests/outcome/test_ops_catchup.py`：规划口径 6 例（窗内/超窗/已 ok/running/未到点/非排程日）+ 上限与排序 + 非交易日中止 + 失败不阻断（实 32 例）
- [x] 3.2 `tests/outcome/test_scheduler.py`：启动注册一次性补跑 job（TESTING=1 不注册）+ `OPS_CATCHUP_ON_STARTUP=0` 不注册
- [x] 3.3 `tests/test_ops_api.py`：`missed_today` 四态（已过且无行 / 未到点 / 失败行 / 周末与月批非排程日）
- [x] 3.4 `tests/outcome/test_ops_statuses_on.py`：按天读状态的跨日/跨任务边界（新增，单列一文件）

## 4. 验证与收口

- [x] 4.1 `uv run pytest tests/outcome tests/factors/test_monitor.py -q` 全绿（990 passed, 2 skipped）；`tests/test_ops_api.py` 等 6 文件 242 passed
- [x] 4.2 `uv run ruff check` 通过；`uv run mypy` 对改动四文件无新增错误（`evals/*` 7 条为既有基线）
- [x] 4.3 真实链路重放：按 2026-10-22 17:29 时点只读重放，命中 `screening_daily` / `factor_decay_monitor` / `factor_builtin_register` 三项（`tmp/dryrun_catchup_050.py`，报告见 `tests/validation/2026-10-23-add-ops-missed-run-catchup-validation.md`）
- [x] 4.4 更新 `docs/incidents/050-factor-ic-missed-fire-20261022.md` 防复发状态 + README 索引
- [x] 4.5 `openspec validate add-ops-missed-run-catchup --strict` 通过

## 5. 未纳入本轮（已移交 issue）

- [x] 5.1 前端渲染 `missed_today`（交互类 → 另立 delta + E2E）→ #453
- [x] 5.2 `factor_decay_monitor` 多日回填窗口（改 IC 口径语义，需单独评审）→ #454
- [x] 5.3 进程收尾可读终态（incident 050 防复发第 3 条，与 046 同族）→ #455（2026-10-23 两度实证：僵尸 running 行挡住补跑规划，screening_daily 两次未被接管）
