## 1. 数据层

- [x] 1.1 `report_documents` 增列 `page_offsets` / `report_key` / `depth_class`（`PRAGMA table_info` 检查后 ALTER，幂等）
- [x] 1.2 `report_extractions` 增列 `sources_json`（或等价页码字段），保持旧字符串形态兼容
- [x] 1.3 单元测试：迁移幂等、旧库打开不报错、新列默认 null

## 2. 页偏移与抽取页码

- [x] 2.1 `extract_pdf_text` 返回 `(text, page_count, page_offsets)`，拼接规则固定并记 `text_version`
- [x] 2.2 `ingest_inbox` 写入 `page_offsets`；解析失败置 null 且运行记录 degraded
- [x] 2.3 `report_extraction.md` 契约增加 `source_page`（核心逻辑/催化剂/风险/个股），说明「定位不到置 null，不得猜」
- [x] 2.4 `_coerce_extraction` 接受字符串与 `{text, source_page}` 两种形态；越界页码归 null
- [x] 2.5 测试：12 页样例偏移切片正确、扫描件 null、旧抽取兼容、越界页码归一

## 3. 跨源关联

- [x] 3.1 实现标题归一（NFKC/去空白标点/casefold）与 `(broker, report_date ±1d)` 候选查询
- [x] 3.2 恰好 1 条命中写 `report_key`；0 条/多条留 null 并记 `ambiguous_match`
- [x] 3.3 测试：命中、无匹配、歧义、关联失败不阻断入库与合并

## 4. 深度分级与评级归一

- [x] 4.1 实现 `depth_class` 确定性分类（deep/comment/unknown）并落库
- [x] 4.2 扩充评级归一表（中英文同义词），未识别保留原文并记 unknown
- [x] 4.3 测试：深度/点评/未知分类、同义词归一、未知不丢弃、分级不改变合并结果

## 5. 重解析与验收

- [x] 5.1 `inbox --force-reparse`：按 `doc_key` 更新偏移/关联/分级，不新增行、不调 LLM
- [x] 5.2 原文件缺失时保留原行并记 `file_missing`
- [x] 5.3 测试：重解析幂等、补齐偏移、文件缺失不删行
- [x] 5.4 提示词修改后执行 `uv run python scripts/deploy_prompts.py`，确认 `report_extraction` 版本发布
- [x] 5.5 `openspec validate complete-research-report-provenance --strict` 通过
- [x] 5.6 `uv run pytest` 定向测试全绿；`uv run ruff check` 通过
- [x] 5.7 用真实 PDF 跑一轮：确认页偏移、`source_page`、`report_key`、`depth_class` 落库
