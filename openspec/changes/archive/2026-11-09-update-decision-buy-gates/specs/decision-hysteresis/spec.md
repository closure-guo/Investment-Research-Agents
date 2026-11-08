# Delta for decision-hysteresis

## MODIFIED Requirements

### Requirement: 证据均衡带识别与默认观望

系统 SHALL 在决策层（risk_judge 前置）识别「证据均衡带」：Research Manager 评级为中性，或多空辩论两轮后关键论据呈对峙态势且无新增量事实（判据由 RM 结构化输出与辩论锚点统计确定性计算）。均衡带成立时，决策上下文 SHALL 显式携带均衡带标志，均衡带标志的效力收窄为**要求 risk_judge 的 reasoning 如实披露对峙点**——MUST NOT 对执行动作（buy/sell）施加增量申报义务：均衡带内的首次建仓（近窗无历史决策）与维持近窗前向方向的执行动作 SHALL 以价位逻辑与既有证据为充分依据放行，正常进入后续门禁。增量申报与打回降级机制仅适用于**方向翻转**（与近窗前向方向不同的执行动作），归「方向滞回与增量事实申报」约束。`decision_hysteresis` state 键（balanced_zone 等判定要素）保留供 trace 观测。
(Previously: 均衡带内 risk_judge 的执行动作（buy/sell）SHALL 以证据增量支撑为前提——无增量引用的执行动作被打回重申一次，重申仍无增量降级为 watch/hold 并标注「证据均衡无增量，默认观望」——该机制对首次建仓构成「无新披露即不得买」，财报真空期与首建场景结构性禁止买入，2026-11-06 消融实证生产臂 RM 中性时因此恒 watch。旧场景名「均衡带内 sell 无增量被降级」保留为归档漂移守卫的连续命名锚点，行为以本 requirement 修订为准。)

#### Scenario: 均衡带内 sell 无增量被降级

- **GIVEN** RM 评级中性、辩论锚点统计多空对峙（均衡带成立），近窗无该标的历史决策或近窗前向方向与 sell 一致，risk_judge 输出 sell 且 reasoning 披露了对峙点但未申报新增量事实
- **WHEN** 均衡带复核执行
- **THEN** 执行动作 SHALL 放行，不触发打回降级
- **AND** 「无增量即降级」机制自本 requirement 修订（update-decision-buy-gates，2026-11-07）起移除：均衡带标志的效力收窄为要求 reasoning 如实披露对峙点，打回降级仅适用于方向翻转

#### Scenario: 均衡带内 sell 携带真增量放行

- **GIVEN** 均衡带成立，risk_judge 输出 sell 且 reasoning 引用「今日放量跌破 MA60 收盘确认」类技术形态破位增量
- **THEN** 执行动作 SHALL 放行（增量事实显式申报），正常进入后续门禁

#### Scenario: 非均衡带不受影响

- **GIVEN** RM 评级明确看多或看空（非中性）且辩论一方锚点占优
- **THEN** 均衡带约束 SHALL NOT 生效，决策路径与现状一致

#### Scenario: 均衡带内首次建仓无增量放行

- **GIVEN** RM 评级中性、辩论锚点统计多空对峙（均衡带成立），近窗无该标的历史决策（首次建仓）
- **WHEN** risk_judge 输出 buy 且 reasoning 披露了对峙点但未申报新增量事实
- **THEN** 执行动作 SHALL 放行，不触发打回，正常进入后续门禁

#### Scenario: 均衡带内方向翻转仍受滞回约束

- **GIVEN** 均衡带成立，近窗前向方向为 watch
- **WHEN** risk_judge 输出 buy 且 reasoning 未申报触发翻转的增量事实
- **THEN** 按「方向滞回与增量事实申报」处理：打回重申一次，重申仍无增量维持前向方向

#### Scenario: 均衡带标志仍落 state

- **WHEN** 均衡带判定执行
- **THEN** `decision_hysteresis` state 键保留 balanced_zone 等判定要素（trace 观测语义不变）
- **AND** 均衡带成立时 risk_judge 上下文仍携带均衡带标志（供 reasoning 披露对峙点）
