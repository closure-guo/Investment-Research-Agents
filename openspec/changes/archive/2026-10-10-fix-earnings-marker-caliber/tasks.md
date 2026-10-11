# Tasks: fix-earnings-marker-caliber

## 1. 规范与实现

- [x] 1.1 后端 `charts.py`：`_mark_earnings` 注记「财报」→「报告期止」；`build_chart_data` 注释「财报发布日期（从年报报告日推算）」→ 如实表述为报告期截止日
- [x] 1.2 前端 `Charts.tsx`：折线卡片标题「红色虚线为年报发布日」→「红色虚线为年报报告期截止日」；热力图卡片标题「年报发布窗口期股价变化（%）」→「年报报告期窗口股价变化（%）」
- [x] 1.3 前端 vitest 契约同步（`stockPriceKline.test.tsx` 注释措辞），`npm test` 全绿
- [x] 1.4 后端图表测试全绿（`pytest tests/ -k chart`）

## 2. 验证

- [x] 2.1 人工验证：stub 会话报告页截图确认折线/K 线两形态卡片标题与 PNG 竖线注记新措辞，落 `tests/validation/2026-10-10-fix-earnings-marker-caliber-validation.md`
- [x] 2.2 如实注记：§5.6 Playwright e2e 基建未建设（#162 待裁决），本 delta 以前端 vitest 契约 + 人工浏览器验证替代，不静默勾选

## 3. 收口

- [x] 3.1 PR 合并后 sync + archive
- [ ] 3.2 issue #243 子项 3 核销评论
