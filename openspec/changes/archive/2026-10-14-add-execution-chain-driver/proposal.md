# Proposal: add-execution-chain-driver

## Why

执行栈（`add-signal-portfolio-risk-gate` + `add-paper-broker`）**整条链路处于休眠**：`generate_signal` / `aggregate_targets` / `check_order` / `PaperBroker.place_order` 在全仓 `src/` 中零调用方（只有单测调用）。两条主规范要求因此在运行态不成立：

- trade-execution「标准化 Signal 契约」：**「系统 SHALL 将审批通过的 TradeDecision 转换为 Signal 落库」**——无任何运行时路径执行该转换；
- trade-execution「确定性风控门禁」：**「所有订单 SHALL 通过 risk_gate 方可进入执行」**——无订单产生，门禁从未在运行态运行。

后果：paper 账户永远空仓（战绩页双曲线恒为单曲线）、组合战绩无数据、风控门禁与撮合的运行态行为零验证。

## What Changes

- 新增日批执行通道 `paper_execute_daily`（ops 注册表 9→10，工作日 **16:10**，先于 paper 结算 16:20）：
  1. **决策 → 信号**：取 `decision_log` 中尚未生成信号的审批通过决策 → `signal_from_decision` → 落库（同 `decision_id` 幂等，不重复生成）；
  2. **信号 → 目标**：尚未产生订单的信号按其交易日聚合为目标持仓（`aggregate_targets`，等权/strength 加权，单票 20% / 总仓 80% 上限与缩减记录复用既有实现）；
  3. **目标 → 订单**：与当前持仓差分生成买卖订单（100 股整手；`flat` 信号 → 平仓目标 0）；
  4. **门禁**：逐单经 `check_order`（纯代码，无 LLM）；拒绝者落 `orders`（`rejected` + 结构化原因码），不静默丢弃；
  5. **下单**：通过者经 `PaperBroker.place_order` 以**执行日（决策日次一交易日）开盘口径**撮合（fill=开盘价×(1±滑点)）；一字板等不可成交沿用 pending + 次日重试（既有机制）。
- 入场时点与回测口径一致：**决策日次一交易日开盘**（job 在执行日 16:10 用当日快照 bar 撮合「昨日及更早的未执行信号」）。
- 快照不可用时跳过并落原因（`no_snapshot_data`，同 `run_paper_settle_daily` 先例）——信号顺延至下一交易日执行，不伪造成交。
- 既有测试最小更新（计数类）：注册表 9→10、E2E eval-ops 计数。

非目标（Out of scope）：

- **不做持仓退出/再平衡**（horizon 届满自动退出、目标漂移再平衡 → 后续 delta）；本 delta 的退出仅由 `flat`（卖出决策）信号触发。
- 不做实盘接口（仅 paper，Broker 抽象不变）。
- 不做执行台账/门禁日志的界面（见 `add-broker-console`）。
- 不改分析管线（不在管线内触发信号；驱动器只读 `decision_log`）。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `trade-execution`: 新增「信号到订单的日批执行」要求。
- `broker-execution`: 新增「门禁拒绝留痕」要求。

## Impact

- **后端**：新增 `execution/driver.py`（日批执行编排）；`outcome/ops/jobs.py` + `scheduler.py` 注册（9→10）。
- **测试**：驱动器单测（合成决策/快照 → 持仓闭环、幂等、门禁拒绝留痕、快照缺失跳过、flat 平仓）；注册表计数更新。
- **前端**：无代码改动（双曲线数据自动出现）。
