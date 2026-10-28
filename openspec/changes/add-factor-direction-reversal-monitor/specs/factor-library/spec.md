# Delta for factor-library

## MODIFIED Requirements

### Requirement: 衰减监控与自动降级

系统 SHALL 每日重算 active 因子的近 60 日滚动 IC；当某因子滚动 IC 均值连续 20 个交易日为负且 |滚动 ICIR| < 0.1 时，SHALL 自动将其状态置为 `degraded`、从选股打分权重中移除，并落库降级事件（含触发时的滚动指标值）。

因子行 SHALL 持久化**入池定向符号**（`orientation`：−1 / 0 / 1）：由闸④的候选训练段 RankIC 符号在入库时写入（`admit_candidates` 单点承担，月任入口——月批/builtin/收割/外部导入——统一生效）；不可定（训练段符号为 0）SHALL 记 0。存量因子 SHALL 按全史已记录 `factor_ic` 均值符号一次性回填（无样本或均值为 0 → 0），回填幂等可重跑。

衰减监控 SHALL 另设**方向反转判据**：`orientation ≠ 0` 的 active 因子，当其近 60 日滚动 IC 均值连续 20 个交易日的符号**与入池定向相反**时（无论强弱，不加 ICIR 幅度门槛——滚动均值本身已为 60 日平滑），SHALL 自动置 `degraded`、从选股打分权重中移除，并落降级事件（`reason=DIRECTION_REVERSAL`，detail 含入池定向符号与触发时滚动读数）。降级后因子 SHALL NOT 参与打分与桶内保种排名（degraded 不在 active 集合内，语义由既有状态机承载）；其同式重提与复核恢复走既有通道（retired/degraded 允许重提、degraded 可复核恢复 `active`）。两判据（衰减/反转）同日同时命中时 SHALL 只落一条降级事件，reason 取反转判据（方向失效优先于强度衰减）。

(Previously: 仅「滚动 IC 均值连续 20 日为负且 |滚动 ICIR| < 0.1」单一衰减判据；无入池定向持久化、无方向反转判据——负向因子 IC 翻正永不降级。)

#### Scenario: 衰减降级

- **GIVEN** 因子 F 连续 20 个交易日滚动 IC 均值为负且滚动 ICIR = 0.05
- **WHEN** 当日衰减监控日批执行
- **THEN** F 状态变为 `degraded`，降级事件落库含滚动 IC/ICIR 快照
- **AND** 当日选股打分不再包含 F

#### Scenario: 负向因子方向反转降级

- **GIVEN** 因子 F 入池定向 `orientation=-1`（训练段 IC 均值为负），其近 60 日滚动 IC 均值连续 20 个交易**日为正**（负向关系反转）
- **WHEN** 当日衰减监控日批执行
- **THEN** F 状态变为 `degraded`，降级事件 reason 为 `DIRECTION_REVERSAL`，detail 含 `orientation=-1` 与触发时滚动读数
- **AND** 当日选股打分不再包含 F

#### Scenario: 短期翻正未达窗口不降级

- **GIVEN** 因子 F 入池定向 `orientation=-1`，其 60 日滚动 IC 均值最近 5 日为正、此前为负（连续为正仅 5 个滚动点）
- **WHEN** 当日衰减监控日批执行
- **THEN** F 状态保持 `active`（连续 20 日窗口未满足）

#### Scenario: 定向不可定的因子不走反转判据

- **GIVEN** 因子 F `orientation=0`（存量回填时无 IC 样本），其滚动 IC 均值连续 20 日为正
- **WHEN** 当日衰减监控日批执行
- **THEN** 反转判据不触发（仅原「负且弱」判据照常评估）

#### Scenario: 入库时定向符号持久化

- **GIVEN** 候选因子经五道闸入库（闸④训练段 RankIC 均值为负，定向 −1）
- **WHEN** 入库完成
- **THEN** 因子行 `orientation=-1`，与闸④ `detail.orientation` 一致

#### Scenario: 存量因子定向回填幂等

- **GIVEN** 库内既有 12 条因子无 `orientation` 值，其中全史 IC 均值为负者 10 条
- **WHEN** 执行存量回填（重跑一次）
- **THEN** 10 条回填为 −1，其余按全史均值符号回填（无样本者记 0），重跑结果不变（幂等）
