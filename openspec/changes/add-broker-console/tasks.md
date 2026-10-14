# Tasks: add-broker-console

## 1. 后端只读接口（先写失败测试）
- [ ] 1.1 `api.py`：`GET /api/broker/overview` + `/positions` + `/orders` + `/gate-log`
      —— 单测：空账户如实（null vs 0 语义）；字段齐备（状态/原因码/decision_id/signal_id）；gate-log 过滤（仅 rejected）；快照缺失时市值 null 标注
- [ ] 1.2 TESTING 种子扩展（`broker` 键：cash + 两持仓 + 四态订单 + 一条 ST_FLAG 拒绝）供 E2E

## 2. 前端页与入口
- [ ] 2.1 `/broker` 页（PageShellA：指标条 + 持仓构成图 + Tab 表（持仓/订单/门禁拒绝））+ 路由 + 侧边栏折叠图标 + 命令面板入口
- [ ] 2.2 Vitest：指标条（含 null 标注）/ 构成图 / Tab 切换 / 状态徽章映射 / 空态
- [ ] 2.3 Playwright E2E（stub 种子 → 台账四态与拒绝视图；`tests/e2e/playwright/tests/broker-console.spec.ts`）

## 3. 验证
- [ ] 3.1 全量测试通过（后端 + 前端 + E2E stub 门禁）
- [ ] 3.2 真实栈人工验证：页面打开（当前空账户 → 空态如实；执行链接线后随日批出现真实订单，挂 #325 后补验）；截图
- [ ] 3.3 既有测试零修改（红线）
- [ ] 3.4 验证报告落 `tests/validation/2026-10-14-add-broker-console-validation.md`
