# Delta for Report Export

## ADDED Requirements

### Requirement: 导出排版保真（列表/内联样式/HTML 折叠降级）

导出解析器 SHALL 识别报告 Markdown 中的列表块、行内样式标记与 HTML 折叠标签，PDF/DOCX/PPTX 三格式渲染 MUST 不出现 Markdown/HTML 字面符号：

- **列表块**：连续的 `- `/`* `/`+ `/有序 `N. ` 行 MUST 解析为列表结构逐项渲染（PDF 项目符号列表、DOCX List Bullet、PPTX 缩进条目），MUST NOT 拼平为单一段落；
- **行内样式**：`**加粗**`/`*斜体*` 标记 MUST NOT 以字面星号出现在任何格式的成品文本中——支持富文本的渲染路径（PDF HTML、DOCX run）按样式渲染，纯文本路径（PPTX 文本框）去除标记保留内容；
- **HTML 折叠降级**：`<details>`/`</details>`/`</summary>` 包装行 MUST 丢弃；`<summary>T</summary>` MUST 降级为标题行（文字保留）；`<br>` 及变体 MUST 转为换行，HTML 标签字面 MUST NOT 出现在成品正文。

行内标记的解析语义：解析器 SHALL 保留 Section 文本中的行内标记原文，并提供统一的纯文本归一工具（去除 `**`/`*` 标记）供无富文本能力的渲染路径使用——渲染器按自身能力选择，不要求解析器预先篡改文本。

#### Scenario: 列表项逐条渲染不拍平

- **GIVEN** 报告 Markdown 含连续列表行（如 `- **方向**: watch` 与 `- **置信度**: 55%`）
- **WHEN** 导出为 PDF/DOCX/PPTX 任一格式
- **THEN** 每个列表项独立成条（PDF 为项目符号列表条目、DOCX 为 List Bullet 段落、PPTX 为缩进条目）
- **AND** 不同列表项不出现在同一段落文本中

#### Scenario: 行内加粗星号不残留

- **GIVEN** 报告 Markdown 段落或列表项含 `**加粗**` 与 `*斜体*` 标记
- **WHEN** 导出为 PDF
- **THEN** 成品正文无 `**`/`*` 字面星号，加粗内容以 `<strong>` 富文本呈现
- **WHEN** 导出为 DOCX
- **THEN** 加粗内容以 bold run 呈现、斜体以 italic run 呈现
- **WHEN** 导出为 PPTX
- **THEN** 文本框内容为去除标记后的纯文本，无星号残留

#### Scenario: HTML 折叠标签降级

- **GIVEN** 报告 Markdown 含 `<details><summary>点击展开</summary>` 正文 `</details>` 块（含 `<br>` 变体）
- **WHEN** 导出为任一格式
- **THEN** `<details>`/`</details>`/`</summary>` 标签不出现于成品
- **AND** summary 文字以标题行形式保留
- **AND** `<br>` 位置成为换行/条目边界而非字面标签

#### Scenario: 解析器保留标记由渲染器选择处理方式

- **GIVEN** 含 `**加粗**` 的段落被 `parse_markdown` 解析
- **WHEN** 读取对应 Section 的文本
- **THEN** 行内标记原文保留（未被解析器删除）
- **AND** 归一工具对同一文本返回去除 `**`/`*` 标记的纯文本
