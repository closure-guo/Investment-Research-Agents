# Design: complete-research-report-provenance

## Approach

### 页偏移的表示

`report_documents.text` 仍是拼接后的全文（保持现有消费方不变），新增 `page_offsets` 为 JSON 数组：

```json
[{"page": 1, "start": 0, "end": 3926}, {"page": 2, "start": 3928, "end": 6464}]
```

- `start`/`end` 是字符下标，左闭右开，直接指向 `text`；`end` 不含页间分隔符。
- 拼接规则固定为 `"\n".join(page_texts)`，偏移按同一规则计算；`text_version` 随规则变化递增，旧值不混用。
- 解析失败（加密 PDF、扫描件无文本层）时 `page_offsets=null`，文档仍入库；运行记录 `degraded`，不阻断其他文件。
- 存量文档不回填：`page_offsets` 为 null 即表示「未知」，MUST NOT 用页数均分伪造。

### 抽取的页码

`report_extraction.md` 契约新增可选字段：

- `core_logic` / `catalysts` / `risks` 的每一项从字符串改为可选对象 `{text, source_page}`；为兼容旧抽取，消费端 SHALL 同时接受字符串与对象两种形态。
- `stocks[]` 增加 `source_page`。
- 模型只填原文能定位的页；定位不到置 null。MUST NOT 依据段落顺序猜页。

### 跨源关联

匹配键：`(broker, report_date, normalized_title)`。

- `normalized_title`：Unicode NFKC、去除空白与常见标点、去重连续空格、casefold；不删除中文实词。
- `broker` 优先用抽取的 `broker`，缺失回退 `report_documents.org`；`report_date` 优先抽取，缺失回退 `publish_date`。
- 在 `research_reports` 中按 `(org, publish_date, 同日期窗口 ±1 天)` 取候选，再比归一标题；恰好 1 条命中才写 `report_key`，0 条或多条歧义都留 null。
- v1 只做归一后精确匹配，不做编辑距离模糊匹配：宁可漏关联，不可错关联。
- 关联是弱连接：`report_key` 为空不影响投喂文档的抽取与合并。

### 深度分级

确定性关键词规则，优先级从高到低：

1. 标题含「深度」「专题」「策略」「展望」→ `deep`
2. 标题含「点评」「跟踪」「快评」「更新」→ `comment`
3. 其余 → `unknown`

只落库与展示，不用于过滤文档；v1 不参与任何加权（一致预期仍是东财快照，无法按单篇加权）。

### 评级符号归一

复用现有 `_RATING_MAP`，补机构同义词（优于大市/跑赢行业/推荐/强烈推荐/ Accumulate/Overweight 等）；未识别保留原文，归一结果记 `unknown`。评级归一与 `source_page` 同属抽取层，不改变合并摘要的现有字段。

### 重解析

`inbox --force-reparse`：

- 按 `doc_key`（内容哈希）逐条读取 `file_path`，重算 `page_offsets`、`report_key`、`depth_class` 并更新；不新增行。
- `file_path` 已失效（PDF 被移动/删除）→ 保留原行并记 `error=file_missing`，不删除、不清空全文。
- 重解析不重复调用 LLM；只有显式 `extract --force` 才重抽。

### 迁移

SQLite 用 `PRAGMA table_info` 检查后 `ALTER TABLE ADD COLUMN`，幂等；新列默认 null。`report_documents` 的既有唯一键 `doc_key` 不变。

## Alternatives Considered

- **把每页文本拆成独立行**：否决。现有消费方按单文档读取全文，拆行会破坏接口；偏移数组更小、兼容性更好。
- **用页码均分估算**：否决。扫描件和空白页会让估算严重失真，且违反「不伪造」纪律。
- **标题编辑距离模糊匹配**：否决。v1 误关联的代价高于漏关联；先积累样本再评估。
- **引入 MinerU 解析**：否决（本 delta）。当前券商数字 PDF 用 pypdf 足够（UBS 样例 12 页/57,973 字完整抽出）；扫描件需求另立 delta。

## Risks

- **偏移漂移**：若未来改变拼接规则，旧偏移失效。对策：`text_version` 标记，重解析才更新。
- **同名报告**：同一机构同日多篇标题相近时报文歧义。对策：多条命中留 null，不猜。
- **页码误标**：模型可能把页脚页码当成正文页码。对策：`source_page` 是可选字段，缺失优于错误；抽样验证。
- **存储增长**：页偏移 JSON 很小（每页约 30 字节），可忽略。
