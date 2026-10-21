# 研报消费五 delta · 端到端与架构图验证（2026-10-20）

## 范围

worktree `.worktrees/analyst-ex-post-verification`（分支 `feat/analyst-ex-post-verification`）上的 5 条 OpenSpec delta：

1. `add-analyst-ex-post-verification`
2. `complete-research-report-provenance`
3. `add-macro-strategy-report-context`
4. `add-report-triggered-analysis`
5. `add-foreign-broker-sentiment`

代码未提交/未合并；主检出 `src/` 未改动（实施期间主检出有运行中分析）。

## 端到端测试（隔离端口，未打生产栈）

| 套件 | 命令 | 结果 |
|---|---|---|
| daily-ops | `npx playwright test daily-ops.spec.ts -c playwright.isolated.config.ts` | **6 passed（35.3s）** |
| eval-ops-console | `npx playwright test -c playwright.eval-ops.config.ts` | **5 passed（51.1s）** |

- daily-ops 后端 8001 / 前端 5174；eval-ops 后端 8005 / 前端 5178；均使用 `TESTING=1` 与独立 `data/test-e2e-*.db`。
- daily-ops 覆盖：28 个任务按时刻升序渲染、五态徽章、选股批次两态、空态、评估运维跳转、一键补跑、每日汇报。
- eval-ops 覆盖：28 个任务卡片、cohort 开关确认/取消零写入、正式批拒绝不启动回放、完整性校验补跑终态、cohort 关闭时零 LLM。
- eval-ops 中 AKShare 网络失败按既有降级路径处理，不影响断言。

## 单元/集成回归

- `tests/data`：376 passed。
- `tests/outcome`：912 passed, 2 skipped。
- 5 条 delta 定向套件（存储/抽取/结算/记分卡/job/触发/外资/citation/宏观上下文）：全部通过。
- 最终 broad 跑：2269 passed, 2 skipped；5 个 `tests/outcome/test_ops_jobs.py` 任务计数断言因新增 2 个 job 失败，已同步 26→28 并修复，`tests/outcome` 复跑 912 passed。
- `ruff check`：全部变更文件通过；`git diff --check` 无空白问题。
- `openspec validate --changes --strict`：11/11 通过。

## 提示词发布

| Prompt | 版本 | 内容 |
|---|---|---|
| `report_extraction` | v3 | `source_page` + `sector_views` + `market_view` + 披露表不入 stocks |
| `macro_analyst` | v32 | 卖方策略观点消费 + 防锚定 + 数据 claim 仍只引 macro_indicators |
| `foreign_broker_event` | v1 | 外资二手快讯事件抽取 |

## 架构图

- 图源：`docs/assets/system-flow.json`（Archify architecture）。
- 更新：新增「研报数据面」节点（16:52–17:05）与到 PREP 的边；新增「研报消费与检验」说明卡；支撑卡改为九库 SQLite / 28 任务；`viewBox` 660→720；README 文案同步 28 任务、九库、研报消费链。
- `archify validate --quality showcase`：9/9 artifact checks，0 errors / 0 warnings。
- `archify deliver`：artifact SHA-256 `b7758e59…`，731,201 bytes。
- `archify visual-check`：1440×900 / 2048×1320 明暗四档全部 `ok=true`，无横纵溢出，最小投影节点文字 9px。
- PNG 已用 2048×1320 明暗截图替换 `docs/assets/system-flow.png` / `system-flow.dark.png`；visual-check 临时 sidecar 已清理。

## 遗留

- `add-foreign-broker-sentiment` tasks 5.4/5.5 未完成：未配置真实 `FOREIGN_NEWS_FEEDS`，真实 feed 跑一轮与人工抽查 10 条抽取待做。
- 代码未提交/未合并；生产栈未重建，生产 `research_reports.db` schema 待部署后由 `init_db` 自动补列。
- 生产分析未验证新链路（E2E/单测均为隔离环境）。

## 追加：内外资分开打分（add-region-split-broker-scoring）

- 新增第 6 条 delta：region/score_family 标签、内资 depth 分桶与深度加权、外资情绪分与市场级 20 日命中率、外资独立记分卡、统一只读视图。
- 新增 `config/analyst_scoring.json`（深度/外资先验权重）；`docs/evals/metrics.md` §1.19 登记口径。
- 回归：`tests/data + tests/outcome + tests/screening + tests/nodes + test_ops_api + test_graph_5layer` **2288 passed, 2 skipped**；新增加固测试 123 passed；`ruff` 变更文件全过；`openspec validate --changes --strict` **12/12**。
- 真实库副本：22 条 rating_maintain claims、0 条已结算（无到期样本）、0 条外资事件；迁移/查询正常，统一视图两块均为空态。
- 真实 feed 尝试：CNBC Markets + WSJ Markets 可达，共解析 50 条、0 条命中（当日无 broker + China 同现），运行记录 `status=ok`；因此 `add-foreign-broker-sentiment` 5.4 的事件落库与 5.5 的人工抽查仍未完成，保持未勾。
- 打分 delta 后 E2E 复跑：daily-ops **6/6**（22.2s）、eval-ops-console **5/5**（52.4s），隔离端口/库，未打生产。
- 未做：外资真实 feed 事件落库；本 delta 未改前端。

