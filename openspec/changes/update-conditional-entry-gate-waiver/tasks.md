# Tasks: update-conditional-entry-gate-waiver

- [x] delta 提案通过 `openspec validate --strict`
- [x] trader/risk_judge 提示词新增 buy 入场形态申报契约段（契约测试先行，红→绿）
- [x] risk_judge 提示词否决纪律漏洞修补（二选一覆盖全部 action + 弃权修正归②申报；契约测试先行，红→绿）
- [x] fund_manager 提示词新增方位采纳清单义务段（契约测试先行，红→绿）
- [x] risk_judge 节点 buy 逆势立即入场软标注（`final_entry_form_check`，I-1 模式；TDD：逆势+未申报→标注 / 条件入场或非逆势→pass）
- [x] `final_entry_form_check` 接线：state.py 注解 + fund_manager.FINAL_CHECK_LABELS 注册（进 FM 审批上下文）
- [x] 全量回归：tests/metrics、tests/nodes 相关面 + prompt 契约测试全绿
- [x] `uv run python scripts/deploy_prompts.py` 发布三个 prompt
- [ ] PR 合并 → 部署重启 → 生产验证（cohort/池分析各抽 1 笔 buy 观察 `final_entry_form_check` 与申报措辞）
