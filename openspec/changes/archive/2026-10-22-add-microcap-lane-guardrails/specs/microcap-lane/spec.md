# Delta for microcap-lane（add-microcap-lane-guardrails）

## ADDED Requirements

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
