# matching-engine delta: add-matching-engine

## ADDED Requirements

### Requirement: 单一撮合实现

系统内所有成交判定（回测回放、仿真撮合、未来实盘回报解析）SHALL 调用同一撮合函数 `execution/matching.py::match_order`；禁止在任何其他模块内重新实现成交价格或成交可行性逻辑。

#### Scenario: 跨模式一致性

- **GIVEN** 同一订单（买入 600519，1000 股，2026-09-01）与同一份当日快照数据
- **WHEN** 分别以 backtest 模式与 paper 模式执行撮合
- **THEN** 两种模式返回的成交价、成交量、费用完全一致

### Requirement: A 股交易约束

撮合函数 SHALL 强制以下约束并给出结构化未成交原因：(a) 一字涨停时买入订单不成交，原因码 `LIMIT_UP_UNFILLABLE`；(b) 一字跌停时卖出订单不成交，原因码 `LIMIT_DOWN_UNFILLABLE`；(c) 当日买入持仓当日不可卖出（T+1），原因码 `T1_SELL_BLOCKED`；(d) 委托数量非 100 的整数倍时拒绝，原因码 `ODD_LOT`（卖出时持仓尾数不足 100 股的零股除外）。

#### Scenario: 一字涨停买单不成交

- **GIVEN** 标的 X 当日快照为一字涨停（开盘价=收盘价=涨停价）
- **WHEN** 提交买入订单
- **THEN** 返回成交量 0、原因码 `LIMIT_UP_UNFILLABLE`
- **AND** 不产生任何持仓与资金变动

#### Scenario: T+1 卖出拦截

- **GIVEN** 账户于 T 日买入标的 Y 500 股
- **WHEN** T 日提交卖出 Y 的订单
- **THEN** 返回成交量 0、原因码 `T1_SELL_BLOCKED`

### Requirement: 分档滑点与成本模型

撮合函数 SHALL 按标的当日成交额所处的全市场分位档应用滑点参数（档位与滑点值由配置提供，默认不少于三档），并计入佣金与印花税（卖出方）；滑点参数与成本参数 SHALL NOT 硬编码于函数体内。

#### Scenario: 滑点档位移交配置

- **GIVEN** 配置文件定义三档滑点（如 0.05% / 0.1% / 0.2%）
- **WHEN** 修改配置中最低档滑点为 0.08% 且不改代码
- **THEN** 低流动性档标的的买入成交价按新参数上浮 0.08%
