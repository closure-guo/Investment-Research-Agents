## ADDED Requirements

### Requirement: 小票账本只读查询

既有执行台账只读端点（`/api/broker/overview` / `/api/broker/positions` /
`/api/broker/orders` / `/api/broker/gate-log` 四个端点按 `book` 路由）SHALL 接受可选
`book` 查询参数（`main` / `microcap`），缺省 `main` 且缺省调用行为与既有契约逐字一致；
`book=microcap` SHALL 返回独立小票执行库的总览（可用资金 / 持仓市值 / 总资产 / 浮动盈亏）/
持仓 / 订单历史 / 门禁拒绝记录，字段口径与主档一致；`book` 取值超出枚举 SHALL 返回 422，
MUST NOT 静默回退。小票账本净值 MUST NOT 经 broker 端点披露——净值经每日运营汇报的小票行
披露（可得时给出、不可得写「无数据」，见 daily-ops-brief 规格）。

#### Scenario: 缺省 main 行为不变

- **WHEN** 不带 `book` 参数请求台账只读端点
- **THEN** 返回主档账本数据，响应与变更前一致

#### Scenario: 小票账本可查

- **GIVEN** 小票执行库已有持仓与订单行
- **WHEN** 以 `book=microcap` 请求 overview / positions / orders / gate-log
- **THEN** 返回小票库数据，字段口径与主档一致
- **AND** 主档库数据不出现在响应中
- **AND** 响应中不含净值曲线数据（净值经每日运营汇报小票行披露）

#### Scenario: 非法 book 拒绝

- **WHEN** 请求带 `book=foo`
- **THEN** 返回 422，不返回任何账本数据
