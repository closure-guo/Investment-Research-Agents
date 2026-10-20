# Delta for trade-execution

## ADDED Requirements

### Requirement: 自动选股分析的执行衔接

自动选股分析产出的观点 SHALL 进入既有日批执行通道，MUST NOT 享受特殊路径或绕过审批/风控：

- 观点落 `predictions` 表后，由既有 `paper_execute_daily` 按「决策日早于执行日」规则在下一交易日
  16:10 生成信号、以执行日开盘价口径撮合；
- 只有 `fund_manager_decision=approve` 且 `action=buy` 的观点 SHALL 生成 `long` 信号；
  `watch/hold` SHALL 生成 `flat`（不建仓），`sell/short` SHALL 生成 `flat`（A 股信号路径不做空），
  MUST NOT 被误判为买入；
- 同一观点 SHALL NOT 因日批重试或手动补跑产生重复订单（既有 `decision_id` 幂等锚点不变）；
- 执行仍为 paper broker；在真实券商适配器落地前，系统 MUST NOT 声称已接入实盘。

#### Scenario: 买入观点次日开盘成交

- **GIVEN** 自动选股分析在 D 日收盘后产出 `approve + buy` 观点
- **WHEN** D+1 日 `paper_execute_daily` 执行
- **THEN** 系统生成 `long` 信号并以 D+1 开盘价（含滑点）撮合
- **AND** 同一观点重复执行不产生第二笔订单

#### Scenario: 观望观点不建仓

- **GIVEN** 自动选股分析产出 `action=watch`
- **WHEN** 日批执行
- **THEN** 信号方向为 `flat`，不生成买入订单
- **AND** 当日汇报披露「0 笔买入」及原因

#### Scenario: 看空观点不误买

- **GIVEN** 自动选股分析产出 `sell + sell_type=short`
- **WHEN** 日批执行
- **THEN** 信号契约为 `flat`（长仓退出/不建仓），MUST NOT 生成买入订单
