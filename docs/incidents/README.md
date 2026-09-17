# 问题与事件记录

本目录记录项目中发现的**系统性问题**、根因分析和修复方案。

> **边界说明**：本目录是系统性问题的复盘（为什么会出问题、如何防止再发），供团队长期参考避坑。
> 单次变更的 archive 验收证据（测试是否通过、行为是否符合预期）请落 `tests/validation/`。
> 一个 bug 若既是系统性问题又需验证报告，两边都放，incident 引用 validation，不重复内容。

| 编号 | 日期       | 标题                                                                | 状态     |
| ---- | ---------- | ------------------------------------------------------------------- | -------- |
| 001  | 2026-06-01 | [LLM 幻觉：数据正确但输出失真](001-llm-hallucination-20260601.md)   | 已修复   |
| [027](027-price-validate-state-keys-dropped-20260913.md) | 2026-09-13 | AnalysisState 未声明 price_check 家族/derived_metrics/citation 回路键——图合并静默丢弃，价位校验 fail 打回与修正在真实图从未生效 | 图通道契约测试锁死声明 |
| 002  | 2026-05-26 | [报告准确性复盘：茅台 FA 分析偏差](002-report-accuracy-20260526.md) | 部分修复 |
| 003  | 2026-06-03 | [股票名称获取失败 + NaN 处理缺陷](003-stock-name-and-na-handling-20260603.md) | 已修复   |
| 004  | 2026-06-04 | [数据准确性系统性问题 — efficiency 年份错位 + ROE 口径 + LLM 自算](004-data-accuracy-20260604.md) | 已修复 |
| 005  | 2026-06-04 | [GARP 格式化 bug + 杜邦口径混淆 + 测试污染](005-garp-dupont-test-pollution-20260604.md) | 已修复 |
| 006  | 2026-07-16 | [深度模式无响应 - citation 重试无限循环](006-citation-infinite-loop-20260716.md) | 已修复 |
| 007  | 2026-07-16 | [侧边栏会话时间显示 "Invalid Date"](007-sidebar-invalid-date-20260716.md) | 已修复 |
| 008  | 2026-07-16 | [深度分析交互卡顿 - AKShare 数据拉取失败无重试](008-deep-analysis-stuck-akshare-20260716.md) | 部分修复 |
| 009  | 2026-07-17 | ["热门股票"类时效性查询被错误路由进深度分析管线](009-hot-stock-query-routing-20260717.md) | 已修复 |
| 010  | 2026-07-23 | [测试全过但前端交互 bug 频出 - 缺少行为 spec 约束](010-frontend-interaction-bugs-missing-spec-20260723.md) | 处理中 |
| 011  | 2026-06-04 | [AKShare 预计算字段缺失 - 应收账款周转率返回 NaN](011-akshare-ar-turnover-nan-20260604.md) | 已修复 |
| 012  | 2026-07-27 | [SSE 流式测试 deselect（技术债追踪）](012-sse-stream-tests-deselected-20260727.md) | 追踪中 |
| 013  | 2026-08-04 | [流式输出概率性文字错乱 — 三轮静态推理失败 + 并发写 DB 数据丢失](013-sse-concurrent-text-corruption-20260804.md) | 已修复 |
| 014  | 2026-07-16 | [刷新页面导致历史会话清空 — 事件循环被高频同步 SQLite 写冻结](014-refresh-clears-session-list-20260716.md) | 已修复 |
| 015  | 2026-08-05 | [E2E timeline suite report_ready 丢失 — REPORTS_DIR 递归创建缺失](015-reports-dir-mkdir-parents.md) | 已修复 |
| 016  | 2026-08-16 | [litellm 流式 logging 线程在 Windows socketpair 竞态死锁 — 跑批挂死](016-litellm-stream-logging-deadlock.md) | 已修复 |
| 017  | 2026-08-16 | [方舟 GLM-5.2 reasoning 吃满 max_tokens 配额 — 截断/空输出炸行](017-ark-glm-reasoning-token-starvation.md) | 已修复 |
| 018  | 2026-08-17 | [LLM Provider 迁移连环兼容性故障 - 7 bug 全景与 Gateway 根治重构](018-llm-provider-migration-gateway-refactor.md) | 已修复(表层)/重构落地 |
| 019  | 2026-08-24 | [LLM 输出截断治理 — 静默截断、重试空转与 reasoning 配额吃空](019-llm-output-truncation-governance.md) | 阶段修复 |
| 020  | 2026-08-28 | [citation 校验器契约疾病 — 索引错位、词表分裂与容差失真制造大面积假 FAIL](020-citation-contract-diseases-20260828.md) | 已修复(待合并) |
| 021  | 2026-08-25 | [深研管线「假卡死」— 事件落库限速终态迟到 + 管线超时空转](021-deep-analysis-session-stuck.md) | 已修复 |
| 022  | 2026-08-30 | [契约修复冒烟验证 — 第四类契约疾病浮现（技术上下文数组方向未声明）](022-citation-smoke-verify-20260830.md) | 部分修复 |
| 023  | 2026-09-01 | [quick 通道迁移遗留三大共振 — 旧 spec 确定性全红 + 断连状态泄漏 running + 「flaky」错误归因](023-agui-channel-stale-specs-disconnect-leak.md) | 已修复 |
| 024  | 2026-09-06 | [App.tsx 流状态所有权分散 — StreamStore 结构性重构根治竞态土壤](024-stream-store-refactor-20260906.md) | 已修复   |
| 025  | 2026-09-07 | [opencode zen/go 网关强制 x-opencode-session 头 — 离线 judge 链路全挂约两周](025-opencode-session-header-20260907.md) | 已修复   |
| 026  | 2026-09-11 | [citation 门禁把校验器解析缺陷记成分析师引用错误并自动重跑分析师 — 39 FAIL 抽样无一真错、重试 100% 打在误报桶](026-citation-gate-misattribution-20260911.md) | 阶段 0–5 已实施，r4 验证通过，终裁待 owner |
| 027  | 2026-09-12 | [ClickHouse 26.6 新分析器在 scores 查询形状(FINAL+JOIN+ORDER+LIMIT)上必然崩溃 — Scores 页 internal error 三周，公共 API 不受影响](027-clickhouse-analyzer-scores-page-20260912.md) | 已修复（enable_analyzer=0 + 镜像固定，UI 待 owner 确认） |
| 028  | 2026-09-16 | [重算路由由 claim_type 自声明决定 — LLM 把自算值标 numerical 即可跳过重算（消融设施发现的第一个机制存在性缺口）](028-recompute-routing-claim-type-20260916.md) | 已修复（harden-recompute-routing 已归档；冻结重放 0/5→2/5，零新增误报） |
| 029  | 2026-09-17 | [单点修复的收益在遥测里不可见——记账要「该分析师全 PASS」，改对也不计入](029-surgical-repair-accounting-scope-20260917.md) | 已登记（处置候选待 owner；A4 补测发现，改对 20/20 vs 记账 4/22） |
| 030  | 2026-09-17 | [材料快照白名单漂移——compute 输出键丢 6/14，同一处第二次丢测量面](030-materials-snapshot-whitelist-drift-20260917.md) | 已修复（排除式 + 完整性守卫 + 回填 115 键 + 摘要回真） |

---

## 按主题分类

### LLM 输出可靠性

- [001](001-llm-hallucination-20260601.md) LLM 编造财务数字、行业PE无源、PE口径混淆
- [019](019-llm-output-truncation-governance.md) 静默截断/重试空转/reasoning 配额吃空 → 续写 + 预算对齐官方治理

### 引用校验（citation）

- [006](006-citation-infinite-loop-20260716.md) 深度模式无响应 - citation 重试无限循环
- [020](020-citation-contract-diseases-20260828.md) 考卷与答案册不同源：context 裁剪索引/中文词表/绝对容差 → 61% 假 FAIL；负索引约定 + 单一词表 + 相对容差，离线重判 41→5（残量全为真幻觉）
- [022](022-citation-smoke-verify-20260830.md) 修复后多标的冒烟：稳态标的 2.2% FAIL 清零成疾；异动股暴露第四类契约疾病（技术 context 数组方向未声明 → LLM 期次错位，校验器裁决正确）
- [027](027-clickhouse-analyzer-scores-page-20260912.md) 基础设施镜像浮动 tag 的教训：clickhouse `latest` 08-20 拉入 26.6.1 当日 Scores 页即挂（新分析器回归），因人工标注走离线导出三周未被发现；修法=默认 profile 回旧分析器+固定版本，无降级
- [026](026-citation-gate-misattribution-20260911.md) 020「考卷与答案册不同源」复发（日期显示格式/季度标签/单位量级/direction 符号字段）：FAIL 桶 100% 误报，重试只盯误报桶白烧 75 次生成并覆盖好报告；指标拆三报（真幻觉率/校验器误报率/结构不可验），处置对象必须匹配归因桶

### 评分与分析模型

- [002](002-report-accuracy-20260526.md) 指标体系不适合白酒行业、归因逻辑错误、评分模型缺陷
- [003](003-stock-name-and-na-handling-20260603.md) 行业阈值覆盖、NaN 处理缺陷、数据降级链路

### 数据质量

- [004](004-data-accuracy-20260604.md) efficiency.py 年份错位 bug、ROE 口径差异、LLM 自算未提供指标
- [011](011-akshare-ar-turnover-nan-20260604.md) AKShare 预计算字段缺失（应收账款周转率 NaN），降级为自算
- ADR-0005 `docs/adr/0005-validate-financials.md` 勾稽校验（4条规则）

### 测试体系与开发流程

- [010](010-frontend-interaction-bugs-missing-spec-20260723.md) 测试从实现反推、e2e 形同虚设、缺少行为 spec 约束 → 引入 OpenSpec + Superpowers SDD 体系
- [013](013-sse-concurrent-text-corruption-20260804.md) 并发 bug 用静态推理修不好（必须 E2E 复现 + 运行时证据）；测试/生产共用 SQLite 导致数据不可恢复 → 补并发 E2E + DB 环境变量隔离
- [023](023-agui-channel-stale-specs-disconnect-leak.md) 带红合并 + 「retry 能过就算 flaky」的错误归因让 CI 确定性断裂隐形两天；UI 通道迁移必须带走旧 E2E；SSE 生成器状态落库需把 aclose/GeneratorExit 当一等断连路径测试
