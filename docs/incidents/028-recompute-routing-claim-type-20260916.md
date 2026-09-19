# 028: 重算路由由 claim_type 自声明决定——LLM 把自算值标成 numerical 即可跳过重算（2026-09-16）

## 症状

P1 注入 pilot 冻结重放批次（600519 / 000001 × value_error，8 单元）中，**5 个有效对全部两态逃逸**——
污染值到了产物（派生指标字典被改），A3 一次都没拦住（开态关态判定皆 escaped）。逐条核查 5 个载体 claim：
`claim_type` **全部为 `numerical`**，判定全 PASS，`ground_truth == 申报值`。

## 根因

`citation.verify_claims` 按 `claim_type` 分派校验路径：

- `numerical` → `_verify_numerical`：**直读 state**，真值取自 `field_ref` 指向的派生 dict；
- `computational` → `_verify_computational`：**从原始数据重算**（`_COMPUTATIONAL_RECALC` 注册表）。

派生 dict（`cashflow_metrics` / `profitability_metrics` / `derived_series` …）既是被注入污染的对象，
也是 LLM 自算值可能的落点。标成 `numerical` 后真值取自该 dict → **真值 == 申报值 → 恒 PASS**，
重算路径永不触发。

**设计层缺口：校验深度由被校验对象的自我声明决定**——LLM 只要把自算值标成 `numerical` 即可
跳过重算，软约束守不住硬防线。

## 影响面

- **消融侧**：10 标的材料里挂在重算注册表根、标签非 `computational` 的 claim 共 **324 条**
  （technical_indicators 116 / profitability_metrics 53 / derived_series 49 / solvency_metrics 34 /
  cashflow_metrics 29 …）——修复前这批全部走直读。
- **生产侧真实暴露面**：**LLM 自算值标成 numerical → 跳过重算 → 自算错误逃逸**。
  **威胁模型边界（须如实写清）**：value_error 的 context 注入在生产对应「state 本身被污染」，
  state 由 `compute_metrics` 代码计算——那条威胁线的正经防线在数据层（三表勾稽、多源回退），
  citation 链路拦不住它属**分工使然**；本缺口不是「citation 链路形同虚设」——它对自己承诺的
  威胁面（LLM 引错 state 里的数）是有效的。严重度按生产暴露面（自算逃逸）定级。

## 判定

**真错误（设计层缺口）**——非误报、非契约病。由注入法消融设施发现（冻结重放：污染 context 下
真跑一趟分析师 → 冻结 claim → 机制两态重放），是该项目第一个由消融设施发现的机制存在性缺陷。

## 修复（已实施并归档：`harden-recompute-routing`）

1. **路由无视标签**：`field_ref` 根键命中重算注册表 → 一律走重算（`_recomputable_root`）；
2. **比对统一**：抽出 `_compare_numeric_claim`（方向对齐 + percent/万/亿候选归一 + 相对容差）供
   数值型/计算型共用——此前计算型自带简化比对（只有 ×100），同 claim 换标签即换容差语义；
3. **显式降级**：重算输入不可得（state 缺原始报表）时退回直读并**保留 `coverage_gap` 标记**
   （降级可见，不静默）。

**验证**：冻结重放前后对比（零 LLM，同一份冻结 claim）**0/5 → 2/5 拦下**（`value_mismatch` 桶）；
干净材料误报基线（596 claim）**零新增值级误报**（注册表根上 7 条 FAIL 全为既有语义检查桶）；
规范经 delta MODIFIED 并入 `citation-verification` 主规范库。
