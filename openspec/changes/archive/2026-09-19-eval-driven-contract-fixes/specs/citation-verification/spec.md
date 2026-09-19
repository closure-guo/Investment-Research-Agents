# citation-verification Delta

## ADDED Requirements

### Requirement: 同义列名字段解析消歧

校验器按 field_ref 对快照 DataFrame 取值或重算时，SHALL 先做**列名消歧**：当目标表存在多个语义同名/近义列（如利润表同时存在「归属于母公司的净利润」与「归母净利润」）且数值不同时，解析器 SHALL（按优先级）——① 命中官方科目全名列则视为唯一权威列；② 仅命中短名/别名时，若存在对应官方全名列 SHALL 改用全名列；③ 无法消歧时 SHALL 将该 claim 判为 `blocked`（解析歧义）并进四桶拆报的 blocked 桶，SHALL NOT 静默取任一列的值参与 value_mismatch 比较。

依据：2026-09-18 A4 自然腿 owner 终裁（601318，报表 1347.78 亿为官方全名列真值、校验器取短名列 235.23 亿产生假 FAIL）。

#### Scenario: 官方全名优先

- **WHEN** field_ref 为 `income_statement.20251231.归母净利润`，且该表同时存在「归属于母公司的净利润」（官方全名）与「归母净利润」（短名）两列且数值不同
- **THEN** 校验器 SHALL 取官方全名列的值作为 ground truth，短名 field_ref SHALL 映射到全名列

#### Scenario: 不可消歧判 blocked

- **WHEN** 存在多个近义列且无官方全名列可判定权威来源
- **THEN** 该 claim SHALL 判 `blocked` 进解析桶，SHALL NOT 产生 value_mismatch FAIL

### Requirement: claim 值槽语义类型校验

数值型 claim 的 `stated_value` SHALL 与 `field_ref` 所指字段的**语义类型**一致（水平值 level 不得填变化量 delta，反之亦然）。当 claim 的 interpretation 表明其数值是区间变化量（环比/同比变化、差值），而 field_ref 指向水平值字段（或相反）时，校验器 SHALL 将该 claim 判为**契约错误**进解析桶（`claim_contract_error`），SHALL NOT 计入 value_mismatch（分析师幻觉口径），且 SHALL NOT 触发修复回路（正文往往正确，修的应是 claim 契约）。

依据：2026-09-18 A4 自然腿 owner 终裁（600276，claim 把 PMI 环比变化 0.6 填进 PMI 水平值槽 49.8，正文算术正确）。

#### Scenario: 变化量入水平槽判契约错误

- **WHEN** claim 的 field_ref 为 `macro_indicators.pmi.0.制造业-指数`（水平值 49.8），stated_value 为 0.6 且 interpretation 为「环比回升 0.6 个百分点」
- **THEN** 校验器 SHALL 判 `claim_contract_error` 进解析桶，SHALL NOT 判 value_mismatch

#### Scenario: 契约错误不触发修复

- **WHEN** 某 claim 判为 `claim_contract_error`
- **THEN** 该 claim SHALL NOT 进入单点修复或全量重试路径，SHALL 在校验报告中单独呈现供契约修复
