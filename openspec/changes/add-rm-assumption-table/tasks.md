# Tasks: add-rm-assumption-table

- [ ] 基线标注：对当前 main 的近期产出报告标注假设综合质量基线（judge 若新增「假设显式性」
      维度，先改 `docs/evals/metrics.md` §1 口径再动 rubric——标注表过三道关）
- [x] prompt 契约测试：`research_manager.md` 含固定 4 行假设表契约（维度/证据论点编号引用/
      拐点信号与状态耦合/论据不足降级纪律）——沿用 Prompt Enum Consistency 测试模式
      （tests/test_prompt_contracts.py，PR #559）
- [x] `research_manager.md` 按契约改写（输出格式加 `assumption_table` JSON 示例与纪律条款）
      （prompt:14-48 H1-H4 契约在位，11-03 对 HEAD 核验）
- [x] `nodes/debate.py` 论点编号渲染抽共用函数（行为不变重构，辩论层既有测试全绿）
      （debate.py:93 编号行前置于正文 + rebuttal_to；tests/nodes/test_debate_arguments.py）
- [x] RM 上下文接线编号渲染（含 key_arguments 缺失退化路径单测）（nodes/research_manager.py）
- [x] `state.py` 新增 `research_manager_assumptions: list[dict] | None`（state.py:134，11-03 对 HEAD 核验）
- [x] RM 节点表解析与降级：合法性校验（4 行/H1-H4/枚举/非空）、非法整表丢弃+告警、
      合法落 state + conclusion 文本拼装；四条路径单测（合法/缺行/枚举非法/整体解析失败）
      （nodes/research_manager.py；tests/nodes/test_research_manager.py；880 定向测试过，
      CI 绿 lint-and-test 13m33s / stub 18m02s 2026-11-02）
- [x] 报告「多空辩论结论」节假设表渲染 + 结构化字段缺失退化（单测；新元素带 data-testid）
      （report.py:458-471「多空假设表」渲染；测试改断言节标题具区分性；报告为 markdown，
      design.md 的 data-testid 不适用——见 2026-11-01 实施轮记录）
- [x] prompt 部署：`uv run python scripts/deploy_prompts.py` 完成（否则 eval 门禁拒跑）
      （PR #559 合并前已部署 Langfuse；CI 绿含 deploy 一致性门禁）
- [ ] E2E：报告页假设表渲染与缺失降级两场景（交互类门禁，禁止 mock 被测系统）
      （挂账：场景方案须人工审批后再落 spec，见 project-workflow §3 Step 2）
- [ ] 人工验证报告落 `tests/validation/`（含新旧报告渲染对照截图）
- [ ] 评估收口：落地后单变量 cohort 评估一轮（守门：RM 解析失败率不升、citation_pass 不降、
      pass@k 决策稳定性不动摇），`metrics.md` 时间线加行 + `runs.jsonl` 追加
      （挂账：与「采信/推翻申报」消费端 delta 解耦，单变量归因）
