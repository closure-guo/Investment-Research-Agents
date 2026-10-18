# Proposal: add-watch-trigger-tracking

## Why

watch 决策的双向触发位目前只存在于 `reeval_triggers` 自由文本里——K 线图不画、机器不可追踪、预测池不携带。601066（2026-10-08）实证：watch 决策的触发位 22.91/24.6 仅出现在散文中，K 线图只画 entry/stop/target（watch 无此三价），触发位从此「系统自己看不见」。同时发现两处承诺窟窿：track-record 主规范已承诺「完整分析原文可由该观点的 session_id 关联」，但 `predictions` 表根本没有 `session_id` 列（`ingest.py` 收到该参数却未落库）；国庆 8 天数据真空实证（报告日 10-08 vs 行情截止 09-30）显示触发位锚定旧收盘价，报告无任何失效声明。

watch 是高频决策（预测池 open 记录中 neutral 占大头），触发位是它唯一的可执行承诺——这份承诺应当是结构化数据，而不是散文。

## What Changes

- `TradeDecision` 新增 `trigger_high`/`trigger_low` 可选数值字段（schema 宽松清洗先例；watch 申报约束由规则节点承担，同 `require-watch-hold-rationale` 先例）
- 终稿完整性检查扩展：watch 终稿缺触发位打回一次，仍缺放行 + 如实标注
- 价位门禁扩展：结构化触发位直接做方向校验（`trigger_high` ≤ 最新收盘 / `trigger_low` ≥ 最新收盘 → 空洞形态 anomaly），复用既有打回/未恶化放行/恶化阻断分层
- K 线图采集携带并渲染双向触发位水平虚线（服务端 PNG + 前端交互图同一份数据）
- 报告「交易决策」节渲染上破/下破触发位行（缺失如实「未申报」）、入池跟踪声明行；报告日与行情截止日间隔 > 3 自然日时渲染数据真空提示行
- `predictions` 表补 `session_id`/`trigger_high`/`trigger_low` 列（补 spec 已承诺的 session_id 窟窿），ingest 落库

**非目标**（明确不做）：触发位的行情轮询监控与触发自动提醒（本 delta 只落结构化字段铺路）；前端会话页展示关联预测的结算结果（交互类，后续 delta，本 delta 的 `session_id` 列为其铺路）。

## Capabilities

- **Modified Capabilities**:
  - `track-record`（观点数据模型补 session_id/trigger 列）
  - `report-kline-chart`（数据采集与双端渲染扩展触发位——基线在待归档 change `update-price-chart-kline`，本 delta 以其 spec 为基线，archive 顺序上本 delta 排在其后）
- **New Capabilities**: 无（均为既有 capability 的 ADDED requirement）
  - `agent-node-contracts`：watch 双向触发位结构化契约
  - `price-level-tooling`：结构化触发位方向校验
  - `report-decision-rendering`：watch 触发位与入池跟踪渲染（含数据真空提示）

## Impact

- 代码：`models.py`（TradeDecision）、`nodes/validate.py` 或 `nodes/risk.py`（终稿完整性检查扩展）、`metrics/decision_price_check.py`（结构化触发位校验）、`charts.py`（决策位采集与画线）、`nodes/report.py`（渲染）、`outcome/track_record/model.py` + `ingest.py`（DDL + 落库）、`frontend/` K 线组件（新参考线渲染）
- Prompt：`prompts/trader.md`（watch 触发位申报纪律）——修改后 MUST 执行 `scripts/deploy_prompts.py` 发布（prompt-deploy-consistency 门禁）
- 数据：`predictions` 表 ALTER 加列（append-only 兼容，存量行 trigger 为 NULL 不追溯）
- 交互类判定：K 线前端组件新增参考线渲染 → 适用 E2E 门禁（§3 Step 4.5）
