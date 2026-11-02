# microcap-lane Specification

## Purpose
TBD - created by archiving change add-microcap-lane-guardrails. Update Purpose after archive.
## Requirements
### Requirement: 含退市股历史池

系统 SHALL 维护含退市标的的证券池：退市清单 SHALL 被抓取并落库（标的、退市日、交易所）；`security_master` SHALL 扩展退市标记与退市日字段；退市标的的价格历史在数据源可得范围内 SHALL 被补采。池构建（回测/统计宇宙）SHALL 支持包含退市标的：对时点 t，标的可见当且仅当 上市日 ≤ t ≤ 退市日；无价格历史的退市标的 SHALL 如实标注缺失，MUST NOT 静默剔除。

#### Scenario: 防幸存者偏差回归

- **GIVEN** 样本集含一只已退市标的（退市日 D）与一只存续标的
- **WHEN** 构建退市日之前时点 t（t < D）的池
- **THEN** 退市标的出现在池中
- **AND** 该回归测试在无退市池逻辑时失败（先红后绿）

#### Scenario: 退市后不可见

- **GIVEN** 同上样本
- **WHEN** 构建 t > D 的池
- **THEN** 退市标的不在池中

### Requirement: 小票通道约束档位

`constraints.py` SHALL 提供约束档位：`main`（默认档，与现状逐字一致）与 `microcap`（流动性阈值默认 1000 万元，其余参数同主档；档位参数 SHALL 可配置且常量仍单一来源）。过滤层与批次编排 SHALL 支持按档位执行；使用默认档的既有调用行为 MUST 逐字节不变。

#### Scenario: 主档行为不变

- **GIVEN** 既有默认调用路径
- **WHEN** 以默认档执行过滤
- **THEN** 结果与变更前逐项一致

#### Scenario: 档位决定流动性阈值

- **GIVEN** 一只近 20 日日均成交额 2000 万元的标的
- **WHEN** 分别以 main 档与 microcap 档执行
- **THEN** main 档剔除（`ILLIQUID`），microcap 档通过

### Requirement: 通道批次产出与前置门禁

小票通道 SHALL 产出带 `pool=microcap` 标注的批次记录（含建议执行约束：单票上限默认 5%、滑点档位提升，作为记录字段）。批次产出的可执行性 SHALL 受前置门禁约束：仅当（a）含退市股池就绪与（b）拥挤度数据就绪两项前置齐备时，批次可标记为可执行候选；缺任一前置时批次 SHALL 标记 `observation_only` 并披露缺失前置，MUST NOT 静默按可执行对待。通道 v1 MUST NOT 向执行链产生订单。

#### Scenario: 前置缺失观察模式

- **GIVEN** 退市股池或拥挤度数据未就绪
- **WHEN** 通道日批产出批次
- **THEN** 批次标记 `observation_only` 并列出缺失前置
- **AND** 不产生任何订单

#### Scenario: 前置齐备

- **GIVEN** 两项前置均就绪
- **WHEN** 通道日批产出批次
- **THEN** 批次标记为可执行候选（记录字段），仍不自动产生订单（v1）

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

