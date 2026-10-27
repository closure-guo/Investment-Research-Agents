# Delta for factor-ic-gap-visibility

## ADDED Requirements

### Requirement: IC 样本缺口读数

The system SHALL 在因子信号就绪 payload 中为每个 active 因子提供 `ic_gap_days`：快照交易日历上「最近一条落库 IC 日期之后、面板 as_of（含）之前」的交易日数；因子无任何 IC 记录时 `ic_gap_days` SHALL 等于面板窗口内交易日数（诚实上界，不虚构为 0）。

#### Scenario: 因子存在缺口

- GIVEN 某 active 因子最近落库 IC 日期为 as_of 前第 5 个交易日
- WHEN 读取因子信号 payload
- THEN 该因子 `ic_gap_days = 4`（最近 IC 日之后至 as_of 之间共 4 个交易日无 IC）
- AND payload 顶层 `ic_gap_present = true`

#### Scenario: 因子无缺口

- GIVEN 某 active 因子最近落库 IC 日期等于面板窗口内最新可算日
- WHEN 读取因子信号 payload
- THEN 该因子 `ic_gap_days = 0`
- AND 若所有 active 因子 `ic_gap_days` 均为 0，payload 顶层 `ic_gap_present = false`

#### Scenario: 因子无任何 IC 记录

- GIVEN 某 active 因子在 `factor_ic` 无任何记录
- WHEN 读取因子信号 payload
- THEN 该因子 `ic_gap_days` 等于面板窗口内交易日数
- AND payload 顶层 `ic_gap_present = true`

### Requirement: 缺口读数只读展示

The system SHALL 仅在 payload / 前端 / 日报中展示缺口读数；缺口读数 MUST NOT 参与五道闸判定、选股打分或任何决策路径（口径边界见 `docs/evals/metrics.md` §1.21）。

#### Scenario: 缺口读数不改变选股模式

- GIVEN 某 active 因子 `ic_gap_days > 0` 且样本数 ≥ `MIN_IC_SAMPLES_FOR_SCORING`
- WHEN 选股日批运行
- THEN 选股模式不因缺口读数变化（仍按既有样本数门槛判定）
