# Proposal: add-research-report-ingest

## Why

研报全文入库目前只有「投喂目录扫描」一条路：用户必须手动把 PDF 放进 `data/research_report_inbox`，等下一次 16:52 日批才入库。前端没有任何投喂入口，快速模式的系统操作工具也覆盖不到这一步。补一个直投入库接口 + 前端投喂页 + 快速模式写工具，把人工投喂从目录约定升级为随时可用的一等入口（本次浏览器 E2E 抽验同时确认：研报库连读取面都没有，本变更顺带给投喂文档一个最小的查看面）。

## What Changes

- 新增共享入库服务 `ingest_research_document(filename, data, ...)`：单文档直投入库，复用既有内容哈希幂等、PDF/文本解析、跨源关联与 LLM 结构化抽取；`ingest_inbox` 目录扫描重构为复用同一函数（行为不变）。
- 新增 HTTP 端点 `POST /api/research/reports/ingest`：multipart 文件上传（pdf/txt/md），可选 `org` / `publish_date` / `title` 覆盖文件名推断，同步返回回执（`doc_key` / `status` ingested|skipped / `char_count` / `report_key` / `depth_class` / 抽取错误如实披露）。
- 新增前端「研报投喂」页（`/research`）：文件上传 + 元数据表单 + 最近投喂文档列表（复用 `list_documents`，不含正文字段），成功/重复/失败三态回执。
- 新增快速模式写工具 `ingest_research_report`：对话内文本投喂，走既有两轮确认协议与 `operator_write` 审计。
- `quick_mode.md` 增加投喂政策；提示词随变更发布（staging；production 标签随本变更合并部署后移动，避免容器尚无该工具时模型空调）。

**明确不做**：不自动下载全文（合规边界不变）；不做 URL 抓取投喂；不在入库后自动触发关联标的分析（`trigger-analysis` 仍独立使用）。

## Capabilities

- **New Capabilities**: `research-report-ingest`（共享入库服务、HTTP 端点、前端投喂页、agent 写工具四组契约）
- **Modified Capabilities**: 无（`research-report-dataplane` 既有「手工投喂与全文入库」要求仍然成立且不被修改；`quick-mode-operator` 尚未归档入主规范库，其写工具清单在本变更归档时由归档协调合并——见 design.md 风险节）

## Impact

- 后端：`src/finance_agent/data/research_reports.py`（共享入库函数 + `ingest_inbox` 重构复用）、`src/finance_agent/api.py`（新端点）、`src/finance_agent/operator/writes.py` + `tool_registry.py` + `agent_factory.py`（新工具，13→14）
- 提示词：`src/finance_agent/prompts/quick_mode.md`（staging 发布）
- 前端：`frontend/src/pages/research/`（新页 + 路由 + 侧边栏入口）
- 测试：后端单测（服务幂等 / 端点契约 / 工具确认协议）、前端 vitest、E2E（上传→回执→列表）
- 归档协调点：`add-quick-mode-operator`（active）的 `QUICK_OPERATOR_TOOLS` 清单需并入本变更新增的工具名
