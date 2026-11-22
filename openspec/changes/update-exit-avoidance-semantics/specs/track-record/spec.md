# Delta for track-record

## MODIFIED Requirements

### Requirement: 观点全量记录

(此前需求全文见主规范。本 delta 仅修改 direction 映射句与其 Scenario；其余条款原样保留。)

系统 SHALL 建立并维护 `predictions` 全量观点记录，每个 fund manager 产出的观点（approve/reject/hold/watch/neutral）均落一条记录。**direction 映射（update-exit-avoidance-semantics）**：`buy→long`；`sell` 按 `sell_type` 分型——`sell_type=exit`（清仓离场）→ **`neutral`（回避判定语义：离场后标的跑输 = 离场正确，与 watch/hold 回避语义数学同构）**；`sell_type=short`（做空建仓）→ `short`；`sell_type` 未申报 → `short`（历史形态/回测腿语义不变）；`hold/watch→neutral`。reject/return 的方向映射不变。存量 exit 观点已按 short 结算的读数 SHALL NOT 追溯改写（§1.9 预登记纪律：历史读数按产出时版本解释）；本 delta 版本登记于 docs/evals/metrics.md。

(Previously: direction 由 action 映射 buy→long、sell→short、hold/watch→neutral——exit 叙事的反弹减仓收益方向与做空结算相反，记分卡与执行叙事系统性反向（#606 影响 3，深南电路实证）。)

#### Scenario: exit 观点落库为回避语义

- **GIVEN** Fund Manager 产出 `sell_type=exit` 的清仓决策（approve）
- **WHEN** 观点落库
- **THEN** direction SHALL 为 `neutral`
- **AND** 判定 SHALL 走回避语义（T+20 区间超额 < −2% → avoidance_win，> +2% → avoidance_loss，带内 → avoidance_neutral，结果写 avoidance_status、状态落终态 `avoidance`）
- **AND** SHALL NOT 按 short 符号结算（记分卡与执行叙事反向的根除）

#### Scenario: short 观点仍按做空结算

- **GIVEN** Fund Manager 产出 `sell_type=short` 的做空决策
- **WHEN** 观点落库
- **THEN** direction SHALL 为 `short`，判定按做空符号（与既有行为一致）

#### Scenario: 未申报 sell_type 的回测/历史形态维持 short

- **GIVEN** `sell_type=None` 的 sell 观点（回测腿 replay 构造或历史形态）
- **WHEN** 观点落库
- **THEN** direction SHALL 为 `short`（回测腿语义独立、不传 sell_type 的调用方行为不变）
