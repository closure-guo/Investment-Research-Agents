# 人工验证报告：选股页信息层级（update-screening-page-priority）

**日期**：2026-10-23
**关联 delta**：`openspec/changes/update-screening-page-priority/`
**E2E 门禁**：`tests/e2e/playwright`（隔离 TESTING 栈 8001/5174）`event-watchlist.spec.ts` + `market-regime.spec.ts` → **6 passed**；
只读门禁 `factor-harvest-ui.spec.ts` 此前已对真实栈通过。
**截图**：`tests/e2e/playwright/tmp/screening-before.png` / `screening-after.png`

## 问题（实测）

- 池表是裸 `<table>`，排在市场温度计、因子信号、事件观察之后 → 首屏 1200px 内看不到选出来的 3 只。
- 事件观察 `items.map` 全量平铺且条目默认展开：`/api/events/watchlist` 当时返回 **432 条**，每条约 190px
  → 页面总高约 **8 万 px**，整页被事件清单占据。

## 改动

- `ScreeningPage.tsx`：池表包成 `screening-pool-card`（标题「精选池」+ 数量 + 批次日期 + 口径），
  陈旧/降级/移除横幅留在卡内；顺序改为 池卡片 → 市场温度计 → 因子信号 → 事件观察。
- `EventWatchlistCard.tsx`：清单进 `event-watchlist-scroll` 固定高度滚动框（`data-max-height=420`、
  `overflow-y-auto`）；显示总条数；首屏渲染 50 条 + 「加载更多」逐次追加；行默认折叠
  （`event-row-toggle-{id}`），明细（要素卡/证据）按需渲染。

## 验证证据

| 项 | 读数 |
|---|---|
| 区块顺序（真实 DOM） | `screening-pool-card` → `market-regime-card` → `factor-signal-card` → `event-watchlist-card` |
| 池卡片 | 「精选池 / 共 3 只 / 批次 2026-10-22」，首屏可见 3 行（600519 / 600900 / 601398） |
| 事件框 | 共 432 条；渲染 50 行；`clientHeight=420`、`scrollHeight=3386`、`overflow-y=auto` |
| 页面高度 | `document.body.scrollHeight = 1200`（= 视口高度，不再被清单撑长） |
| 前端单测 | `npx vitest run` → **916 passed**（新增：池卡片置顶/标题数量、固定高度+加载更多、默认折叠+展开） |
| 类型检查 | `npx tsc -b` → 0 错误 |
| E2E | `npx playwright test event-watchlist market-regime --config playwright.isolated.config.ts` → **6 passed** |

## 口径说明（测试调整均为「默认态变化」，非弱化断言）

- 单元测试 4 处、E2E 1 处「要素/证据」断言前补 `event-row-toggle-{id}` 点击——折叠只是默认态，
  展开后断言逐字不变。
- 两处「温度计/事件区块位于滚动容器首子节点」的旧断言按新契约改为「池卡片在首位，上下文卡片在后」。

## 结论

- [x] 精选池置顶且带标题/数量/批次/口径，首屏可见
- [x] 事件观察固定高度滚动、默认折叠、首屏 50 条可加载更多
- [x] 空态/失败态仍在池卡片内，未破坏既有三态语义
- [ ] archive（验证通过后可执行）
