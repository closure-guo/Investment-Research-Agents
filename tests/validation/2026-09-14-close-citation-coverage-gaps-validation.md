# 验证报告：close-citation-coverage-gaps（引用与派生键覆盖收口）

**日期**：2026-09-14
**delta**：`openspec/changes/close-citation-coverage-gaps/`
**类型**：后端行为修复（无 UI/SSE 交互面变更）；③ 激活既有设计（sanity 校验回路）

## 1. ③ 图通道静默丢弃（真 bug，027 同型）

**根因复现（最小实验）**：`StateGraph` 声明 `TypedDict` 只保留声明键——节点返回 `{'declared':1,'derived_series':...,'price_levels':...}` 时输出仅 `['declared']`（`derived_series`/`price_levels` 均被丢弃）。

**门禁（TDD 先红）**：`tests/test_graph_5layer.py::TestNodeOutputChannels::test_compute_outputs_all_declared_and_channeled`
- 修复前红：`未声明键会被 LangGraph 静默丢弃（027 教训）：['derived_series', 'price_levels']`
- 补声明后绿；同时校验产出键 ⊆ 图 `channels`

**真实图验证（新增）**：`tests/test_pipeline_stub.py::TestDerivedKeysSurviveGraphMerge`
- 带 40 根 K 线的 state 灌入 `build_5layer_graph().invoke(...)`（TESTING stub）
- 断言 `price_levels.available is True` 且 `derived_series` 非空 —— 修复前必被丢弃

**行为影响（激活既有设计）**：
- `validate_trade_prices` 参考带/价格关系/偏离三类 sanity 校验在**有 K 线数据时开始生效**（此前恒走「price_levels 不可用，跳过校验」）；hold/watch 无价位要求路径不变
- Trader context「价位参考」节、分析师 context「常用派生值」表开始渲染
- 真实数据运行（docker，有 K 线）为最终确认环境；当前 CI/E2E stub 数据不含 K 线（stub fetch 不产出 kline），E2E 行为不变

## 2. ② 派生键重算注册覆盖

**门禁（TDD 先红）**：`tests/test_citation.py::TestComputationalRegistryCoverage::test_recompute_registry_covers_all_compute_outputs`
- 修复前红：8 个派生键未注册（`derived_series/growth_rates/health_score/price_levels/relative_valuation/traffic_lights/peer_comparison/quarterly_trend`）
- 注册（同一份 compute 代码重算）后绿；豁免表 `_UNREGISTERED_EXEMPT` 当前为空且要求每条附理由

**可重算性**：`test_newly_registered_key_recomputes_pass_and_fail` —— `growth_rates` 子路径按容差判 PASS / 超容差判 value_mismatch FAIL

## 3. ① 比较型差值重算

**测试（TDD 先红 6 条 → 全绿）**：`tests/test_citation.py::TestComparativeDifferenceRecompute`
- 差值在容差内 → PASS（ground_truth = 重算差值 -2.86）
- 超容差 → value_mismatch FAIL
- 符号方向不符（direction=positive 而差值为负）→ FAIL
- 方向未申报 → 仅量级比对、PASS 且 `coverage_gap=True`（显式降级，不静默）
- 差值型不要求基期值申报（方向型 D3 裸奔 FAIL 语义不变）
- 差值型申报了基期值仍按既有容差校验（错值 → FAIL）

**兼容短路（回归保护）**：LLM 偶发以「某一端的值」填 `stated_value`（如 PMI claim 申报 49.8 而正文说「回升 0.6 个点」）——此类形态**保持 UNVERIFIABLE**（未知语义不武断判错），由既有测试 `TestComparativeEchoSkipped::test_comparative_numeric_direction_field_not_false_fail` 锁定；差值裁决仅在申报値既不≈a 也不≈b 时进行。

## 真实数据定向验证（本地 cache.db 真实行情 600519，无网络/无 LLM）

脚本：`tmp/verify_close_gaps_realdata.py`（一次性，不入库）；数据：2025-2026 真实日线/财报快照。

**③ 价位参考与派生值（此前被图丢弃 → 恒 None）**

```
price_levels: available=true, entry_ref=1275.16,
  stop_band_long=[1237.47, 1256.32], target_band_long=[1312.85, 1350.53], full_band=[1113.32, 1401.04]
derived_series: chg_5d=-4.12% chg_20d=-4.98% chg_60d=+7.44% drawdown_from_high_250d=-16.06% rebound_from_low_250d=+9.12%
```

**③ validate 三类 sanity 校验真实生效（四组申报）**

| 申报 | 结果 |
|---|---|
| 合理（收盘附近，-5%/+8%） | `pass` + derived_metrics（stop_distance 5.0%、风险回报比 1.60） |
| stop>entry（long 关系违规） | `fail`：价格关系违规（实际 1338.92/1275.16/1377.17） |
| entry 偏离收盘 50% | `fail`：偏差超 15% + stop/target 同时落带外（三类命中） |
| stop/target 落带外 | `fail`：落在参考带 [1113.32, 1401.04] 之外 |

（修复前该节点恒返回「price_levels 不可用，跳过校验」——三类校验一次都不会跑。）

**① 差值重算（真实 MA5/MA20）**

MA5=1295.30、MA20=1298.32、差值 -3.02：

| 申报 | 结果 |
|---|---|
| 差值 3.02 + direction=negative | `PASS`（gt=-3.02） |
| 差值错报 5 倍（15.12） | `FAIL value_mismatch`（delta=12.10） |
| 差值 3.02 + 方向未申报 | `PASS` + `coverage_gap=True`（显式降级） |
| metric_name/period 申报齐全下的隔离验证 | 方向已申报 gap=False / 未申报 gap=True（归属本分支，不由全局口径兜底） |

## 端到端验证（docker/本地真栈 + 真实 LLM + 真实行情缓存）

本地后端（非 TESTING）直打 `/api/analyze`（600519，真实 LLM）：24 节点跑完、报告产出（`report_ready`）。

- **Trader 本次决策为「观望（置信度 55%）」** → 按设计 `validate_trade_prices` 返回「hold/watch 无价位要求」（band 校验对无价位决策不适用）；band 校验的真实数据行为由上方四组定向申报覆盖（合理→pass、关系违规/偏离/带外→fail 及 reason）。
- **Langfuse trace `68814a6f47383b197a37ca638deecc9b`（session 86838494-a7b）实据**：
  - `trader` generation input 命中「**价位参考**」×1 —— 修复前该节因 `price_levels` 被图丢弃而**从未渲染**；
  - `technical_analyst` generation input 命中「**常用派生值**」×1 —— 修复前同样不存在。

## 验证期发现并修复：`derived.` 前缀契约缺口（micro-delta `normalize-derived-root-alias`）

**发现**：analysts context 提示 LLM「field_ref 前缀 `derived.`」，而 state 根键是 `derived_series`——图通道修好前该 context 节从未渲染（死文案），修好后**首次真正渲染**，照提示写就会被误判：

```
derived.chg_5d          → FAIL path_unresolvable   （值正确，前缀简写）
derived_series.chg_5d   → PASS
```

**处置**：`_ROOT_ALIASES = {"derived": "derived_series"}` 统一接入普通解析与计算型注册表查找；context 提示改规范前缀 `derived_series.`；spec「路径形态归一」增补条款（delta `2026-09-14-normalize-derived-root-alias` 已归档 + sync，主规范 53/53）。TDD：`TestDerivedRootAlias`（2 例）+ `TestComputationalRegistryCoverage::test_alias_root_recomputes`（先红后绿）。

**真实运行核对**：本次端到端 run 的 citations 实际写作 `technical_indicators.MA.5.-1`（规范路径），未踩该坑；缺口为**潜在**误报源，随本 PR 一并消除。

## 回归

- 受影响模块：`tests/test_citation.py` / `tests/test_graph_5layer.py` / `tests/test_pipeline_stub.py` / `tests/nodes/test_validate_trade_prices.py` 全绿
- 全量：`pytest tests/ --ignore=tests/e2e --ignore=tests/scripts -m "not live"`（见收口记录）+ ruff + mypy（改动文件）

## 结论

三处缺口全部闭合：图通道（声明 + 系统性门禁）、派生键引用（注册 + 覆盖门禁）、比较型差值（重算 + 符号校验）。**遗留观察项**：真实数据环境下 sanity 校验的实际触发率与打回质量（首次上线后按 metrics 台账口径观察，若产生误打回按 incident 流程归因）。
