# Tasks: wire-builtin-factor-registration

- [x] 先红后绿：`tests/factors/test_builtin_register_job.py`（空库注册 / 全量已存在跳过且不触面板 / 部分缺失只提交子集 / 含 retired 不回填 / 面板不可得 fail loud）
- [x] `register_builtins` 增 `factors` 参数（默认 None = 全量；既有调用方零改动）
- [x] `run_factor_builtin_register` 任务入口（存在性检查 → 缺失子集 → 面板 → 五道闸 → 摘要 `{registered, rejected, skipped, reasons}`）
- [x] ops 注册表第 13 个 job（`factor_builtin_register`，15:50 mon-fri）+ scheduler 注册与重试包装；既有计数断言 12→13 同步
- [x] `uv run pytest tests/factors tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py -q` 全绿（361 passed）+ api 切片 960 passed；ruff / mypy 干净
- [x] `openspec validate wire-builtin-factor-registration --strict` 通过
- [x] 部署（重启后端，6e5958b7）+ 生产补跑 run 92：五条 builtin 真实过闸读数落验证报告（含深面板 ② 证据）
- [x] 下游联动观测：库仍空（0/5 入库）→ 监控 checked=0、选股 L2 仍「无有效 IC 信息」（如实记录；② 落地后自愈）
- [x] 验证报告落 `tests/validation/2026-10-15-wire-builtin-factor-registration-validation.md` + sync + archive
