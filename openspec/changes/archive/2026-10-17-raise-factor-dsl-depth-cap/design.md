# Design: raise-factor-dsl-depth-cap

## D1 证据：深度 4 是当前翻译覆盖率的最大单点约束

- 第三轮复跑（10-17）：32 条候选 19 可翻译；不可翻译的 13 条中 **9 条为「深度 5 > 上限 4」**
  （余：Sum×2、Sqrt×1、滚动 Max×1）。全部 9 条都是自然形态——`ref` 使比值/差值类子树
  普遍达深度 3，套一层滚动统计量即到 5：
  - `ts_mean(div(open,ref(close,1)),5)`（5 日均隔夜跳空，深度 5）
  - `ts_std(sub(div(close,ref(close,1)),1),20)`（20 日收益波动，深度 5）
  - `ts_mean(sub(div(close,ref(close,5)),1),N)`（反转族平滑，深度 5）
- 上限 4 的设计语境是「LLM 提案防过度嵌套的初版护栏」（add-factor-dsl D7 的物理栈保护另在）；
  引入 `ref` 后其语义代价被放大（一个叶子算子撑起 2 层深度）。

## D2 只动深度一条：5 而非更大，其余两条上限不动

- 一次一个变量：本轮只把 4 → 5（恰覆盖证据中的 9/32 形态）；深度 6 形态（如
  `Sqrt(Mean(Log(H/L)²,10))` 类）留待复跑后单独评估。
- 自由参数 ≤3 / 字段种类 ≤3 两条上限不动（无证据支持放宽；字段种类上限与「跨字段堆砌」的
  过拟合面直接相关，维持紧约束）。
- 物理栈保护不变：`parse` 对深嵌套仍把 `RecursionError` 转译为 `FactorComplexityError`
  （消息随新上限，行为不变）。

## D3 prompt 同步是同一变量，不是第二个变量

- `factor_proposal.md` 明写「复杂度硬约束：AST 深度 ≤ 4」——上限改了而 prompt 不改 = prompt
  陈述错误数字（比缺一个算子更糟：LLM 会自我限制）。故本 delta 同步 prompt 并按
  prompt-deploy-consistency 纪律 `deploy_prompts.py` 发布（eval 门禁 `_verify_prompt_sync`
  以 Langfuse production 为准）。
- 边界：prompt 的算子清单仍不加 `ref`（那是独立跟进项）——本 delta 的 prompt 变更**只有数字**。

## Risks / Trade-offs

- 深度 5 引入略宽的表达式空间（多一层嵌套）——质量把关仍由五道闸承担（闸①覆盖/闸②ICIR/
  闸③多重检验/闸④冗余/闸⑤样本外）；复杂度护栏的职责是「防组合爆炸」，不是「判质量」。
- 已入库因子不受影响（上限只作用于新解析；存量表达式深度 ≤4 < 5）。
