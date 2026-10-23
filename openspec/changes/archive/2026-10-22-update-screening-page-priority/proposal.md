# Proposal: update-screening-page-priority

## Why

2026-10-22 收盘后实测 `/screening`：精选池（3 只）排在市场温度计、因子信号、事件观察之后，
首屏 1200px 内完全看不到；池表是裸 `<table>`，没有标题、数量、口径，看起来像附属内容。
更严重的是事件观察清单全量平铺（`/api/events/watchlist` 当时返回 432 条、每条默认展开），
页面总高约 8 万 px——把「选股池」页面变成了「事件清单页」。

## What Changes

- 精选池置顶并卡片化：标题「精选池」+ 数量 + 批次日期 + 口径说明；陈旧/降级/移除横幅留在池卡片内。
- 上下文卡片顺序：市场温度计 → 因子信号 → 事件观察（排在池之后）。
- 事件观察改为固定高度滚动框（420px 上限、内部滚动、表头区固定）：
  - 行默认折叠（来源/标的/公告日/结论徽章/理由一行），点击行标题展开来源要素与证据；
  - 分页式渲染：首屏 50 条 + 「加载更多」，并展示总条数。

## Capabilities

- **Modified Capabilities**: `frontend`（「选股页」「选股页事件观察区块」两条 Requirement）

## Impact

- 代码：`frontend/src/pages/screening/ScreeningPage.tsx`、`frontend/src/pages/screening/EventWatchlistCard.tsx`。
- 测试：`frontend/src/test/screeningPage.test.tsx`、`eventWatchlistCard.test.tsx`、`screeningMarketRegime.test.tsx`。
- 不改后端契约、不改取数端点、不改轮询节奏；纯前端信息层级与渲染策略调整。
