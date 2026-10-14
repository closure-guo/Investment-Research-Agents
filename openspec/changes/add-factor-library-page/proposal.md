# Proposal: add-factor-library-page

## Why

因子库（`add-factor-library-and-monitor`）交付了五道闸准入、双层查重、分桶保种、衰减监控与 builtin 注册路径，但**只有后端**：库里有哪些因子、各自 ICIR/闸门结果/衰减状态、被 LLM 提案进来的因子长什么样——人无从查看。设计系统 proposal 的 7 页面愿景明列「因子库」页；没有它，因子治理（谁被降级、为什么）只能翻库或日志。

## What Changes

- **后端只读接口**：`GET /api/factors`（列表：名称/表达式/状态（active/retired）/来源（builtin/llm）/ICIR/最近闸门结果/衰减标记）、`GET /api/factors/{id}`（详情：DSL、IC 序列、事件历史）；SHALL NOT 暴露任何写操作（写路径仍只属于五道闸与监控）。
- **前端 `/factors` 页**（PageShellB 列表页：筛选栏（状态/来源）+ 表格 + 行展开详情）：状态徽章（StatusBadge）、LLM 提案来源标注（AiBadge）、空态如实。
- 入口：侧边栏折叠图标 + 命令面板。

非目标（Out of scope）：

- 不做因子编辑/手工入库/手工降级（写路径不变，界面只读）。
- 不做因子回测/试算界面（属后续评估面）。
- 不做提案审核流（因子提案 agent 的准入由五道闸自动裁决，本页只呈现结果）。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `factor-library`: 新增「因子库只读查询」要求。
- `frontend`: 新增「因子库页」要求。

## Impact

- **后端**：`api.py` 两个只读端点（包装既有 `list_factors` / `list_events` / `list_ic_series`，零业务复制）。
- **前端**：`frontend/src/pages/factors/`（新页）+ 路由 + 入口。
- **测试**：API 单测（列表字段/详情/空库/404）；前端 Vitest（列表/筛选/展开/徽章/空态）；Playwright E2E（stub 种子两因子：active+llm 与 retired+builtin）。
