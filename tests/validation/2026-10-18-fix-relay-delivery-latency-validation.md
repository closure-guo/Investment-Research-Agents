# 人工验证报告: fix-relay-delivery-latency（issue #328 第 1 步：埋点 + 复现量化）

**日期**: 2026-10-18
**验证人**: ZCode 验证 runner（单测 + 8 worker 负载实跑采集）
**关联 issue**: #328（E2E 运行中瞬态族：深析阶段 UI 整段冻结，疑 SSE relay 投递延迟）
**E2E 门禁**: 本变更为可观测性埋点（零行为变更）——按 issue 第 1 步执行「埋点 + 高负载量化」；E2E 套件作为复现负载源运行
**执行环境**: worktree `.worktrees/fix-relay-latency` @ 基于 main 86cdbead；timeline 套件端口对（8001/5174、8002/5175、8003/5176）

## 实现（publish→deliver 时延埋点）

- `publish`/`publish_terminal`/`publish_many`/`_publish_sync` 在 fan-out 前登记 `seq → monotonic`（插序裁剪 ≤512，内存有界）；
- 订阅者生成器**投递点**（重放补漏 + 实时队列两处 yield 前）记录时延样本（滚动 ≤256/流）；无时点的重放历史事件天然跳过；
- 超阈单条打 WARNING（`STREAM_DELIVERY_WARN_MS` 默认 3000ms，每会话 10s 限流）——生产日志可见；
- `backlog_stats` 增 `delivery_latency_ms: {n, p50, p95, max}` → 既有出口 `GET /api/v1/ops/stream-backlog`；
- 采集器 `tests/scripts/collect_stream_latency.py`（多端口周期轮询取跨轮 max，缺峰不漏）。

## 验证结果

| # | 场景 | 证据 | 通过 |
|---|---|---|---|
| 1 | 实时队列投递记录时延 | 单测模拟事件循环饥饿（同步 sleep 0.15s）→ 样本 max ≥100ms（修复前字段不存在） | ✅ |
| 2 | 重放历史事件不计量 | 直接落 journal 的事件 `n == 0` | ✅ |
| 3 | 超阈 WARNING（阈值可配 + 限流） | caplog 捕获「投递延迟超阈」 | ✅ |
| 4 | 端点导出形状 | `test_ops_stream_backlog` 钉无样本时全 None（不冒充 0） | ✅ |
| 5 | 回归 | `test_stream_registry* + test_ops_stream_backlog` 28 项绿；ruff/mypy 零告警 | ✅ |
| 6 | **8 worker 高负载复现** | `CI=1 npx playwright test --config=playwright.timeline.config.ts --retries=0 --workers=8`（三端口对并行）：**24 passed / 1 skipped / 0 failed**；采集器 244 样本 / 16 会话，**max 18.0ms、p50 亚毫秒** | ✅ |

**读数**（采集原始件 `tests/validation/2026-10-18-relay-latency-load-samples.jsonl`）——各会话峰值 top：

```
8002:431a9701-c6b  18.0 ms     8002:e7536d07-624   2.8 ms     8002:985fad6f-ad6   1.7 ms
8002:95de60f6-5f3   1.5 ms     8002:651e15a6-75b   1.1 ms     其余 <1 ms
```

## 结论与未达成项

1. **本机未复现投递停滞**：8 worker 负载下 publish→deliver 峰值 18ms（远低于 3000ms 阈值）→ 「relay 投递在高负载下成批延迟」这一机制假设**在本机负载面不成立**；#328 的候选机制收窄为：CI 慢运行器专属时序（#312 运行器级劣化族）或前端渲染侧（事件已送达而 UI 未重绘）——两者均需其各自环境证据，不属于本机可判定面。
2. **埋点已就位供生产/CI 观测**：下次该族在任何环境发作时，`/api/v1/ops/stream-backlog` 的 `delivery_latency_ms` 与「投递延迟超阈」WARNING 将直接区分「事件没产出」（样本低、日志静默）与「产出后投递停滞」（样本尖峰 + WARNING）——#328 后续按此分叉。
3. **未做强制 flush / 心跳 tick**（issue 建议第 2 步）：其前提是「确认成批延迟」，本机读数不支持，故按 issue 逻辑暂不实施（避免为未证实的机制引入热路径变更）。
