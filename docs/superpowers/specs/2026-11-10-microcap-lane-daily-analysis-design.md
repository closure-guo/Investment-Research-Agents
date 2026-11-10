# 设计：小市值池每日自动分析 + 独立模拟盘（microcap lane）

> 日期：2026-11-10｜状态：设计已获用户逐节批准
> 前置事实源：`openspec/specs/stock-screening/spec.md`（microcap lane 章节）、
> `src/finance_agent/screening/pool_analysis.py`（主档自动分析编排）、
> `src/finance_agent/execution/driver.py`（执行链驱动器）、实跑数据（§9）。

## 0. 背景与目标

小票通道（`microcap_screening_daily`，16:45）已在生产每日出池（今日 40 只，
`frontier_gate=ready`），但止步于观察：`orders_placed` 恒 0，不进分析链，读侧仅页内切换展示。

本设计打通「每日自动分析小票池」：

1. 每天 16:47 对小票池前 10 只跑五层深度分析（与主档同管线同质量）；
2. 决策入预测池时带 **lane 标记**，与主档 track-record 隔离；
3. FM-approved 的小票决策进**独立模拟盘**（第二执行 DB）：单票 5% 上限、滑点档上移一档，
   把 `MICROCAP_SUGGESTED_SINGLE_CAP` / `MICROCAP_SLIPPAGE_TIER_BUMP` 两个「记录字段」
   转正为生效约束——小票滑点现实性正是这条 lane 的核心观察点。

**用户裁决记录**（2026-11-09 设计对话）：

| 分叉 | 裁决 |
|---|---|
| 目标定位 | 观察 + 独立模拟盘（不混入主模拟盘） |
| 每日规模 | 前 10 只（与主档同量级成本） |
| 两池重叠标的 | 跳过 + 顺延补位（今日实跑：跳 4 补 4，凑满 10 只全新） |
| 独立模拟盘形态 | 方案 A：第二执行 DB 文件（主档库逐字节不动） |

## 1. 每日分析编排（新 job `auto_pool_analysis_microcap`，16:47）

1. **读池**：`latest_successful_pool(pool='microcap')`；新鲜度规则同主档（当日或前一
   交易日补跑池，更旧 → `skipped_stale_pool`）。
2. **选样**：按 rank 取前 `AUTO_POOL_MICROCAP_MAX_SYMBOLS`（env，默认 10，cap 100），
   **跳过当日 `pool_analysis_runs` 已有 queued/completed 行的标的并顺延补位**。
   去重查询不按 pool 过滤（幂等键 `(trade_date, symbol)` 保持全局）——同日同标的
   主档已分析则小票批跳过，天然实现跨池去重；代价是重跑更保守（不会双烧），可接受。

   **裁定：跳过 + 顺延补位优于「复用主档分析结果」**（2026-11-10 设计审议）：

   1. *时序*：16:47 时主档批次分析多半仍在跑（共用队列），无结果可复；复用须推迟
      到深夜或两阶段派发，打破固定日批纪律、失败模式剧增。
   2. *同日日主判定会误杀*：`track_record/job.py` 对同 `(symbol, 归属日)` open 观点
      只留 created_at 最晚者，其余关闭 `duplicate_of_day`（lane 无感）——复用产生的
      第二条 prediction 按创建时序随机被杀：杀小票行则复用静默失效，杀主档行则污染
      主档 track-record（红线）。修复须 lane 感知化，动生产结算关键路径。
   3. *偏倚方向保守且可补偿*：跳过使小票账本不含主档也喜欢的票 → 低估而非高估小票
      alpha；忠实 top-10 影子组合可用主档决策的结算数据事后离线重算，复用无独占收益
      （仅 4 只票的滑点档差异，可估算）。
   4. *成本差可调兜底*：约 4 分析/日，`AUTO_POOL_MICROCAP_MAX_SYMBOLS` 降档即可，
      零架构改动。复用的重开条件：主档批次可保证 16:47 前完成 + 成本成为主要矛盾 +
      观察通道升级实盘级模拟，届时一并做 lane 感知日主判定（后续 delta）。
3. **时效性重校验**：复用 `screening_freshness_removals`（停牌/一字板盘前移除）。
4. **建会话**：标题后缀「小票池自动分析」，session 事件 `source="auto_pool_microcap"`；
   入同一个 `AnalyzeQueue`（与主档/人工批次共用并发上限）。
5. **幂等与失败**：键 `(trade_date, symbol)` 全局；失败行（failed/dispatch_failed/
   session_failed）重派建全新会话（issue #546 语义原样继承）。
6. **开关**：`AUTO_POOL_MICROCAP_ENABLED`（默认开），独立于主档开关。

**表变更**：`pool_analysis_runs` 加 `pool TEXT NOT NULL DEFAULT 'main'` 列
（幂等 `ALTER TABLE ADD COLUMN`，存量行自动 'main'，无需回填）；小票批写行显式
`pool='microcap'`。列只做归属标注，**不进主键**。

## 2. 决策 lane 标记

- `predictions` 加 `lane TEXT NOT NULL DEFAULT 'main'`（同款幂等加列）。
- **ingest 时定 lane**：按 `session_id` 反查 `pool_analysis_runs.pool` → 命中即该值；
  查不到（手动/主档会话）→ 'main'。
- **主驱动器（16:10）加 `lane='main'` 过滤**——小票决策从此进不了主账本；
  加列后存量 open 观点全为默认 'main'，主档行为与今天逐位一致（回归证据 = 既有测试全绿）。
- **decision settle（16:00）不动**：预测级结算 lane 无关，小票观点照常按窗口结算——
  结算链免费给出小票 lane 的记分卡数据。
- cohort 评估自建 universe（指数成分会话），不消费 `pool_analysis_runs`，无预算交互。

## 3. 独立小票模拟盘（第二执行 DB）

**账本**：`EXECUTION_MICROCAP_DB_PATH`（默认 `data/execution_microcap.db`），五张表
同 DDL 新建——`store.py` 本就按调用期读 env / 测试经 `db_path` 注入，第二库零 DDL 改动。

**驱动器参数化**：`run_execution_daily` 加 `lane` + `db_path` 参数（主档调用处传
`lane='main'`，行为不变）；新封装 `run_execution_microcap` 作为 ops job 入口。

| 参数 | 主档 | 小票 | 出处 |
|---|---|---|---|
| 观点过滤 | `lane='main'` | `lane='microcap'` | §2 lane 列 |
| 单票上限 | 20% | **5%** | `MICROCAP_SUGGESTED_SINGLE_CAP` 转正 |
| 滑点档 | 按成交额分位取档 | **同额取档后索引 +1（封顶末档）** | `MICROCAP_SLIPPAGE_TIER_BUMP` 转正 |
| 总仓上限 / regime 系数 / T+1 / 整手 / 门禁 | 0.8 | **同主档** | 只动小票特有经济学，两本账可比 |
| 初始资金 | 100 万 | 100 万 | 同 PaperBroker 默认 |

滑点上移实现：`matching.slippage_rate_for_amount` 加 `tier_bump` 参数（选中档位索引
+N 再 clamp 末档），撮合/门禁全程纯代码无 LLM 不变。

**T+1 语义不变**：今天 16:47 出的小票决策，明天 16:12 由小票驱动器转信号、按明天
开盘价撮合；首日无小票观点 → 驱动器空转返回摘要如实披露。

**job 编排**（新增两个；主档 job 只加 lane 过滤）：

```
16:10 paper_execute_daily        （加 lane='main' 过滤，其余不动）
16:12 paper_execute_microcap     （新）
16:20 paper settle（主档）
16:22 auto_pool_analysis_daily   （主档，不动）
16:24 paper_settle_microcap      （新，写小票库 paper_equity）
16:45 microcap_screening_daily   （既有，不动）
16:47 auto_pool_analysis_microcap（新）
```

失败语义沿用小票通道先例：批次 failed 上抛 RuntimeError 进 ops 历史，不阻断下游。
两库无共享写路径，16:10/16:12 并行无锁竞争（共用快照库只读）。

**时序提示**：16:47 入队的 10 只排在 16:22 主档批次后共用并发上限，小票分析结果
可能到晚间才齐——观察期预期行为，非缺陷。

## 4. 读侧（v1 只给 API，UI 不动）

- 既有 execution 读端点加 `book=microcap` 查询参数（缺省 main，行为不变）：持仓 /
  现金 / 净值曲线 / 订单历史。
- `GET /api/screening/pool?pool=microcap` 已有（#565），不动。
- **日报（20:30）加一节小票行**：小票池规模 / 今日分析 N 只 / 派发失败 / 小票账本净值。
- 按 lane 切分的记分卡与净值双曲线 UI：**不做**，lane 列落库后 SQL 可查，UI 留后续 delta。

## 5. 既有消费者审计（`pool` 列引入的兼容性）

加列后凡聚合 `pool_analysis_runs` 而不过滤 pool 的读者都会把小票行计入——逐点裁定：

| 读者 | 现状 | 改动 | 理由 |
|---|---|---|---|
| `daily_brief.py:127` 自动选股行 | `WHERE trade_date=?` 无 pool 过滤 | **加 `AND pool='main'`**，另起小票行 | 不改则日报主档行混入 10 条小票行，口径漂移 |
| `report_trigger.py:205` `_today_used`（研报触发预算） | `COUNT(*) WHERE trade_date=?` | **加 `AND pool='main'`** | 主档预算语义不变（红线）；小票分析量由「每日 10 只」自身兜底，v1 不另设预算 |
| `report_trigger.py:227` `_already_today`（同日去重） | 按 `(trade_date, symbol)` 查 | **不动** | 与幂等键全局语义一致：同日同标的无论哪条 lane 分析过都算重 |
| `api.py:3974` timeout_partial 披露 | `list_pool_analysis_runs(day)` | **不动** | 披露发生在 16:22 主档派发时，小票行 16:47 后才存在，时序上无交集 |
| `reconcile_pool_analysis_runs` | 全表对账 | **不动** | 对账应覆盖两池（正好是期望行为） |

## 6. 测试与上线

- **分类判定：非交互类**（前端零改动；建会话属无头日批，与既有主档自动批次同类）
  → 不走 E2E 门禁，人工首跑观察报告仍落 `tests/validation/`。
- 单测清单：
  1. ingest lane 归属（session→pool 反查命中 / 查不到回退 main）；
  2. 主驱动器 lane='main' 过滤（含存量默认值行为不变）；
  3. 小票驱动器：5% 单票聚合、滑点档上移（含 clamp 末档）、lane='microcap' 取数；
  4. 跨池去重选样（跳过 + 顺延补位，含补位不足 10 只的如实披露）；
  5. `pool` 列读写（写显式 microcap / 读过滤 main）；
  6. 日报小票行与主档行互不混。
- 回归红线：主档路径逐字节不变——两列均为 DEFAULT 'main' 加列，主驱动器过滤后行为
  与今天一致；既有测试全绿即为证据。
- 迁移：幂等 `ADD COLUMN`，无回填。
- 上线纪律：重建撞日批窗口红线照旧（先查 `/api/sessions` 与 `/api/v1/ops/jobs` 无
  running）；首跑选非窗口时段重启后，次一交易日 16:47 观察首跑。

## 7. 影响的 openspec specs（delta 地图）

| spec | 变更类型 | 要点 |
|---|---|---|
| `stock-screening` | MODIFIED + ADDED | microcap lane 章节：自动分析编排需求（新）；`pool_analysis_runs` pool 列 |
| `decision-outcome` | MODIFIED | `predictions.lane` 列 + ingest 归属规则 + 主驱动器过滤 |
| `broker-execution` | MODIFIED | 第二执行 DB、驱动器 lane/db_path 参数化、5% 上限、滑点 tier_bump、新 job 16:12/16:24 |
| `daily-ops-brief` | MODIFIED | 日报小票行；主档行 pool 过滤 |
| 研报触发相关（如 `research-report-*` 触发预算 spec） | MODIFIED | `_today_used` 加 pool='main'（若该行为无 spec 覆盖则按意图变更路由补 delta） |

## 8. 非目标（本 delta 明确不做）

- lane 维度的前端 UI（双曲线/页签）；
- 小票 lane 独立预算/限流机制（每日 10 只即兜底）；
- 小票池准入门槛重评（frontier_gate 判据扩展）；
- 小票通道读运行时配置（spec 明令禁止，不触碰）；
- 同日同标的跨 lane 双分析（刻意排除，见 §1.2）；
- 重叠标的复用主档分析结果（裁定与重开条件见 §1.2，后续 delta 候选）。

## 9. 实跑数据锚点（2026-11-09，设计依据）

- 主档池 36 只（L1 过 4091 / L2 Top100 / L3 keep 98）；小票池 40 只
  （L1 过 5196 / L3 keep 100，`frontier_gate=ready`）。
- 两池前 10 交集 4 只（002040/600006/600251/600496），且全在主档前 10 →
  小票批今日将跳 4 补 4。
- 两档 `factor_liquidity_rank_corr` 均为负（主档 -0.33 / 小票 -0.37）：
  当前因子组合在低流动性段给分更高，小票池与主档池存在结构性重合——lane 隔离
  的记分卡数据正好可用于检验两池是否产出有区分度的 alpha。
