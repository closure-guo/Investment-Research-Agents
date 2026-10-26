# 人工验证报告: add-research-report-ingest

日期：2026-10-25 ｜ 分支：feat/research-report-ingest ｜ worktree：.worktrees/research-ingest

## 交付面

1. **共享入库服务** `ingest_research_document`（`data/research_reports.py`）：内容哈希幂等、PDF/文本分流（UTF-8 优先 GBK 兜底）、元数据覆盖、落盘投喂目录（冲突加短哈希后缀）、抽取联动跨源关联、失败如实披露。`ingest_inbox` 目录扫描重构为逐文件复用该函数，回执与降级语义不变。
2. **HTTP 端点** `POST /api/research/reports/ingest`（multipart，30MB/后缀白名单/`extract` 快速路径）+ `GET /api/research/reports/documents`（列表不含正文）。
3. **agent 写工具** `ingest_research_report`（快速模式第 14 个系统操作工具）：两轮确认协议、空文本拒绝、幂等回执、`operator_write` 审计；确认绑定含 `text_sha1`，两次调用正文被改动会被拒绝。
4. **前端** `/research` 研报投喂页：上传 + 元数据表单 + 三态回执 + 最近文档列表（不含正文）；路由/侧边栏折叠图标/命令面板（⌘K）三处入口。

## 自动化证据

| 门禁 | 结果 |
|---|---|
| 共享服务单测（`tests/data/test_research_report_ingest.py`） | 11 passed |
| 端点单测（`tests/test_research_report_ingest_api.py`，含 documents 读取） | 7 passed |
| 写工具单测（`tests/test_operator_writes.py` 追加 6 项） | 17 passed（全文件） |
| 注册表/工厂/门禁/确认协议回归 | 32 passed |
| 目录扫描重构回归（collectors + provenance） | 随汇总跑 |
| 定向汇总（11 个测试文件） | **106 passed** |
| ruff check + ruff format --check | All checks passed |
| mypy | 98 errors = main 基线（非门禁，零新增） |
| 前端 vitest 全量 | **952 passed**（889 存量 + 63） |
| 前端 tsc -b | PASS |
| E2E（playwright.ingest.config.ts，真实前后端 + 隔离库） | **1 passed (7.9s)**：空态→上传 ingested+解析降级披露→列表出现→重复上传 skipped 幂等 |

## 实现口径与偏差记录

- **目录扫描落库标题从原始文件名 stem 改为 `clean_doc_title` 清洗结果**（去机构前缀/尾日期）：重构统一走共享函数的顺带正向修正；既有 collectors 测试全绿，跨源关联逻辑输入不变。
- **新增依赖 `python-multipart`**（FastAPI multipart 收件必需），已入 pyproject + uv.lock；容器镜像需随合并重建（`--build`）。
- **E2E 用无文本层 PDF**：`extract_document` 走真实 LLM 网关且无 TESTING 桩，为保 E2E 确定性选择解析降级路径（不触发 LLM）；正常路径由单测覆盖。
- **quick_mode 提示词已发布 Langfuse `staging`**（imported 1）；`production` 标签随本变更合并部署后移动——容器尚无该工具时先移 production 会导致模型空调用。
- **生产部署注意**：后端重建前照例查 running 会话；`data/` 卷挂载保证投喂落盘可见。

## owner 主观抽验清单（部署后）

- [ ] /research 页上传一份真实 PDF：回执含字数/深度分级，列表出现该文档
- [ ] 快速模式粘贴一段研报正文说「把这份研报入库」：预览 → 回复「确认执行」→ 回执 ingested；再发同样内容 → 回执 skipped
- [ ] 把任意 PDF 放进 `data/research_report_inbox` 等日批：确认与直投不产生重复行（哈希幂等）

## 浏览器人工端到端（2026-10-25 20:10–20:30，真实 LLM）

隔离栈（worktree 后端 8008 / 前端 5181，独立 DB + 独立投喂目录，真实 deepseek-v4.1-flash，生产栈未触碰）：

| 场景 | 结果 |
|---|---|
| ① /research 页上传（DataTransfer 注入 .txt，等价 setInputFiles） | ✅ 回执「入库成功 doc_key e75130e9… 244 字 comment」，列表刷新含「贵州茅台三季报点评/国信证券/2026-10-24」；截图视觉核验通过（暗色/布局/三态齐全） |
| ② 快速模式对话投喂（粘贴正文「把这份研报入库」） | ✅ 预览诚实（68 字/标题/「机构与发布日期：你未提供，暂缺」/去重说明）→ 回复确认词 → 回执 ingested doc_key 3acf4a7e… |
| ③ 同内容再次投喂 | ✅ 回执 skipped（doc_key 与首次一致，模型如实解释内容哈希去重） |
| ④ 直投 ↔ 目录扫描幂等互斥 | ✅ 直投 PDF → 扫描 skipped=1 → 再直投 skipped（txt 不在扫描范围，天然互斥） |

要点与观察：

- **跨轮确认按设计生效**：新 agent run 的 chat_history 不回放工具调用，模型不记得上轮已发预览；服务端按 session+action+params（含 text_sha1）匹配预览，confirm=true 直接通过——单测 `test_confirmation_survives_new_agent_run` 的真实 LLM 版复现。
- 真实 LLM 抽取落库正常（broker/日期/类型），但对短合成文本 `stocks=null`——既有抽取面质量观察项，与本变更无关，登记为候选 issue。
- 空投喂目录首启显示「暂无投喂文档」空态正确；测试后临时栈已清理（8008/5181 已释放），生产 5173 全程未受影响。
