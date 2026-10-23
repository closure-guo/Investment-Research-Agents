# Tasks: update-screening-page-priority

> 上线顺序 = 任务顺序。TDD：先红后绿；每层测试先行。

## T1 精选池置顶与卡片化

- [x] 先红后绿：`screeningPage.test.tsx` 新增「池卡片先于温度计/事件观察 + 标题/数量/批次」
- [x] `ScreeningPage.tsx`：池表包成 `screening-pool-card`（标题/数量/批次/口径），横幅留在卡内
- [x] 上下文卡片顺序改为：市场温度计 → 因子信号 → 事件观察

## T2 事件观察固定高度 + 折叠 + 分页

- [x] 先红后绿：`eventWatchlistCard.test.tsx` 新增「固定高度滚动 + 首屏 50 条 + 加载更多」
- [x] 先红后绿：新增「默认折叠，点击行标题展开明细」
- [x] `EventWatchlistCard.tsx`：`event-watchlist-scroll`（max-height 420）、总条数、`event-watchlist-load-more`、`event-row-toggle-{id}`
- [x] 既有 4 个要素断言用例补「先展开再断言」（口径不弱化：折叠只是默认态）

## T3 收口

- [x] `npx vitest run` 全绿（916 passed）
- [x] `npx tsc -b` 通过
- [x] `openspec validate update-screening-page-priority --strict` 通过
- [x] 重建前端镜像 + 浏览器实测截图落 `tests/e2e/playwright/tmp/`，人工验证报告落 `tests/validation/2026-10-23-screening-page-priority-validation.md`
