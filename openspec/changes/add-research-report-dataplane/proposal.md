# Proposal: add-research-report-dataplane

## Why

研报/评级/一致预期是 2026-10-17 生态调研（docs/evals/2026-10-17-研报驱动分析管线生态调研.md）证据强度排序第一的输入（结构化预期数据 30 年同行评审背书），但当前系统只按需拉个股研报列表的 8 条元数据，且实测该链路自上线起字段即残缺（机构/日期因列名不匹配恒空、目标价字段接口根本不提供，跑五周无人发现）；五类研报（个股/行业/策略/宏观/晨报）的日频元数据、评级变动事件、全市场一致预期均无任何落库——分析师事后检验与预期修正因子两条下游线没有原料。2026-10-19/20 渠道调研已实测确认免费主干可行（东财 reportapi 五类全市场按日拉取、巨潮评级变动日频、东财全市场盈利预测一次 2887 只、同花顺单股明细），本 delta 把这条数据面立起来。

## What Changes

- **修正并扩展个股研报取数**（bug 修复 + 字段面扩展）：机构/日期对齐真实列名；「目标价」改取接口实际提供的盈利预测列（当年/次年/次两年 EPS 与 PE）与「近一月个股研报数」；新增契约测试防 fixture 与真实接口漂移（本次 org/date 空跑五周的根因）。
- **新增研报数据面**（新库 `data/research_reports.db`）：四张表——研报主表 / 评级变动表 / 盈利预测表 / 分析师表；期次键（标题+机构+日期哈希）与幂等重放从第一天内建。
- **日批采集（全部免费源）**：东财五类研报元数据全市场按日增量（`/report/list` qType=0/1 + `/report/jg` qType=2/3/4）；巨潮评级变动（按日全市场）；东财全市场盈利预测快照（研报数+评级分布+三年 EPS）。新浪研报列表作第二源做交叉计数与降级。同花顺单股一致预期明细按需拉取（分析时）。
- **断更与覆盖状态显式化**：每标的最新研报日期/近 30 日条数为可查询状态，MUST NOT 以静默空列表伪装「无覆盖」（600845 断更 18 个月的教训）。
- **调度接入**：三个日批 job 注册进 start_scheduler（含注册表镜像守护测试，#376 教训）；东财侧串行限流（≥1s+抖动、复用会话、UA+Referer）。
- **消费侧最小接线**：基本面分析师上下文在研报段之后追加「一致预期」段（评级分布 + 一致预期 EPS + 覆盖机构数，无覆盖显式「无分析师覆盖」），prompt 同步扩条并按权威源流程发布。
- **历史回填**：东财接口按日期区间分窗回填（起始 2024-01-01），第三方日更仓库（manymore13/report）仅作回填后对照，不作依赖。
- **手工投喂与全文入库**：新增投喂目录（`RESEARCH_REPORT_INBOX_DIR`），扫描 PDF → 全文解析入库（`report_documents`，文件内容哈希幂等，`usage_policy=personal_research` 个人研究自用）；系统仍不自动下载全文（反爬/稳定性），全文只经人工投喂进入。
- **投喂文档结构化抽取**：新文档入库后经 LLM 抽取评级/目标价/EPS 预测/核心逻辑/催化剂/风险（`report_extractions`，prompt 合同 + 确定性评级归一化；失败降级不阻断入库）。
- **按标的合并**：多篇投喂研报按标的确定性聚合（报告数/机构/评级分布/目标价与 EPS 区间/观点去重），并在分析师上下文注入「手工投喂研报合并」段（无记录不注入）。
- **边界**：观点→权重不在本 delta（维持 10-17 调研结论）；产出不对外分发（个人研究自用约定）。

## Capabilities

### New Capabilities

- `research-report-dataplane`: 研报元数据/评级变动/一致预期/分析师档案的采集、落库、幂等、覆盖状态、日批调度与限流纪律；手工投喂 PDF 的全文解析、LLM 结构化抽取与按标的合并（个人研究自用；免费主干）。

### Modified Capabilities

- `analyst-data-sources`: ①「公告与研报数据获取」字段面修正与扩展（机构/日期列名对齐、目标价→盈利预测列、近一月研报数、契约测试）；②「分析师消费新信源」追加一致预期段与无覆盖显式语义。

## Impact

- 新增：`src/finance_agent/data/research_reports.py`（采集器+落库+投喂+抽取+合并）、`src/finance_agent/data/research_reports_store.py`（SQLite 六表：四业务表 + `report_documents` + `report_extractions`）、`src/finance_agent/prompts/report_extraction.md`、`report_extraction` purpose（types/router）、四个调度 job（含 16:58 投喂扫描）、依赖 `pypdf`、E2E/测试配置补 `RESEARCH_REPORTS_DB_PATH` 隔离（incident 038 教训：新库必须全配置显式隔离）。
- 修改：`src/finance_agent/data/akshare_client.py::fetch_research_reports`（字段修复+扩展）、`src/finance_agent/nodes/analysts.py::_build_fundamental_context`（一致预期段）、`src/finance_agent/nodes/fetch.py`（一致预期按需拉取同花顺明细）、`src/finance_agent/prompts/fundamental_analyst.md`（消费条款，须跑 deploy_prompts.py）。
- 下游（本 delta 不做，仅受益）：分析师事后检验（复用 predictions/结算/FDR）、预期修正类因子进五道闸、/research 页面。
- 付费源（Tushare 摘要/AShareHub/终端）仅登记为备选，不在本 delta 接入。
