## Why

比较型差值数字（「MA5 较 MA20 低约 X%」）目前处于三不管，且它本该依赖的「派生值预生成」通路在生产里是断的。三件事，一条链：

1. **规范缝隙：比较型差值 UNVERIFIABLE 且不计缺口。** `_verify_comparative`（`citation.py:578`）把 `stated_value` 当方向枚举（greater_than / less_than / equal_to），非枚举值直接返回 UNVERIFIABLE、无 bucket、且在 D3 基期校验之前就返回。差值走的是 comparative 分支不是 computational 分支——既不进「计算型重算注册表全覆盖」的缺口计数，又不在三枚举内。`docs/evals/metrics.md` §3「校验器 follow-up ①」登记为"仍开放"，无 delta。问题规模本身**不可观测**。
2. **派生值预生成在生产失效（两处独立断点，incident 027 同构）。** `compute.py:61` 写 `result["derived_series"]`，但 `AnalysisState` 未声明该键——LangGraph 图合并**静默丢弃未声明键**（incident 027 根因原话），`analysts.py:381` 的 `state.get("derived_series")` 恒为 None，「常用派生值（工具预生成，直接引用）」context 块从未注入，技术分析师一直在心算 5/20/60 日涨跌幅。即使注入成功，context 文案教 LLM 用前缀 `derived.`，真实键是 `derived_series`，`_resolve_field_ref` 对根键是裸 `dict.get` 且无别名——任何 `derived.*` claim 必判 `path_unresolvable`。toolize 验证报告在节点函数层用 dict state 测了"注入"，没走编译图，也没让一条 `derived.*` claim 过 `verify_claims`；incident 027 修复加的 `TestStateChannelsDeclared` 只锁了 validate/citation 家族的键。
3. **均线间差幅没有预生成项。** 现有派生值只有区间涨跌幅与距高低点回撤/反弹；「MA5 vs MA20」这类技术面最高频的比较陈述，LLM 没有现成值可引用，只能心算——而项目的贯穿原则是"能算的都不让模型算"。

路线选择（2026-09-15 分析）：先让问题可测（补缝计数），再把已有的预防机制接回去（修 derived 通路），再扩展预生成清单（均线差幅）；**通用的「申报公式让校验器重算」（路线 2）不在本 delta**——它把新判据押回 LLM 申报、引入一层校验器语义债，且需求规模未定量，待本 delta 的计数数据决定是否立项。

## What Changes

- **A 类修复（规范意图不变，先红后绿）**：`AnalysisState` 声明 `derived_series: dict`；技术面 context 文案的可引用前缀改为真实键 `derived_series.`；补进 `TestStateChannelsDeclared`；新增端到端用例——编译图跑通后技术分析师 context 含派生值、`derived_series.chg_5d` 的 numerical claim 经 `verify_claims` PASS。
- **派生值清单扩展**：`calc_derived_series` 新增均线间差幅 `ma_spread_5_20_pct`、`ma_spread_20_60_pct`、`close_vs_ma20_pct`、`close_vs_ma60_pct`（百分比；正 = 前者高于后者；数据不足为 None 如实标缺）。`derived_series` 注册进计算型重算注册表（`_RECOMPUTE_REGISTRY`，同一份 `calc_derived_series` 重算）与有符号量根键集合 `_SIGNED_ROOTS`（direction 校验适用），并配独立重算 fixture 测试。
- **比较型差值显式降级**：comparative claim 的 `stated_value` 非三枚举时，SHALL 判 UNVERIFIABLE、bucket `comparative_delta_unregistered`、计入覆盖缺口，并在 UNVERIFIABLE 拆报中单列（与文本 / 未注册两类并列为第三类）。**不新增验证语义**——不重算差值、不判 PASS/FAIL；三枚举路径行为不变。
- **口径登记**：`docs/evals/metrics.md` §1.3 拆报表新增 `citation_unverifiable_comparative_delta` 跟踪行；§3 follow-up ① 改为"计数已落地，路线 2 触发条件 = 该计数占 UNVERIFIABLE 比例 ≥ 阈值（首轮实测后定）"。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `price-level-tooling`：「分析师派生值预生成」——清单加均线差幅、存放于声明的 state channel、context 标注真实可引用前缀、根键注册进重算注册表与有符号量集合、端到端可引用验收。
- `citation-verification`：新增「比较型差值申报的显式降级与计数」——非枚举 stated_value 的 UNVERIFIABLE 计缺口 + 独立桶 + 拆报单列。

## Impact

- **业务链路**：技术分析师 context 多一个派生值块（此前从未真正注入）+ 4 个新字段——这是**行为恢复 + 小幅扩展**，分析师 prompt 文件不改（该 context 块由 `analysts.py` 代码生成），**无需 `deploy_prompts.py`**；报告与前端不消费 `derived_series`，非交互类变更。
- **校验器**：新增一个 UNVERIFIABLE 子桶与一个重算根键；三枚举 comparative 行为、数值容差、阻断层语义不变。`citation_unverifiable_ratio` 可能上升（此前不计的差值开始计数）——这是可见性回归到真实，不是退化；时间线标切点。
- **消融 / 实验报告**：拆报多一列；派生值 context 注入后技术分析师引用行为可能变化（更多 `derived_series.*` claim、更少心算数字）——属预期方向，收口时按桶核对。
- **回归**：`tests/metrics/test_technical.py`（新字段 + 数据不足 None）、`tests/test_citation*.py`（comparative 非枚举桶与缺口计数、`derived_series` 重算 fixture、signed 根键）、`tests/nodes/test_validate_trade_prices.py::TestStateChannelsDeclared`、新增编译图端到端用例。
