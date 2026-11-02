# Tasks: add-rm-assumption-table

- [ ] 基线标注：对当前 main 的近期产出报告标注假设综合质量基线（judge 若新增「假设显式性」
      维度，先改 `docs/evals/metrics.md` §1 口径再动 rubric——标注表过三道关）
- [ ] prompt 契约测试：`research_manager.md` 含固定 4 行假设表契约（维度/证据论点编号引用/
      拐点信号与状态耦合/论据不足降级纪律）——沿用 Prompt Enum Consistency 测试模式
- [ ] `research_manager.md` 按契约改写（输出格式加 `assumption_table` JSON 示例与纪律条款）
- [ ] `nodes/debate.py` 论点编号渲染抽共用函数（行为不变重构，辩论层既有测试全绿）
- [ ] RM 上下文接线编号渲染（含 key_arguments 缺失退化路径单测）
- [ ] `state.py` 新增 `research_manager_assumptions: list[dict] | None`
- [ ] RM 节点表解析与降级：合法性校验（4 行/H1-H4/枚举/非空）、非法整表丢弃+告警、
      合法落 state + conclusion 文本拼装；四条路径单测（合法/缺行/枚举非法/整体解析失败）
- [ ] 报告「多空辩论结论」节假设表渲染 + 结构化字段缺失退化（单测；新元素带 data-testid）
- [ ] prompt 部署：`uv run python scripts/deploy_prompts.py` 完成（否则 eval 门禁拒跑）
- [ ] E2E：报告页假设表渲染与缺失降级两场景（交互类门禁，禁止 mock 被测系统）
- [ ] 人工验证报告落 `tests/validation/`（含新旧报告渲染对照截图）
- [ ] 评估收口：落地后单变量 cohort 评估一轮（守门：RM 解析失败率不升、citation_pass 不降、
      pass@k 决策稳定性不动摇），`metrics.md` 时间线加行 + `runs.jsonl` 追加
