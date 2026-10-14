# add-memory-confirmation 实施验证报告

**日期**: 2026-10-14
**范围**: 记忆确认入口（条款查询/确认/驳回 API + `/memory` 审批页）
**交付**: `api.py`（三端点 + `memory` 种子/`memory_reset`）、`memory/semantic.py`（+`reset_clauses` TESTING 复位助手）、`frontend/src/pages/memory/MemoryPage.tsx`（PageShellC）+ 路由/侧边栏/命令面板入口、三套测试

## 一、实施摘要

接线前：spec「语义记忆人工确认」要求「经人工确认后方可生效」，但 `confirm_clause`/`reject_clause` 只是 Python 函数——无 API、无界面，候选条款永远无法转正。接线后：

- **API**：`GET /api/memory/clauses?status=`（默认 pending，三态过滤）、`POST /api/memory/clauses/{id}/confirm`、`POST /api/memory/clauses/{id}/reject`（幂等；未知 id 404）；端点只包装既有 `list_clauses`/`confirm_clause`/`reject_clause`，零业务复制。
- **页面**：`/memory`（PageShellC 审批队列）——三态筛选（默认待确认）、条款详情（正文/来源/提炼时间）、确认/驳回；正文 `AiBadge`、状态 `StatusBadge`；确认按钮旁明示「确认后即刻生效注入风控辩论」；空态如实（等待月批提炼，注明 #327）。
- **入口**：侧边栏折叠图标（`aria-label="记忆"`）+ 命令面板。
- **TESTING 种子**：`memory` 键（条款 seed）+ `memory_reset`（E2E 每例复位）。
- `reset_clauses` 置于 `memory/semantic.py` 而非 api.py：表名 `memory_clauses` 只允许出现在 semantic.py（既有门禁测试逐文件扫描「无第二读入口」），api.py 自拼 SQL 会让门禁变红——docstring 已写明。

## 二、验证证据

| 项 | 命令 | 结果 |
|---|---|---|
| 后端（API + 既有 memory 域回归） | `uv run pytest tests/test_api_memory.py tests/memory -q` | **62 passed**（含：三态过滤字段齐备 / 确认生效 / 幂等 / 未知 id 404 / 未确认不注入回归保留——`tests/memory/` 既有测试零修改） |
| 前端 | `npm test -- --run src/test/memoryPage.test.tsx src/test/darkModeShortcuts.test.tsx` | **17 passed**（memoryPage 9 例：三态筛选/确认流/驳回流/空态/AiBadge 与后果提示；darkModeShortcuts 8 例） |
| E2E（默认 config，stub 后端） | `CI=1 npx playwright test tests/memory-confirmation.spec.ts` | **4 passed**（确认流/驳回流/三态过滤/空态如实；连跑两次绿） |
| lint | ruff check/format 改动文件 | 全过 |

**E2E 冷启动 flake（如实记录）**：首次运行 1 failed（确认流）+1 flaky（三态过滤）；随后 `--retries=0` 与默认重试各跑一次均 **4 passed**——判定为首次冷启动（vite 首次编译新页/后端首次初始化种子库）的时序抖动，非断言缺陷；**待夜间 CI（全新环境）复跑复核**。

**E2E 端口处置**：运行前确认 `GET /api/sessions` 0 running → `docker stop` 后端/前端释放 8000/5173 → 运行 → `docker start` 恢复（复检前端 200、306 会话在库）。

## 三、既有文件改动清单（逐条）

1. `frontend/src/test/darkModeShortcuts.test.tsx`：CommandPalette 新增必填 prop `onOpenMemory` → 测试工装同步（契约合法扩容，断言不变）
2. `frontend/src/types.ts` / `CommandPalette.tsx` / `App.tsx`：入口与类型扩展（非测试）
3. `memory/semantic.py`：+`reset_clauses`（TESTING 专用，无生产调用方，理由见 docstring）
4. 其余既有测试零修改。

## 四、遗留

1. 真实栈人工验证：合并部署后打开 `/memory`（当前空态如实；种一条 pending → 确认 → 已确认列表 + 注入集合变化，截图）。
2. 月批提炼自动接线仍是 issue #327 的「月批接线」后续项（本 delta 只解确认入口）。

## 五、结论

记忆确认入口完成：spec「人工确认」首次获得可操作面（API + 审批页）；未确认不注入的既有保证经回归测试保持；E2E 四场景绿（冷启动 flake 已记录并留待夜间复跑复核）。
