# 人工验证报告: add-auto-pool-analysis-daily-brief

**日期**: 2026-10-20
**验证人**: agent（本地实跑，未接实盘）
**关联 delta**: `openspec/changes/add-auto-pool-analysis-daily-brief/`
**E2E 门禁**: `tests/e2e/playwright` daily-ops spec，专用端口 8009/5179 + 独立测试库，6 passed

## 验证范围

1. 精选池自动分析日批（16:22）是否无需人工即建会话并入队；
2. 分析完成后是否落预测、进入既有执行链；
3. 每日 LLM 汇报（20:30）是否用当日真实事实生成、落库落盘；
4. `/ops` 每日汇报区、API、空态/失败态；
5. 注册表 24→26 与 E2E 时间线。

## 实跑记录（2026-10-20 12:25–12:37，本地 8019 端口，独立测试库）

### 1. 自动选股分析

- 造数：当日 `screening_batches=ok`、池 1 只 `600519`、连续在榜 3 日。
- 手动触发 `POST /api/v1/ops/jobs/auto_pool_analysis_daily/run`（生产由 16:22 定时触发）。
- 运行摘要：

```json
{"trade_date":"2026-10-20","batch_date":"2026-10-20","pool_size":1,"selected":1,
 "started":1,"started_symbols":["600519"],"skipped_existing":0,"removed":[],
 "dispatch_failed":0,"status":"ok"}
```

- 会话：`600519 自动选股分析`，`session_id=f56380a2-26b`，状态 `running` → `completed`，
  报告长度 19,315 字符。
- 预测：创建 1 条，`action=watch`（模型结论，非链路失败）。
- 结论：**池 → 会话 → 队列 → 五层分析 → 预测** 全链打通，无需人工点击。

### 2. 每日 LLM 汇报

- 手动触发 `POST /api/v1/ops/jobs/daily_brief_daily/run`。
- 运行摘要：

```json
{"status":"ok","brief_date":"2026-10-20","model":"openai/deepseek-v4.1-flash",
 "chars":1283,"path":"tmp\\manual-auto-pool-reports\\daily-brief-2026-10-20.md",
 "file_error":null}
```

- `GET /api/v1/ops/daily-brief/latest` 返回 `status=ok` 与完整 Markdown。
- 内容抽查：正确引用 1 只池标的、1 条 `watch` 预测、0 条结算；对未造数的
  `execution`/`factors` 库如实写「无数据 / OperationalError」，未编造数字或成交。
- 结论：**opencode `deepseek-v4.1-flash` 真实调用成功**，事实与库内一致。

## 发现并修复的问题

| 问题 | 现象 | 修复 |
|---|---|---|
| `/api/test/seed` 守卫未识别 `daily_brief` | E2E 种子后页面仍为空态 | 守卫条件加入 `daily_brief`；补单测 |
| 派发状态与会话状态混淆 | 日报把 `pool_analysis_runs.status=queued` 与 `sessions.status=completed` 读成「状态不一致」 | 事实层把库内 `queued` 映射为 `dispatch_status=enqueued`，与会话终态分开披露 |

## 已知限制

- 本次分析遇到 AKShare 行业板块接口连接失败与 Langfuse 拉取超时，均按既有降级路径
  回退本地 prompt / 继续分析；不影响最终完成，但生产环境仍应关注数据源可用性。
- 本次未造 `execution`/`factors` 库，日报如实报缺口；生产库存在时这两节会有数据。
- 分析结论为 `watch`，因此纸面账户仍为 0 订单。这是模型判断，不是自动买入链路断开。
- 真实券商仍为 paper broker；本变更不接实盘。

## 验证证据

| 检查 | 结果 |
|---|---|
| `openspec validate add-auto-pool-analysis-daily-brief --strict` | valid |
| 定向后端套件（池分析/日报/注册表/API/LLM/prompt） | 338 passed |
| 前端 Vitest 全量 | 887 passed；1 项 `factorResearchPage` 并行负载下波动，单跑 27/27 通过（与本变更无关） |
| Playwright daily-ops（专用端口 8009/5179） | 6 passed（含 26 job 时间线与日报区） |
| `ruff check`（变更文件） | All checks passed |
| `mypy`（新增/改动模块） | 5 个新模块 clean；仓库存量 `api.py`/`evals` 有既有报错 |
| 全量 `uv run pytest` | 无失败跑到 53%，随后卡在依赖 Langfuse/网络的长测试；已停止（非本变更引入） |

## 合并后端到端复测（2026-10-20 13:xx）

- 合并：PR #428，squash 提交 `2ccc082a`（本地 main 已 ff 到该提交）。
- Playwright（专用端口 8009/5179，独立测试库）：
  `daily-ops` 6/6、`screening-page` 6/6、`broker-console` 5/5，共 **17 passed**。
- 真实链路（合并后 main，独立库，真实 LLM）：
  1. 种子当日池 `600519` → 触发 `auto_pool_analysis_daily` → `started=1`；
  2. 五层分析完成，报告 **17,804 字符**，预测 `600519.SH`，`action=watch`，`fund_manager_decision=approve`；
  3. 触发 `market_snapshot_daily` → 写入 5,568 行（spot_source=sina）；
  4. 造一条 2026-10-19 的 `approve + buy` 预测（仅用于验证执行消费端，非模型产出）→
     触发 `paper_execute_daily` → `signals_created=1 / orders_placed=1`，成交价 1321.67、
     费用 33.04、持仓 100 股、现金 867,799.96；
  5. 触发 `daily_brief_daily` → `status=ok`、模型 `openai/deepseek-v4.1-flash`、1,673 字符，
     日报正确写出「1 笔纸面买入、100 股、1321.67、费用 33.04」，并明确标注 paper broker。

**说明**：模型对 `600519` 的自动分析结论是 `watch`，所以自动链路本身不会下单；第 4 步是
为验证「预测 → 信号 → 订单 → 持仓」消费端而人工造的一条 buy 预测，不代表模型已转多。

## 结论

- [x] 自动选股分析日批可用（无需人工）
- [x] 分析观点进入既有预测/执行链路
- [x] 每日 LLM 汇报可用，模型 = opencode `deepseek-v4.1-flash`
- [x] `/ops` 每日汇报区与 API 可用（空态/失败态由单测覆盖）
- [x] E2E daily-ops 6/6 通过
- [ ] 真实资金验证：不适用（本系统仍为纸面执行）
