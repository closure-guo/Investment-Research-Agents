# Delta for factor-library

## ADDED Requirements

### Requirement: 跨批次候选检验账本

系统 SHALL 在因子库中维护候选检验账本（`factor_admission_ledger`），逐候选记录其五道闸评估事实：批次标识与来源（monthly_broad/monthly_deep/harvest/builtin/external）、表达式、闸③置换检验读数（`p_value`/同批 BH 校正 `p_adj`/同批口径 `q`/同批族大小 `n_hypotheses`）、准入结果（是否入库、`factor_id`、拒收闸口 reason code）与评估时刻。写入 SHALL 由批量准入编排（`admit_candidates`）单点承担——任免入口（月批/收割/builtin/外部导入）不得各自接线遗漏；未达闸③的候选（预检失败除外）SHALL 记账但 p 值为 NULL。

账本 SHALL 提供只读读数：库级汇总（总行数 / 已验证数（p 非空）/ 已入库数 / 分批明细）与**全局 BH 反事实预览**（把账本中全部非空 p 值作为一个跨批次家族做一次 BH 校正，输出按给定 q 会拒掉多少——回答「全局账本执行后今日读数会怎样」）。读数 MUST NOT 改变任何准入结论（本 requirement 只保证数据可查询，不执行跨批次校正）。

账本 SHALL NOT 影响既有闸门口径：闸③ 的批内 BH-FDR 校正语义、单批族定义（同批候选数 K）与幂等重跑行为保持不变。库复位（`reset_factor_library`）SHALL 一并清空账本。

#### Scenario: 单批准入逐候选记账

- **GIVEN** 一批候选经 `admit_candidates` 评估（含入库、闸②拒收、闸③拒收各若干）
- **WHEN** 批处理完成
- **THEN** 账本 SHALL 为每个进入批量准入的候选各记一行（同 batch_id、同 source）
- **AND** 入库行 SHALL 携带 factor_id 且 p 值非空；闸③拒收行 SHALL 携带 p 值与该批 `n_hypotheses`/`p_adj`/`q`；闸①②拒收行 p 值 SHALL 为 NULL 且携带拒收闸口 reason code

#### Scenario: 跨批次族查询与全局反事实

- **GIVEN** 账本内已有多个批次、多个已验证候选（p 非空）
- **WHEN** 读取库级汇总与全局 BH 反事实预览（给定 q）
- **THEN** 汇总 SHALL 返回总行数 / 已验证数 / 已入库数 / 分批明细（batch_id、source、批大小、已验证数）
- **AND** 反事实预览 SHALL 返回：按全部已验证 p 值作单一家族 BH 校正后仍显著的数量、其对应的 factor_id 清单（如已入库）
- **AND** 读数 MUST NOT 修改任何行或因子状态

#### Scenario: 未达闸③候选 p 值 NULL 不污染家族

- **GIVEN** 某候选在结构查重或闸①/②被拒（未做置换检验）
- **WHEN** 记账
- **THEN** 该行 p_value/p_adj/n_hypotheses SHALL 为 NULL
- **AND** 全局 BH 反事实预览 SHALL 只消费非空 p 值（NULL 行不计入家族）

#### Scenario: 复位清空账本

- **WHEN** 执行 `reset_factor_library`
- **THEN** 账本 SHALL 与 factors/factor_ic/factor_events 一并清空并返回行数
