# Proposal: add-analyst-revision-factors

## Why

因子库现有 10 个 active 因子**全部是价量因子**（量比/振幅/量价相关等变体，准入账本 429 处理/11 入库），供给端 RD-Agent 只能在 OHLCV 六字段的 DSL 里反复发明已知价量形态，70% 候选死在闸②——供给与检验结构性错配。而系统已建成的研报数据面（`research_reports.db`：一致预期快照 9472 行、评级变动事件 139 行、事件式 claim 120 行）**每日流动却零因子产出**。「分析师预期修正」是文献证据扎实的异象族，与现有量价池天然低相关（闸④最容易过），且落在 LLM 强项区（事件解读）。本 delta 把研究面数据接进与价量因子**同一套五道闸**，给因子库换一个不同来源的供给。

## What Changes

- 新增**研究面因子族**（预计算值因子通道）：因子以「date × code 值矩阵」进入五道闸，而非 DSL 表达式——统计闸口（闸②③④⑤）逐字不变，闸①缺失率与闸0查重为值因子定义专门口径。
- 首批三个因子：`eps_rev_1m`（近 30 日一致预期 EPS 修正幅度）、`rating_chg_1m`（近 30 日净评级变动）、`coverage_chg_1m`（近 30 日覆盖动量）。
- 生效日语义：事件/快照于 T 日收盘后可得（研报日批 ~17:00 落库）→ 因子值挂 **T+1 交易日**，防前视，与执行链 D+1 口径一致。
- 闸①值因子口径：缺失率对**因子宣称覆盖的事件人群**计（非全市场面板）；另设 **IC 广度下限**（单日有值票数 ≥ 20 该日 IC 才计入样本）。
- 闸0值因子查重键：`revision:<feature_key>`（无 AST，同 feature_key 在库即 STRUCTURAL_DUPLICATE）。
- 新增日批任务「研究面因子派生与准入」（研报采集后、选股日批前），source=`research`；事件历史不足时拒收留痕 `insufficient_event_history` 并支持按状态重放（数据积累后重提）。
- 因子库 source 枚举扩容（`builtin` / `llm` / `external` / `research`）与只读查询、准入账本、衰减监控的值因子路径。

## Capabilities

### New Capabilities

- `analyst-revision-factors`: 研究面预期修正因子族——数据派生契约、生效日语义、值矩阵契约、准入口径、日批接线、与分析师事后检验的边界。

### Modified Capabilities

- `factor-library`: 五道闸准入管线（值因子的闸①缺失率与 IC 广度口径）、双层查重（值因子查重键）、因子库只读查询（source 枚举与值因子展示）、跨批次候选检验账本（source 枚举）、衰减监控与自动降级（值因子 IC 重算路径）。

## Impact

- 后端：新增 `src/finance_agent/factors/revision.py`（派生视图纯函数 + 值因子准入入口）；`validate.py`（值因子闸口分支）、`library.py`（查重键/source）、`monitor.py`（值因子 IC 重算）、`ops_api.py`/日批注册（新任务）。
- 数据：只读 `data/research_reports.db` 三表（`earnings_forecasts` / `rating_changes` / `analyst_claims`），无新表无迁移；值不落独立因子值表（监控时点派生，缓存为可选优化）。
- 前端：因子库页来源标签扩容（`research` → 「研究面」）；无新页面。
- 与 `analyst-ex-post-verification` 的边界：事后检验判**分析师**（claim → outcome 记分卡），本 delta 判**信号可交易性**（因子 → 五道闸）——复用同一数据源，互不写入。
- 与 `add-factor-monotonicity-gate`（并行在飞）同改 `factor-library`「五道闸准入管线」requirement——后归档方须合并重写 MODIFIED 块（既有惯例）。
