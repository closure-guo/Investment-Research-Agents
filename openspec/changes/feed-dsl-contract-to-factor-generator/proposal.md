# Proposal: feed-dsl-contract-to-factor-generator

## Why

收割账本（2026-10-26 诊断）显示 1452 条已处理候选仅 12 条入库（0.8%），其中 **68%（991 条）死在转译段、根本没进五道闸**：410 条死于复杂度超限（自由参数>3 为主）、389 条死于 DSL 缺横截面算子、65 条死于 formulation 使用无具体数值的 ε 符号常量。根因是**生成侧（RD-Agent）不知道 DSL 表达契约**——跨会话记忆桥（`explored_factors.json`）只回喂"已探索清单"（去重指引），不回喂"能表达什么"。研究侧持续产出结构性不可译的候选（且比例在升：84%→94%），转译 LLM 成本照付、统计检验价值为零。

## What Changes

- 跨会话记忆桥导出载荷新增**机器可读 `contract` 块**：复杂度上限（值取自 `factors.dsl` 单一真相源）、可表达算子白名单与行情字段白名单（取自 `factors.qlib_translate` 映射表）、表达规则文本（数值常量必须具体、禁横截面运算、禁矩阵/回归/ML 流水线）。
- 导出载荷新增**闸口拒收计数**（gate → 条数，取自已裁决状态）。
- distro 生成侧消费端（`explored_feedback.py` 渲染块）把契约块渲染进首轮生成 prompt（先于已探索清单）；契约缺失时仅渲染已探索清单（fail-safe 不阻断生成）；渲染总量维持字符预算上界。
- 不改五道闸、不改转译等价性契约（`factor_transcription.md` 的"禁止猜测"语义逐字不变——ε 修复走上游补数值，不走下游猜测）。

## Capabilities

- **Modified Capabilities**: `factor-proposal`（ADDED 需求「生成侧 DSL 契约回喂」）

## Impact

- `src/finance_agent/factors/feedback.py`（payload 构造）
- `tests/scripts/spike/explored_feedback.py`（distro 消费端源码，部署件）
- `scripts/export_factor_feedback.py`（CLI 摘要行，最小改动）
- 部署面：distro `rdagent` site-packages 消费端覆盖（下一个研究进程生效）；后端容器重建后日批自动导出带契约块
- 无前端/交互变更（非交互类，不触发 E2E 门禁）
