# Tasks: feed-dsl-contract-into-proposal-prompt

- [ ] 前置（硬门槛）：#482 判效读数归档（10-31 前后 untranslatable 趋势 + 入库数），确认进入第二变量；若契约进上下文已达目标区间则本 delta 显式降级/关闭
- [x] 模板契约段渲染函数（同源读 `explored_factors.json` contract 块、白名单摘要策略、段长上界、fail-safe）TDD：先红后绿
- [x] distro 补丁脚本（prompt 装配处注入 + 落点探针 + 幂等）+ 补丁单测
- [x] 模板契约段与记忆桥契约段一致性测试（同输入断言数值一致）
- [x] fail-safe 回归（源缺失/损坏/字段异常 → 空串不阻断，不虚构）
- [ ] 部署到 distro site-packages + 探针验证（首轮 prompt 实际含契约段）（**部署门：tasks 第 1 项判效读数归档后**）
- [ ] 实施后 3-5 个收割批次 untranslatable/入库读数 vs 前置基线对照，写入 `docs/evals/metrics.md` 时间线
- [ ] 人工验证报告落 `tests/validation/` → sync + archive
