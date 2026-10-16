# Delta: factor-library（unify-quant-research-architecture）

## ADDED Requirements

### Requirement: 准入面板深度下限

五道闸准入评估（本地提案、builtin 注册、外部导入三入口共用）所用面板 SHALL 以最近不少于 150 个交易日为下限深度构造（独立准入面板常量，不影响衰减监控面板口径）；快照库可用历史不足下限时准入任务 SHALL fail loud（错误信息含面板深度不足与补历史指引），MUST NOT 以浅面板放行评估。

#### Scenario: 深度不足 fail loud

- **GIVEN** 快照库仅 90 个交易日可用
- **WHEN** 任一准入入口（提案月批 / 基线注册 / 外部导入）执行
- **THEN** 任务记 failed，错误信息含准入面板深度不足提示
- **AND** 因子库零新增、零评估（MUST NOT 以浅面板出闸口结论）

#### Scenario: 深面板长窗因子可算

- **GIVEN** 准入面板 ≥150 个交易日、候选为 20 日窗因子
- **WHEN** 闸①可计算性评估执行
- **THEN** 热身期缺失占比落在阈值口径内（≈13%+覆盖缺口，< 20%），该候选以真实覆盖与 IC 质量参与后续闸口裁决，不再被结构性拒收
