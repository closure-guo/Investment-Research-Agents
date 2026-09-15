## 1. A 类修复：接回 derived 通路（规范意图不变，先红后绿，不可跳过）

- [x] 1.1 红灯①：`tests/nodes/test_validate_trade_prices.py::TestStateChannelsDeclared` 断言集合加入 `derived_series`——当前必红（键未声明，证明图合并会丢弃）
- [x] 1.2 红灯②：新增编译图端到端用例（TESTING stub，零真 LLM）——`build_5layer_graph()` 跑通后技术分析师 context 含「常用派生值」块且前缀为 `derived_series.`；`derived_series.chg_5d` numerical claim 经 `verify_claims` PASS——当前必红（context 块从未注入 / 前缀不可解析）
- [x] 1.3 修复：`state.py` 声明 `derived_series: dict`；`analysts.py:385` 前缀文案改 `derived_series.`；两条红灯转绿
- [x] 1.4 既有 `tests/nodes/test_toolize_quick.py`、`tests/metrics/test_levels.py` 不回归
- [x] 1.5 （执行期审查追加）`price_levels` 同族未声明通道补声明（`state.py` + 通道契约断言）——代码质量审查实证：`compute.py:60` 写入被图合并丢弃，trader 价位参考带上下文从未渲染、validate 带校验/参考带修正为死代码；A 类修复（spec `price-level-tooling` 本就要求）

## 2. 均线差幅预生成（TDD 先红后绿）

- [x] 2.1 红灯：`tests/metrics/test_technical.py`——四个差幅字段数值（相对容差 0.5%）/ 均线值与 `calc_technical` 的 `MA.<w>.-1` 一致 / K 线 30 期时 60 日相关两项 None、20 日相关两项正常 / kline 缺失全 None
- [x] 2.2 `calc_derived_series` 实现四字段（复用 `calc_technical` 均线口径，不另算均线）；context `derived_view` 自动覆盖新字段（None → 「数据不足」）
- [x] 2.3 红灯 + 实现：`derived_series` 注册进 `_RECOMPUTE_REGISTRY`（`calc_derived_series(s["kline"])`）配独立重算 fixture（≥60 期 K 线）；加入 `_SIGNED_ROOTS`，`direction` 符号比对用例（negative 一致 PASS / positive 相反 FAIL `direction_mismatch`）

## 3. 比较型差值显式降级与计数（TDD 先红后绿）

- [x] 3.1 红灯：非枚举 `stated_value` → UNVERIFIABLE + bucket `comparative_delta_unregistered` + 覆盖缺口 +1、不进阻断分母 / 三枚举 PASS·FAIL 行为与 D3 基期校验用例全部不变 / 不重算差值（stated 与真值差再离谱也不判 FAIL）
- [x] 3.2 `_verify_comparative` 非枚举分支带 bucket；缺口计数通道纳入该桶；拆报输出 `citation_unverifiable_comparative_delta`（`evals/run.py` 报告 + trace 元数据），与 text / unregistered 并列
- [x] 3.3 `tests/test_citation*.py`、`tests/nodes/test_citation_node.py` 全绿

## 4. 口径登记（口径登记实际落在 metrics.md §1.3 拆报行 + §3「同族登记」段，2026-09-15）

- [x] 4.1 `docs/evals/metrics.md` §1.3 拆报表新增 `citation_unverifiable_comparative_delta` 跟踪行；`derived_series` 通路恢复记录落 §3「同族登记」段（含 `price_levels` 同族与两条待决策）——提交走 git 对象层仅提交本方 4 行，并发工作流的未提交改动原样保留
- [x] 4.2 §3「校验器 follow-up ①」改为：计数已落地；路线 2 触发条件 = 首轮实验该计数占 UNVERIFIABLE 比例 + 逐条归因（可枚举组合 vs 长尾），阈值首轮实测后定

## 5. 真实链路验证（人工环节——toolize 验证只测了函数层，本次必须过编译图）

- [x] 5.1 本机跑一次 deep（真 LLM）：✅ 2026-09-15 600519 运行成功；技术面 context 含派生值块由**真实数据通路**核验（fetch+compute+context 实测：9 字段含四差幅 -1.23/1.39/-1.82/-0.45；报告引用「MA20 较 MA60 高 1.39%」与预生成值逐字一致）；**Langfuse trace 核对因 Docker 停机拆为 5.4**
- [x] 5.2 非枚举 comparative 差值条数已取得：**4 条**（占该 run UNVERIFIABLE 9 条中的 44%，实时落 `citation_unverifiable_comparative_delta`）；逐条组成归因待 5.4（trace 缺失）
- [x] 5.3 报告落 `tests/validation/2026-09-15-ground-comparative-delta-claims-validation.md`（§5 全量套件结果待追加）
- [x] 5.4（2026-09-16 补，Langfuse 在线）trace 核对完成：① 技术分析师 trace 输入含派生值块（18090 字符，`derived_series.` 前缀）；② `citation_unverifiable_ratio` score metadata 携三类计数 `{text:5, unregistered:0, comparative_delta:6}`；③ 逐条组成 n=6 **全为跨期水平比较、无 MA/涨跌幅类** → 路线 2 决策修正（优先申报纪律而非公式重算）——详见验证报告 §6

## 6. 收口

- [x] 6.1 `openspec validate ground-comparative-delta-claims --strict` ✅；全量 `uv run pytest`：**2388 passed / 2 skipped / 3 failed（3 个全为 `pytest.mark.live` nightly 层，失败系清空 `LANGFUSE_HOST` 以规避 Langfuse 停机挂起所致，非 live 层 0 失败）**；ruff/mypy 各任务全绿——详见验证报告 §5（含挂起根因：data_snapshot 测试经 `_collect_prompt_versions` 拉 Langfuse prompt 挂起）
- [x] 6.2 归档前置（2026-09-16 全部满足）：本文件全勾（5.4 trace 已补）；验证报告落盘（含 §6 trace 补录）；spec sync 完成（citation-verification +1 需求 / price-level-tooling 1 需求 MODIFIED，validate 56/56）；incident 027 追记已完成（6d593fe）
