# Tasks: feed-dsl-contract-into-proposal-prompt

- [x] 前置（硬门槛）：#482 判效读数归档（10-31 前后 untranslatable 趋势 + 入库数），确认进入第二变量；若契约进上下文已达目标区间则本 delta 显式降级/关闭
      （A2 读数 2026-10-31 归档：判据未满足 → 未触发降级条件，确认进入第二变量
      ——owner 授权 agent 决策；本补丁经取证 10-29 00:01 已应用，双路径已齐活）
- [x] 模板契约段渲染函数（同源读 `explored_factors.json` contract 块、白名单摘要策略、段长上界、fail-safe）TDD：先红后绿
- [x] distro 补丁脚本（prompt 装配处注入 + 落点探针 + 幂等）+ 补丁单测
- [x] 模板契约段与记忆桥契约段一致性测试（同输入断言数值一致）
- [x] fail-safe 回归（源缺失/损坏/字段异常 → 空串不阻断，不虚构）
- [x] **独立审查修复轮**（两 subagent 交叉审查）：I1 算子全量列出（≤32 词表，防样例误导主力算子）、I2 只剩抬头的契约视为无契约 + 复杂度损坏值过滤、I3 补丁单测迁入 `tests/factors/`（默认收集与 CI 门禁内）、I4 真实 distro 快照夹具（`tests/fixtures/distro_factor_proposal_20261028.txt`，OLD 锚点漂移守卫）、I5 同级模块依赖探针、M1 探针先于备份、M2 编译失败回滚、M3 MARKER 精确化、M4 一致性断言强化、M5 行级截断（不按字符切片丢白名单行）
- [x] 部署到 distro site-packages + 探针验证（首轮 prompt 实际含契约段）（**部署门：tasks 第 1 项判效读数归档后**）
      （【取证回填】补丁实为 2026-10-29 00:01 已应用——10-28 晚宕机重建会话所为未回填；
      2026-10-31 四重取证（mtime/备份/幂等复跑/py_compile）+ 渲染探针 846 字符非空，
      详见 tests/validation/2026-10-31-feed-dsl-contract-into-proposal-prompt-deployment.md；
      首个真实会话 prompt 落日志复核随任务 10 批次读数一并核）
- [ ] 实施后 3-5 个收割批次 untranslatable/入库读数 vs 前置基线对照，写入 `docs/evals/metrics.md` 时间线
- [ ] 人工验证报告落 `tests/validation/` → sync + archive
