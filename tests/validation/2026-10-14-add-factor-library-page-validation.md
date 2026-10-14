# add-factor-library-page 实施验证报告

**日期**: 2026-10-14
**范围**: 因子库只读接口 + `/factors` 列表页（PageShellB）
**交付**: `api.py`（`GET /api/factors` + `GET /api/factors/{id}` + `factors` 种子/`factors_reset`）、`factors/library.py`（+`reset_factor_library` TESTING 复位）、`frontend/src/pages/factors/FactorLibraryPage.tsx` + 路由/侧边栏/命令面板入口、三套测试、E2E 独立库隔离（`FACTOR_LIBRARY_DB_PATH` 注入两个 config + .gitignore）

## 一、实施摘要

因子库此前只有后端（五道闸/衰减监控/分桶不可见）。本 delta：列表（名称/表达式/状态 active|retired/来源 builtin|llm/ICIR/最近闸门结论/衰减标记）+ 详情（完整 DSL/IC 序列/事件历史）；页面筛选栏（状态/来源）+ 表格 + 行展开；`llm` 来源 AiBadge、状态 StatusBadge；空态如实。接口只读（写路径仍只属五道闸与监控）。

## 二、验证证据

| 项 | 命令 | 结果 |
|---|---|---|
| 后端 | `uv run pytest tests/test_api_factors.py tests/factors -q` | **292 passed**（字段齐备/详情/空库 200 空数组/未知 id 404） |
| 前端 | `npm test -- --run src/test/factorLibraryPage.test.tsx src/test/darkModeShortcuts.test.tsx` | **17 passed** |
| E2E（默认 config） | `CI=1 npx playwright test tests/factor-library.spec.ts` | **4 passed**（列表/筛选/行展开/空库空态） |
| lint | ruff check/format 改动文件 | 全过 |

E2E 端口处置：0 running → `docker stop` 后端/前端 → 运行 → `docker start` 恢复（复检前端 200）。

## 三、既有/周边文件改动清单（逐条）

1. `tests/e2e/playwright/playwright.config.ts` / `playwright.isolated.config.ts`：注入 `FACTOR_LIBRARY_DB_PATH`（seed/factors_reset 会清表——不隔离会污染开发库 `data/factor_library.db`；同 SCREENING_DB_PATH 既有模式）
2. `.gitignore`：+`data/test-e2e-factor-library.db*`
3. `frontend/src/test/darkModeShortcuts.test.tsx`：palette 新必填 prop 工装同步（断言不变）
4. `factors/library.py`：+`reset_factor_library`（TESTING 专用）
5. 其余既有测试零修改。

## 四、遗留

真实栈人工验证（合并部署后打开 `/factors`：当前空库 → 空态如实；截图）挂合并后部署核验。

## 五、结论

因子库页交付：只读接口 + PageShellB 列表页 + 徽章语义（AiBadge 标 llm 来源、StatusBadge 标状态）在 stub E2E 上四场景绿；E2E 造数库隔离配套齐全（配置注入 + .gitignore）。
