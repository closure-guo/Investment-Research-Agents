# 因子 DSL 深度上限 4 → 5（raise-factor-dsl-depth-cap）

## Why

ref 扩展后的复跑读数（2026-10-17，32 条候选）把瓶颈挪到了**复杂度深度上限**：9/32 条候选倒在
「深度 5 > 上限 4」——全部是「滚动统计量套一层价量比值/差值」的自然因子形态
（如 5 日均隔夜跳空 `ts_mean(div(open,ref(close,1)),5)`、20 日收益率波动
`ts_std(sub(div(close,ref(close,1)),1),20)`）。`ref` 使「价量比值/差值」普遍达到深度 3，
再套一层滚动统计量即触顶——上限 4 已成为外部翻译覆盖率的最大单点约束（证据 9/32）。

## What Changes

- **MODIFIED「复杂度硬约束」（factor-dsl）**：AST 深度上限 4 → 5（自由参数 ≤3 与字段种类 ≤3
  两条不变）；深度 6 起仍拒收。
- **实现面（非规范变更）**：`dsl.py` 常量与错误消息示例；`factor_proposal.md` prompt 的
  复杂度说明同步（「AST 深度 ≤ 4」→「≤ 5」）并按 prompt-deploy-consistency 纪律发布到 Langfuse。
- **测试**：深度边界用例翻转为「恰 5 通过 / 6 拒收」；翻译器复杂度拒收用例加深一层。

## Impact

- 代码：`factors/dsl.py`（MAX_AST_DEPTH + 文档串）；`prompts/factor_proposal.md`（部署产物同步）；
  翻译器/提案/闸门路径零改动（都经 `dsl.parse` 单一入口）。
- 测试：`tests/factors/test_dsl.py`（深度相关断言）、`tests/scripts/spike/test_qlib_translate.py`
  （复杂度拒收用例）。
- **不做（明确边界）**：深度 6 及以上不开（一次一个变量；若复跑显示仍有大批深度 6 形态再单独评估）；
  自由参数/字段种类两条上限不动；闸②门槛不动（另一条独立决策线）。
- 复跑证据：翻译批（A 10 条 + B 22 条）第三轮读数 → 验证报告。
