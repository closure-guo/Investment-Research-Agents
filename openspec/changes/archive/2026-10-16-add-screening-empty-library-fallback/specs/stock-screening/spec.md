# Delta: stock-screening（add-screening-empty-library-fallback）

## ADDED Requirements

### Requirement: 无可打分因子降级通道

系统 SHALL 在 L2 打分器拒绝打分的条件成立时（无 active 因子，或全部 active 因子无有效 IC 记录）
进入显式降级通道：L2 SHALL 被显式跳过（MUST NOT 静默等权、MUST NOT 伪造因子权重或 IC）；系统
SHALL 以确定性流动性过渡排序产出 Top-N 候选——近 20 日日均成交额降序、同额按标的代码升序——
并照常落 L2 全量名单（在榜稳定性门槛照常累计）、照常执行 L3 粗筛与精选池落库；批次 SHALL 标注
选择模式 `selection_mode ∈ {factor, fallback}` 并经批次历史接口、池接口与选股页横幅三处披露，
披露文案 SHALL 明示该排序为流动性过渡规则、非因子信号。因子库恢复有可打分因子后，后续批次
SHALL 自动回到 `factor` 路径（降级为死分支，无配置开关）。降级通道 MUST NOT 吞掉其他失败：
L1/L3/池等其余任一步骤失败时批次仍 SHALL 记 failed、不产出精选池。

#### Scenario: 库空降级产出

- **GIVEN** 因子库无 active 因子、当日快照就绪
- **WHEN** 选股日批运行
- **THEN** 批次状态为 ok 且 `selection_mode=fallback`
- **AND** L2 名单与精选池照常产出（候选按近 20 日日均成交额降序，`factor_contribs` 为空）

#### Scenario: 三处披露

- **GIVEN** 最近成功批次为降级批次
- **WHEN** 读取池接口与批次历史接口
- **THEN** 两处均含 `selection_mode=fallback`，池接口另含明示「非因子信号」的披露文案
- **AND** 选股页展示降级披露横幅

#### Scenario: 恢复自动切回

- **GIVEN** 因子库已含可打分因子（active 且有 IC 记录）
- **WHEN** 选股日批运行
- **THEN** 批次 `selection_mode=factor` 且 L2 走因子打分路径（Top-N 携带贡献分解）

#### Scenario: 降级不吞失败

- **GIVEN** 因子库无 active 因子，且 L3 粗筛因 LLM 网关整体不可用而失败
- **WHEN** 选股日批运行
- **THEN** 批次状态为 failed、当日精选池零行（fail-closed 语义不因降级通道放宽）

## MODIFIED Requirements

### Requirement: 选股日批编排

系统 SHALL 在每个交易日快照更新成功后依次执行 L1 过滤 → L2 打分 → L3 粗筛 → 精选池落库（无可打分因子时 L2 SHALL 按「无可打分因子降级通道」显式跳过并披露，MUST NOT 静默等权）；任一步骤失败时该批次 SHALL 标记失败、不产出精选池，并触发前端横幅告警；L2 全量名单（含未入选标的）SHALL 每日完整落库（降级批次为降级排序名单，同一落库契约）。

#### Scenario: 中途失败不产残缺池

- **GIVEN** 某日 L3 粗筛因 LLM 网关整体不可用而失败
- **WHEN** 日批结束
- **THEN** 当日精选池为空且批次状态为 failed
- **AND** 前端选股页展示最近一个成功批次的精选池并标注其日期

### Requirement: 批次历史查询

系统 SHALL 提供选股批次历史只读接口：按交易日期倒序返回近 N 批次，字段含 日期、状态（ok / failed）、各层计数（`l1_passed` / `l2_top` / `l3_keep`）、精选池数量（`pool_size`）、选择模式（`factor` / `fallback`）、错误信息与起止时间；库为空时 SHALL 返回空列表而非错误；接口 SHALL NOT 暴露写操作。

#### Scenario: 历史字段齐备

- **GIVEN** 库中含 1 条成功批次与 1 条失败批次
- **WHEN** 请求批次历史
- **THEN** 返回两条（日期倒序），成功批次含各层计数与池数、失败批次含错误信息

#### Scenario: 空库不报错

- **GIVEN** 尚无任何批次
- **WHEN** 请求批次历史
- **THEN** 返回 200 与空列表
