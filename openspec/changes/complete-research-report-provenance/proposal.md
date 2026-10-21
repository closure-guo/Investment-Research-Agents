## Why

投喂全文链路已经能用，但三个缺口让它只能「读得到」而不能「查得清」：各页文本被拼成一整段，页边界丢失，抽取出的观点无法标注「第几页」；元数据行（东财）与全文行（投喂）互不相识，同一篇报告多来源到达时无法去重；`report_type` 只有五类，没有「深度/点评」维度，无法按报告质量加权；评级符号未统一，跨机构统计时需要每次临时映射。

## What Changes

- `report_documents` 新增 `page_offsets`（每页起止字符偏移的 JSON）与 `report_key`（与 `research_reports` 关联的期次键，可空）；入库时计算并写入。
- 新增投喂入库的**页偏移解析**：`extract_pdf_text` 返回全文、页数与页偏移；新文档入库即带页码边界。
- 抽取结果新增 `source_page`：核心逻辑/催化剂/风险每条、个股每条可标注来源页；原文没有明确页码时置 null，MUST NOT 猜测。
- 新增**跨源关联**：抽取完成后按 `(broker, report_date, 归一标题)` 与 `research_reports` 匹配，命中写入 `report_key`；未命中留空，不伪造。
- 新增**深度分级**：`depth_class ∈ {deep, comment, unknown}`，按标题关键词确定性分类（「深度/策略/专题」→deep，「点评/跟踪/快评」→comment），落库但不用于丢弃文档。
- 新增**评级符号归一**：将各机构评级统一到买入/增持/中性/减持/卖出/未知，未识别保留原文并记 `unknown`。
- 新增 `inbox --force-reparse`：对已入库文档重算页偏移与 `report_key`，不产生重复行；存量文档不回填时 `page_offsets` 显式为 null。
- 非目标：不引入 OCR/MinerU；不重写历史文档全文；不改变现有合并摘要的语义（v1 不按深度加权）。

## Capabilities

### New Capabilities

（无。）

### Modified Capabilities

- `research-report-dataplane`: 手工投喂全文入库新增页码边界、跨源关联键、深度分级与评级符号归一；现有幂等与个人研究自用边界不变。

## Impact

- 数据：`data/research_reports.db` 的 `report_documents` 增列（`page_offsets`、`report_key`、`depth_class`），`report_extractions` 增列（`source_page` / `sources_json`）。
- 代码：`src/finance_agent/data/research_reports.py`（`extract_pdf_text`、`ingest_inbox`、`_coerce_extraction`、`merge_reports_for_stock` 的展示）、`src/finance_agent/data/research_reports_store.py`（DDL、upsert/get）、`src/finance_agent/prompts/report_extraction.md`（`source_page` 契约）。
- CLI：`inbox --force-reparse`。
- 测试：页偏移正确性、跨源匹配、深度分类、评级归一、重解析幂等。
- 文档：`openspec/specs/research-report-dataplane/spec.md` archive 时合并；无前端变更。
