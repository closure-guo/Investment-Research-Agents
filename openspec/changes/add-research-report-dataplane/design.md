# Design: add-research-report-dataplane

## Context

2026-10-19/20 渠道调研（含实测）确认：东财 `reportapi` 免费覆盖五类研报元数据且支持全市场按日拉取（个股 qType=0 不带 code 按 [T-3,T] 窗口约 6 页、行业 qType=1、策略/宏观/晨报 `/report/jg` qType=2/3/4）；巨潮评级变动按日全市场（约 80 条/天）；东财全市场盈利预测一次调用约 2887 只；新浪研报列表五类免费。同时现存 bug：`fetch_research_reports` 读「research机构」「报告日期」「目标价」三个不存在的列名，机构/日期恒空、目标价接口不提供，单测 fixture 自造列名故从未暴露——已空跑五周。10-17 生态调研给出下游定位：结构化预期数据证据最强，本 delta 是它的数据地基；分析师事后检验与预期修正因子不在本 delta。

## Goals / Non-Goals

**Goals:**

- 修好并扩展现有按需研报取数（列名对齐 + 盈利预测列 + 契约测试）。
- 建独立 SQLite 数据面 `data/research_reports.db`：五表（研报主表 research_reports / 评级变动表 rating_changes / 盈利预测表 earnings_forecasts / 分析师表 analysts / 投喂全文表 report_documents），期次键与幂等第一天内建。
- 四个日批 job 接入 start_scheduler：五类研报元数据、巨潮评级变动、预期快照（+ 分析师档案聚合随研报 job 跑）、投喂目录扫描。
- 断更/无覆盖/失败三态显式；新浪第二源计数交叉与降级。
- 基本面分析师上下文接线一致预期段（快照优先→按需直拉→缺失标注）。
- 手工投喂 PDF → 全文解析入库（文件哈希幂等，`usage_policy=personal_research`）。

**Non-Goals:**

- 自动下载研报全文 PDF（反爬/稳定性；全文只经人工投喂进入）。
- 观点→权重、研报文本因子（10-17 调研维持：先验=无收益证据）。
- 分析师事后检验框架与预期修正因子（等样本积累，复用 predictions/结算/FDR，另行 delta）。
- 前端页面（/research 类视图后续另立 delta）。
- 付费源接入（Tushare 摘要 / AShareHub / Choice/iFinD/Wind / 朝阳永续——仅备选登记，见 proposal）。
- 投喂文档的 LLM 结构化抽取（首版只落全文与基础元数据，抽取/检索另行 delta）。
- 产出对外分发（个人研究自用约定）。

## Decisions

1. **独立库而非复用 sessions.db / market_snapshot.db**：研报域与既有库的生命周期、备份策略、E2E 隔离配置都不同；沿用「每域一库」先例（screening/factor_library/market_snapshot/execution）。库路径 `RESEARCH_REPORTS_DB_PATH`，缺省 `data/research_reports.db`；**所有测试/E2E 配置必须显式设置该变量**（incident 038：新库漏隔离打穿生产）。
2. **期次键 = sha1(normalize(标题) + '|' + 机构 + '|' + 发布日期)**，主表唯一索引；评级变动键 =（标的, 机构, 发布日期, 评级, 前次评级）；预期快照键 =（快照日期, 标的）。幂等全部走 upsert（INSERT OR REPLACE 语义 + updated_at 保留），重跑/补窗天然安全——沿用收割桥 harvest_state 与预测池去重的既有经验。
3. **采集走直连 requests 而非 akshare**：akshare 仅封装了个股一类（`stock_research_report_em`）且列名映射不可控（本次 bug 的共犯）；五类接口是同一套 reportapi，直连自控限流/列名/分页。akshare 保留用于巨潮（`stock_rank_forecast_cninfo` 已实测可用）与同花顺明细（`stock_profit_forecast_ths`）。
4. **限流器独立小模块**：串行 + ≥1s + 随机抖动 + 会话复用 + UA/Referer，只用于东财域名；公开工程实践（a-stock-data 防封铁律）与本机东财历史封禁史（incident：行情域 IP 封禁）都要求这一点。
5. **一致预期消费链**：`_build_fundamental_context` 读数据面当日快照 → 库无快照（新部署/日批未跑）时按需单次全市场直拉过滤本标的 → 再失败显式「一致预期数据缺失」。不进 fetch 节点并发池（避免拉长分析准备阶段；数据面日批才是主供给）。
6. **断更判定**：覆盖状态查询按「有史以来行数 >0 且近 90 日 0 条」断更、「无任何行」无覆盖、「当日采集失败」失败态，三态来自主表与运行记录而非猜测。
7. **回填即日批的窗口放大**：同一采集函数，`--backfill 2024-01-01` 按月分窗循环；不做第三方仓库依赖（manymore13/report 仅可选对照脚本，不进生产代码）。
8. **契约测试三层**：① fetcher 单测 fixture 换真实快照（仓库内 JSON，来源与日期标注）；② 采集器对 reportapi 字段做同款快照契约；③ E2E/CI 不打真网（既有纪律），真实接口漂移由 nightly @live 家族补一条可选用例。
9. **投喂通道 = 目录 + 文件哈希 + pypdf**：扫描 `RESEARCH_REPORT_INBOX_DIR`（缺省 `data/research_report_inbox`），按文件内容 sha1 幂等（重复投喂 no-op，不覆盖首次入库时间），用 pypdf 抽全文与页数，文件名推断机构/日期（解析不出留空，不编造）；`usage_policy=personal_research` 标记个人研究自用。选 pypdf 而非 PyMuPDF：BSD 许可、纯 Python、免版权争议；解析质量不足时再换引擎（引擎选择不影响表结构）。
10. **投喂即抽取**：新文档入库后同步做一次 LLM 结构化抽取（`purpose=report_extraction`，JSON 合同，`max_tokens=6000`——deepseek 类端点 reasoning 与正文共享配额，2500 实测正文为空）；抽取结果独立存 `report_extractions`，失败只降级不改入库。评级归一化在代码侧做确定性映射（含看多/看空等观点型表述），模型给「未知」时退回原始评级再映射；已有抽取不重复调用（force 重抽）。
11. **合并 = 确定性聚合，不再调 LLM**：按标的读取全部抽取行 → 报告数/机构/评级分布/目标价与分年 EPS 区间/观点去重清单；分析期纯读库，无投喂记录返回 None。LLM 综合摘要暂不做（一致性风险 + 成本，另行 delta）。
12. **投喂走独立 job（16:58）而非文件系统 watcher**：日频扫描足够（研报是低频输入），避免常驻 watcher 的进程生命周期与运维复杂度；失败只影响本 job 运行记录。

## Risks / Trade-offs

- **东财反爬升级**（PDF 域已实测 JS 挑战）：元数据接口目前稳定，但需限流纪律 + 新浪计数降级 + 失败显式，接口整体失效时数据面停止增量而非静默空（run 状态可见）。
- **PDF 解析质量参差**（扫描件/非标准字体可能抽不出文本）：首版接受「抽出多少存多少」，char_count 与错误入运行记录可发现；扫描件 OCR 不在本 delta。
- **抽取准确率无自动评估**：抽取字段目前靠人工抽查（目标价/EPS 是主要风险点）；prompt 合同已写死「缺失即 null、禁止推断」，后续可加 50 篇人工对照做准确率评估。
- **投喂全文入库的存储增长**：SQLite 存全文，单篇 PDF 文本通常几十 KB～几百 KB；目录与库同机，个人使用量级可控；后续需要时再分块/压缩。
- **东财列表与东财盈利预测页口径不一致**（600845 实测：列表断更、预测页有 4 家覆盖）：覆盖状态必须以「表间并集」读数，设计里三态查询跨主表与预期表，避免单表误判。
- **同花顺明细按需拉取受源限流**：分析时单次调用、带缓存（TTL 1 天），失败降级缺失标注，不重试轰炸。
- **五表首版字段偏窄**（如分析师执业编号、报告子类型未进）：接受——先攒样本，分析师事后检验 delta 再扩列（SQLite 加列成本低）。
- **断更 90 日阈值是拍的**：先验值写常量、暴露在覆盖状态输出里可调；不当魔法数藏代码。
