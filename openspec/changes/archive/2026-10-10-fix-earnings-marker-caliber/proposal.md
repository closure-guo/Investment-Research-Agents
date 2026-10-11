# Proposal: fix-earnings-marker-caliber

## Why

股价图上的红色虚线标注数据源是利润表「报告日」（报告期截止日，如 2024-12-31），但双端文案均自称「财报发布日」：

- 服务端 PNG：竖线注记「财报」+ 代码注释「财报发布日期（从年报报告日推算）」
- 前端折线卡片标题「红色虚线为年报发布日」、热力图卡片标题「年报发布窗口期股价变化」

FY2025 实际披露在 2026 年 3–4 月，标注落在 12-31 非披露窗口（issue #243 子项 3，10-05 评估已因此误读标记位置）。真实披露日历接口（东财）当前 IP 受限不可验证，先把措辞修正为与数据口径一致。

## What Changes

- 标注语义如实化：双端「财报/年报发布日」措辞统一改为「报告期截止日」；服务端竖线注记「财报」改为「报告期止」；代码注释同步
- 数据口径不变（仍以报告期截止日定位竖线），不改任何取数逻辑

## Capabilities

### New Capabilities

- （无）

### Modified Capabilities

- `report-kline-chart`: MODIFIED「K 线图双端渲染」——标注语义从「财报发布日」更正为「报告期截止日」，措辞约束入规范

## Impact

- `src/finance_agent/charts.py`（注记文案 + 注释）
- `frontend/src/Charts.tsx`（折线卡片标题、热力图卡片标题）
- 前端 vitest 契约用例注释同步（`stockPriceKline.test.tsx`）
- 交互类变更：走完整管线，人工验证报告落 tests/validation/
