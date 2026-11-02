# Proposal: add-rm-assumption-table

## Why

Research Manager 是辩论层→决策层的唯一铰链，但它是全链路唯一没有结构化输出契约的 LLM 节点
（prompt 全文 22 行，输出仅 reasoning/rating/confidence 三字段）：Bull/Bear 两轮辩论产出的
key_arguments（带 kind/anchors 锚点纪律）被压缩成一段自由文本后，锚点可追溯性即丢失——
trader 只能引用散文，报告只能渲染分歧卡，辩论层的结构化投入在综合环节归零。
参照 equity-researcher 技能的「投资论点综合分析表」（4 维假设 × 多空证据 × 拐点信号 × 状态判定），
以 RM 现有「只读辩论历史、不读原始 state」的安全性质为前提，把综合环节升级为结构化假设表。

## What Changes

- research_manager 提示词新增假设表输出契约：固定 4 行（H1 竞争格局 / H2 成长驱动 / H3 盈利质量 /
  H4 估值与预期），每行含可核验命题、多空证据（引用辩论论点编号）、可观察拐点信号、状态判定
  （成立/注意/不成立）与时间标签；证据 MUST 转引辩论论点、MUST NOT 引入辩论中不存在的论据。
- RM 节点上下文改为复用辩论层既有的「R{n} 论点: ①…②…」编号渲染（debate.py 1.10 交锋结构化引用），
  为证据引用提供稳定论点身份。
- RM 节点新增假设表解析与降级：表合法时落 state `research_manager_assumptions` 并以人读表格
  追加进 `research_manager_conclusion` 文本（trader/risk_judge/FM 无需改 prompt 即可看到）；
  表缺失或非法时丢弃表、告警日志、保留 rating/confidence 既有语义，MUST NOT 中断管线。
- 「多空辩论结论」报告节在分歧卡之后渲染假设表（结构化字段缺失时退化为现状渲染，禁止编造行）。
- 决策层（trader/risk_judge）prompt 本轮不动：假设表经结论文本自然进入其上下文，
  显式「采信/推翻申报」纪律留给后续 delta（单变量归因）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-prompt-contracts`: 「Research Manager 评级表态」requirement 扩展——在既有评级表态契约上
  增加假设表输出契约（4 行固定维度、证据论点编号引用纪律、拐点信号与状态耦合规则）。
- `agent-node-contracts`: 新增两条 requirement——RM 辩论论点编号渲染（上下文契约）、
  RM 假设表结构化解析与降级（state 键、结论文本拼装、降级语义）。
- `report-decision-rendering`: 新增「多空假设表渲染」requirement——分歧卡之后的表渲染与缺失降级。

## Impact

- `src/finance_agent/prompts/research_manager.md`（契约主体）→ 改后必须执行
  `uv run python scripts/deploy_prompts.py`（prompt-deploy-consistency）
- `src/finance_agent/nodes/research_manager.py`（上下文构建 + 解析 + state 写入 + 结论拼装）
- `src/finance_agent/nodes/debate.py`（论点编号渲染函数抽出共用，仅重构不改行为）
- `src/finance_agent/state.py`（新增 `research_manager_assumptions: list[dict] | None`）
- `src/finance_agent/nodes/report.py`（多空辩论结论节渲染）
- 测试：prompt 契约内容测试（沿用 Prompt Enum Consistency 模式）、RM 节点解析/降级单测、
  报告渲染测试；报告渲染属交互类变更 → E2E 门禁适用
- 评估：改 prompt 前先对当前 main 产出做基线标注，落地后跑一轮 cohort 评估（单变量）；
  judge 若需新维度（假设显式性）先改 `docs/evals/metrics.md` §1 口径再动 rubric
- 不受影响：decision-hysteresis（仍读 `research_manager_rating`）、战绩结算、judge RM 变量、
  trader/risk_judge prompt
