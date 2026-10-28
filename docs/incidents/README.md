# 问题与事件记录

本目录记录项目中发现的**系统性问题**、根因分析和修复方案。

> **边界说明**：本目录是系统性问题的复盘（为什么会出问题、如何防止再发），供团队长期参考避坑。
> 单次变更的 archive 验收证据（测试是否通过、行为是否符合预期）请落 `tests/validation/`。
> 一个 bug 若既是系统性问题又需验证报告，两边都放，incident 引用 validation，不重复内容。
>
> **编号消歧（2026-10-10，issue #180）**：027 与 032 曾被两起事故双占——prose/evals 文档中的
> 「incident 027」「incident 032」全部指价位校验 state 键丢弃、战绩指标失真这两起（保留原号）；
> ClickHouse 分析器崩溃改编 **041**、股票列表缓存毒化改编 **040**，伴生配置文件同步改名
> （`incident-041-legacy-analyzer.xml`，目录级挂载改名即生效）。

| 编号 | 日期       | 标题                                                                | 状态     |
| ---- | ---------- | ------------------------------------------------------------------- | -------- |
| 001  | 2026-06-01 | [LLM 幻觉：数据正确但输出失真](001-llm-hallucination-20260601.md)   | 已修复   |
| [027](027-price-validate-state-keys-dropped-20260913.md) | 2026-09-13 | AnalysisState 未声明 price_check 家族/derived_metrics/citation 回路键——图合并静默丢弃，价位校验 fail 打回与修正在真实图从未生效 | 图通道契约测试锁死声明 |
| [035](035-llm-config-state-key-dropped-20261004.md) | 2026-10-04 | AnalysisState 未声明 llm_config——LangGraph 静默过滤入口键，请求级模型配置在管线整条哑火回退 env（027 同族，门禁盲区=入口键） | 已修复（声明通道 + TestEntryChannels 入口键门禁） |
| [036](036-glm53-thinking-leak-delivery-contract-20261004.md) | 2026-10-04 | 换 glm-5.3 后「研究聚焦」泄露模型英文独白进交付物（max_tokens=400 被推理吃满截断 + verbatim 嵌入零校验 + raw_reasoning 回退坑）——纯文本交付物不在 llm-output-contract 覆盖内 | 已修复（#221 合并+部署+拓荆真实验证收口） |
| [037](037-event-backlog-sqlite-transient-terminal-loss-20261004.md) | 2026-10-04 | 6 万 thinking_token 积压（15 分钟滞后）× SQLite 瞬断落在积压排空窗口——管线已完成但 UI 冻结、run_deep_analysis 返回空、聊天谎称无报告，R2+ 节点事件永久丢失（021 同族复发） | 已修复（#224 合并+部署+ReAct 真实验证收口；含 20:39 同型误判超时第二实例） |
| [038](038-e2e-default-suite-hits-production-vite-proxy-20261007.md) | 2026-10-07 | E2E 默认套件整套路由经 vite 代理（默认 8000）打穿到生产容器：reuseExistingServer 只探活性复用生产后端，真实 LLM 建会话/触发深度分析，10-03 已有同型先例；predictions 零污染、33 垃圾会话已清 | 已处置（当晚清污+worktree config 补 VITE_API_TARGET；预防项待 issue：默认 config 显式代理+health 模式核验） |
| [039](039-relay-lag-prereport-backlog-20261008.md) | 2026-10-08 | thinking 前置积压使压缩门槛（report_seen）永不生效——图已完成但终态推迟数小时、会话滞留 running 被重启 reconcile 补打 interrupted（021/037 同族第三发，茅台两会话实证；#224 治了终态落库可靠性未治排序） | 根因修复 PR #266（issue #265）；timeline O(n²) 写放大等三子项挂账 |
| [040](040-stock-list-cache-poisoning-20260929.md) | 2026-09-29 | 股票列表缓存毒化——单次抓取失败清空进程内检索锚点，全股票「未找到」（原编号 032，2026-10-10 撞车消歧改号） | 已修复（失败不缓存 + 60s 冷却重试 + 有声失败） |
| [041](041-clickhouse-analyzer-scores-page-20260912.md) | 2026-09-12 | ClickHouse 26.6 新分析器在 scores 查询形状(FINAL+JOIN+ORDER+LIMIT)上必然崩溃 — Scores 页 internal error 三周，公共 API 不受影响（原编号 027，2026-10-10 撞车消歧改号） | 已修复（enable_analyzer=0 + 镜像固定，UI 待 owner 确认） |
| [042](042-timeline-lag-loss-20261011.md) | 2026-10-11 | 消费滞后丢落库时序——#266 压缩分支与 #304「flush 全量」规范矛盾（graph_done 置位≠消费者读完，积压 thinking 静默缺段；CI flake 取证揪出，1743/3000 精确反推） | 已修复（PR #307 附带 commit 871a31be，滞后回归测试钉死） |
| [043](043-snapshot-parallel-backfill-index-corruption-20261016.md) | 2026-10-16 | 快照库 16 进程并行回填 + 硬杀进程 + 容器跨挂载读 → 索引层损坏（唯一索引失效→17 重复键）；表数据零丢失，表重建恢复；防护理待落 | 已恢复（integrity ok；防护随 unify-quant-research-architecture 收口） |
| [044](044-e2e-config-snapshot-leak-api-wedge-20261017.md) | 2026-10-17 | E2E 隔离配置缺 SNAPSHOT_DB_PATH——本机跑 isolated/timeline 等 7 个套件时其后端读生产快照库，与容器跨挂载 SQLite 争用（#344 读侧变体）→ 生产后端 API 整体卡死 ~40-60min（快照库零损伤） | 已修复（7 配置补隔离 + 套件复跑验证；隔离清单门禁待落） |
| [045](045-local-e2e-reused-prod-stack-port-shadow-20261018.md) | 2026-10-18 | 本地 E2E 误复用生产栈（外部拉起 + reuseExistingServer 探活）→ 测试会话污染生产库；并发发现 [::1] 端口遮蔽（wslrelay/docker 代理与本地服务争用，浏览器命中容器旧前端，038 同族合并） | 部分修复（写控制文件用例加 fixture 守卫 + 专用端口验证；环境治理三项挂账） |
| [046](046-sessions-db-wal-handle-index-corruption-20261020.md) | 2026-10-20 | worker 持有已删除 `sessions.db-wal` 句柄（delete-pending）→ 新连接无法进入 WAL 模式，会话/战绩/评分卡/日报/cohort API 全断约 2 小时；并发发现 `idx_session_events_session` 索引损坏（REINDEX 被 malformed 拒绝），按 sqlite_master 导出重建，事件日志抢救 97.5%（丢 28,027 行尾部），355 会话与业务表零丢失；日批全部补跑 | 已恢复（库 integrity ok + 日批补跑；迁 named volume / 健康探针 / 页级校验 / 派发回写挂账） |
| [047](047-wsl-memory-exhaustion-container-enomem-20261021.md) | 2026-10-21 | WSL VM 12GB 上限 + swap 耗尽（rdagent 常驻进程 RSS 2.5–3.3GB×多个）→ 前端 Vite watcher 与后端 import 相继 `ENOMEM` 退出/启动失败；宿主仍有 19GB 空闲，瓶颈在 VM；手动重启后端首尝试被拒 | 已恢复（停栈 → 重启 Docker Desktop（未 `wsl --shutdown`）→ 起栈；前后端/端点/UI 验证通过；RD-Agent 内存配额挂账） |
| [048](048-prompt-drift-unmerged-branch-20261022.md) | 2026-10-22 | 生产 Langfuse `trader` prompt 来自未合并分支 `add-holdings-context`，与 main 代码契约错位 9 天（`deploy_prompts.py` 从工作树发布、不校验分支）；eval 门禁拒绝 round14 时暴露，强制按 main 重发后放行 | 已处置（按 git 权威口径重发 production）；分支守卫 / 日常一致性检查待落 |
| [049](049-pandarallel-dev-shm-exhaustion-distro-io-error-20261022.md) | 2026-10-22 | pandarallel chunk 落 `/dev/shm`（`MEMORY_FS_ROOT` 默认，tmpfs 8.8GB = VM 18GB 的 50%）→ 并行 IC 计算 `Errno 28` 崩会话；随后 distro 进入读/exec 全 EIO 的不可用态，自愈启动器 69 次 SIGBUS 零效果，靠 07:26:31 VM 重启才恢复（2h20m）。**同日 07:40 / 12:19 同一计算改撞 18GB 内存上限**：OOM 杀 worker → master 卡 unix socket、worker 卡 futex，会话死锁且永不退出 ⇒ 看护不重拉，监控因「只探进程存在 + 进度读日志末行」全程显示「运行中」。**源头是 `factor_runner.py:54` 的 lambda 闭包捕获两个 DataFrame**：`dill.dumps` 把整份矩阵按值复制进每个 worker（实测 48MB→0.4KB，每 worker ~5.4GB，默认 8 worker） | 已修复并验收（`patch-ic-closure.py` + `MEMORY_FS_ROOT` 改磁盘 + worker 数上限；12:46 dedup 实跑通过、OOM 零新增）；**僵死判定已交付**（delta `add-factor-research-stall-detection`，阈值 900s）；**distro 探活与自愈已交付**（delta `add-factor-research-distro-liveness-probe`：宿主侧探活 + 三条件合取门控 + 只读 `distro` 块 + 页面独立提示，E2E 19/19、门控真值表 7/7） |
| [050](050-factor-ic-missed-fire-20261022.md) | 2026-10-22 | 15:45 `factor_decay_monitor` 触发落在进程重建窗口（15:35 后、15:50 前）：APScheduler `MemoryJobStore` 重启后不回溯错过触发点，`misfire_grace_time=3600` 只覆盖「调度器存活但执行延迟」→ 当日 IC 采集零记录，`factor_ic` 停在 10-20，筛选 bootstrap 门槛（≥10 样本）继续不满足、10-21 批次走流动性降级；同窗口 15:50/15:55/16:45 三个长任务被判 `stale_process_restart` 或线程池异常 | 数据已手工补齐（20:55 触发 `recorded=10`）+ 补跑 builtin/microcap；**机制已修复**（delta `add-ops-missed-run-catchup`：启动补跑白名单 9 项/6h 窗/上限 3 + `missed_today`；按 10-22 17:29 重放命中当日三项漏跑）；前端渲染与「解释器收尾可读终态」仍挂账 |
| [051](051-rdagent-workspace-snapshot-disk-blowup-20261026.md) | 2026-10-26 | RD-Agent 每会话在 distro 内物化全量因子面板快照（`combined_factors_df.parquet` 0.4–1.9GB + result.h5）且零清理——10-22 IC 修复让会话首次跑完全程后加速至 ~110GB/天，4.4 天把 vhdx 打到 817GB、宿主 D: 剩 18.7GB → VHDX 无法扩张 → distro 部分块不可读（READ EIO → python mmap SIGBUS=exit135，探针 `/bin/true` 假阴性）→ 管线 20:56 起停摆；生产栈同窗口不可达疑似同因 | **当晚全恢复**：`wsl --shutdown` 重启 distro + 删 48h 外快照（3,295 目录/390GB/0 失败）+ diskpart 压实（vhdx 817→428GiB，D: 18.7→407.4GB）+ 生产栈/看护重启验证通过；防复发 delta `add-factor-workspace-retention-disk-watch` 立项（保留窗清理 + 双侧磁盘水位 + 低水位停发 + 探活读写双探针）；102.7GB 迁移备份暂留待新盘稳定后清 |
| [052](052-factor-detail-panel-in-request-regression-20261027.md) | 2026-10-27 | 因子详情接口在请求内现算 500 日准入面板（风格暴露/单调性诊断）；唯一护栏「窗口 IC < 30 短路」被 10-22 收割入库因子的 IC 历史回填绕过（active 因子全 60–90 点）→ 全部 active 因子详情挂死：探针 71 分钟未完成、CPU 180%+ 自放大堆积、容器 7h 零条详情完成记录（retired 因子秒开是判别特征）。**数据充分性常量被复用为 HTTP 成本豁免**是深层病灶，复发型（首次根因就写在代码注释里） | **PR #502**：诊断读数改日批预计算落库（factor_diagnostics 表 + factor_diagnostics_daily 17:15 增量日批），详情接口纯读库 + 无条件零面板构建（打桩为 raise 的回归测试钉死）；延迟预算观测挂账 issue |
| [053](053-sqlite-wal-attach-failure-9p-kills-pool-analyses-20261028.md) | 2026-10-28 | 9p bind mount 上 SQLite 连接创建全面失败 136 秒（16:58:02–17:00:18，95+ 条 `unable to open database file` / `disk I/O error` / `database is locked`）→ **10 个池分析全部中断、0 份报告落库**（9 行僵尸 running），microcap 日批/研报触发批次/因子工具（降级空因子）/因子详情 500 同窗口中招。046 挂账项「迁 named volume」未落地的第三发；引信=052 的详情请求内建 500 日面板（#502 已修未部署，容器 `api.py:4510` 仍是 `_panel_from_snapshot(None)`）；放大器=每连接 `PRAGMA journal_mode=WAL` + `read_universe` 读路径跑 DDL（3 连接/天；500 日面板 1,500 次）× 面板不缓存；重试预算仅 ~3.1s vs 实测停摆 136s | **待处置**：DB 迁 named volume（P0）/ 每连接去 WAL pragma / 读路径去 DDL / 面板单查询+缓存 / 部署 #502 / 重试预算 ≥60–180s + 终态待补标记 |
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
| 028  | 2026-09-16 | [重算路由由 claim_type 自声明决定 — LLM 把自算值标 numerical 即可跳过重算（消融设施发现的第一个机制存在性缺口）](028-recompute-routing-claim-type-20260916.md) | 已修复（harden-recompute-routing 已归档；冻结重放 0/5→2/5，零新增误报） |
| 029  | 2026-09-17 | [单点修复的收益在遥测里不可见——记账要「该分析师全 PASS」，改对也不计入](029-surgical-repair-accounting-scope-20260917.md) | 已登记（处置候选待 owner；A4 补测发现，改对 20/20 vs 记账 4/22） |
| 030  | 2026-09-17 | [材料快照白名单漂移——compute 输出键丢 6/14，同一处第二次丢测量面](030-materials-snapshot-whitelist-drift-20260917.md) | 已修复（排除式 + 完整性守卫 + 回填 115 键 + 摘要回真） |

---

## 按主题分类

### LLM 输出可靠性

- [001](001-llm-hallucination-20260601.md) LLM 编造财务数字、行业PE无源、PE口径混淆
- [019](019-llm-output-truncation-governance.md) 静默截断/重试空转/reasoning 配额吃空 → 续写 + 预算对齐官方治理
- [036](036-glm53-thinking-leak-delivery-contract-20261004.md) 换 glm-5.3 后「研究聚焦」泄露模型英文独白进交付物——结构化输出合同只护 JSON 路径，纯文本直通交付物零设防，截断救的是完整性救不了正确性 → delta add-output-contract-guard

### 引用校验（citation）

- [006](006-citation-infinite-loop-20260716.md) 深度模式无响应 - citation 重试无限循环
- [020](020-citation-contract-diseases-20260828.md) 考卷与答案册不同源：context 裁剪索引/中文词表/绝对容差 → 61% 假 FAIL；负索引约定 + 单一词表 + 相对容差，离线重判 41→5（残量全为真幻觉）
- [022](022-citation-smoke-verify-20260830.md) 修复后多标的冒烟：稳态标的 2.2% FAIL 清零成疾；异动股暴露第四类契约疾病（技术 context 数组方向未声明 → LLM 期次错位，校验器裁决正确）
- [041](041-clickhouse-analyzer-scores-page-20260912.md) 基础设施镜像浮动 tag 的教训：clickhouse `latest` 08-20 拉入 26.6.1 当日 Scores 页即挂（新分析器回归），因人工标注走离线导出三周未被发现；修法=默认 profile 回旧分析器+固定版本，无降级
- [026](026-citation-gate-misattribution-20260911.md) 020「考卷与答案册不同源」复发（日期显示格式/季度标签/单位量级/direction 符号字段）：FAIL 桶 100% 误报，重试只盯误报桶白烧 75 次生成并覆盖好报告；指标拆三报（真幻觉率/校验器误报率/结构不可验），处置对象必须匹配归因桶

### 决策校验与门禁

- [034](034-decision-price-check-false-positives-20261002.md) 决策价位校验器两误报：VaR95 语境的 95 被当股价（**回归评审原始 P0 案例「95 元幻觉」实为此误报，LLM 从未输出 95 元价位**）+ 复合回踩触发「先跌破/回撤至 X 再站稳 X」被句法判空洞；门禁机器按 spec 正确运转、LLM 行为一致可辩护 → 修复 delta fix-decision-price-check-false-positives；门禁自动阻断准入边界待 owner 终裁

### 评分与分析模型

- [002](002-report-accuracy-20260526.md) 指标体系不适合白酒行业、归因逻辑错误、评分模型缺陷
- [003](003-stock-name-and-na-handling-20260603.md) 行业阈值覆盖、NaN 处理缺陷、数据降级链路

### 数据质量

- [004](004-data-accuracy-20260604.md) efficiency.py 年份错位 bug、ROE 口径差异、LLM 自算未提供指标
- [011](011-akshare-ar-turnover-nan-20260604.md) AKShare 预计算字段缺失（应收账款周转率 NaN），降级为自算
- [033](033-stale-financials-valuation-chain-break-20260929.md) 财报论据结构性过时（只取年报+季度只取净利，中报毛利率回升/负债率腰斩系统性失明）+ 估值链路断裂（PE 缺失被兜底文案伪装成「PE≥行业平均」，市值从不进 LLM 上下文）+ 阈值无行业覆盖三因叠加；非分析师幻觉，数字全部可溯源 → 修复 delta update-financial-freshness-and-valuation
- ADR-0005 `docs/adr/0005-validate-financials.md` 勾稽校验（4条规则）

### 流式事件与会话链路

- [014](014-refresh-clears-session-list-20260716.md) 刷新页面清空历史会话——事件循环被高频同步 SQLite 写冻结
- [021](021-deep-analysis-session-stuck.md) 深研管线「假卡死」— 事件落库限速终态迟到 + 管线超时空转
- [037](037-event-backlog-sqlite-transient-terminal-loss-20261004.md) thinking_token 积压（15 分钟滞后）× SQLite 瞬断落在积压排空窗口——管线已完成但三界面三种说法，R2+ 节点事件永久丢失；终态事件不允许与批量 token 队列竞争
- [038](038-e2e-default-suite-hits-production-vite-proxy-20261007.md) E2E 默认套件经 vite 代理（默认 8000）+ reuseExistingServer 打穿到生产后端——本机生产常驻时本地跑门禁必然命中；前端代理目标必须显式、复用前必须核验 TESTING 模式（031「默认不安全」主题的 E2E 侧变体）
- [039](039-relay-lag-prereport-backlog-20261008.md) thinking 洪峰在 final_report 之前——压缩门槛以消费到 report chunk 为前提，在「消费滞后」这个优化目标场景下恰好失效；复现测试的流序必须取自生产 trace（既有场景 D 手造顺排流绕开了真实语序）
- [046](046-sessions-db-wal-handle-index-corruption-20261020.md) worker 持有已删除 `sessions.db-wal` 句柄（delete-pending）→ 新连接无法进入 WAL 模式，会话/战绩/日报/cohort 全断约 2 小时；并发发现事件索引损坏，导出重建抢救 97.5%；与 043/044 同属 Windows bind mount 上的 SQLite 易损家族 → 热库迁 named volume 挂账

### 环境与部署

- [047](047-wsl-memory-exhaustion-container-enomem-20261021.md) WSL VM 12GB 上限 + swap 耗尽（rdagent 常驻进程为主）→ 前端 Vite watcher 与后端 import 随机 `ENOMEM`；宿主空闲 19GB 但 VM 无余量；重启生产容器前必须看 `free -m`/`SwapFree`，Swap 满时先重启 Docker Desktop，禁用 `wsl --shutdown`（会杀 rdagent）
- [048](048-prompt-drift-unmerged-branch-20261022.md) 生产 Langfuse trader prompt 来自未合并分支 `add-holdings-context`（落后 main 104 提交），与 main 代码契约错位 9 天：prompt 要求 `受众持仓` 上下文与 exit 默认，main 代码一律不提供/默认 short；评估门禁才暴露，部署脚本从工作树发布且无分支守卫
- [053](053-sqlite-wal-attach-failure-9p-kills-pool-analyses-20261028.md) `data/` 是 9p/drvfs bind mount（`msize=65536`），SQLite WAL 需要 `-shm` 共享内存 → 并发连接创建时全线 `unable to open database file` / `disk I/O error`：136 秒窗口打掉 10 个池分析（0 报告）与 microcap/研报触发/因子工具。**重要教训**：四个存储模块每个连接都跑 `PRAGMA journal_mode=WAL`、`read_universe` 读路径跑 DDL、面板逐日 3 连接不缓存（500 日 = 1,500 次 open）；`session_store` 重试只覆盖 ~3.1s，远小于实测停摆；且 #502 修了但没部署，详情接口仍在请求内建 500 日面板。DB 必须迁 named volume（043/044/046 同族挂账）

### 测试体系与开发流程

- [010](010-frontend-interaction-bugs-missing-spec-20260723.md) 测试从实现反推、e2e 形同虚设、缺少行为 spec 约束 → 引入 OpenSpec + Superpowers SDD 体系
- [013](013-sse-concurrent-text-corruption-20260804.md) 并发 bug 用静态推理修不好（必须 E2E 复现 + 运行时证据）；测试/生产共用 SQLite 导致数据不可恢复 → 补并发 E2E + DB 环境变量隔离
- [023](023-agui-channel-stale-specs-disconnect-leak.md) 带红合并 + 「retry 能过就算 flaky」的错误归因让 CI 确定性断裂隐形两天；UI 通道迁移必须带走旧 E2E；SSE 生成器状态落库需把 aclose/GeneratorExit 当一等断连路径测试
- [031](031-test-db-leak-predictions-20260922.md) 测试泄漏生产库：假决策写进 predictions 66 行（8 天未发现）→ 测试隔离必须「默认安全」（conftest 全局兜底），旁路落库挂点在测试环境须指向临时库
- [032](032-track-record-portfolio-metrics-corruption-20260929.md) 历史战绩组合指标失真（快照年化 +2853%）：neutral 幻影空头盯市 + 坏参考价无校验 + 年化 n 口径放大三缺陷叠加 → 盯市符号单点对齐、参考价双护栏、交易日历年化口径、指标量级合理性纳入健康检查
