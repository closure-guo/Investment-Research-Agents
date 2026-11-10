## Why

小票通道（`microcap_screening_daily`，16:45）已在生产每日出池（流动性下限 1000 万档，
2026-11-09 实跑 40 只、`frontier_gate=ready`），但止步观察：`orders_placed` 恒 0、不进
分析链、决策零产出。两个转正记录字段（`MICROCAP_SUGGESTED_SINGLE_CAP` /
`MICROCAP_SLIPPAGE_TIER_BUMP`）当初注释即写明「执行接通时生效」，观察通道若永不接通
分析，小票段因子有效性（低流动性段容量租金下的真实可交易性）就永远没有读数。设计依据：
`docs/superpowers/specs/2026-11-10-microcap-lane-daily-analysis-design.md`（用户逐节批准）。

## What Changes

- 新增日批 job `auto_pool_analysis_microcap`（16:47）：读小票池按 rank 取前 10
  （`AUTO_POOL_MICROCAP_MAX_SYMBOLS`）跑五层深度分析；与主档批次同日重叠标的
  **跳过 + 顺延补位**（裁定与重开条件见设计 §1.2——复用被同日日主判定误杀 +
  时序依赖，否决）；时效性重校验、幂等、失败重派语义全部继承主档批次。
- `pool_analysis_runs` 加 `pool TEXT NOT NULL DEFAULT 'main'` 列（幂等 ALTER，存量行
  自动 main，无需回填）；小票批写行显式 `pool='microcap'`；列不进主键。
- `predictions` 加 `lane TEXT NOT NULL DEFAULT 'main'` 列（同款幂等加列）；ingest 按
  `session_id` 反查 `pool_analysis_runs.pool` 定归属，查不到回退 main。
- 主档执行驱动器（16:10 `paper_execute_daily`）加 `lane='main'` 过滤——小票决策从此
  进不了主账本；主档行为与今天逐位一致（存量行默认 main）。
- 新增第二执行账本 `EXECUTION_MICROCAP_DB_PATH`（默认 `data/execution_microcap.db`），
  五张表同 DDL 新建；驱动器参数化 `lane` + `db_path` 复用；新 job
  `paper_execute_microcap`（16:12）与 `paper_settle_microcap`（16:24）。
- 小票账本执行经济学转正：单票上限 5%（`MICROCAP_SUGGESTED_SINGLE_CAP`）、滑点档
  同额取档后索引 +1 封顶末档（`MICROCAP_SLIPPAGE_TIER_BUMP`，`slippage_rate_for_amount`
  加 `tier_bump` 参数）；总仓 0.8 / regime 系数 / T+1 / 整手 / 门禁与主档一致。
- 既有消费者兼容修复（设计 §5）：`daily_brief.py` 自动选股行与 `report_trigger.py`
  `_today_used` 预算计数加 `pool='main'` 过滤（主档口径不变）；`_already_today` 同日
  去重与 `api.py` timeout 披露、`reconcile_pool_analysis_runs` 对账**不动**（理由在案）。
- 读侧 v1：既有 execution 读端点加 `book=microcap` 查询参数（缺省 main 行为不变）；
  日报（20:30）加小票行（池规模 / 今日分析 N 只 / 派发失败 / 小票账本净值）。

不改变：主档池分析全链（逐字节）、小票通道 L1-L3 链与档位（仍禁读运行时配置）、
16:00 预测结算（lane 无关）、cohort 评估（自建 universe 无交互）、前端 UI（零改动）。

## Capabilities

### New Capabilities

（无——本变更全部落在既有 capability 的行为扩展上。）

### Modified Capabilities

- `stock-screening`: microcap lane 章节新增「小票池每日自动分析」需求（编排 / 跨池去重
  / 开关），及 `pool_analysis_runs` 归属列（写侧显式 microcap、读侧按需过滤）。
- `decision-outcome`: `predictions.lane` 归属列 + ingest 反查归属规则；同日日主判定
  语义不变（lane 无感维持）。
- `trade-execution`: 主档执行通道 lane 过滤（MODIFIED「信号到订单的日批执行」）+
  小票执行通道与独立账本（ADDED：16:12/16:24 编排、5% 单票、滑点 tier_bump）。
- `broker-execution`: 小票账本只读查询（`book=microcap` 参数，缺省 main 不变）。
- `daily-ops-brief`: 日报小票行；主档自动选股行 `pool='main'` 过滤。
- `report-triggered-analysis`: 「触发上限与预算」MODIFIED——`_today_used` 预算计数
  仅计 `pool='main'` 运行行，小票行不挤占主档触发预算。

## Impact

- 代码：`screening/pool_analysis.py`（编排参数化）、`screening/store.py`（pool 列）、
  `outcome/track_record/model.py`+`ingest.py`（lane 列与归属）、`execution/driver.py`+
  `matching.py`+`store.py`+`brokers/paper.py`（参数化与第二账本）、`outcome/scheduler.py`+
  `outcome/ops/jobs.py`（三新 job + 主档 job 过滤）、`outcome/daily_brief.py`（过滤 + 小票行）、
  `screening/report_trigger.py`（预算过滤）、`api.py`（读端点 book 参数）。
- DB 迁移：`screening.db` 与 `track_record` 库各一列幂等 `ALTER TABLE ADD COLUMN ...
  DEFAULT 'main'`；新文件 `data/execution_microcap.db` 首次连接自建表。
- 配置：新 env `AUTO_POOL_MICROCAP_ENABLED` / `AUTO_POOL_MICROCAP_MAX_SYMBOLS` /
  `EXECUTION_MICROCAP_DB_PATH`（均有默认值，零配置可跑）。
- 测试：非交互类判定（前端零改动、无头日批）→ 不走 E2E 门禁；单测 6 组 +
  主档回归既有全绿；人工首跑观察报告落 `tests/validation/`。
- 风险：主档路径任何行为漂移即红线——两列 DEFAULT 'main' + 主驱动器过滤为仅有的
  主档触碰点，回归证据 = 既有测试全绿。
