# 027: 价位校验回路 state 键未声明——fail 打回与价位修正在真实图从未生效（2026-09-13）

## 症状

round7 校准后的 delta 端到端验证（`tests/scripts/verify_deltas_e2e.py`）发现：两条真实 trace 的终态中 `price_check` 与新键 `derived_metrics` 均为 None/缺失——validate 节点明明返回了这些键。

## 根因

`AnalysisState`（`state.py`，`TypedDict(total=False)`）未声明 `price_check` / `price_check_feedback` / `price_check_attempts` / `price_level_corrected` / `price_level_correction_reason`（及其后补的 `derived_metrics` / `citation_coverage_gap` / `value_mismatch_repaired`）。LangGraph 图合并**静默丢弃未声明键**——这一行为项目此前已在 FM 键处踩过并注释（「未声明则被图合并丢弃」），但 validate 家族与 citation 家族的键漏补。

**影响面（r1–r7 全部实验）**：

1. `after_validate_trade_prices` 路由恒读到空 dict → **fail 打回 trader 的重试从未触发**，首次校验失败的方案原样放行进入风险辩论；
2. 二次失败走参考带修正的路径不可达（`price_check_attempts` 恒被丢为 0 以下语义）→ **「价位修正」机制从未在真实图生效**，报告的「价位修正」行从未出现；
3. 单元测试全绿：各测试直接以 dict 调用节点函数，绕过图合并——与 FM 盲审批（incident 前身，1.14）同一模式：**节点测试覆盖不了图合并契约**。

判定为「真错误」桶（机制性缺陷，非数据问题）：修复方式为补声明，不涉及 prompt/数据。

## 修复

- `state.py` AnalysisState 补声明 8 键（price_check 家族 5 + derived_metrics 1 + citation_coverage_gap / value_mismatch_repaired 2，后者由同次键审计发现）；
- 图通道契约测试锁死（`tests/nodes/test_validate_trade_prices.py::TestStateChannelsDeclared`：断言 `build_5layer_graph().builder.channels` 含全部键）；
- 全量节点测试 277 绿。

## 附带发现

- 审计同时发现 `deterministic-derived-metrics` delta 的 `derived_metrics` 若非本次 E2E 验证也会带病上线（写入即被丢弃）——**端到端验证的必要性实证**；
- citation_node 的 `markdown`/`claims` 顶层键为 helper 函数返回，非节点返回，不涉状态（误报排除记录）。

## 预防

- 图通道契约测试已固化；后续新增 state 键的 delta，tasks 中 MUST 包含「AnalysisState 声明 + 通道契约测试」步骤。

## 补漏（2026-09-14，delta `close-citation-coverage-gaps`）

同型漏网再发现两键：`compute_metrics` 一直产出 `derived_series` / `price_levels`（toolize-price-levels 的价位参考与派生值表），但未声明 → 被图静默丢弃 → 三处消费方恒读 None（validate 的参考带/价格关系/偏离三类 sanity 校验**从未生效**；Trader 价位参考节、分析师派生值表从未渲染）。

- 处置：`AnalysisState` 补声明两键；守卫由「逐键补」升级为**系统性门禁**——`tests/test_graph_5layer.py::TestNodeOutputChannels` 断言 `compute_metrics` 全分支产出键 ⊆ 声明 ⊆ 图 `channels`；真实图验证 `tests/test_pipeline_stub.py::TestDerivedKeysSurviveGraphMerge`。
- 教训强化：**027 的逐键守卫挡不住漏网键**（本次两键即漏网）——「产出键集合可枚举的节点」一律用集合级门禁，不逐键写断言。

## 关联复发（2026-09-15，delta `ground-comparative-delta-claims`）

同根因第三、四例，由该 delta 的编译图端到端测试与代码质量审查发现：

1. **`derived_series`**（`compute.py:61` 写入，toolize-price-levels 家族）：未声明被图合并丢弃 → 技术面 context 的「常用派生值」块在生产从未注入（`analysts.py` 读取恒 None），且 context 文案教 LLM 用前缀 `derived.` 与真实键不一致——即使注入，任何 `derived.*` claim 也必判 `path_unresolvable`。toolize 验证报告只在节点函数层以 dict state 测「注入」，未走编译图（与本文第 3 条同一模式）。
2. **`price_levels`**（`compute.py:60` 写入，同一 toolize delta）：未声明被丢弃 → `trader.py:64-71` 价位参考带上下文从未渲染；`validate.py:113-124` 恒走「price_levels 不可用，跳过校验」分支，价位带校验与参考带修正为死代码。

修复（2026-09-15）：两键补声明 + 进 `TestStateChannelsDeclared` + 新增编译图端到端用例（断言派生值真实到达技术面 context 且 `derived_series.<字段>` claim 可引用 PASS）；`price_levels` 带校验可达性由既有节点级用例 + 编译图证据共同锁定。

**审计教训补录**：027 当次的 AST 审计只覆盖「节点返回字典的顶层键」，`compute` 写入的 `price_levels` / `derived_series` 逃过了审计。通道契约测试的断言集合必须对齐**数据生产者全集**（compute / validate / citation 各家族逐键核对），而不是只对齐已发现的问题家族——本次两例都是「同一次审计的漏网」。

