# Proposal: add-factor-dsl

## Why

- LLM 因子提案若以自由 Python 表达，无法安全执行、无法结构化查重、无法控制复杂度——必须有一个可解析的表达式 DSL 作为提案与验证之间的契约。
- 无复杂度约束的表达式空间天然鼓励过拟合（嵌套越深越容易在历史数据上"挖"出假信号），复杂度上限必须在解析期就拒收，而不是等回测后才发现。
- 数值相关性查重抓不住"换皮因子"（窗口 10 改成 11），需要 AST 结构级查重能力作为因子库去重的底层。

## What Changes

- 新增 `factors/dsl.py`（纯函数）：因子表达式 DSL 的词法/语法解析器，产出 AST；算子集初版含 `ts_mean/ts_std/ts_corr/ts_rank/delta/rank/zscore/add/sub/mul/div/max/min/abs/log` 及行情字段（open/high/low/close/volume/amount，均前复权口径）。
- 复杂度硬约束（解析期拒收，错误消息含违规细节）：AST 最大深度 4、自由参数（窗口期等）最多 3 个、表达式引用的原始字段最多 3 种。
- AST 工具函数：规范化（结合律排序等）+ 子树同构检测 `is_subtree_isomorphic(ast_a, ast_b)`，供因子库结构查重调用。

非目标（Out of scope）：

- 不做因子计算引擎（表达式求值在 `add-factor-library-and-monitor` 的快照数据上实现）。
- 不做 LLM 提案逻辑（属 `add-factor-proposal-agent`）。
- 不支持基本面字段横截面算子（初版仅限价量；基本面因子留待后续 delta）。

## Capabilities

### New Capabilities
- `factor-dsl`: 因子表达式的可解析契约、复杂度硬约束与结构查重原语。

## Impact

- **后端**：`src/finance_agent/factors/dsl.py`（新增纯函数模块）。
- **测试**：每个算子的解析单测；复杂度三条约束各自的拒收单测；子树同构正反例（含"窗口 10 vs 11 结构同构"用例）。
- **验证**：用 Alpha101 中 10 个公开公式做解析 round-trip（解析→序列化→再解析，AST 相等）人工核对，报告落 `tests/validation/`。
