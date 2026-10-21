# Design: add-foreign-broker-sentiment

## Approach

### 采集

- 源为配置化 URL 列表（RSS/Atom 或返回 JSON 的新闻 API）；`FOREIGN_NEWS_FEEDS` 为空时 job 直接记 `empty`，不发起请求、不调 LLM。
- 每个源独立抓取，超时/HTTP 错误只影响该源；本次运行记 `degraded` 并保留其他源结果。
- 只取标题、链接、发布时间与源提供的短摘要（截断 500 字）；MUST NOT 请求或存储正文全文。
- 关键字过滤：机构名（UBS/瑞银、摩根士丹利、摩根大通、高盛、花旗、野村、汇丰、巴克莱、德银等）+ 中国/港股/A 股关键词；命中任意组合才进入抽取。
- User-Agent 与请求间隔沿用研报采集纪律（串行、≥1 秒、可配置）；不绕过付费墙。

### 事件抽取

新 prompt `foreign_broker_event.md`，输入为标题+短摘要：

```json
{
  "broker": "UBS",
  "published_at": "2026-10-20",
  "action": "target_up|target_down|rating_up|rating_down|positioning|sentiment|other",
  "target_type": "market|sector|stock|unknown",
  "target": "中国股市|银行|某标的",
  "direction": "positive|negative|neutral|unknown",
  "summary": "不超过 120 字，只陈述原文",
  "confidence": "high|medium|low"
}
```

- 只抽原文明确的动作；「据悉/传闻」不得升级为确定事件。
- 机构无法识别时 `broker=null`，事件仍可入库但标记 `low`。
- 抽取失败：原始条目仍入库，`extraction_status=failed`，抽取字段为 null；不阻断其他条目。

### 存储

`foreign_broker_events`：

- `event_key`（`hash(title, url, published_at)` 主键）、`title`、`url`、`source`、`published_at`、`fetched_at`；
- 抽取字段：`broker`、`action`、`target_type`、`target`、`direction`、`summary`、`confidence`；
- `extraction_status`、`model`、`prompt_version`、`usage_policy='personal_research'`。
- 同一 `event_key` 重复入库 no-op；标题被改写但 URL 相同也视为同一事件（键含 URL）。
- 不存全文；`summary` 只来自源摘要或抽取，长度硬上限 500。

### 与内资分开

- 事件表带 `region='foreign'`（本表全为 foreign，字段保留以便未来统一视图）。
- 记分卡查询按 `region` 分区；v1 不做外资事件的收益结算（样本与标的映射都不稳），只做描述统计：按机构/动作/对象的条数与方向分布。
- 内资 `analyst-ex-post-verification` 的机构权重 MUST NOT 读取本表。

### 调度与接口

- 日批 `foreign_broker_news_daily`：每交易日 17:05（晚于分析师结算 17:00 与研报日批），抓取→过滤→抽取→落库。
- 只读接口 `GET /api/v1/ops/research/foreign-sentiment`：按机构/动作/对象/日期过滤，返回事件与方向计数；空态显式。
- 所有失败写运行记录（`collection_runs` 或等价），job 不因单源失败整体 failed。

## Alternatives Considered

- **自动抓外资原文 PDF**：否决。反爬、登录与个人自用边界；且原文全文的合规风险高于二手快讯。
- **直接进宏观分析师上下文**：v1 否决。二手快讯噪声高、样本未验证；先只读观测，积累 2–3 个月再评估注入。
- **与内资共用一张事件表和一套权重**：否决。两者口径不同（内资有逐篇评级与目标价，外资是二手转述），混在一起会污染机构权重。
- **用 LLM 直接生成「外资情绪分」**：否决。先存事件与描述统计，分数等有结算口径再做。

## Risks

- **源不稳定/付费墙**：单源失败降级，多源配置；不绕过付费墙。
- **重复报道**：同一事件被多家转载，键只按 URL 去重会重复计数。对策：描述统计按 `(broker, action, target, 日期窗口)` 去重展示，原始行保留。
- **抽取幻觉**：标题党或摘要不完整时模型可能补全。对策：prompt 只抽原文、缺失为 null；`confidence` 标记；抽样人工核验。
- **关键字漏抓**：机构名变体多。对策：配置化关键字表，运行摘要记录过滤前后条数。
- **合规**：只存标题/链接/短摘要，不存全文；`usage_policy=personal_research`；不对外分发。
