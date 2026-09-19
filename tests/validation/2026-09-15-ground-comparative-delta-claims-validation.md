# 验证报告：ground-comparative-delta-claims（比较型差值可观测 + derived 通路修复 + 均线差幅）

日期：2026-09-15
delta：`openspec/changes/ground-comparative-delta-claims/`
执行：本机（Windows / uv）+ 火山方舟端点；分支 `ground-comparative-delta-claims`（b61c42a 起）
运行环境注记：Docker Desktop 停机 → Langfuse 不可达（trace 证据见 §4 遗留）

## 0. 变更范围

| # | 变更 | 层 | commit |
|---|---|---|---|
| 1 | `derived_series` 声明为 state 通道 + context 前缀对齐真实键（incident 027 同构断点，A 类） | 输入通路 | `5818d9c` + `14e91e3` |
| 2 | `price_levels` 同族补声明（审查发现：trader 价带上下文从未渲染 / validate 带校验死代码） | 输入通路 | `14e91e3` |
| 3 | 均线差幅四字段（`ma_spread_5_20_pct` / `ma_spread_20_60_pct` / `close_vs_ma20_pct` / `close_vs_ma60_pct`）+ `_calc_ma` 口径唯一化 | 输入通路 | `9551b39` + `9cf70db` |
| 4 | `derived_series` 注册重算根键 + 有符号量（派生值 claim 可验、方向参与比对） | 校验通路 | `f3bbac4` |
| 5 | comparative 非枚举差值：UNVERIFIABLE + 独立桶 `comparative_delta_unregistered` + 覆盖缺口（不新增验证语义） | 校验通路 | `61d893a` |
| 6 | 拆报计数链路 `citation_unverifiable_comparative_delta`（state → task → evaluator）+ 三类计数进 trace 元数据 | 门禁/评估 | `5f56d23` + `3b64c9d` |
| 7 | 口径登记（metrics §1.3/§3）+ incident 027 关联追记 | 文档 | 见 metrics.md / incident 027 |

## 1. 单元与门禁（逐任务红→绿）

| 任务 | 红（失败原因） | 绿（证据） | 静态检查 |
|---|---|---|---|
| T1 通道+前缀 | 缺声明断言 / 图合并丢弃 `derived_series` / 前缀不可解析，三例各自红 | 40 passed（含编译图用例 ~5s）；`tests/nodes` 299 passed；`test_pipeline_stub` 17 passed | ruff clean |
| T2 差幅字段 | `KeyError: 'ma_spread_5_20_pct'` ×4 | 20 passed（含 round-2 精确断言、`b==0`/`n==60`/空表边界、`-0.0` 归一） | ruff/mypy clean |
| T3 注册+有符号 | `"derived_series" not in _COMPUTATIONAL_RECALC`；方向场景 `PASS != FAIL` | 57 passed；**numerical 路径 54,880 组合差分零差异**、spec 复核 18k 组合零差异 | ruff/mypy clean |
| T4 非枚举桶 | `bucket is None`（复审复现：2 failed/2 passed） | 79 passed；三枚举回归不变；路由层 gate 在 `FAIL` → 新桶不进 `fail_buckets`/重试/单点修复 | ruff clean |
| T5 计数链路 | 节点 KeyError / evaluator 14≠15 / task 缺键 | 75 passed + 474 回归；`unregistered` 排除式经反证（移除排除则计 1） | ruff clean |
| T5 补 | trace metadata `KeyError: 'metadata'` | 76 passed（三类计数进 `citation_unverifiable_ratio` score metadata，共享 `_unverifiable_class_counts`） | ruff clean |

## 2. 真实链路运行（2026-09-15，600519 贵州茅台，deep，真 LLM）

运行成功，报告 9001 字符。**新计数在真实运行生效**（三类互斥不重叠）：

```
citation_unverifiable_comparative_delta: 4
citation_unverifiable_text:              4
citation_unverifiable_unregistered:      1
citation_blocked: 1.0 / analyst_true_fail: 4.0 / verifier_normalized: 8.0
citation_coverage: 0.811（< 0.90 警告线，非阻断）/ surgical_repaired: 0.0
```

**路线 2 决策首批数据**：比较型差值占该 run UNVERIFIABLE 总数 9 条中的 **4 条（44%）**；逐条组成未取（无 trace）。报告正文含 MA 类比较句（「MA20 较 MA60 高 1.39%」），故至少 1 条属可枚举组合；其余待 Langfuse 恢复后逐条归因。
（背景读数按 AGENTS.md 纪律不作处置解读：blocked=1 与 r9 同量级，属当前生成方差。）

## 3. 真实数据通路核验（无 LLM，独立于模型行为）

- `fetch_data(600519)` + `compute_metrics` → 技术面 context **实际包含**「常用派生值」块，前缀 `derived_series.`，9 字段含四差幅真实值：
  `ma_spread_5_20_pct=-1.23 / ma_spread_20_60_pct=1.39 / close_vs_ma20_pct=-1.82 / close_vs_ma60_pct=-0.45`
- **报告原文引用「MA20 较 MA60 高 1.39%」，与预生成 `ma_spread_20_60_pct=1.39` 逐字一致**——「预生成 → LLM 引用」链路实证（该数值由工具计算，非模型心算）。

## 4. 边界与遗留

- **trace 级证据已补（2026-09-16，Docker/Langfuse 恢复后）**：三项全部取得，见 §6。
- **运行时 prompt 拉取失败回退本地**（「可能版本漂移」警告）——本 delta 无 prompt 变更，不影响结论；Langfuse 已恢复，本项不再适用。
- **全量套件本机间歇性外部 I/O 挂起**：两轮实证（本轮 55 分钟 / 13.1s CPU；T3 implementer 另一次 30 分钟无进展）。Task 2 早前完整跑通 **2366 passed / 2 skipped / 0 failed**。本轮带 `faulthandler_timeout` 重跑结果见 §5。
- **已登记待决策**（metrics §3 同族登记）：① 方向校验扩面至 computational 路径（spec 对齐，FAIL 构成变化需按桶归因）；② recalc 返回 None 的「数据不足」子字段被判 FAIL `path_unresolvable`，建议改判 UNVERIFIABLE（另立项）。
- 委派质量遗留（Minor，交终审裁定）：T5 计数可补 `status` 守卫与注释；T4 可补「带 metric_name/period 申报」用例把分支自源缺口钉进 CI；归档验证报告 `test_fourteen_evaluators` 引用悬空（信息性）。

## 5. 全量套件（带 faulthandler 重跑 + Langfuse 禁用）

```
LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= LANGFUSE_HOST= LANGFUSE_BASE_URL= \
  uv run pytest -q --ignore=tests/e2e
→ 2388 passed, 2 skipped, 3 failed in 901.70s
```

- **非 live 层 0 失败**。3 个失败全部为 `pytest.mark.live`（nightly 层，不进 PR 门禁）且失败原因即环境覆盖本身：`requests.exceptions.MissingSchema: Invalid URL '/api/public/traces'`（为规避下面这条挂起而清空了 `LANGFUSE_HOST`）——非本 delta 缺陷。
- **两轮全量未完成的原因（挂起根因，已实证）**：`tests/evals/backtest/test_data_snapshot.py:225::test_metadata_contains_prompt_versions_and_model` → `evals/backtest/data_snapshot.build_snapshot` → `evals/run._collect_prompt_versions` → `prompts/loader._fetch_info` → Langfuse `get_prompt`（含 backoff 重试）——服务停机时连接**挂起不返回**（faulthandler 转储：`socket.create_connection` 阻塞 55+ 分钟 / CPU 仅 13s）。规避：空 Langfuse 环境变量跑（本地回退，与真实 deep 运行的降级路径同源）；带 Langfuse 的完整门禁需先恢复 Docker。
- 时序说明：本轮在 delta `add-debate-argument-anchors` Task 1 提交前完成收集（pytest 收集期导入模块），反映的是 **delta 2 完成态**的代码。

## 6. trace 级证据补录（2026-09-16，Langfuse 在线，两次 deep 600519）

- **① 派生值注入**（技术分析师 trace `04f1c32dc8327dd5f0e4a5c692fcfd4f`）：输入 18090 字符，含「常用派生值（工具预生成，直接引用；field_ref 前缀 `derived_series.`）」块与四差幅真实值（-1.23 / 1.39 / -1.82 / -0.45 同前）。
- **② 三类计数进 trace**（trace `b98eac8709caa11709a54c8f8f777b2f`）：`citation_unverifiable_ratio = 0.1549` 的 score metadata 为 `{"unverifiable_text": 5, "unverifiable_unregistered": 0, "unverifiable_comparative_delta": 6}`——**报告与 trace 同口径**。
- **③ 非枚举差值逐条组成**（同 trace，n=6）：CPI 同比 0.8 / M2 同比 7.5 / ROE 32.53 / 利息覆盖倍数 / 经营现金流/净利润 0.75 / 销售费用率 4.30——**全部为「从 A 降至/升至 B」的跨期水平比较（双端均可从 state 推导），无一条均线/涨跌幅类**；对照另一轮（无根 span）该计数为 0，条数在轮次间波动。
- **路线 2 决策修正（重要）**：6/6 为可枚举双端比较，缺口在**申报纪律**（LLM 把 comparative 当叙述类型、stated_value 填当期值、未申报 `stated_value_b`），而非缺公式重算机制——候选处置应优先「双端申报纪律的 prompt 强化 / 对『comparative 携数值 stated_value 且 field_ref 可解析』形态给更精确的提示性分桶」，原「formula 申报重算」（路线 2）降级为备选。该形态因本 delta 的独立桶**首次可见**，正是计数的目的。

## 结论

| 验收项 | 状态 |
|---|---|
| 单元/门禁红→绿 + 静态检查 | ✅ |
| 真实 deep 运行（新计数生效、无回归崩溃） | ✅ |
| 真实数据通路（派生值注入 + 被引用逐字一致） | ✅ |
| 全量套件（非 live 0 失败） | ✅ |
| trace 级证据 | ✅（2026-09-16 补，见 §6） |
