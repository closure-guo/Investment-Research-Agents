# Proposal: add-anchor-value-grounding

## Why

issue #242 断点 2（光大 601818 报告审计）：中报净利同比 -24.01% 在确定性数据中不存在（最新报告期快照同比「暂缺」、季度链只有单季 -8.06%/-40.35%），唯一来源是新闻舆情标题；研究经理论点却锚定 `fundamental.中报净利润同比`，该锚被 `add-debate-argument-anchors` 的零 LLM 校验判为 resolved——因为 inference 型锚点回声回退命中了舆情标题。锚点「声明形态」（field_ref，指向结构化数据）与「实际命中来源」（非结构化舆情文本）的错位，现有校验零记录。之后该数以「可验证的持续趋势」名义传播 13+ 处。

互补的第二类缺口：`data` 型锚点 field_ref 解析命中，但论点文本声称的数字与命中值无一可对上（可追溯性缺口）——现有校验只判「锚存不存在」，不判「文本数字可否溯源到锚定值」。

## What Changes

`debate_anchors.py` 确定性校验升级（零 LLM、fail-open 语义不变）：

1. **每锚点记录解析途径 `matched_via`**（`field_ref` / `echo`）——检查记录新增平行数组字段。
2. **数值溯源检查**：当论点存在任一经 field_ref 命中的锚点时，从论点 `text` 提取数值 token（排除标识符形态：`MA5`/`R1`/`2024Q1`——ASCII 字母相邻；排除孤立年份形态），若文本含数值 token 且无一与任一命中值匹配（宽容匹配：原值 / ×100 百分数 / 两位舍入 / 绝对值，相对容差 0.5%；方向正确性归 judge，程序只判数字可溯源）→ 论点级 `status="value_mismatch"`（`anchored` 仍 true）。
3. **field 形态回声错位计数**：inference 型锚点为 field 形态（含 `.` 且首段为 state 根键）却仅经回声命中 resolved 时，计入记录 `echo_only_field_refs` 与 stats 新桶 `field_ref_echo_only`——光大 -24.01% 案例的确定性信号。
4. **stats 新桶** `value_mismatch` / `field_ref_echo_only`。

fail-open 不变：SHALL NOT 改变路由、SHALL NOT 阻断、SHALL NOT 触发重跑。检查记录的下游消费（FM 审批上下文告警面）由断点 3 delta `add-fm-grounding-surface` 承接（Produces 接口必须有消费任务）。

## Impact

- Affected specs: `agent-node-contracts`（MODIFIED「辩论论点结构化锚点」）
- Affected code: `src/finance_agent/debate_anchors.py`；`tests/nodes/test_debate_anchors.py` 两处契约钉随 delta 更新（record 键集 / stats 键集）
- 不改：辩论 prompt、路由逻辑、`models.py` 的 `DebateArgument` schema
