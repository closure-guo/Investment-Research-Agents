# add-daily-ops-page 实施验证报告

**日期**: 2026-10-14
**范围**: 选股批次历史只读接口 + `/ops` 日批运维页（PageShellD，数据驱动时间线）
**交付**: `screening/store.py`（+`list_batches`）、`api.py`（`GET /api/screening/batches` + 种子批次历史两态）、`frontend/src/pages/ops/DailyOpsPage.tsx` + 路由/侧边栏/命令面板入口、三套测试、`PageShellD` 可选契约扩容（`ShellBatch.statusLabel?`）

## 一、实施摘要

- **当日时间线**：数据驱动（`GET /api/v1/ops/jobs` 排程时刻升序，**不硬编码任务清单**——当前 10 任务，注册表扩容自动跟随）；五态徽章映射 ok→active「成功」/ failed→failed「失败」/ 今日无运行行→retired「未跑」/ running→running「运行中」/ skipped-disabled→degraded「跳过」；摘要与失败原因**逐字**；一键补跑（`POST /api/v1/ops/jobs/{id}/run`，202/409 already_running/409 cohort_disabled/500 各按端点返回如实提示；任何响应后刷新运行历史——失败尝试同落新行）。
- **选股批次历史表**：日期倒序 / 状态 / 各层计数 / 池数 / 错误逐字。
- **评估运维跳转**（→ 设置页 eval-ops 分区）；各区块空态与取数失败态**分离**（批次通道 500 不得回落成「暂无历史」空态冒充正常——自审修复项）。
- **壳组合**：每任务一张批次卡（title=时刻·任务名、subtitle=摘要/错误/补跑、status=五态徽章），`steps` 留空（PageShellD 的 steps label 渲染于徽章内 `whitespace-nowrap`，容不下摘要/按钮——文件头注明；「一项 + 任务作步骤」的组合形态留作后续迭代）。
- **契约扩容**：`ShellBatch.statusLabel?` 可选 prop（省略行为不变，既有壳测试零改动）——壳的批次徽章默认文案是生命周期词（正常/已停用/降级），与本页要求的「成功/失败/未跑/跳过」语义不符；若改为页内自绘徽章将违反「禁止自绘状态样式」主规范，故取可选 prop 路线。

## 二、验证证据

| 项 | 命令 | 结果 |
|---|---|---|
| 后端 | `uv run pytest tests/test_api_screening_batches.py -q` | **10 passed**（字段齐备/空库 200 空数组/limit 钳制/日期倒序/只读 405/种子两态/撞键优先级） |
| 后端邻域回归 | `pytest tests/test_api_screening.py tests/test_api_screening_batches.py tests/test_ops_api.py tests/screening -q` | **227 passed** |
| 前端（新页） | `npm test -- --run src/test/dailyOpsPage.test.tsx` | **10 passed**（排程排序+数据驱动/五态徽章与文案/补跑调用与刷新/409·500 提示/批次历史整行逐字/两空态/通道故障不冒充空态/跳转/直达与入口） |
| 前端全量 | `npx vitest run` | **95 files / 798 passed**（基线 94/788，+1 文件 +10 例，零回归）；`npx tsc -b` 干净 |
| E2E | `npx playwright test -c playwright.isolated.config.ts tests/daily-ops.spec.ts` | **5 passed (15.2s)** |
| **视觉基线** | `-c playwright.ui-baseline.config.ts` | **14 passed**——新图标不在基线采集视口内，**无需重采**（D5 遗留第 1 条随之关闭） |
| lint / 类型 | ruff check/format 过；mypy 5 错经对照确认与既有零新增 | ✓ |

E2E 端口处置：默认 config 端口被生产栈占用（未停容器）；门禁证据取自既有先例 isolated config（8001/5174）同一 spec。

## 三、自审发现并已修复（3 项）

1. 批次通道 500 原先回落成「暂无历史」空态（把缺失显示成正常）→ 独立 `ops-batches-error` 显式报错 + 补测试；
2. 未知运行/批次状态原落到「未跑/失败」不实文案 → 灰徽章 + 原状态逐字；
3. 补跑失败（500）原先不刷新 → 任何响应都刷新运行历史。

## 四、既有/周边文件改动清单（逐条）

1. `frontend/src/test/darkModeShortcuts.test.tsx`：palette 新必填 prop `onOpenOps` 工装 +2 行（断言零改动）
2. `frontend/src/components/shells/PageShellD.tsx`：+可选 prop `statusLabel?`（省略行为不变；既有壳测试零改动）
3. `tests/e2e/playwright/playwright.isolated.config.ts`：+`SCREENING_DB_PATH` 注入（该 config 此前缺此注入；默认 config 已有）
4. 其余既有测试零修改；`.gitignore` 无需改（既有条目覆盖）。

## 五、遗留

1. `/ops` 未加入视觉基线矩阵（需改既有基线 spec，留裁决/后续）；
2. 真实栈人工验证（合并部署后打开 `/ops`：时间线 10 项 + 封禁期失败/未跑态如实 + 补跑一次核对历史落行；截图）挂合并后部署核验；
3. PageShellD「一项 + 任务作步骤」组合形态留作后续迭代（当前为每任务一卡）。

## 六、结论

日批运维页交付：数据驱动时间线（10 任务自动跟随注册表）+ 五态徽章 + 逐字原因 + 一键补跑 + 批次历史，在 stub E2E 上五场景绿、视觉基线 14 passed 无扰动；「缺失不得冒充正常」经三处自审修复加固。
