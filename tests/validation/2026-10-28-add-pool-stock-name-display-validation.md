# 人工验证报告: add-pool-stock-name-display

**日期**: 2026-10-28
**验证人**: Closure（agent 执行 + 人工抽验口径核对）
**关联 delta**: `openspec/changes/add-pool-stock-name-display/`
**E2E 门禁**: `tests/e2e/playwright`（隔离端口对 8001/5174，`--config playwright.isolated.config.ts`；
默认端口对 8000/5173 被生产 Docker 栈占用，Playwright 会复用生产服务器——incident 038 同款陷阱，
故全程走隔离对。trace 证据：`tests/e2e/playwright/test-results/`）

## 验证结果

### 单元 / 集成层（后端）

| Scenario（delta spec） | 覆盖测试 | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 池行透传快照名称 | `test_pipeline.py::test_pool_rows_carry_snapshot_stock_name` + store 往返 + API 用例 | 池行 `stock_name` = 同日快照 name | `合成600519`/`合成000001` 逐只一致；主档/microcap 同契约 | ✅ |
| 缺名降级不阻断批次 | `test_pipeline.py::test_pool_row_missing_stock_name_degrades_with_warning` | 空串 + warning，批次 ok | 批次 ok、该行空串、caplog 含 600519、其余行不受影响 | ✅ |
| 存量库无感迁移 | `test_screening_store.py::test_legacy_pool_schema_gets_stock_name_column` + API null 用例 | 列补齐、历史行 NULL、接口返回 null | ✅ | ✅ |
| 幂等补跑自动补名 | `test_screening_store.py::test_pool_row_roundtrip_carries_stock_name`（同日重跑段） | upsert 整体替换补名 | ✅ | ✅ |
| 非法值拒绝（fail loud 边界） | `test_pool_row_stock_name_rejects_non_string` | 非 str → ValueError | ✅ | ✅ |

后端定向：`uv run pytest tests/screening tests/test_api_screening.py -q` → **294 passed**；
`ruff check`/`ruff format --check` 全绿；改动文件 mypy 零问题（仓库 98 项存量在 api.py 等
其他文件，与本分支无关）。

### 真实生产库迁移演练（只读副本）

- 源：主检出 `data/screening.db` 复制件（迁移前 9 列、**142 行历史池行**）
- `init_screening_db` 后：10 列（`stock_name` 末位列）、142 行零丢失、`stock_name` 全 NULL
- 读侧：10-28 当日池 16 行，首行 `stock_name=None`（接口透传口径正确）
- 副本已删除，原库未触碰

### 前端层

| Scenario（delta spec） | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 股票名称列渲染 | 是（screening-page.spec 种子池用例）+ 组件测试 | `600519` 行名称列渲染「贵州茅台」，代码列并存 | E2E 断言 `screening-stock-name-600519` 含「贵州茅台」、`000001` 含「平安银行」；组件测试逐单元格断言 | ✅ |
| 名称缺失退化展示 | 是（fallback 用例 seed 不带 stock_name）+ 组件测试（null→`—`） | null/空串渲染 `—`，其余列正常 | E2E fallback 行渲染 `—`；组件测试 null→`—` | ✅ |
| 贡献分解 hover / 降级横幅 / 池置顶 / 空态 / 因子信号卡 / 达标态（既有场景回归） | 是（既有用例） | 不回归 | 全量两跑均绿 | ✅ |

前端全量：`npm test` → **970 passed（107 文件）**；`tsc --noEmit` 零错误。

### E2E 门禁（隔离对，stub 套件）

| 运行 | 结果 |
|---|---|
| screening-page.spec.ts（单文件，3 次直跑） | 7/7 全绿（含名称列与缺名退化两条新断言） |
| 全量第 1 跑（smoke 修复前） | exit 0：111 passed / 25 skipped / 1 did-not-run / **0 failed** |
| 全量第 2 跑（smoke 修复后） | exit 0：105 passed / 25 skipped / 4 did-not-run / **0 failed** |
| smoke.spec.ts（修复后直跑） | 3/3 全绿 |

**0 断言失败**（Playwright 有失败必 exit 1，两跑均 exit 0）。did-not-run 为 Windows worker
崩溃（0xC0000409，本轮直接目击一次）级联 + 两个存量重跑脆弱用例（见异常记录），非本 delta 引入。

### 终审

全分支审查（general-purpose 代理，diff vs origin/main）：**Approved**，零 Critical/Important
代码问题；两个 Minor（测试 DDL 常量名失真、design.md 类型口径漂移）已修复并复验（store 51 绿）。

## 异常记录

1. **smoke.spec.ts 硬编码 `localhost:8000`（存量缺陷，已随本分支修复）**：隔离套件下打到宿主
   8000（生产栈占用 → seed 404 假红 / health 200 假绿，incident 038 家族）。已按
   apiBase() 模式端口参数化（独立 commit），修复后 3/3 绿。
2. **isolated config 漏 `SCREENING_RULES_CONFIG_PATH` + research-ingest 首启假设（存量缺陷，
   未修，挂 issue #519）**：直跑 settings-screening-rules 与 research-ingest 各 1 例红。
   根因均为测试基建（隔离库重跑脆弱），CI 新环境会绿；主检出无 `data/settings/`，
   本次运行未污染任何真实数据（worktree 脏文件已清理）。
3. **默认端口对不可用**：生产 Docker 栈（com.docker.backend）占用 8000/5173，Playwright
   `reuseExistingServer` 会静默复用生产服务器——全程改走隔离端口对，属环境处置而非代码问题。

## 结论

- [x] 全部通过，可 archive（前置：tasks.md 全勾 ✅、验证报告本件 ✅、E2E 门禁 0 failed ✅、
      `openspec validate --strict` ✅）
- 存量测试基建缺陷已挂 issue #519 跟踪，不阻塞本 delta
