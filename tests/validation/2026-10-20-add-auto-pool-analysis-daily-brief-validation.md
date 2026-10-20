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

## 结论

- [x] 自动选股分析日批可用（无需人工）
- [x] 分析观点进入既有预测/执行链路
- [x] 每日 LLM 汇报可用，模型 = opencode `deepseek-v4.1-flash`
- [x] `/ops` 每日汇报区与 API 可用（空态/失败态由单测覆盖）
- [x] E2E daily-ops 6/6 通过
- [ ] 真实资金验证：不适用（本系统仍为纸面执行）
