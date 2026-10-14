# Proposal: add-memory-confirmation

## Why

decision-memory 规范要求「候选条款 SHALL 经**人工确认**后方可生效注入风控辩论层」，但运行态**没有任何确认入口**：`confirm_clause` / `reject_clause` 只是 `memory/semantic.py` 的 Python 函数（无 API、无界面、无脚本）。语义记忆因此永远停留在「候选」态、永不转正——记忆层的设计收益（已结算经验回流风控辩论）为零。issue #327 已登记「月批接线」后续项，但即便月批接线完成，没有确认入口仍然卡死。

## What Changes

- **后端 API**：`GET /api/memory/clauses?status=pending|confirmed|rejected`（条款列表：id/正文/来源/提炼时间/状态）、`POST /api/memory/clauses/{id}/confirm`、`POST /api/memory/clauses/{id}/reject`（幂等；未知 id → 404；返回条款当前状态）。
- **前端 `/memory` 页**（复用设计系统 PageShellC 审批队列：左列表 + 右详情 + 确认/驳回）：三态筛选（待确认/已确认/已驳回）、条款正文与来源、AiBadge（条款由 LLM 提炼）、StatusBadge（三态）；入口 = 侧边栏折叠图标 + 命令面板。
- 既有保证不变：未确认条款 SHALL NOT 出现在任何 prompt 中（回归测试保留）；确认/驳回后风控辩论注入集合随之变化。

非目标（Out of scope）：

- 不做月批提炼的自动接线（issue #327 的「月批接线」独立处理；本 delta 只解确认入口）。
- 不做条款编辑/合并（只支持确认与驳回）。
- 不做情景记忆（episodic）的界面（其检索无需人工确认）。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `decision-memory`: 新增「确认操作入口」要求（API 与界面）。
- `frontend`: 新增「记忆确认页」要求。

## Impact

- **后端**：`api.py` 新增三个端点（包装既有 `list_clauses` / `confirm_clause` / `reject_clause`，零业务逻辑复制）。
- **前端**：`frontend/src/pages/memory/`（新页）+ 路由 + 侧边栏/命令面板入口。
- **测试**：API 单测（列表过滤/确认/驳回/404/幂等/未确认不注入回归）；前端 Vitest（三态/确认流/空态）；Playwright E2E（stub：确认后待确认列表减一、已确认列表增一）。
