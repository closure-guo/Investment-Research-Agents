# Delta for factor-proposal

## ADDED Requirements

### Requirement: 横截面转译路径

外部研发供给的转译链路 SHALL 支持横截面构念（截面排序、截面标准化及其组合）到合法 DSL 表达式的转译。实施路径从「Qlib 映射层横截面算子桥」与「直出本系统 DSL」两案中择一（择一决策与理由记录于本 change 的 design.md，不进主规范）。转译 SHALL NOT 引入近似转译——映射语义须逐值核对（Qlib `Rank(x,N)` 滚动秩 ↔ 本系统 `ts_rank` 等），任何找不到逐值等价语义的构念 SHALL 维持 `untranslatable` 留痕。`qlib_translate` 既有映射的回归测试 SHALL 全绿，作为转译路径变更的合入硬门禁。横截面转译路径解锁后，生成侧契约的「禁横截面运算」条款 SHALL 随契约真相源自动解除（MUST NOT 在生成侧保留手工第二真相源）。

#### Scenario: 横截面候选进入五道闸

- **GIVEN** 收割候选含一条横截面构念表达式，其构念在语义核对清单内有逐值等价映射
- **WHEN** 收割日批执行转译
- **THEN** 该候选产出合法 DSL 表达式（截面算子 `rank`/`zscore` 在算子集内），进入五道闸
- **AND** 不计 `untranslatable`

#### Scenario: 无逐值等价的构念维持拒译

- **GIVEN** 候选表达式含语义核对清单之外的构念（无逐值等价映射）
- **WHEN** 转译执行
- **THEN** 该候选计 `untranslatable` 留痕
- **AND** MUST NOT 以近似语义转译入库

#### Scenario: 既有映射回归全绿

- **GIVEN** 转译路径变更（映射桥扩充或直出切换）已实现
- **WHEN** 运行 `qlib_translate` 既有映射回归测试
- **THEN** 全部通过
- **AND** 任一失败 SHALL 阻断合入

#### Scenario: 生成侧契约自动解除禁横截面条款

- **GIVEN** 横截面转译路径已实现且契约真相源（`factors.dsl` / `qlib_translate` 导出）更新
- **WHEN** 下一次生成 prompt 组装
- **THEN** 契约段不再含「禁横截面运算」条款（真相源跟随）
- **AND** 生成侧无手工契约副本需要同步
