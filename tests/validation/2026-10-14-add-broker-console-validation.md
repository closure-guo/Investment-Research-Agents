# add-broker-console 实施验证报告

**日期**: 2026-10-14
**范围**: 执行台账只读接口 + `/broker` 台账页（PageShellA：指标条 + 持仓构成图 + Tab 表）
**交付**: `api.py`（`/api/broker/overview|positions|orders|gate-log` + `broker` 种子/`broker_reset`）、`execution/store.py`（+`delete_all_broker_state`）、`frontend/src/pages/broker/`（页 + 三个表格组件 + shared）、路由/侧边栏/命令面板入口、三套测试、E2E 独立执行库隔离（`EXECUTION_DB_PATH` 注入两个 config + .gitignore）

## 一、实施摘要

paper 台账此前只有数据库、门禁拒绝无回查面。本 delta：overview（资金/持仓市值/总资产/浮盈）+ positions + orders（四态 + 原因码 + decision_id/signal_id）+ gate-log（rejected 视图）；页面 PageShellA（指标条 + 市值构成饼图 + 持仓/订单/门禁拒绝三 Tab），状态 StatusBadge 映射 pending→running / filled→active / rejected→failed / expired→retired；空账户空态如实。

**估值口径（保守）**：持仓市值按快照 `close_raw`；无持仓 → 0.0（空集真实读数）；有持仓但快照不可 mark → 整体 null + `market_value_note`/`missing_symbols`/`snapshot_date` 标注（MUST NOT 填 0 冒充，spec 允许）。

## 二、验证证据

| 项 | 命令 | 结果 |
|---|---|---|
| 后端 | `uv run pytest tests/test_api_broker.py -q` | **12 passed**（空账户 null/0 语义 / 字段齐备 / gate-log 仅 rejected / 缺快照标注） |
| 后端邻域回归 | `pytest tests/test_api_{broker,screening,factors,snapshot_status,track_record,memory}.py tests/execution -q` | **330 passed** |
| 前端（新页） | `npm test -- --run src/test/brokerConsolePage.test.tsx` | **9 passed** |
| 前端全量 | `npx vitest run` | **94 files / 788 passed**；`npx tsc -b` 干净 |
| E2E | `npx playwright test -c playwright.isolated.config.ts tests/broker-console.spec.ts` | **5 passed (15.9s)** |
| lint / 类型 | ruff check/format 过；mypy api.py/store.py 5 错经 stash 对照确认与 HEAD 同款零新增 | ✓ |

**E2E 证据形态（裁决）**：默认 config 端口 8000/5173 被生产栈占用（未停容器）；门禁证据取自既有先例的 **isolated config（8001/5174）同一 spec + 同一 env 注入**——接受为 T2.3 门禁通过（端口差异不改变被测系统形态）。该次运行快照库无行情行 → 实际走「快照缺失 → 市值显式标注」分支；有快照分支由单测覆盖（close_qfq 刻意错开证明取 close_raw）。

## 三、既有/周边文件改动清单（逐条）

1. `frontend/src/test/darkModeShortcuts.test.tsx`：palette 新必填 prop `onOpenBroker` 工装同步（+1 行，断言零改动）
2. `tests/e2e/playwright/playwright.config.ts` / `playwright.isolated.config.ts`：注入 `EXECUTION_DB_PATH: data/test-e2e-execution.db`（默认 config 此前无该注入；与 track-record/ui-baseline 的执行库是不同文件，无冲突）
3. `.gitignore`：+`data/test-e2e-execution.db*`
4. 其余既有测试零修改。

## 四、遗留（两条裁决项，已记录）

1. **视觉基线**：`playwright.ui-baseline-pages.spec.ts` 侧栏截图因新增图标需 `--update-snapshots` 重采（先例同筛选/因子入口）——**待 D6（日批运维入口）落地后统一重采一次**，避免两次基线扰动。
2. 真实栈人工验证（合并部署后打开 `/broker`：当前空账户 → 空态如实；执行链数据出现后随日批更新）挂合并后部署核验。

## 五、结论

执行台账页交付：四端点 + PageShellA 页 + 门禁拒绝视图在 stub E2E 上五场景绿；估值口径「不填 0、缺价显式标注」经测试与 E2E 双覆盖；E2E 造数库隔离配套齐全。
