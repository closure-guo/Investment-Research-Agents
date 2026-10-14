# Tasks: add-memory-confirmation

## 1. 后端 API（先写失败测试）
- [x] 1.1 `api.py`：`GET /api/memory/clauses`（status 过滤，默认 pending）+ `POST /api/memory/clauses/{id}/confirm` + `POST /api/memory/clauses/{id}/reject`
      —— 单测：三态过滤字段齐备；确认/驳回生效与幂等；未知 id 404；**未确认不注入回归保留**（既有 memory 单测零修改）
- [x] 1.2 TESTING 种子扩展（`memory` 键：条款 seed）供 E2E/隔离库使用

## 2. 前端页与入口
- [x] 2.1 `/memory` 页（PageShellC；三态筛选；详情 + 确认/驳回；AiBadge + StatusBadge；空态如实）+ 路由 + 侧边栏折叠图标 + 命令面板入口
- [x] 2.2 Vitest：三态筛选 / 确认流（列表迁移）/ 驳回流 / 空态 / AiBadge 与后果提示文案
- [x] 2.3 Playwright E2E（stub 种子 → 确认流；`tests/e2e/playwright/tests/memory-confirmation.spec.ts`）

## 3. 验证
- [x] 3.1 全量测试通过（后端 + 前端 + E2E stub 门禁）
- [x] 3.2 真实栈人工验证：页面打开（当前空态如实）、种一条 pending → 确认 → 注入集合变化（截图 + API 载荷）
- [x] 3.3 既有测试零修改（红线）
- [x] 3.4 验证报告落 `tests/validation/2026-10-14-add-memory-confirmation-validation.md`
