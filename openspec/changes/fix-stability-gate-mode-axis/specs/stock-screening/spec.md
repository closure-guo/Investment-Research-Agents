# Delta for stock-screening

## MODIFIED Requirements

### Requirement: 在榜稳定性门槛

标的 SHALL 在**同选股口径**（`screening_batches.selection_mode`，与 L2 榜同 `(trade_date, pool)`）
最近 5 个交易日内处于 L2 Top-N 的天数 ≥ 3（`min_days` 可配置）方可进入精选池。其他口径的日子
不进轴、不打断计数——排序口径换挡（factor↔fallback）SHALL NOT 把在榜连击清零重攒。`as_of`
当日不在榜（含当日无榜单行）SHALL 无资格。批次行缺 `selection_mode` 的日子按同模式计入；
连续在榜计数（`consecutive_days_in_top`）保留原语义，仅供精选池 `consecutive_days` 信息列使用。

轴模式来源：精选池资格判定发生在当日批次行落库**之前**（批次行与池行同事务最后落库），当日
批次行在判定时必然不存在——资格判定 SHALL 以批次自身已知的 `selection_mode` 定轴（该值在 L2
打分前即已确定），MUST NOT 依赖「读取当日批次行」获取模式，MUST NOT 因当日批次行不存在而
退化为跨口径混合 5 日轴。全轴滚动降级（不猜模式）仅保留给无法确定模式的带外调用（如对存量
数据的查询）；日批路径 MUST NOT 走到该降级。

#### Scenario: 模式内单日跌出不否决

- **GIVEN** 标的 Y 在同口径第 1、2、4、5 日在榜、第 3 日跌出
- **WHEN** 第 5 日评估精选池资格
- **THEN** Y 的同模式窗口命中数为 4，进入精选池

#### Scenario: 口径换挡不清零重攒

- **GIVEN** 标的 Y 在因子口径第 1、2 日在榜，第 3、4 日为流动性口径（Y 不在榜），第 5 日回到
  因子口径且 Y 在榜
- **WHEN** 第 5 日评估精选池资格
- **THEN** 同模式轴命中第 1、2、5 日 = 3，进入精选池
- **AND** 连续在榜信息列如实记 1（两口径并存，信息不冒充）

#### Scenario: 命中不足仍无资格

- **GIVEN** 标的 Y 在同口径近 5 个交易日仅 2 日在榜
- **WHEN** 评估精选池资格
- **THEN** Y 不进入精选池（`min_days=2` 时进入，可配置语义保留）

#### Scenario: 当日不在榜无资格

- **GIVEN** 标的 Y 近 5 个同模式交易日命中 4 次但当日不在榜
- **WHEN** 当日评估精选池资格
- **THEN** 命中数记 0，不进入精选池

#### Scenario: 日批路径同模式轴生效

- **GIVEN** 当日 `selection_mode=factor` 且当日批次行尚未落库（资格判定先于批次行落库）；
  标的 Z 在因子口径近 5 个交易日命中 3 次，且 fallback 日的榜不含 Z
- **WHEN** 日批计算精选池资格
- **THEN** 轴仅含同模式交易日，Z 的命中数为 3，进入精选池
- **AND** 混合轴口径下 Z 仅命中 2 次的旧行为不复发（fallback 日不稀释因子口径命中）

#### Scenario: 无模式带外调用降级保留

- **GIVEN** 调用未显式提供模式，且 `as_of` 批次行缺 `selection_mode`（如对存量数据的查询）
- **WHEN** 计算滚动命中
- **THEN** 退回全轴滚动（不猜模式），行为与既有契约一致
