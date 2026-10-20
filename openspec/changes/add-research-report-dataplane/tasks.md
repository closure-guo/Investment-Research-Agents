# Tasks: add-research-report-dataplane

> 粗粒度验收项；TDD 细步由实施计划（writing-plans）拆解，做完即弃。

## 1. 修复与契约测试（bug 先行，意图不变部分）

- [x] 1.1 `fetch_research_reports` 列名修复：机构读「机构」、发布日期读「日期」；移除恒空目标价占位；新增近一月个股研报数与可得财年 EPS 预测字段（TDD：先写失败测试复现 org/date 恒空）
- [x] 1.2 真实快照契约测试：fixture 取自真实接口响应（仓库内 JSON、标注采集日期），断言列名与字段映射；人为改列名测试必红
- [x] 1.3 `_build_fundamental_context` 研报段字段面对齐（标题/机构/评级/发布日期），消费侧对目标价占位的引用同步清理

## 2. 数据面存储（四表 + 幂等）

- [x] 2.1 `research_reports_store.py`：四表 schema（主表/评级变动/盈利预测/分析师）+ 期次键唯一索引 + upsert 幂等（TDD：同批二次导入行数不变、跨源合并计源）
- [x] 2.2 `RESEARCH_REPORTS_DB_PATH` 环境变量接线（缺省 data/research_reports.db）+ .env.example 登记
- [x] 2.3 测试/E2E 全配置显式隔离该库路径（pytest 各 config、E2E playwright 配置、eval-ops 专属 config——incident 038 教训清单逐一核对）

## 3. 采集器（免费主干）

- [x] 3.1 东财限流模块：串行 + ≥1s+抖动 + 会话复用 + UA/Referer（单测验证间隔与单会话）
- [x] 3.2 五类研报采集器：/report/list qType=0（全市场按窗）/ qType=1 + /report/jg qType=2/3/4，分页拉全、published_at 必带、空窗记 ok（TDD：mock 响应分页/空窗/失败三态）
- [x] 3.3 巨潮评级变动采集（akshare `stock_rank_forecast_cninfo`），事件唯一键落库幂等
- [x] 3.4 东财全市场盈利预测快照采集（研报数+五档评级分布+三年 EPS），快照日期+标的键
- [x] 3.5 同花顺单股明细按需拉取 + 1 天 TTL 缓存 + 失败降级缺失标注
- [x] 3.6 新浪第二源：四类（公司/行业/策略/宏观，晨报无独立分类）当日计数抓取，用于交叉与东财失败降级记录
- [x] 3.7 采集器契约测试：reportapi/巨潮/预期接口各一份真实快照 fixture 断言列名

## 4. 日批与调度

- [x] 4.1 三个日批 job（研报元数据/评级变动/预期快照）注册 start_scheduler，注册面镜像守护测试扩容（#376 模式）
- [x] 4.2 运行记录：每次运行写 run 状态（ok/空窗/failed/degraded+新浪计数），失败不阻断兄弟 job
- [x] 4.3 分析师档案聚合随研报 job 更新（覆盖标的数/近90日报告数/评级分布）
- [x] 4.4 历史回填入口：`--backfill 2024-01-01` 按月分窗复用采集函数，幂等可重跑
- [x] 4.5 实跑验证：本地跑一轮日批 + 一次回填抽验（抽查 3 标的主表行与巨潮当日事件数），读数记入验证报告（实跑读数见 tests/validation/2026-10-20-add-research-report-dataplane-validation.md）

## 5. 消费侧接线

- [x] 5.1 覆盖状态查询函数：三态（无覆盖/断更/失败）+ 最新研报日期/近30日条数（TDD：600845 型断更样例返回断更态）
- [x] 5.2 `_build_fundamental_context` 一致预期段：快照优先→按需直拉→缺失标注；无覆盖显式「无分析师覆盖」禁零值占位
- [x] 5.3 `fundamental_analyst.md` prompt 扩条（一致预期消费+防锚定），权威源修改后跑 `deploy_prompts.py` 发布（eval 门禁依赖）（2026-10-20 已发布，复跑预检显示与 Langfuse production 内容一致）

## 6. 验证收口

- [x] 6.1 `uv run pytest` 全绿（含既有测试无修改通过）（6338 passed；3 failed + 25 errors 均为 test_deploy_script/test_prompt_history 的 git push 环境基线红，主检出同复现）
- [x] 6.2 `uv run ruff check` + `uv run mypy` 通过
- [x] 6.3 人工验证报告落 `tests/validation/`：一轮实跑日批读数（五类行数/评级事件数/预期快照行数）+ 一只标的的分析报告对照（一致预期段呈现、无覆盖标的呈现）
- [x] 6.4 E2E 套件回归：40 passed；daily-ops 任务数 20→24 已修（验证 4 个新 job 渲染）；其余 4 个失败中 3 个单独复跑通过（并发 flake）、1 个主检出同样复现（既有问题）

## 7. 手工投喂与全文入库（个人研究自用）

- [x] 7.1 `report_documents` 表 + 存取函数（TDD：列表不含全文、详情含全文；重复键写入 no-op）
- [x] 7.2 投喂扫描：目录遍历 / pypdf 全文与页数 / 文件名机构与日期推断 / 文件内容哈希幂等 / 解析失败降级（TDD）
- [x] 7.3 投喂 job 注册（16:58）+ 调度注册面镜像计数更新
- [x] 7.4 实跑：投喂一份真实 PDF（页数 / 字符数 / 重复投喂幂等复查），读数补入验证报告
- [x] 7.5 `.gitignore`：投喂目录与 E2E 隔离库不入库

## 8. 投喂文档 LLM 抽取与按标的合并

- [x] 8.1 `report_extractions` 表 + 存取（TDD：重抽覆盖、列表读取）
- [x] 8.2 LLM 抽取：`report_extraction` purpose + prompt 合同 + 确定性评级归一化 + 输出预算（TDD：抽取落库/已有不重复/失败降级/看空→卖出）
- [x] 8.3 按标的合并 + 分析师上下文注入（TDD：多篇聚合、无记录不注入）
- [x] 8.4 实跑：两份真实 PDF 抽取 + 合并读数（目标价/评级分布）补入验证报告
- [x] 8.5 prompt 发布：`report_extraction.md` 与 `fundamental_analyst.md` 经 `deploy_prompts.py` 发布（2026-10-20 已发布；同批 risk_judge/trader 因 Langfuse 侧存量不一致被预检排除，与本 delta 无关）
