# Tasks: update-export-typography

## 1. parser 列表块与 HTML 降级

- [x] 1.1 `parse_markdown` 主循环在 heading/image/table 判定前加列表识别：`- `/`* `/`+ `/有序 `N. `（含多级缩进条目按原文缩进层级记录）连续行聚合为 `Section(type="list", items=[...])`，`items` 保留行内标记原文，条目前缀标记剥除
- [x] 1.2 HTML 降级预处理（主循环前的行级归一）：`<details>`/`</details>`/`</summary>` 行删除；`<summary>T</summary>` 行改写为 `### T`；`<br>`/`<br/>`/`<br />` 替换为换行
- [x] 1.3 `Section` 增加可选 `items: list[str]` 字段与 `level` 复用（列表缩进层级）；`type` 注释更新
- [x] 1.4 `strip_md_inline(text) -> str`：去 `**bold**`/`*italic*` 标记（不成对时保守保留原文），供纯文本渲染路径使用

## 2. 三渲染器接线

- [x] 2.1 pdf_exporter `_sections_to_html`：list → `<ul><li>`（有序列表 `N. ` → `<ol>`）；新增 `_inline_to_html`（escape 后 `**`→`<strong>`、`*`→`<em>`），paragraph 与列表项统一走它
- [x] 2.2 docx_exporter：list → 逐项 `_add_paragraph`（富文本 helper 复用）+ `List Bullet` 样式；有序列表用 `List Number`
- [x] 2.3 pptx_exporter：list → 逐项 add_paragraph（`level=1` 缩进）；paragraph 与列表项文本经 `strip_md_inline`
- [x] 2.4 全渲染器 paragraph 路径不回归：既有 heading/table/image/separator 行为逐字节不变（test_export 套件守护）

## 3. 测试（先红后绿）

- [x] 3.1 test_parser：连续列表聚合为单 list Section（items 数与文本断言、行内标记保留）；有序/无序/混合；HTML details/summary/br 降级（wrapper 消失、summary→heading level3、br→换行）；strip_md_inline（成对剥除/不成对保留）
- [x] 3.2 test_pdf/docx/pptx 渲染断言：pdf HTML 含 `<ul><li>` 与 `<strong>` 且无 `**` 字面；docx 段落 style 含 List Bullet 且 run bold；pptx 文本无 `**` 且条目 level=1
- [x] 3.3 回归：现有 test_parser/test_service/test_export_api 全绿

## 4. 验证

- [x] 4.1 定向测试（tests/export/）+ 全量套件 + ruff 绿
- [x] 4.2 真报告冒烟：从本地 sessions.db 取含列表/details 的真实 report_markdown，worktree 代码直调 markdown_to_pdf/_sections_to_html 产出 PDF，抽验列表条目与星号清除
