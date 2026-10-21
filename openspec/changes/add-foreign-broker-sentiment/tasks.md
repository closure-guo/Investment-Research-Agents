## 1. 数据与配置

- [x] 1.1 `research_reports.db` 新增 `foreign_broker_events`（幂等键、原始字段、抽取字段、extraction_status、usage_policy）
- [x] 1.2 新增 `FOREIGN_NEWS_FEEDS` / `FOREIGN_NEWS_KEYWORDS` 配置读取与空态语义
- [x] 1.3 单元测试：空配置 no-op、表迁移幂等

## 2. 采集

- [x] 2.1 实现 RSS/Atom 与 JSON feed 解析（标题/链接/发布时间/短摘要，摘要截断 500 字）
- [x] 2.2 实现串行抓取、UA、≥1 秒间隔、单源失败降级
- [x] 2.3 实现关键字过滤（机构名 + 中国/港股/A 股），记录过滤前后条数
- [x] 2.4 测试：feed 解析、摘要截断、单源失败、过滤命中

## 3. 抽取与落库

- [x] 3.1 新增 prompt `foreign_broker_event.md` 与 purpose `foreign_broker_event`（types/router）
- [x] 3.2 实现抽取字段白名单与归一（action/target_type/direction/confidence），缺失为 null
- [x] 3.3 实现幂等落库 `hash(title,url,published_at)`；抽取失败保留原文并标 failed
- [x] 3.4 测试：明确事件、模糊表述不升级、抽取失败保留、重复抓取幂等

## 4. 调度与只读接口

- [x] 4.1 注册 `foreign_broker_news_daily`（17:05），失败/降级/空态留痕；注册表镜像守护覆盖
- [x] 4.2 新增只读接口（机构/动作/对象/日期过滤），空态显式
- [x] 4.3 确认内外资分区：内资记分卡与权重不读取该表
- [x] 4.4 测试：决策零变化、空态、过滤查询

## 5. 发布与验收

- [x] 5.1 `uv run python scripts/deploy_prompts.py` 发布 `foreign_broker_event`
- [x] 5.2 `openspec validate add-foreign-broker-sentiment --strict` 通过
- [x] 5.3 `uv run pytest` 定向测试全绿；`uv run ruff check` 通过
- [ ] 5.4 用真实 feed 跑一轮：确认事件落库、摘要不超限、内外资分开可查
- [ ] 5.5 人工抽查 10 条抽取，确认无原文外推断、无全文入库
