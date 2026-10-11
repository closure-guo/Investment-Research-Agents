# Proposal: update-export-typography

## Why

issue #239 缺陷 B（2026-10-06 十份历史 PDF 逐页质检发现）：`export/parser.py` 只处理 heading/paragraph/table/separator/image 五种块，导致 PDF/DOCX/PPTX 三格式共用输出「表达层失真」：

1. **列表拍平**：`- **方向**: watch - **置信度**: 55%` 多个列表项被拼成一行段落（10 份 PDF 全部存在，单份 `**` 残留 30+ 处）；
2. **内联样式字面输出**：`**加粗**`/`*斜体*` 星号直接印进成品（pdf/pptx 无内联处理；docx 已有富文本 helper 但列表拍平后同样受损）；
3. **HTML 折叠标签字面输出**：`<details><summary>点击展开…</summary>` 原始标签印进正文（600519 10-05 报告 p04/p07-p09 实证）。

内容可读、不阻断使用，但成品观感与正文规范度受损。

## What Changes

- **parser 列表块**：连续 `- `/`* `/`+ `/`N. ` 行聚合为新 `list` Section（`items` 保留行内标记），不再拼进段落；
- **parser HTML 降级预处理**：`<details>`/`</details>`/`</summary>` 行删除；`<summary>T</summary>` 降级为三级标题行；`<br>` 变体替换为换行；
- **parser 内联工具**：`strip_md_inline()` 提供「去 `**`/`*` 标记」的纯文本归一；解析器不篡改 `text`（保留标记），渲染器按自身能力选择富文本或纯文本；
- **三渲染器接线**：pdf（HTML 路径）列表渲染 `<ul>/<li>`、内联 `**bold**`→`<strong>`、`*italic*`→`<em>`（escape 后转换）；docx 列表项走既有 `_add_paragraph` 富文本 + `List Bullet` 样式；pptx 列表项缩进渲染、paragraph/list 文本经 `strip_md_inline` 去星号；
- **范围外**：#239 缺陷 A（图表源文件仅存容器 /tmp，重导出静默丢图）涉及存储/卷设计与容器重建运维红线（须查 /api/sessions 无 running 会话），留 owner 裁决后另行立项，本 change 不动。

## Capabilities

- `report-export`：ADDED Requirement「导出排版保真」（列表/内联/HTML 折叠降级三场景 + 三格式一致）。

## Impact

- `src/finance_agent/export/parser.py`、`pdf_exporter.py`、`docx_exporter.py`、`pptx_exporter.py`
- `tests/export/test_parser.py` + 三渲染器测试（新增断言）
- 非交互类变更（无 UI/SSE/状态流转），免 E2E 门禁；不涉 prompt，免 deploy
- 风险：parse_markdown 消费方仅三渲染器与 tests（grep 实证），Section 新增 type/字段向后兼容（旧 type 行为不变）
