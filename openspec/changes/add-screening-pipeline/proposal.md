# Proposal: add-screening-pipeline

## Why

- L0-L3 各层就绪后需要日批编排把它们串成每日自动产出精选池的管线，否则选股能力只能靠手工触发、无法形成前向跟踪数据。
- 从 L3 到五层深度分析之间存在时间差，标的可能已涨停/停牌/出新公告，直接进入深度分析会产生基于过期状态的决策。
- 用户需要可见的候选池界面（含贡献分解与入选天数）与"一键深度分析"入口，否则选股产出无法消费。

## What Changes

- 新增 `screening/pipeline.py` 日批编排：收盘后依次执行 快照更新确认 → L1 → L2 → L3 → 精选池落库；任一步失败则批次标记失败并触发快照告警同款横幅，禁止用残缺结果出池。
- 信号时效性校验节点：精选池标的进入深度分析前，重新检查当日状态（停牌/一字涨跌停/新发事件）；状态恶化则从本次分析名单移除并记录原因。
- 前端新增 `/screening` 页：当日候选池表格（综合分/因子贡献分解 hover/连续在榜天数/L3 理由/状态标签）+ 「对精选池跑深度分析」按钮（触发现有 `POST /analysis` 批量流程，复用 SSE 进度）。
- 候选池每日落库（含未入选的 L2 全量名单），作为前向命中率统计的原料。

非目标（Out of scope）：

- 不做自动下单（属执行层 delta）；深度分析默认批量并发 2（防 token 风暴），可配置。
- 不做因子库管理界面与组合级战绩页（后续体验 delta）。

## Capabilities

### Modified Capabilities
- `stock-screening`: 新增日批编排、精选池落库、信号时效性校验。
- `frontend`: 新增 `/screening` 路由页。
- `pipeline-events`: 批量深度分析的 SSE 进度复用契约。

## Impact

- **后端**：`src/finance_agent/screening/pipeline.py`（新增）、`api.py` 新增 `/api/screening/*` 接口。
- **前端**：`frontend/src/pages/screening/`（新增页面）、侧边栏入口。
- **测试**：日批失败不产残缺池单测；时效性校验移除单测；前端页面 Vitest + Playwright stub 用例。
- **验证**：连续 5 个交易日跑通完整日批并人工核对精选池，报告落 `tests/validation/`。
