# decision-outcome delta: add-signal-portfolio-risk-gate

## ADDED Requirements

### Requirement: 决策到信号的出口

审批通过（fund_manager approve）的 TradeDecision SHALL 生成对应 Signal 并建立 `decision_id` 外键关联；被拒绝或退回的决策 SHALL NOT 生成 Signal。

#### Scenario: 仅审批通过生成信号

- **GIVEN** 同日一条 approve 决策与一条 reject 决策
- **WHEN** 信号生成执行
- **THEN** 仅 approve 决策产生 Signal，且 Signal.decision_id 指向该决策
