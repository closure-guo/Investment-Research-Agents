# Tasks: wire-builtin-factor-registration

- [ ] 先红后绿：`tests/factors/test_builtin_register_job.py`（空库注册 / 全量已存在跳过且不触面板 / 部分缺失只提交子集 / 含 retired 不回填 / 面板不可得 fail loud）
- [ ] `register_builtins` 增 `factors` 参数（默认 None = 全量；既有调用方零改动）
- [ ] `run_factor_builtin_register` 任务入口（存在性检查 → 缺失子集 → 面板 → 五道闸 → 摘要 `{registered, rejected, skipped, reasons}`）
- [ ] ops 注册表第 13 个 job（`factor_builtin_register`，15:50 mon-fri）+ scheduler 注册与重试包装；既有计数断言 12→13 同步
- [ ] `uv run pytest tests/factors tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py -q` 全绿；ruff / mypy 干净
- [ ] `openspec validate wire-builtin-factor-registration --strict` 通过
- [ ] 部署（重启后端）+ 生产补跑：五条 builtin 真实过闸读数落验证报告
- [ ] 下游联动实跑：因子衰减监控补跑（写 IC）→ 选股补跑（L2 打分是否恢复）
- [ ] 验证报告落 `tests/validation/` + sync + archive
