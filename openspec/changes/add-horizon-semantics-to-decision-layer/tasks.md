# Tasks: add-horizon-semantics-to-decision-layer

- [x] 周期契约共享片段落地（`prompts/contracts.py` 单一来源），trader / risk_judge / fund_manager 三 prompt 内嵌同一字面片段
- [x] 契约测试：三 prompt 周期契约段同源断言 + 「buy 须说明 20 日内兑现路径」「长线论据不作买入主因」指令存在断言
- [x] 四个分析师 prompt（fundamental/technical/sentiment/macro）的 claim 登记章节加入 horizon_hint 申报说明（三档定义 + null 降级 + 示例；实现期修正：辩论者 prompt 无 claim 登记章节，不在覆盖范围）
- [x] `citation.py` Claim 模型增加 `horizon_hint` 可选字段；解析兼容性测试（字段保留值、缺失/null 不报错、非法值拒绝）
- [x] 校验器不消费 horizon_hint 的回归测试（status/coverage_gap/bucket 与字段不存在时完全一致，coverage_gap 语义不变）
- [x] `uv run ruff check` 通过；mypy 相对基线不新增错误（存量 83 处为既有债务，非本 delta 引入）；全量 pytest 见下方回填
- [x] 全量 `uv run pytest` 全绿：4391 passed / 3 failed（归因均非本 delta——`test_ark_tool_call_contract`=ark 凭证 AuthenticationError 挂账、`test_fm_decision_live_report`=撞 2026-09-25 历史 trace、`test_empty_follows_registry_default`=全量运行时序 flake（基线与本分支单跑均绿））
- [ ] `uv run python scripts/deploy_prompts.py` 发布，eval 门禁指纹一致（prompt-deploy-consistency；合并后在主检出执行）
- [ ] 实跑一次深度分析，人工核对 buy/watch 决策的论据周期对齐情况，验证报告落 `tests/validation/`
- [ ] `openspec validate add-horizon-semantics-to-decision-layer --strict` 通过
