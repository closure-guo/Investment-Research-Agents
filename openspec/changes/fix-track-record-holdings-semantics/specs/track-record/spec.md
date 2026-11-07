# Delta for track-record

> 提案阶段 delta：以 ADDED 表达「持仓状态」新口径维度。owner 拍板进入实施时，
> 本 delta 将改写为对「观点数据模型」与「观点全量记录」的 MODIFIED 合并形态。

## ADDED Requirements

### Requirement: 持仓状态口径（holdings_state）

系统 SHALL 为每条观点记录 `holdings_state` 三态：`held`（受众已申报持有）、
`not_held`（受众已申报未持有）、`unreported`（受众持仓未申报，默认值）。
direction 映射 SHALL 按 holdings_state 分流：

- `held` + `sell`（含 `sell_type=exit`）→ `short` 方向性结算（持有人退出敞口，
  价格下跌获益的数学对持有人成立）；
- `not_held` + `sell`（含 `sell_type=exit`）→ `avoidance` 语义（复用既有
  neutral 回避判定机制：收益记 0，机会成本单列展示，SHALL NOT 进主账本胜率）；
- `unreported` → 保持现状映射但单独打标，SHALL NOT 混入主账本方向性统计。

存量观点的 `holdings_state` SHALL 为 NULL（视同 `unreported`）且不追溯回填；
切换点之前的存量方向性观点 SHALL 标注口径来源（`legacy_short_mapping`），
净值曲线在切换点前后分段展示。

#### Scenario: 未持有者看空按回避语义

- **GIVEN** 一条 `holdings_state=not_held` 的 sell（sell_type=exit）观点
- **WHEN** 落库与判定
- **THEN** direction SHALL NOT 记为 short，SHALL 按 avoidance 语义判定（收益 0，
  机会成本单列）
- **AND** SHALL NOT 进主账本胜率

#### Scenario: 持有者看空保持方向性

- **GIVEN** 一条 `holdings_state=held` 的 sell（sell_type=exit）观点
- **WHEN** 落库与判定
- **THEN** direction SHALL 记为 short，按既有方向性规则结算

#### Scenario: 未申报持仓不混入主账本

- **GIVEN** 一条 `holdings_state=unreported` 的 sell 观点
- **WHEN** 战绩统计
- **THEN** 该观点 SHALL 保持现状映射但单独打标
- **AND** SHALL NOT 计入主账本方向性统计
