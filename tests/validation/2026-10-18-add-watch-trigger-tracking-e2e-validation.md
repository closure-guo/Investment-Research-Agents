# 人工验证报告（补）: add-watch-trigger-tracking 任务 12 E2E 收口

**日期**: 2026-10-18
**验证人**: ZCode 验证 runner（E2E 门禁 + stub 契约回归）
**关联 delta**: openspec/changes/add-watch-trigger-tracking/（任务 12 由本期补上真实 E2E）
**E2E 门禁**: **通过**——timeline 配置（三端口对）全量在 stub 变更后运行；新增 spec 与 K 线回归双绿

## 背景

任务 12 原以「§5.6 Playwright e2e/ 基建未建设」如实注记替代（vitest 契约 + 人工浏览器截图）。基建已由 Playwright 接管消解后，本期补上真实 E2E：需要 stub 管线产出 watch 决策携带结构化触发位（此前 stub 决策为 hold 且无触发位，报告不渲染触发位行、K 线无触发参考线，无法构成链路覆盖）。

## 变更

1. `_llm_utils._STUB_TRADE_DECISION`：action `hold` → `watch`，新增 `trigger_high=181.0` / `trigger_low=178.0`（stub K 线最新收盘 = 180.0，方向校验 `high > close > low` 有余量；过价位门禁 `_check_structured_trigger_levels`）；
2. `tests/nodes/test_stub_contract_sync.py`：契约护栏更新（watch + 触发位在场 + 方向相容断言）；
3. 新增 `tests/e2e/playwright/tests/report-watch-trigger-lines.spec.ts`（pipeline 端口对 8002/5175）：深度分析完成 → 报告「交易决策」节 `上破触发位` 行含 181、`下破触发位` 行含 178 → K 线卡（chart-stock-price）heading + canvas 挂载；注册进 `playwright.timeline.config.ts` testMatch。

## 验证结果

| # | 场景 | 证据 | 通过 |
|---|---|---|---|
| 1 | 触发位链路 E2E | 新 spec 实跑：**1 passed（42.9s）**——报告节触发位行渲染申报值 + K 线卡 canvas 挂载 | ✅ |
| 2 | K 线既有回归 | `report-kline-chart.spec.ts` 实跑 1 passed（57.1s，stub 切 watch 后卡片/标题断言不变） | ✅ |
| 3 | stub 契约护栏 | `test_stub_contract_sync`（watch + 触发位方向相容）；tests/nodes + test_pipeline_stub + contract 合计 **702 项绿** | ✅ |
| 4 | timeline 全量门禁 | stub 变更后全量套件 `CI=1 --workers=8 --retries=0`：**35 passed**（含本 spec；变更前 34+1 skipped） | ✅ |
| 5 | 默认套件（含全量后端） | 交 CI 执行：本机默认套件端口 8000/5173 与运行中生产栈相同，按 incident 038 纪律不在容器运行时本地跑 | 待 CI |

（markLine 结构级断言由既有前端 vitest `stockPriceKline.test.tsx` 触发位正反用例承担，canvas 内容不做 DOM 断言属正确边界。）

## 未达成项 / 披露

1. owner 签字仍待（沿用原验证报告结论）。
2. 触发位链路 E2E 依赖 stub 决策恒为 watch——buy/sell 路径的触发位「不渲染」语义由单测（report_decision_render / chartsMarkLine 正反用例）承担，不在 E2E 面。
