## Why

citation 校验器经 incident 026 处置后已能正常使用，但归因收口时留下「认不出」的三处缺口，且都不是靠人肉追能收敛的：

1. **比较型差值引用判不了**：LLM 常写「MA5 较 MA20 低约 3.62」这类**数值差值**申报，而 `_verify_comparative` 只认 `greater_than/less_than/equal_to` 方向枚举 → 一律 UNVERIFIABLE（不计错、不计缺口、也不计覆盖），归因表里长期挂着（metrics.md §3 follow-up ①）。
2. **派生键重算注册不齐**：`compute_metrics` 产出 17 个派生键，`_COMPUTATIONAL_RECALC` 只注册 9 个 → 其余 8 个（`derived_series`/`growth_rates`/`health_score`/`relative_valuation`/`traffic_lights`/`peer_comparison`/`quarterly_trend` + `price_levels`）一旦被按计算型引用即 UNVERIFIABLE；且现状靠「想起来才补」，没有门禁挡住新键。
3. **两个派生键被图静默丢弃（真 bug，与 incident 027 同型）**：`compute_metrics` 产出 `derived_series` 与 `price_levels`，但 `AnalysisState` 未声明 → LangGraph 合并丢弃（本地最小实验确证）→ 三个消费方全部读到 None：分析师 context 的「常用派生值」表、Trader context 的「价位参考」节从未渲染；`validate_trade_prices` 的**参考带/价格关系/偏离三类 sanity 校验从未生效**（永远走 `price_levels 不可用，跳过校验` 分支）。toolize-price-levels 设计的校验回路由此整条哑火。

## What Changes

- **① 比较型差值重算**（`citation-verification`）：`comparative` claim 的 `stated_value` 为数值时按「差值申报」处理——用 `field_ref`/`field_ref_b` 双端真值重算差值，与申报值按容差比对（参考系取两操作数绝对值较大者）；`direction` 已申报则校验符号方向，未申报则跳过方向检查并计覆盖缺口（沿用既有显式降级纪律）；`stated_value_b` 由「必填」改为「可选（申报则校验）」——差值型 claim 的申报对象是差值本身。
- **② 派生键重算注册补齐 + 覆盖门禁**（`citation-verification`）：8 个未注册派生键接入 `_COMPUTATIONAL_RECALC`（复用 `_recompute_snapshot` 的「同一份 compute 代码重算」模式）；新增门禁——`compute_metrics` 的全部产出键 SHALL ⊆ 注册表 ∪ 显式豁免表（豁免带理由），新派生键未注册即测试红。
- **③ 图通道声明补齐 + 节点产出键门禁**（`agent-node-contracts`）：`AnalysisState` 补声明 `derived_series` / `price_levels`（含注释说明三处消费方）；新增门禁——`compute_metrics` 产出键 SHALL ⊆ `AnalysisState` 声明且已建图通道（incident 027 的守卫推广为系统性检查，不再逐键补）。激活后 `validate_trade_prices` 的参考带校验、Trader 价位参考节、分析师派生值表按原设计生效，须以真实运行验证行为合理（含打回/自动修正路径可观测量）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `citation-verification`: 比较型 claim 增加「数值差值申报」重算路径（MODIFIED）；新增「派生键重算注册覆盖门禁」（ADDED）。
- `agent-node-contracts`: 新增「节点产出键 ⊆ AnalysisState 声明与图通道」系统性门禁（ADDED）。

## Impact

- **行为变更（仅 ③ 激活既有设计）**：`price_check` 三类 sanity 校验由「从未生效」变为生效——可能产生一次打回（`price_check_feedback`）或二次失败后的按带修正（`price_level_corrected`），均已有可观测量与既有单测；需真实运行验证（含价位带的合理性）。
- 代码面：`citation.py`（差值重算 + 注册表）、`state.py`（两键声明）、`nodes/validate.py` 注释口径（去掉「price_levels 不可用」的常态假设不需要；保持代码不变）、新增两组门禁测试。
- 文档面：metrics.md §3 两条 follow-up 状态更新；`docs/evals` 台账修复计数若涉及。
