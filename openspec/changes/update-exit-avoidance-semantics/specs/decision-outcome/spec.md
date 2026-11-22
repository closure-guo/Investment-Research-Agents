# Delta for decision-outcome

## MODIFIED Requirements

### Requirement: 决策落库

(此前需求全文见主规范。本 delta 仅修改 direction 映射句；其余条款——快照冻结、结算入场价派生、旁路写入——原样保留。)

每个产出的观点 SHALL 写入 `predictions` 表：不再只记 approve，**reject、hold/watch、neutral 方向的观点同样落库**（全量记录）。记录 SHALL 含观点快照（rationale_snapshot，写入后冻结）、direction（action 映射：buy→long、**sell 按 sell_type 分型——exit→neutral（回避判定语义）、short→short、未申报→short**、hold/watch→neutral）、confidence、entry_price（**参考价**：决策时点实时价或最近收盘，注明口径，供展示与盯市）、horizon_days（显式写入，默认取配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`，默认值 20 交易日）、source_type（backtest/live）、权威 created_at（服务端生成）。参考价不可得时 SHALL 存档并记 WARN，状态保持 open（判定不依赖参考价）。neutral 观点判定后 `status` SHALL 落终态 `avoidance`（细粒度结果在 `avoidance_status`），离开 open 池。

(Previously: direction（action 映射：buy→long、sell→short、hold/watch→neutral）——exit 叙事的反弹减仓收益方向与做空结算相反（#606 影响 3）。update-exit-avoidance-semantics：exit→neutral 走回避判定，离场后跑输 = 离场正确。)

#### Scenario: exit 决策落库为 neutral

- **GIVEN** Fund Manager 产出 `sell_type=exit` 决策（approve/reject 均可能）
- **WHEN** 观点落库
- **THEN** direction SHALL 为 `neutral`
- **AND** 判定后 `status` SHALL 落终态 `avoidance`（细粒度结果在 `avoidance_status`）
