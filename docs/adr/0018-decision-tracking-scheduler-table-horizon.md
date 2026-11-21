# ADR-0018: 决策事后追踪的定时任务、落库表归属与周期默认值

**Status**: Accepted  
**Date**: 2026-09-05  
**关联 delta**: decision-outcome-tracking、add-track-record、add-track-record-stage-b/c

## Context

`decision-outcome-tracking` delta 提出「决策→结果」事后闭环：决策落库、定时结算、战绩查询。实现分两阶段演进，其中三个架构层选择按流程需要人工确认（原 tasks 注明「agent 不自建 ADR」，本 ADR 由维护者授权 agent 代拟并确认）：

1. 定时结算任务的框架选型——影响部署形态与依赖面；
2. 决策落库表的归属与命名——原始提案为 `decision_log`，实现已演进为 `predictions` 全量观点记录；
3. 基准与持仓周期的默认值——结算语义的数值契约。

现状约束：StreamRegistry 为进程内内存结构，后端必须单 uvicorn worker（AGENTS.md 红线）；存储复用既有 SQLite（ADR-0004 分层持久化）；结算属旁路观测，不得阻断业务管线。

## Decision

### D1：定时任务框架——APScheduler in-process

**选择**：`APScheduler BackgroundScheduler` 与 uvicorn 同进程运行（`src/finance_agent/outcome/scheduler.py`），CronTrigger 每个交易日 16:00（收盘后）触发日批结算。

- 失败重试：意外异常指数退避重试 3 次（5s / 20s，低频日批用 `time.sleep` 足够），全失败记 ERROR 等下一交易日。
- 幂等：job 内结算以 `settled_at IS NULL` 为准，重跑安全。
- 开关：`TESTING=1` 或 `DECISION_SETTLE_ENABLED=0` 时禁用；job 异常不传播（旁路铁律，不阻断业务管线）。

**备选否决**：
- 独立 Celery worker——引入 broker 依赖，对日均 < 100 条的结算频率属过度工程；
- 系统 cron + 独立脚本——部署割裂，绕开应用内配置与日志体系；
- `asyncio` 自研循环——重新发明调度、重试与误触发防护。

单 worker 部署下 in-process 无竞争（AGENTS.md 已禁止 `--workers N`），若未来引入多 worker，需先将调度器迁出为独立进程——这是本决策的唯一失效条件。

### D2：落库表归属——`predictions` 表（全量观点记录），取代 `decision_log` 提案

**选择**：不建 `decision_log`（仅 approve 记录），改为 `predictions` 表全量落库，归属 `outcome/track_record/model.py`（`init_predictions` 启动建表，共享写入入口 `api.py` 各路径委托）。

- 每个 fund manager 产出的观点（approve / reject / hold / watch / neutral）均落一条记录，reject 同样计分——只记 approve 会产生幸存者偏差，战绩页无法回答「拒绝了多少坏决策」。
- 记录含观点快照 `rationale_snapshot`（写入后冻结）、`direction`（buy→long / sell→short / hold/watch→neutral）、`confidence`、`entry_price`（注明口径）、`source_type`（backtest / live）、服务端权威 `created_at`、`langfuse_trace_id` 反向联查。
- 写入旁路：SQLite 异常仅记 ERROR 日志，不阻断报告产出。

**演进说明**：原始提案（decision-outcome-tracking 决策 1）为 `decision_log` 表 + status 状态机；add-track-record 阶段将语义升级为 predictions 全量观点 + horizon 判定，`decision_log` 未曾建表。旧 stop/target/expired 结算代码（`outcome/settle.py` + `outcome/job.py`）保留在仓内但**未被调度器接线**——当前调度只注册 track_record 的 horizon 判定（`settle_open_predictions`）；若未来启用交易纪律结算，需显式接线并复核幂等边界。

### D3：基准与持仓周期默认值

两套结算语义并存，默认值如下（均可经环境变量覆盖）：

- 交易结算（止损/目标/超期）｜`outcome/settle.py`（未接线，备用）｜`MAX_HOLD_DAYS=20` 交易日｜`BENCHMARK_CODE=000300`（沪深300）｜止损 > 目标 > 超期先到先结算（同日按止损，保守）；超期用结算日收盘
- 战绩判定（horizon 区间超额）｜`outcome/track_record/judgment.py`（调度器当前唯一接线）｜`horizon_days=252` 交易日（上限同值）｜记录级 `benchmark` 字段（缺省沪深300）｜horizon 到点按区间超额收益判定 win/loss/neutral，中性带过滤噪声；short 取负

- 评估起点统一为 `decision_date` 之后（T+1 起评），基准取 date 或之前最后一个收盘。
- 超期强制（20 日）与 horizon 判定（252 日）数值不同是**有意的**：前者是交易纪律（避免 open 无限堆积），后者是观点验证窗口（评估中期判断正确性）；二者服务于不同问题，不互相取代。

## Consequences

- 正面：零新增重依赖（APScheduler 轻量）；结算与业务管线完全隔离；战绩口径可回溯（rationale 冻结 + trace 联查）。
- 代价：调度器与后端同生命周期，后端重启期间错过 16:00 触发需下一交易日补偿（幂等设计使补偿安全）。
- 后续：多 worker 部署前必须迁出调度器；组合层面绩效（Sharpe/回撤）与批量回测不在本决策范围。

## References

- delta 提案：openspec/changes/archive 下 decision-outcome-tracking、add-track-record 系列
- 实现：`src/finance_agent/outcome/scheduler.py`、`settle.py`、`outcome/track_record/`（job/marking/judgment/model）

---

## Revision 2026-11-21: 旧结算死链删除（C6②）

D2 演进说明中「outcome/settle.py + outcome/job.py 保留在仓内」的表述于 2026-11-21 终止：两模块为未接线死码（调度器只注册 track_record horizon 判定；deprecation WARN 自 2026-09 起零生产调用；生产消费链唯一活引用是 evals/golden 判例复算）。C6②（docs/architecture-debt/c6-small-items.md 小件②）执行：

- `evaluate_decision` 及其依赖**原样迁移**至 `evals/golden/settle_legacy.py`（判例复算唯一来源；golden 判例锚定 stop/target 交易纪律语义，与 horizon 语义并存不受影响）；
- `outcome/settle.py` 与 `outcome/job.py` 从生产删除（复活路径：git 历史 + settle_legacy 语义参照）；
- D3「两套结算语义并存」的裁决不变——本修订只改变旧引擎的存放位置，不改任何结算语义。
