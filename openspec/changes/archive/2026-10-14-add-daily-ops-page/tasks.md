# Tasks: add-daily-ops-page

## 1. 后端批次历史（先写失败测试）
- [x] 1.1 `screening/store.py`：`list_batches(limit=20)`（trade_date 倒序，字段含 status/error/起止时间/各层计数/pool_size）；`api.py`：`GET /api/screening/batches`
      —— 单测：字段齐备；空库 200 空数组；limit 钳制
- [x] 1.2 TESTING 种子扩展（`screening` 键已有 batch/pool；补 batches 历史两态种子）

## 2. 前端页与入口
- [x] 2.1 `/ops` 页（PageShellD：数据驱动时间线（/api/ops/jobs 排程排序）+ 状态徽章五态映射 + 摘要/错误逐字 + 一键补跑（复用补跑端点，409/关闭语义如实提示）+ 选股批次历史表 + 评估运维跳转）+ 路由 + 侧边栏折叠图标 + 命令面板入口
- [x] 2.2 Vitest：时间线排序（按排程）/ 五态徽章映射 / 补跑调用与 409 提示 / 批次历史表 / 空态
- [x] 2.3 Playwright E2E（stub 种子：时间线 10 项 + 两批次；`tests/e2e/playwright/tests/daily-ops.spec.ts`）

## 3. 验证
- [x] 3.1 全量测试通过（后端 + 前端 + E2E stub 门禁）
- [x] 3.2 真实栈人工验证：页面打开——时间线含「选股 15:40」等 10 项、当前快照封禁期的失败/未跑态如实；补跑一次并核对历史落行；批次历史空态/数据态；截图
- [x] 3.3 既有测试零修改（红线）
- [x] 3.4 验证报告落 `tests/validation/2026-10-14-add-daily-ops-page-validation.md`
