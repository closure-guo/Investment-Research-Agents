## Why

AGENTS.md 红线（incident 026）要求：**自动化处置只允许挂在「经逐条人工终裁确认为真错误」的桶上**。而 `value_mismatch` 桶上的**单点修复**（surgical-citation-repair）是自动发生的——自动重试关闭后它还是当前唯一的自动纠错通路——但规范里没有写明：它为什么可以免于「终裁前置」，边界又在哪里。缺这条授权条款会导致两种坏结果：① 审计时被读成违反红线；② 后续有人以「修复是 SOP 允许的」为由扩大自动处置范围（放宽阈值、去掉真错口径、跳过重校验、允许引入新数字）。

## What Changes

- `citation-verification` 新增要求「单点修复的自动处置授权与边界」：明确它属于真错桶的**受护栏自动处置**，并列出三条「免终裁前置」护栏——① 不隐藏真错（修复数计入 `citation_analyst_true_fail`）；② 重校验仲裁（回填后整体重校验，以重校验为最终状态）；③ 最小改写 + 可审计（只改错数字与联动措辞、禁新增数字、trace 留痕）。
- 边界固化：仅稀疏失败（同一分析师同轮 value_mismatch <3 处）触发；≥3 处 SHALL NOT 单点修复；自动重试关闭态下修复失败直接标记阻断放行、SHALL NOT 触发重跑。
- 纪律条款：任一护栏的移除或放宽（阈值、口径、跳过重校验、允许新数字）SHALL 走 delta，不得作为实现细节调整。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `citation-verification`: 新增「单点修复的自动处置授权与边界」要求（三条护栏 + 稀疏阈值 + 失败放行语义 + 变更纪律 + 场景）。

## Impact

- **无代码变更**：行为已存在（`src/finance_agent/nodes/citation_repair.py`、`citation_node.py` 接线、13 个单测、`tests/validation/surgical-citation-repair.md` 验证报告）；本 delta 为规范授权与边界固化。
- 关联：`docs/incidents/026-citation-gate-misattribution-20260911.md`（红线来源）、`docs/evals/metrics.md` §1.3（`citation_surgical_repaired` 已入拆报）、`openspec/specs/citation-verification/spec.md` 既有「修复回填后强制重校验」「修复遥测与原桶保留」两条（本要求引用而不重复）。
