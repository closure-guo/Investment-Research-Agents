## ADDED Requirements

### Requirement: 小票账本只读查询

既有执行台账只读端点 SHALL 接受可选 `book` 查询参数（`main` / `microcap`），缺省
`main` 且缺省调用行为与既有契约逐字一致；`book=microcap` SHALL 返回独立小票执行库的
持仓 / 现金 / 净值曲线 / 订单历史，字段口径与主档一致；`book` 取值超出枚举 SHALL
返回 422，MUST NOT 静默回退。

#### Scenario: 缺省 main 行为不变

- **WHEN** 不带 `book` 参数请求台账只读端点
- **THEN** 返回主档账本数据，响应与变更前一致

#### Scenario: 小票账本可查

- **GIVEN** 小票执行库已有持仓与净值行
- **WHEN** 以 `book=microcap` 请求
- **THEN** 返回小票库数据，字段口径与主档一致
- **AND** 主档库数据不出现在响应中

#### Scenario: 非法 book 拒绝

- **WHEN** 请求带 `book=foo`
- **THEN** 返回 422，不返回任何账本数据
