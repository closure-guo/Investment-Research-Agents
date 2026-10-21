## ADDED Requirements

### Requirement: 全文页偏移入库

系统 SHALL 在手工投喂 PDF 入库时记录每页在全文中的字符边界，落 `report_documents.page_offsets`（JSON 数组，元素含 `page` / `start` / `end`，左闭右开）。

- 偏移 SHALL 按固定拼接规则 `"\n".join(page_texts)` 计算，并与 `text` 字段一致。
- 解析失败（加密、无文本层、损坏）时 `page_offsets` SHALL 为 null，文档仍入库，运行记录 SHALL 标 degraded 并继续处理其他文件。
- 存量文档未重解析时 `page_offsets` SHALL 保持 null；MUST NOT 按页数均分或其他方式伪造偏移。

#### Scenario: 数字 PDF 页偏移正确

- **GIVEN** 一份 12 页、可抽取文本层的 PDF
- **WHEN** 投喂扫描入库
- **THEN** `page_offsets` SHALL 含 12 个元素
- **AND** 每个 `[start, end)` 切片 SHALL 落在对应页文本范围内
- **AND** 首元素 `start=0`，末元素 `end=len(text)`（忽略页间分隔符）

#### Scenario: 扫描件降级不阻断

- **GIVEN** 投喂目录含一份无文本层的扫描 PDF
- **WHEN** 投喂扫描运行
- **THEN** 该文档 `page_offsets` SHALL 为 null
- **AND** 运行记录 SHALL 为 degraded 且错误可查
- **AND** 同目录其他 PDF SHALL 正常入库

#### Scenario: 存量文档不伪造偏移

- **GIVEN** 一份在本次变更前入库的文档，未执行重解析
- **WHEN** 查询该文档
- **THEN** `page_offsets` SHALL 为 null
- **AND** MUST NOT 返回按页数均分的估算值

### Requirement: 抽取观点页码标注

投喂文档的结构化抽取 SHALL 支持为每条核心逻辑、催化剂、风险与个股标注来源页 `source_page`；原文无法定位页码时 SHALL 为 null。

- 模型 MUST NOT 依据段落顺序猜测页码。
- 消费端 SHALL 兼容旧抽取的字符串形态与新抽取的 `{text, source_page}` 对象形态。
- 页码 SHALL 落在该文档 `page_count` 范围内；越界值 SHALL 归一为 null 并记入抽取运行记录。

#### Scenario: 观点带页码

- **GIVEN** 一份抽取成功、页偏移可用的投喂文档
- **WHEN** 抽取运行
- **THEN** 至少一条核心逻辑/催化剂/风险 SHALL 携带 `source_page`
- **AND** 每个 `source_page` SHALL 在 1..page_count 范围内

#### Scenario: 无法定位时为空

- **GIVEN** 原文没有清晰的页码线索
- **WHEN** 抽取运行
- **THEN** 对应 `source_page` SHALL 为 null
- **AND** MUST NOT 填入 0 或猜测值

#### Scenario: 旧抽取兼容

- **GIVEN** 一份抽取结果为字符串数组（无页码）的存量文档
- **WHEN** 合并或展示该文档
- **THEN** SHALL 正常处理字符串形态
- **AND** 页码 SHALL 显示为未知，不报错

### Requirement: 跨源报告关联

系统 SHALL 在抽取完成后尝试把投喂文档与 `research_reports` 元数据行关联，写入 `report_documents.report_key`。

- 匹配键 SHALL 为 `(broker, report_date, normalized_title)`；日期允许 ±1 天窗口。
- 归一标题 SHALL 只做 NFKC、去空白与常见标点、casefold；MUST NOT 删除中文实词。
- 恰好 1 条候选命中时 SHALL 写入 `report_key`；0 条或多条歧义时 SHALL 留 null。
- 关联失败 MUST NOT 阻断文档入库、抽取或按标的合并。
- v1 MUST NOT 使用编辑距离等模糊匹配。

#### Scenario: 命中元数据行

- **GIVEN** 投喂文档抽取为机构 UBS、日期 2026-09-30、标题归一后与某 `research_reports` 行一致
- **WHEN** 关联 job 运行
- **THEN** `report_documents.report_key` SHALL 等于该元数据行的 `report_key`

#### Scenario: 无匹配留空

- **GIVEN** `research_reports` 中没有同机构同日期同标题的行
- **WHEN** 关联 job 运行
- **THEN** `report_key` SHALL 为 null
- **AND** 文档 SHALL 仍可被抽取与合并

#### Scenario: 多条歧义不猜

- **GIVEN** 同一机构同日存在两篇归一标题相同的元数据行
- **WHEN** 关联 job 运行
- **THEN** `report_key` SHALL 为 null
- **AND** 运行记录 SHALL 记 `ambiguous_match` 计数

### Requirement: 报告深度分级

系统 SHALL 对投喂文档做确定性深度分级，落 `depth_class ∈ {deep, comment, unknown}`。

- 标题含「深度/专题/策略/展望」SHALL 归 `deep`；含「点评/跟踪/快评/更新」SHALL 归 `comment`；其余归 `unknown`。
- 分级 MUST NOT 用于丢弃文档或阻断抽取。
- v1 MUST NOT 用 `depth_class` 改变合并摘要、一致预期或任何决策权重。

#### Scenario: 深度报告分类

- **GIVEN** 投喂文档标题含「深度」
- **WHEN** 入库完成
- **THEN** `depth_class` SHALL 为 `deep`

#### Scenario: 点评分类

- **GIVEN** 投喂文档标题含「点评」
- **WHEN** 入库完成
- **THEN** `depth_class` SHALL 为 `comment`

#### Scenario: 分级不改变合并结果

- **GIVEN** 同一标的有一篇 deep 与一篇 comment 投喂研报
- **WHEN** 按标的合并
- **THEN** 合并结果 SHALL 包含两篇
- **AND** MUST NOT 因分级丢弃或降权任何一篇（v1）

### Requirement: 评级符号归一

系统 SHALL 把各机构评级确定性归一为买入/增持/中性/减持/卖出/未知六档。

- 归一表 SHALL 覆盖常见中英文同义词（优于大市、跑赢行业、推荐、强烈推荐、Accumulate、Overweight 等）。
- 未识别评级 SHALL 保留原文并把归一档记为 `unknown`，MUST NOT 丢弃该条记录。
- 归一 SHALL 在抽取层完成并落库，合并与展示 SHALL 使用归一结果，同时保留原文供核对。

#### Scenario: 同义词归一

- **GIVEN** 原文评级为「优于大市」
- **WHEN** 抽取结果归一化
- **THEN** 归一档 SHALL 为「买入」
- **AND** 原文 SHALL 保留为「优于大市」

#### Scenario: 未知评级不丢弃

- **GIVEN** 原文评级为归一表未覆盖的词
- **WHEN** 抽取结果归一化
- **THEN** 归一档 SHALL 为「未知」
- **AND** 该条个股记录 SHALL 正常保留

### Requirement: 重解析与幂等

系统 SHALL 提供投喂文档重解析能力，用于补齐页偏移、关联键与深度分级。

- `inbox --force-reparse` SHALL 按 `doc_key` 更新现有行，MUST NOT 新增重复行。
- 重解析 SHALL NOT 重复调用 LLM；只有显式 `extract --force` 才重抽。
- 原文件缺失时 SHALL 保留原行与全文，记 `file_missing`，MUST NOT 删除文档。
- 普通重复扫描 SHALL 仍按内容哈希跳过（既有幂等语义不变）。

#### Scenario: 重解析补齐偏移

- **GIVEN** 一份存量文档 `page_offsets=null` 且原 PDF 仍存在
- **WHEN** 执行 `inbox --force-reparse`
- **THEN** 该行 `page_offsets` SHALL 被补齐
- **AND** 文档行数 SHALL 不变

#### Scenario: 原文件缺失不删行

- **GIVEN** 一份存量文档的原 PDF 已被移动
- **WHEN** 执行 `inbox --force-reparse`
- **THEN** 该行 SHALL 保留，`page_offsets` 保持原值
- **AND** 运行记录 SHALL 含 `file_missing`
