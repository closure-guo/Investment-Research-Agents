# Tasks: add-research-report-ingest

## 共享服务与日批复用

- [x] `ingest_research_document(filename, data, ...)`：PDF/文本分流、sha1 幂等、overrides 覆盖、抽取联动、结构化回执（单测：PDF 直投 / 文本直投 / 幂等 skipped / 覆盖元数据 / 抽取失败披露）
- [x] `ingest_inbox` 重构为逐文件调用共享函数，目录扫描回执与降级语义不变（既有测试回归 + 补一条冲突文件名用例）

## HTTP 端点

- [x] `POST /api/research/reports/ingest`：multipart 收件、后缀白名单 400、30MB 上限 413、`asyncio.to_thread`、`extract` 表单字段、回执契约（单测：成功 / 幂等 / 类型拒绝 / 超限 / 解析失败披露）

## agent 写工具

- [x] `ingest_research_report`（`operator/writes.py`）：两轮确认协议、空文本拒绝、幂等回执、`operator_write` 审计（单测：预览无副作用 / 确认入库 / 重复 skipped / 空文本拒绝 / URL 参数不存在）
- [x] `tool_registry.py` 注册 `TOOL_INGEST_RESEARCH_REPORT` 并入 `WRITE_TOOLS` / `QUICK_OPERATOR_TOOLS`；工具名单一来源测试更新为 14 项
- [x] `agent_factory.py` 快速模式注册（经 `make_write_tools`）；`quick_mode.md` 投喂政策 + staging 发布（deploy_prompts.py --labels staging，production 随合并部署）

## 前端

- [x] `/research` 页：上传控件 + 元数据表单 + 三态回执 + 最近文档列表（vitest：成功 / skipped / 错误 / 空态；`tsc -b` 绿）
- [x] 路由与侧边栏入口注册；不展示文档正文

## 验证与收口

- [x] 定向 pytest 全绿（服务/端点/工具）+ `ruff` + `mypy`
- [x] E2E 门禁：上传→回执→列表 spec 通过（真实浏览器，不 mock 被测系统；LLM 抽取以 extract=false 或无文本层 fixture 规避）
- [x] 人工验证报告落 `tests/validation/`
- [x] `openspec validate add-research-report-ingest --strict` 通过
