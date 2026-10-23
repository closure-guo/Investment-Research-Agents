# Proposal: add-industry-report-context

## Why

研报数据面已按日采集五类研报元数据，行业研报是其中最大的一类（实测 228 / 489 行），字段齐备（标题、机构、分析师、发布日期、行业名、PDF 链接），但**没有任何消费方**。

基本面分析师目前看到的是：本标的研报元数据（top 8）、一致预期、手工投喂全文合并。看不到「同行业最近发生了什么」。策略/宏观观点聚合只覆盖 `策略报告` 与 `宏观研究` 两类（`STRATEGY_REPORT_TYPES`），行业研报被排除在外；研报触发链路只吃人工投喂 PDF 的抽取产物，API 采集的行业研报进不去。

结果是：分析一只电池股时，库里有数十篇当日电池行业研报，分析师一篇都看不到。这不是检索算法的问题，是接入缺失。在补齐接入之前讨论向量检索，等于给没有货的仓库装拣货机器人。

## What Changes

- 新增确定性读取 `get_industry_digest(industry, *, as_of, ...)`：按行业名与 `research_reports.report_type='industry'` 精确匹配，取 `as_of` 之前、窗口内最近 N 篇，输出标题级聚合。
- 新增 `format_industry_digest()` 渲染上下文文本段，**显式披露「仅元数据、未读取正文」**。
- `fetch_data` 计算 `state["industry_report_digest"]`；基本面分析师上下文注入该段（带防锚定条款）。
- 该段标题并入 citation 回声源集合。
- 无记录 → None（段不出现，无占位文本）；窗口内 0 篇但有历史 → 显式 `stale`（只报最新日期与距今天数，不列陈旧标题）。

**不做**（明确排除，避免范围蔓延）：不引入向量库 / embedding / FTS5；不做跨行业语义检索；不下载行业研报 PDF（全文仍只经人工投喂）；不改策略/宏观聚合口径；不改前端。

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**: `research-report-dataplane` — ADDED「行业研报按行业聚合读取」「行业研报注入基本面分析师上下文」「行业研报标题进入引用回声源」

## Impact

- 代码：`data/research_reports_store.py`（按行业查询）、`data/research_reports.py`（聚合 + 渲染）、`nodes/fetch.py`（注入 state）、`nodes/analysts.py`（上下文段）、`citation.py`（回声源）、`state.py`（state 键）、`prompts/fundamental_analyst.md`（数据清单一行）
- 依赖/基础设施：零新增。无新表、无新 job、无新 API、无前端改动。
- 上下文体积：新增 ≤ 8 条标题（约 300–600 字），受硬上限约束，不改既有记忆块裁剪顺序。
- 失败模式：读库异常 → 段不出现（fail-open，与 `get_strategy_digest` 一致），分析不阻断。
