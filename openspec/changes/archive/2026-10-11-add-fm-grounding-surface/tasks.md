# Tasks: add-fm-grounding-surface

## 1. FM 上下文与提示词（TDD 先红）

- [x] 1.1 失败测试：`valuation_snapshot.missing_reasons` 非空 → 上下文含「估值完整性标注」段并列出全部原因；缺失/空 → 无该段
- [x] 1.2 失败测试：`debate_anchor_checks` 含 value_mismatch 记录 → 上下文含「辩论锚点告警」段逐条列示；全零/空 → 无该段
- [x] 1.3 失败测试：`_format_freshness_section` 非 None → 上下文含披露原文；None → 无空段
- [x] 1.4 失败测试：告警逐条列示上限 5 条（6 条违规时第 6 条只计入汇总计数）
- [x] 1.5 失败测试：fund_manager.md 提示词含审批前核查条款（标注/告警显式回应义务 + 披露交叉核对），且无「禁止 approve」表述
- [x] 1.6 实现 `_build_fund_manager_context` 三段 + fund_manager.md「审批前核查」段

## 2. 验证与回归

- [x] 2.1 全量 pytest -m not live 0 失败；ruff/mypy 任务范围零错误
- [x] 2.2 openspec validate --strict 通过（两 spec）
- [x] 2.3 光大场景复核：估值缺失 + value_mismatch 锚点告警 + 披露节三段齐现 FM 上下文、无标注时上下文与现状一致（零回归）→ tests/validation 人工验证报告
- [x] 2.4 合并后 prompt deploy：`uv run python scripts/deploy_prompts.py` 发布 fund_manager.md（条件式条款先发安全，design D4）
