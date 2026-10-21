## Why

外资行的中国策略观点是内资研报没有的一层信息（全球仓位、跨市场相对估值、外资情绪），但外资原文 PDF 不自动抓取（反爬与个人自用边界），系统目前只有个股新闻列表，没有「谁上调/下调了什么」的结构化事件序列。二手快讯可以免费拿到，足以支撑一条「外资风向标」观测线。

## What Changes

- 新增可配置的二手快讯源（RSS/新闻 API URL 列表，`FOREIGN_NEWS_FEEDS`）；未配置时为空态，不报错、不调用 LLM。
- 新增事件抽取（`purpose="foreign_broker_event"`）：从标题/摘要中抽取机构、日期、动作（上调/下调/目标价上调/下调/仓位/情绪）、对象类型（市场/行业/个股）、对象、方向、摘要与来源链接；只依据原文，缺失置 null。
- 新增 `foreign_broker_events` 表，幂等键为 `hash(title, url, published_at)`；同一快讯重复入库 no-op。
- 与内资研报**分开记分卡**：新增 `region=foreign` 维度，不混入 `analyst-ex-post-verification` 的内资机构权重。
- 提供只读查询接口（按机构/对象/方向/日期），v1 **不注入**任何分析师上下文、不改变决策。
- 合规边界：只存标题、链接、短摘要与抽取字段，MUST NOT 抓取或存储外资原文全文；原文仍只经人工投喂进入。
- 非目标：不做外资原文自动抓取、不做对外分发、不做自动调仓。

## Capabilities

### New Capabilities

- `foreign-broker-sentiment`: 外资行二手快讯采集、事件抽取、幂等存储、内外资分开的只读情绪序列。

### Modified Capabilities

（无。v1 只读观测，不改变现有分析与决策行为。）

## Impact

- 数据：`data/research_reports.db` 新增 `foreign_broker_events`。
- 代码：`src/finance_agent/data/foreign_broker_news.py`（新，抓取+解析+抽取+落库）、`src/finance_agent/prompts/foreign_broker_event.md`（新）、`src/finance_agent/llm/types.py`（新 purpose）、`src/finance_agent/llm/router.py`（能力声明）、`src/finance_agent/outcome/scheduler.py`（日批）、`src/finance_agent/ops_api.py` 或 `api.py`（只读端点）。
- 配置：`FOREIGN_NEWS_FEEDS`（默认空）、`FOREIGN_NEWS_KEYWORDS`（默认机构+中国关键词）。
- 依赖：优先复用既有 HTTP 客户端与 XML 解析；不新增重型依赖。
- 提示词：新 prompt 必须发布到 Langfuse（`deploy_prompts.py`）。
- 测试：feed 解析、关键字过滤、抽取空字段、幂等、空配置 no-op、失败降级。
