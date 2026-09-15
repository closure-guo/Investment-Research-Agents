## 1. A 类修复：接回 derived 通路（规范意图不变，先红后绿，不可跳过）

- [ ] 1.1 红灯①：`tests/nodes/test_validate_trade_prices.py::TestStateChannelsDeclared` 断言集合加入 `derived_series`——当前必红（键未声明，证明图合并会丢弃）
- [ ] 1.2 红灯②：新增编译图端到端用例（TESTING stub，零真 LLM）——`build_5layer_graph()` 跑通后技术分析师 context 含「常用派生值」块且前缀为 `derived_series.`；`derived_series.chg_5d` numerical claim 经 `verify_claims` PASS——当前必红（context 块从未注入 / 前缀不可解析）
- [ ] 1.3 修复：`state.py` 声明 `derived_series: dict`；`analysts.py:385` 前缀文案改 `derived_series.`；两条红灯转绿
- [ ] 1.4 既有 `tests/nodes/test_toolize_quick.py`、`tests/metrics/test_levels.py` 不回归

## 2. 均线差幅预生成（TDD 先红后绿）

- [ ] 2.1 红灯：`tests/metrics/test_technical.py`——四个差幅字段数值（相对容差 0.5%）/ 均线值与 `calc_technical` 的 `MA.<w>.-1` 一致 / K 线 30 期时 60 日相关两项 None、20 日相关两项正常 / kline 缺失全 None
- [ ] 2.2 `calc_derived_series` 实现四字段（复用 `calc_technical` 均线口径，不另算均线）；context `derived_view` 自动覆盖新字段（None → 「数据不足」）
- [ ] 2.3 红灯 + 实现：`derived_series` 注册进 `_RECOMPUTE_REGISTRY`（`calc_derived_series(s["kline"])`）配独立重算 fixture（≥60 期 K 线）；加入 `_SIGNED_ROOTS`，`direction` 符号比对用例（negative 一致 PASS / positive 相反 FAIL `direction_mismatch`）

## 3. 比较型差值显式降级与计数（TDD 先红后绿）

- [ ] 3.1 红灯：非枚举 `stated_value` → UNVERIFIABLE + bucket `comparative_delta_unregistered` + 覆盖缺口 +1、不进阻断分母 / 三枚举 PASS·FAIL 行为与 D3 基期校验用例全部不变 / 不重算差值（stated 与真值差再离谱也不判 FAIL）
- [ ] 3.2 `_verify_comparative` 非枚举分支带 bucket；缺口计数通道纳入该桶；拆报输出 `citation_unverifiable_comparative_delta`（`evals/run.py` 报告 + trace 元数据），与 text / unregistered 并列
- [ ] 3.3 `tests/test_citation*.py`、`tests/nodes/test_citation_node.py` 全绿

## 4. 口径登记

- [ ] 4.1 `docs/evals/metrics.md` §1.3 拆报表新增 `citation_unverifiable_comparative_delta` 跟踪行（口径 / 代码位置）；§1.2 备注 `derived_series` 通路恢复切点（技术分析师 claim 分布预期变化）
- [ ] 4.2 §3「校验器 follow-up ①」改为：计数已落地；路线 2 触发条件 = 首轮实验该计数占 UNVERIFIABLE 比例 + 逐条归因（可枚举组合 vs 长尾），阈值首轮实测后定

## 5. 真实链路验证（人工环节——toolize 验证只测了函数层，本次必须过编译图）

- [ ] 5.1 本机跑一次 deep（真 LLM），Langfuse trace 核对：技术分析师输入 context 实际含派生值块（含四个差幅字段或「数据不足」）；技术面 claim 中出现 `derived_series.*` 引用并 PASS
- [ ] 5.2 抽验该 trace 全部 comparative claim：非枚举 stated_value 条数、是否全部落 `comparative_delta_unregistered`、逐条记录是均线/涨跌幅类还是长尾——作为路线 2 决策的首批数据
- [ ] 5.3 报告落 `tests/validation/YYYY-MM-DD-ground-comparative-delta-claims-validation.md`

## 6. 收口

- [ ] 6.1 `openspec validate ground-comparative-delta-claims --strict`；全量 `uv run pytest` 绿 + ruff/mypy（改动文件）
- [ ] 6.2 归档前置：本文件全勾 + 5.3 验证报告落盘 + spec sync；将本 delta 的 derived 断点作为「代码写了 ≠ 机制在跑」第三例追记到 incident 027 的关联段（不新建 incident——同根因）
