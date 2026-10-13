# track-record delta: add-paper-broker

## ADDED Requirements

### Requirement: 虚拟 vs 仿真双曲线

战绩仪表盘 SHALL 并排展示虚拟决策结算净值与 paper 仿真成交净值两条曲线（同一批决策、同一基准期），并标注两者差值为执行损耗；基期只向过去回退的防前视纪律对两条曲线同样适用。

#### Scenario: 双曲线展示

- **GIVEN** 某月虚拟结算净值 1.043、仿真成交净值 1.031
- **WHEN** 打开战绩仪表盘
- **THEN** 两条曲线同时渲染，图例区分「虚拟结算」「仿真成交」
- **AND** 执行损耗标注为 -1.2%（两值之差）
