# Design: add-execution-chain-driver

## Context

执行栈各组件均已实现并单测覆盖，但**没有驱动器把它们串起来**：`decision_log`（`outcome/store.py`，含 action/entry/target/confidence/timestamp）是决策真源；`signal_from_decision` 负责契约转换；`aggregate_targets` 负责组合聚合；`check_order` 负责门禁；`PaperBroker.place_order` 负责撮合与记账；`run_paper_settle_daily`（16:20，已接线）负责日终结算与挂单重试。缺的只有「谁在什么时候按什么顺序调用它们」。

## Decisions

- **驱动器 = ops 日批 job（`paper_execute_daily`，16:10）**：复用统一通道（调度/历史/手动补跑/单飞锁）；排在 paper 结算（16:20）之前，保证当日成交当日入账。
- **入场口径 = 决策日次一交易日开盘**：与回测入场（D+1 开盘）一致；job 在执行日 16:10 时当日快照已就绪（15:30），撮合 fill=当日开盘×(1±滑点)——即「昨日及更早的未执行信号，在今日开盘成交」。这避免了同日盘中决策按当日开盘成交的回溯偏误。
- **幂等锚点**：信号以 `decision_id` 唯一（重复执行不重复生成）；订单 `order_id` 由信号派生（`place_order` 同 order_id 重跑幂等，已有保证）——驱动器重复跑、job 重试、手动补跑三态下均不产生重复订单。
- **门禁拒绝 = 落库而非丢弃**：被拒订单以 `rejected` 状态 + 原因码写 `orders`（表结构已含 status/reason/decision_id/signal_id，**零 schema 变更**）；这同时是 add-broker-console 门禁日志的数据源。
- **快照缺失 = 跳过 + 原因**：`no_snapshot_data`（同 `run_paper_settle_daily` 先例，旁路铁律下 job 记 ok 摘要）；未执行信号自然顺延至下一交易日，不伪造成交。
- **不做持续再平衡**：本 delta 每条信号恰好执行一次（含 `flat` 平仓）；horizon 届满退出与目标漂移再平衡列为非目标（未来 delta），避免在无前向数据期引入未验证的复杂语义。

## Alternatives Considered

- **在分析管线内（决策落库时）即时触发信号生成**：放弃——把执行耦合进管线（含 TESTING stub/重跑路径），且盘中信号无法按 D+1 开盘成交；集中到日批更可测、可重放。
- **常驻事件循环/每信号独立执行**：放弃——与「日批 + 运行历史可查」的既有运维模型不一致。
- **让 paper broker 内部调门禁**：放弃——门禁是订单生成侧的前置判定（组合层需要门禁的拒绝反馈来记账），且 trade-execution 的 spec 语义是「订单进入执行前」经门禁；驱动器逐单判定并把结果落库更贴合契约与审计需求。

## Risks

- 决策数据稀少期（当前仅存 295 会话、部分无决策）→ 多数交易日零信号零订单：**预期行为**，摘要如实（`signals_created=0`）。
- 东财封禁期快照为空 → job 跳过（`no_snapshot_data`）：预期行为；解封后自动恢复。
- 合成数据单测无法覆盖真实撮合边界（涨跌停/停牌）：依赖既有 matching-engine 单测与快照恢复后的真实日批验证（挂 issue #325 补验）。
