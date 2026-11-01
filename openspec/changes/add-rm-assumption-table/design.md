# Design: add-rm-assumption-table

## Approach

**总体**：不动五层拓扑、不动消费端 prompt。RM 是全链唯一同时读到完整辩论历史的节点，
假设表在 RM 产出，经两条通道下发——结构化 state 键（`research_manager_assumptions`，
报告渲染取用）+ 人读表格拼装进 `research_manager_conclusion` 文本（trader/risk_judge/FM
上下文随之可见，零 prompt 改动）。这样单变量归因干净：本轮唯一的 LLM 行为变化就是 RM。

**上下文侧**：RM 的 `_build_research_context` 现在只渲染 `role: content`，论点锚点信息在
进入 RM 前即丢失。把 debate.py 中 R2 上下文已有的「R{n} 论点: ①…②…」编号渲染
（`nodes/debate.py` 1.10 交锋结构化引用）抽为共用函数，RM 上下文复用——证据引用
「Bull R1 论点②」由此获得稳定对位。这是纯重构 + 一处接线，辩论层行为不变。

**解析侧**：表合法性校验（4 行 / code 覆盖 / 枚举 / 非空）独立于 rating/confidence 解析。
**表是增强不是门禁**：非法即整表丢弃 + 告警日志，绝不因表问题污染既有降级语义
（`parse_degraded` 只由整体 JSON 解析失败触发）。理由：RM 输出复杂化后最可能的新失败模式
就是表格式漂移，把它隔离在「可丢弃增强层」保证管线韧性不倒退。

**state 键用 `list[dict]` 不建 Pydantic 模型**：对齐 RM 现有扁平 state 风格
（rating/confidence 均为扁平键）；表结构仅两处消费（节点校验 + 报告渲染），
不值得引入新模型类。校验逻辑收在节点内私有函数。

**渲染侧**：报告渲染从结构化键取数（与分歧卡同模式），不从 conclusion 文本反解。
历史会话无该键 → 退化为现状渲染。新表格元素用 data-testid 锚定，供 E2E 稳定选择。

## Alternatives Considered

- **表放报告生成时二次 LLM 调用产出**：多一次 LLM 调用、且报告节点拿到的辩论历史不如 RM
  完整新鲜；RM 本身就是「综合」职责节点，表放这里语义正确。不选。
- **单独 state 键 + 同轮改 trader/risk_judge prompt 强制采信申报**：归因爆炸——落地后若
  决策质量变化，无法区分是「表质量」还是「消费纪律」的贡献。本轮经 conclusion 文本自然
  进入消费上下文；显式采信/推翻申报纪律确有效果时另立 delta。不选。
- **证据引用的确定性机器校验**（解析「Bull R1 论点②」并核对辩论 key_arguments 存在性）：
  自由文本引用的解析脆弱（角色别名/序号风格漂移都会误判），v1 用 prompt 纪律 + judge
  评估维度兜底；若评估显示引用失真率高，再上确定性校验（届时引用格式已稳定）。不选。
- **「无拐点信号不得判成立」做代码校验**：这是 LLM 推理纪律不是结构约束（「具体可观察」
  本身无客观判据），代码校验会变成又一个误报源（incident 026 教训）。留在 prompt 契约。不选。

## Risks

- **RM 解析失败率上升**（输出复杂化的直接代价）：表校验独立 + 整表可丢弃隔离风险；
  落地后健康检查盯 RM 解析失败率，超过基线即回看表格式漂移。
- **输出 token 增加**（表约 +500-800 token）：deep mode 瓶颈在输出侧 reasoning 配额，
  关注 truncation 事件；必要时收窄 bull/bear_evidence 为引用号而非全文转写。
- **LLM 填空话表**（命题不可核验/信号空泛）：拐点信号与状态耦合规则（无信号不得判成立）
  + 后续 judge「假设显式性」维度；本轮以 prompt 纪律先行。
- **MODIFIED 主规范漂移**：`agent-prompt-contracts` 的 RM requirement 头已逐字核对；
  archive 前重跑 `openspec validate --strict` 并复核主规范未并行演进。
- **历史会话回放**：旧会话 state 无新键、辩论消息可能无 key_arguments——两条降级路径
  （渲染退化 / 表缺失退化）均有场景钉死。
