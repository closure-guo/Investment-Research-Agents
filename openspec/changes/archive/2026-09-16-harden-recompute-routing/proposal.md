# Proposal: harden-recompute-routing

## Why

P1 注入 pilot 冻结批实证：分析师把「指标字典引用」（如 `cashflow_metrics.FCF.2025`）标为
`claim_type="numerical"` 时，校验器走直读路径——真值从 **state 的派生 dict** 取出，而该 dict
可被污染（context 注入）或本就是 LLM 自算值的落点 → `真值 == 申报值` → 恒 PASS，重算路径
（从原始报表重算）**永不触发**。5/5 载体 claim 复现。

设计洞：**校验深度由被校验对象的自我声明（claim_type）决定**——LLM 只要把自算值标成
`numerical` 即可跳过重算。软约束不得守硬防线。

## What Changes

- `verify_citations` 路由：`field_ref` 命中重算注册表根的 claim **无视 `claim_type`** 一律走重算路径。
- 数值型/计算型统一到**同一比对实现**（方向对齐 + 候选归一 percent/万/亿 + 相对容差）——
  此前计算型自带简化比对（只有 ×100），同 claim 换标签就换容差语义。
- 重算输入不可得（state 缺原始报表）时**显式降级**回直读比对并保留覆盖缺口标记（不静默）。

## Impact

- 受影响规范：`citation-verification`（MODIFIED：计算型声明重算注册表全覆盖）。
- 流量：10 标的材料实测 **324 条** claim 标签非 computational 但挂在注册表根 → 修复后新走重算。
- 误报基线（干净材料 596 claim）：PASS 507 / UNVERIFIABLE 64 / FAIL 25，注册表根上 7 条 FAIL
  全为既有语义检查（term/period）桶 → **零新增值级误报**。
- 验证：冻结 claim 已落盘（`reports/ablation/p1-frozen/frozen/`），修复后**零 LLM** 重放即出
  「修复前后拦截率」对比，并充当本修复的回归测试。
