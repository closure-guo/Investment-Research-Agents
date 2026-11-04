# Delta for track-record

## MODIFIED Requirements

### Requirement: 观点结算与判定窗口

系统 SHALL 按统一规则判定观点：**默认判定窗口 T+20 交易日**（配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`；观点自带 `horizon_days` 则以自带为准，上限 1 年）；**判定入场基准 SHALL 为 `settle_entry_price`（判定时从行情派生，非参考价）**；对同一标的发出方向相反的新观点时，旧的 long/short 观点 SHALL 立即以当前价结算（superseded，入场基准同为派生 `settle_entry_price`）；同方向的新观点（含仅调整目标价）SHALL NOT 触发旧观点结算；**neutral 观点 SHALL NOT 走 superseded 提前结算**（回避判定以完整 horizon 窗口为准）。

**两类结算 SHALL 共用同一套收益、超额与分类数学**（`settle_at_exit` 为唯一实现）：区间收益 `raw = sign × (exit/entry − 1)`；区间超额 `excess = raw − sign × (benchmark_exit/benchmark_entry − 1)`；分类按**超额**落入 ±2% 中性带三分（long/short → resolved_win/loss/neutral，neutral 按回避语义）。**`superseded` 结算 SHALL NOT 以「无同期基准区间」为由把 `excess_return` 置空**——基准在批内可用；基准不可得时 `excess_return` SHALL 退化为 `raw_return`（降级，不写 NULL）。

(Previously: `superseded` 分支自行构造 Resolution，`excess_return` 恒为 None（注释理由「无完整同期基准区间」），分类用 raw_return 而非超额；`expiry` 分支另有一套独立实现。两路径口径不一致。)

#### Scenario: superseded 结算携带超额

- **GIVEN** 某 long 观点入场 100，被新观点覆盖时收盘 90；同期基准 4000 → 3800
- **WHEN** 日批执行 superseded 结算
- **THEN** `raw_return` = -0.10，`excess_return` = -0.05
- **AND** `excess_return` SHALL 落库（SHALL NOT 为 NULL）

#### Scenario: 分类以超额为准

- **GIVEN** 某 long 观点 raw = +3%，同期基准 +6%（excess = -3%）
- **WHEN** 结算
- **THEN** 状态 SHALL 为 `resolved_loss`
- **AND** SHALL NOT 因 raw 为正记 `resolved_win`

#### Scenario: 基准不可得时降级

- **GIVEN** 批内基准行情拉取失败（benchmark 为 None）
- **WHEN** superseded 结算
- **THEN** `excess_return` SHALL 等于 `raw_return`
- **AND** SHALL NOT 抛错或写 NULL

#### Scenario: horizon 到点口径不变

- **GIVEN** 观点走到 T+20
- **WHEN** `expiry` 结算
- **THEN** 收益/超额/分类结果 SHALL 与修订前逐位一致（仅实现改为委托 `settle_at_exit`）
