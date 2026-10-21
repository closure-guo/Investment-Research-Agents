## Why

策略/宏观研报每天在采（元数据 143 行业 + 41 策略 + 42 宏观），投喂进来的全文也会被抽取，但抽取结果只按个股代码匹配给基本面分析师；策略报告没有个股代码，于是「抽完即丢」。与此同时，宏观分析师的上下文只有 CPI/PMI/LPR 等指标序列，看不到市场在担心什么、资金在等什么政策、哪些板块被偏好——这恰好是策略研报能补的一层。

## What Changes

- 抽取契约新增 `sector_views[]`（行业、方向、论据、来源页）与 `market_view`（情绪、仓位、关键催化剂），允许为空；策略/宏观报告不再需要伪造个股条目。
- **明确边界**：`stocks[]` 只放正文中真正被讨论、带个股论据的标的；附录披露表中「全覆盖评级 + 当日收盘价」的条目 MUST NOT 进入 `stocks[]`，避免把策略观点错误归因到个股。
- 新增 `strategy_report_digest`：取最近 3 篇、30 天内的策略/宏观抽取结果，确定性聚合机构、日期、行业观点、核心逻辑、催化剂、风险与分歧点；不调用 LLM。
- `_build_macro_context` 注入策略观点段；基本面分析师上下文 MUST NOT 注入该段。
- 策略观点条目纳入 citation 回声源集合，避免宏观分析师引用后全部落 UNVERIFIABLE。
- `macro_analyst.md` 增加「卖方策略观点」消费条款与防锚定声明，按权威源流程发布。
- 无数据不注入；超过 30 天标注陈旧或按配置丢弃；不做行业配置权重、不做调仓、不做个股清单匹配。

## Capabilities

### New Capabilities

（无。）

### Modified Capabilities

- `research-report-dataplane`: 投喂抽取新增 `sector_views` / `market_view`；策略报告个股清单边界显式化；新增策略观点聚合读取。
- `analyst-data-sources`: 宏观分析师上下文新增策略/宏观研报观点段；策略观点纳入 citation 回声匹配。

## Impact

- 代码：`src/finance_agent/prompts/report_extraction.md`（抽取契约）、`src/finance_agent/data/research_reports.py`（`_coerce_extraction`、`get_strategy_digest` / `format_strategy_digest`）、`src/finance_agent/nodes/fetch.py`（state 新增 `strategy_report_digest`）、`src/finance_agent/nodes/analysts.py`（`_build_macro_context`）、`src/finance_agent/citation.py`（回声源）、`src/finance_agent/prompts/macro_analyst.md`。
- 数据：`report_extractions` 新增 `sector_views_json` / `market_view_json`；无新库。
- 测试：抽取空 stocks + 非空 sector_views、披露表不进入 stocks、digest 选择与陈旧、宏观上下文注入、基本面不注入、citation 回声命中。
- 提示词：两个 prompt 修改后必须执行 `uv run python scripts/deploy_prompts.py`。
- 非目标：不做行业轮动/权重、不做组合调仓、不做策略报告触发个股分析（见独立 delta）。
