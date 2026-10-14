# Tasks: add-factor-library-page

## 1. 后端只读接口（先写失败测试）
- [ ] 1.1 `api.py`：`GET /api/factors`（列表）+ `GET /api/factors/{factor_id}`（详情）；空库返回空列表
      —— 单测：字段齐备（状态/来源/ICIR/衰减标记）；详情含表达式/IC 序列/事件历史；空库 200 空数组；未知 id 404
- [ ] 1.2 TESTING 种子扩展（`factors` 键：两因子 seed：active+llm / retired+builtin + 事件）

## 2. 前端页与入口
- [ ] 2.1 `/factors` 页（PageShellB：筛选栏 + 表格 + 行展开）+ 路由 + 侧边栏折叠图标 + 命令面板入口
- [ ] 2.2 Vitest：列表渲染 / 状态与来源筛选 / 徽章（StatusBadge + AiBadge）/ 行展开 / 空态
- [ ] 2.3 Playwright E2E（stub 种子 → 列表与筛选；`tests/e2e/playwright/tests/factor-library.spec.ts`）

## 3. 验证
- [ ] 3.1 全量测试通过（后端 + 前端 + E2E stub 门禁）
- [ ] 3.2 真实栈人工验证：页面打开（当前库空 → 空态如实；截图）
- [ ] 3.3 既有测试零修改（红线）
- [ ] 3.4 验证报告落 `tests/validation/2026-10-14-add-factor-library-page-validation.md`
