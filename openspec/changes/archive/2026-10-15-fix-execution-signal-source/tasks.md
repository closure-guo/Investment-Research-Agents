# Tasks: fix-execution-signal-source

- [x] 先红后绿：`tests/execution/test_driver.py` 造数口径重写（decision_log → predictions：`init_predictions` + `insert_prediction`），11 项含 4 项新增（未审批过滤 / 存量窗口 / 无快照信号照常 / 脏数据不阻断）——先跑红 11/11，实施后全绿
- [x] 驱动器①段取源改 `predictions`（`status='open'`；action 从 `rationale_snapshot` 解出走信号契约映射；`source="predictions"`）
- [x] 审批过滤：仅 `fund_manager_decision == 'approve'` 可转换；`skipped_unapproved` 留痕
- [x] 存量窗口 `window_days`（默认 5 自然日）+ `skipped_backlog` 留痕（含放宽窗口可执行用例）
- [x] 信号生成移到快照守卫之前（无快照照常落库；摘要统一键集，含真实 `signals_created`）
- [x] `tests/execution/` + `tests/outcome/test_ops_jobs.py` + `tests/outcome/test_scheduler.py` 全绿（296 passed）；ruff / mypy 干净
- [x] 真实数据核验：生产库 `predictions` 54 行 open 全量映射核验（52 行 approve 可转换 / 2 行 reject 过滤；`rationale_snapshot` 172/172 可解析）与隔离库实跑（见验证报告）
- [x] `openspec validate fix-execution-signal-source --strict` 通过
- [x] 部署后实跑核验：`paper_execute_daily` 手动补跑 → 11 信号落库（生产观点）→ `/broker` 页读数一致；中断-恢复幂等收敛实证（验证报告 §6）
