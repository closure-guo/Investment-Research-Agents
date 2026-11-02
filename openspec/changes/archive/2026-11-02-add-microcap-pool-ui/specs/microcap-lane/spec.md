# Delta for microcap-lane

## ADDED Requirements

### Requirement: 小市值池读侧可见性

小市值（microcap）档精选池 SHALL 经读侧接口可见：池行落库契约（含 `stock_name` 透传、缺名空串
降级）与主档逐字一致；批次记录字段 `frontier_gate` 与 `exec_constraints` SHALL 随池接口与批次
历史接口透传给消费方，MUST NOT 在读侧被丢弃或改写。读侧可见性 SHALL NOT 反向影响主档缺省口径：
不带档位参数的既有读取 MUST NOT 混入 microcap 行。

展示面（选股页小市值视图）对门禁与约束 SHALL 承担披露义务：`frontier_gate` 的两态
（`ready` / `observation_only:missing=<要点>`）在展示上 SHALL 可区分，观察态 SHALL 列出缺失要点
（`delist_pool` → 退市股池未就绪，`regime_data` → 拥挤度数据未就绪，按落库要点码映射可读文案，
未知要点码原样列出）；`exec_constraints` SHALL 以记录字段口径展示（建议约束，非生效约束），
并 SHALL 同时声明「v1 仅观察、不向执行链产生订单」。

#### Scenario: 门禁两态展示可区分

- **GIVEN** 两个交易日的 microcap 批次分别落库 `frontier_gate="ready"` 与
  `"observation_only:missing=delist_pool,regime_data"`
- **WHEN** 展示面分别读取并渲染两个批次
- **THEN** ready 批次展示「前置齐备，可执行候选」，观察批次展示观察模式与两项缺失要点
- **AND** 两态展示不混淆，且均可见「不自动产生订单」语义

#### Scenario: 约束字段以建议口径透传

- **GIVEN** microcap 批次落库 `exec_constraints={"suggested_single_cap": 0.05, "slippage_tier_bump": 1}`
- **WHEN** 读取池接口
- **THEN** 两字段原样透传，展示面标注为建议约束（非生效约束）

#### Scenario: 主档缺省口径不混入小市值行

- **GIVEN** 库中 main 与 microcap 两档批次并存
- **WHEN** 消费方不带档位参数读取池与批次历史
- **THEN** 返回内容仅含 main 档行
