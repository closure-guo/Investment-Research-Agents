## ADDED Requirements

### Requirement: 外资二手快讯采集

系统 SHALL 从配置化的 RSS/新闻 API 源采集外资行中国相关快讯，未配置源时为空态。

- 源列表 SHALL 由 `FOREIGN_NEWS_FEEDS` 提供；为空时 job SHALL 记 `empty`，MUST NOT 发起请求、MUST NOT 调用 LLM。
- 每个源 SHALL 独立抓取；单源超时或 HTTP 错误 SHALL 降级并在运行记录中可见，MUST NOT 使其他源失败。
- 系统 SHALL 只保存标题、链接、发布时间与源提供的短摘要（≤500 字）；MUST NOT 抓取或存储原文全文。
- 抓取 SHALL 携带常规 UA、串行请求并保持 ≥1 秒间隔；MUST NOT 绕过付费墙或登录。

#### Scenario: 未配置源为空态

- **GIVEN** `FOREIGN_NEWS_FEEDS` 为空
- **WHEN** 日批运行
- **THEN** 运行记录 SHALL 为 `empty`
- **AND** 请求数与 LLM 调用数 SHALL 均为 0

#### Scenario: 单源失败不影响其他源

- **GIVEN** 配置两个源，其中一个返回 503
- **WHEN** 日批运行
- **THEN** 正常源的事件 SHALL 入库
- **AND** 失败源 SHALL 记入运行记录
- **AND** job 状态 SHALL 为 degraded

#### Scenario: 不存全文

- **GIVEN** 某源摘要超过 500 字
- **WHEN** 条目入库
- **THEN** 摘要 SHALL 截断至 500 字
- **AND** MUST NOT 存储正文全文

### Requirement: 外资事件抽取

系统 SHALL 对通过关键字过滤的快讯做结构化事件抽取。

- 关键字过滤 SHALL 覆盖常见外资机构名与中国/港股/A 股关键词；过滤前后条数 SHALL 记入运行摘要。
- 抽取字段 SHALL 至少含：机构、日期、动作（目标价上调/下调、评级上调/下调、仓位、情绪、其他）、对象类型（市场/行业/个股/未知）、对象、方向、摘要、置信度。
- 抽取 MUST 只依据原文；原文未明确的字段 SHALL 为 null 或 `unknown`，MUST NOT 推断或补全。
- 抽取失败时 SHALL 保留原始条目并标 `extraction_status=failed`，抽取字段为 null，MUST NOT 阻断其他条目。

#### Scenario: 明确事件抽取

- **GIVEN** 一条快讯标题明确写「UBS 上调中国银行股评级」
- **WHEN** 抽取运行
- **THEN** `broker` SHALL 为 UBS、`action` SHALL 为评级上调、`target_type` SHALL 为行业、`target` SHALL 为银行

#### Scenario: 模糊表述不升级

- **GIVEN** 快讯写「据悉某外资行可能看多」
- **WHEN** 抽取运行
- **THEN** 不得把可能意图抽为确定 `action`
- **AND** 无法判断时 `action=other` 或 `unknown`

#### Scenario: 抽取失败保留原文

- **GIVEN** 某条快讯抽取 LLM 调用失败
- **WHEN** 日批运行
- **THEN** 该条 SHALL 入库且 `extraction_status=failed`
- **AND** 后续条目 SHALL 继续抽取

### Requirement: 事件存储与幂等

系统 SHALL 以 `hash(title, url, published_at)` 为幂等键落 `foreign_broker_events`。

- 同一事件重复入库 MUST NOT 产生重复行。
- 标题改写但 URL 相同 SHALL 视为同一事件。
- 事件 SHALL 标记 `usage_policy=personal_research`。

#### Scenario: 重复抓取幂等

- **GIVEN** 同一快讯已入库
- **WHEN** 下一次日批再次抓到同一 URL
- **THEN** 表行数 SHALL 不变

### Requirement: 内外资分开统计

外资事件 MUST NOT 混入内资分析师事后检验的机构权重或战绩口径。

- 查询 SHALL 支持按 `region=foreign` 分区。
- v1 SHALL 只提供描述统计（按机构/动作/对象/方向的条数），MUST NOT 生成收益结算或机构有效性结论。
- `analyst-ex-post-verification` 的权重快照与记分卡 MUST NOT 读取本能力的数据。

#### Scenario: 内资权重不受影响

- **GIVEN** 外资事件表有 100 条记录
- **WHEN** 内资机构权重快照生成
- **THEN** 权重 SHALL 与无外资事件时一致

#### Scenario: 描述统计可查

- **GIVEN** 近 30 天有 5 条 UBS 事件，其中 3 条看多银行
- **WHEN** 查询外资情绪
- **THEN** SHALL 返回按机构/对象/方向的计数
- **AND** MUST NOT 返回「UBS 有效/无效」的结论

### Requirement: 只读接入与决策隔离

v1 外资事件 SHALL 只读观测，不进入任何分析师上下文或决策路径。

- 系统 SHALL 提供只读查询接口，按机构/动作/对象/日期过滤，空态显式。
- 宏观/基本面/技术/情绪分析师上下文 MUST NOT 读取外资事件。
- 交易员、风控、基金经理与执行链 MUST NOT 读取外资事件。

#### Scenario: 决策零变化

- **GIVEN** 外资事件表非空
- **WHEN** 执行一次深度分析
- **THEN** 各分析师上下文与决策字段 SHALL 与空表时一致
- **AND** 外资事件 SHALL 不出现在报告中

#### Scenario: 空态显式

- **GIVEN** 外资事件表为空
- **WHEN** 调用只读接口
- **THEN** SHALL 返回空列表与 `status=empty`
- **AND** MUST NOT 返回伪造计数
