# research-report-ingest Specification (Delta)

## ADDED Requirements

### Requirement: 共享直投入库服务

系统 SHALL 提供 `ingest_research_document(filename, data, *, db_path=None, extract=True, overrides=None)` 共享入库函数：按文件内容 sha1 幂等（重复入库返回 `skipped` 且 MUST NOT 覆盖首次入库时间）；PDF 字节经既有 PDF 解析路径抽取全文，纯文本字节（txt/md）按 UTF-8 优先、GBK 兜底解码；`overrides` 中的 `org` / `publish_date` / `title`（显式提供时）SHALL 优先于文件名推断；落 `report_documents` 表（`usage_policy=personal_research`、`source_type=manual_feed`）并执行跨源关联与深度分级；`extract=True` 且正文非空时同步执行 LLM 结构化抽取，抽取失败 SHALL 只在回执中如实披露、MUST NOT 阻断入库。回执 SHALL 为结构化 dict：`status`（`ingested` | `skipped`）、`doc_key`、`filename`、`char_count`、`page_count`、`report_key`、`depth_class`、`parse_error`（可空）。

#### Scenario: PDF 直投正常入库

- GIVEN 一份未入库的 PDF 文件字节（文件名含机构与日期）
- WHEN 调用 `ingest_research_document`
- THEN 回执 SHALL 为 `status=ingested` 且含 `doc_key`、`char_count>0`、`page_count`
- AND `report_documents` SHALL 新增一行，含全文与页偏移

#### Scenario: 重复直投幂等

- GIVEN 同一内容的文档已入库
- WHEN 再次以相同字节调用
- THEN 回执 SHALL 为 `status=skipped` 且 `doc_key` 与首次一致
- AND 文档表行数不变，首次入库时间不变

#### Scenario: 纯文本直投

- GIVEN 一段 UTF-8 编码的研报正文字节（文件名后缀 .txt 或 .md）
- WHEN 调用 `ingest_research_document`
- THEN 全文 SHALL 原样入库，`page_count=1`
- AND `char_count` SHALL 等于解码后字符数

#### Scenario: 显式覆盖元数据

- GIVEN 文件名不含机构与日期约定
- WHEN 调用时显式提供 `overrides={"org": "某券商", "publish_date": "2026-10-24"}`
- THEN 入库行的 `org` / `publish_date` SHALL 为显式值而非文件名推断值

#### Scenario: 抽取失败不阻断

- GIVEN LLM 结构化抽取抛出异常
- WHEN 调用 `ingest_research_document`（`extract=True`）
- THEN 文档 SHALL 正常入库（`status=ingested`）
- AND 回执 SHALL 如实携带抽取失败信息，MUST NOT 以伪造成功的抽取结果冒充

### Requirement: 直投入库 HTTP 端点

系统 SHALL 提供 `POST /api/research/reports/ingest`（multipart/form-data）：`file` 字段必填（后缀限 pdf/txt/md），可选表单字段 `org` / `publish_date` / `title` 覆盖元数据推断。文件字节上限 SHALL 为 30MB（超限 413）；不支持的后缀 SHALL 返回 400 并说明支持类型。端点 SHALL 经线程池执行入库（MUST NOT 阻塞事件循环），同步返回结构化回执（同共享服务回执外加 `filename`）；服务层异常 SHALL 归一为 500 并附错误摘要。本端点为人工投喂入口，MUST NOT 自动下载全文。

#### Scenario: 上传 PDF 成功

- GIVEN 前端选择一份未入库的 PDF
- WHEN POST multipart 上传
- THEN 响应 SHALL 为 200 且 `status=ingested`
- AND 前端可立即在最近投喂文档列表中看到该文档（不含正文）

#### Scenario: 重复上传返回 skipped

- GIVEN 同一内容文档已入库
- WHEN 再次上传同一文件
- THEN 响应 SHALL 为 200 且 `status=skipped`，MUST NOT 产生第二行

#### Scenario: 类型与大小校验

- GIVEN 上传文件后缀为 .exe 或体积超过 30MB
- WHEN POST 上传
- THEN 响应 SHALL 分别为 400（说明支持类型）与 413，MUST NOT 落库

#### Scenario: 解析失败仍入库并披露

- GIVEN 上传一个无文本层/损坏的 PDF
- WHEN POST 上传
- THEN 响应 SHALL 为 200 且 `status=ingested`、`parse_error` 非空
- AND 文档 SHALL 以空正文入库（与目录扫描的降级语义一致）

### Requirement: 前端研报投喂页

前端 SHALL 提供 `/research` 路由「研报投喂」页，含：文件上传控件（选择 PDF/txt/md）、可选元数据表单（机构 / 发布日期 / 标题）、提交按钮与回执区；页内 SHALL 展示最近投喂文档列表（`GET` 侧复用既有 `list_documents` 形态，不含正文字段），列表空态 SHALL 显式区分「暂无投喂文档」。提交后 SHALL 依后端回执渲染三态：`ingested` 成功回执（含 doc_key/字数/深度分级）、`skipped` 幂等提示（含首次入库 doc_key）、HTTP 错误显式报错文案。回执与列表 MUST NOT 展示文档正文。

#### Scenario: 上传成功三态之一

- GIVEN 用户在 /research 页选择一份未入库 PDF 并提交
- WHEN 后端返回 `status=ingested`
- THEN 页面 SHALL 渲染成功回执
- AND 最近文档列表 SHALL 刷新并包含该文档

#### Scenario: 重复投喂提示

- GIVEN 用户上传一份已入库文件
- WHEN 后端返回 `status=skipped`
- THEN 页面 SHALL 显示幂等提示，MUST NOT 误报为失败

#### Scenario: 上传失败显式报错

- GIVEN 后端返回 4xx/5xx
- WHEN 提交完成
- THEN 页面 SHALL 展示显式错误文案（含状态码或原因），MUST NOT 静默无反馈

### Requirement: 投喂文档读取端点

系统 SHALL 提供 `GET /api/research/reports/documents?limit=N`（缺省 20，上限 100）：按入库时间倒序返回投喂文档列表，字段与既有 `list_documents` 形态一致（doc_key/filename/title/org/publish_date/page_count/char_count/report_key/depth_class/usage_policy/source_type/ingested_at），MUST NOT 含正文（text）与页偏移（page_offsets）字段；空库返回 `{documents: []}` 而非 404。

#### Scenario: 列表不含正文

- GIVEN 库中已有已投喂文档
- WHEN GET documents
- THEN 每个响应项 SHALL 不含 `text` 与 `page_offsets` 字段
- AND 列表 SHALL 按入库时间倒序

### Requirement: 快速模式研报投喂写工具

快速模式 SHALL 注册写工具 `ingest_research_report`（加入 `QUICK_OPERATOR_TOOLS` 与写工具分类）：参数为 `text`（必填非空）、可选 `title` / `org` / `publish_date` / `filename`。工具 SHALL 复用既有两轮确认协议：第一次调用（`confirm=false`）只返回预览（将入库的标题/机构/日期/字符数与幂等提示）与一次性 token，不执行入库；用户确认后 `confirm=true` 执行，回执含 `status`（ingested|skipped）与 `doc_key`；`text` 为空或纯空白 SHALL 拒绝并说明。执行 SHALL 记 `operator_write` 审计日志。本工具 MUST NOT 接受 URL 参数（不做网络抓取）。

#### Scenario: 预览无副作用

- GIVEN 用户在快速模式说「把这份研报存入系统」并粘贴正文
- WHEN 模型首次调用 `ingest_research_report`（confirm=false）
- THEN 只返回预览与确认指引，`report_documents` MUST NOT 新增行

#### Scenario: 确认后入库

- GIVEN 预览已返回且用户回复确认词
- WHEN 模型以 confirm=true 再次调用
- THEN 文档 SHALL 入库且回执为 `status=ingested` 含 `doc_key`
- AND `operator_write` 审计 SHALL 记录本次执行

#### Scenario: 重复投喂回执

- GIVEN 相同内容的文档已入库
- WHEN confirm=true 执行
- THEN 回执 SHALL 为 `status=skipped` 并说明幂等语义

#### Scenario: 空文本拒绝

- GIVEN `text` 为空字符串或纯空白
- WHEN 调用工具
- THEN SHALL 拒绝并提示需要正文内容，MUST NOT 进入确认协议
