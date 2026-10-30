# Design: add-research-report-ingest

## Approach

**共享服务层**：`src/finance_agent/data/research_reports.py` 新增 `ingest_research_document(filename, data, *, db_path=None, extract=True, overrides=None)`，把 `ingest_inbox` 单文件循环体中的「哈希幂等 → 解析（PDF/文本）→ 元数据推断/覆盖 → upsert → 抽取联动跨源关联」抽为一个函数；`ingest_inbox` 重构为逐文件调用它，目录扫描回执（scanned/ingested/skipped/errors）语义不变，由既有测试回归兜底。PDF 与文本的分流在服务内按后缀判断：`.pdf` 走 `extract_pdf_text`（解析降级语义与目录扫描一致：失败仍入库、正文空、`parse_error` 披露），`.txt`/`.md` 按 UTF-8 优先、GBK 兜底解码，解码失败抛 `ValueError` 归一为 400。

**HTTP 端点**：`api.py` 中 `trigger-analysis` 旁新增 `POST /api/research/reports/ingest`，FastAPI `UploadFile` 收件；大小校验在读取流时进行（>30MB 直接 413，不落盘）；整条入库经 `asyncio.to_thread` 执行（pypdf 解析与 LLM 抽取都是阻塞调用）。`extract` 作为表单字段（默认 `true`）暴露，供前端/E2E 快速路径跳过 LLM。上传文件落盘到投喂目录（`RESEARCH_REPORT_INBOX_DIR`，缺省 `data/research_report_inbox`），命名冲突时追加短哈希后缀——落盘后目录扫描批次的哈希幂等会自动跳过，不产生双写。

**agent 写工具**：`operator/writes.py` 的 `make_write_tools` 增加 `ingest_research_report`，完全复用 `_preview/_success/_failure/_confirmed` 确认协议骨架与 `_audit` 审计；工具内部经线程池调用共享服务（`extract=True`）。参数里不收 URL（契约禁止网络抓取）。测试经 `RESEARCH_REPORTS_DB_PATH` / `RESEARCH_REPORT_INBOX_DIR` 环境变量隔离（两处均为调用期读取，沿用 store 既有纪律）。

**前端**：新增 `frontend/src/pages/research/ResearchIngestPage.tsx`，复用设计系统 PageShell + StatusBadge；三态回执（ingested/skipped/错误）与最近文档列表（`list_documents` 已有形态，不含正文）。路由与侧边栏入口按 `ui-wiring` 既有页面注册方式接入。

**提示词**：`quick_mode.md` 增加一条投喂政策（何时用该工具、确认协议提示、不收 URL）；经 `deploy_prompts.py --labels staging` 发布，production 标签随本变更合并部署后移动（避免容器尚无该工具时模型空调用）。

## Alternatives Considered

- 方案 A（不选）：HTTP 端点只把文件写进投喂目录、立即返回「待日批入库」回执。省去同步抽取，但入库延迟到 16:52 批次，回执无法含 doc_key/char_count，前端三态回执与 agent 工具回执都不成立。
- 方案 B（不选）：工具/端点各自实现入库逻辑。重复实现哈希幂等与解析降级，两处漂移风险高；共享服务是本项目日批面（收割/对账）已验证的模式。
- 方案 C（不选）：把端点做成异步任务 + 轮询。个人自用规模下单文档入库秒级可完成，异步态引入的任务表/状态机与收益不成比例。

## Risks

- **LLM 抽取在请求路径内同步执行**（可能数秒~数十秒）：`extract` 表单字段提供快速路径；抽取失败有既有兜底（不阻断、如实披露）。E2E 用无文本层 PDF 或 `extract=false` 避免依赖真实 LLM。
- **重构 `ingest_inbox` 引入行为漂移**：先跑既有 `ingest_inbox` 测试基线，重构后回归；回执字段逐项对齐。
- **跨 active change 的工具清单冲突**：`add-quick-mode-operator`（active）持有 `QUICK_OPERATOR_TOOLS` 的能力 delta，本变更新工具名需要在两者归档时合并。对策：本变更 proposal 显式登记协调点；单一来源测试（tool 名单）在本变更内更新为 14 项，若 add-quick-mode-operator 后归档则由其归档时重写 MODIFIED 块。
- **容器内投喂目录可见性**：API/工具落盘依赖 `data/` 卷挂载（与日批同路径）；若部署形态变化导致目录不可写，端点 500 摘要如实披露，不静默丢文件。
