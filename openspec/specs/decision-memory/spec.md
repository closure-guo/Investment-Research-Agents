# decision-memory Specification

## Purpose
TBD - created by archiving change add-decision-memory. Update Purpose after archive.
## Requirements
### Requirement: 情景记忆注入

深度分析启动时，系统 SHALL 检索该标的及同申万三级行业标的的历史已结算决策（含决策方向、结算结果、论据摘要），取 Top-5 注入分析师与 Trader 上下文；注入条目 SHALL 标注来源决策 ID 与日期，并纳入既有 citation 校验覆盖。

#### Scenario: 同标的历史注入

- **GIVEN** 标的 300750 存在 3 条已结算历史决策
- **WHEN** 发起对 300750 的新一次深度分析
- **THEN** 分析师与 Trader 的上下文中包含该 3 条记忆条目（含来源 ID 与日期）
- **AND** Langfuse trace 含用途标签 `decision_memory`

### Requirement: 语义记忆人工确认

系统 SHALL 按月从已结算决策中提炼候选经验条款；候选条款 SHALL 经人工确认后方可生效注入风控辩论层；未确认条款 SHALL NOT 出现在任何 prompt 中。

#### Scenario: 未确认条款不生效

- **GIVEN** 月批产出候选条款 5 条，其中 2 条被人工确认
- **WHEN** 下一次风控辩论执行
- **THEN** prompt 中仅含已确认的 2 条
- **AND** 全部 LLM 调用路径中检索不到未确认的 3 条文本

### Requirement: 确认操作入口

系统 SHALL 提供语义记忆条款的只读查询与确认 / 驳回操作接口：列表接口 SHALL 支持按状态（`pending` / `confirmed` / `rejected`）过滤，返回条款 id、正文、来源、提炼时间与当前状态；确认与驳回操作 SHALL 幂等（对已处于目标状态的条款重复操作返回当前状态而非报错），对未知条款 SHALL 返回 404；接口 SHALL NOT 提供条款编辑、合并或删除。人工确认的生效语义沿用「语义记忆人工确认」要求：仅 `confirmed` 条款参与注入。

#### Scenario: 列表按状态过滤

- **GIVEN** 库中 3 条 pending、2 条 confirmed、1 条 rejected
- **WHEN** 以 `status=pending` 请求列表
- **THEN** 仅返回 3 条 pending，字段含 id / 正文 / 来源 / 提炼时间

#### Scenario: 确认后即刻生效

- **GIVEN** 一条 pending 条款
- **WHEN** 对其执行确认
- **THEN** 该条款状态转为 confirmed，且出现在已确认集合（注入集合）中
- **AND** 再次确认幂等返回 confirmed

#### Scenario: 未知条款 404

- **WHEN** 对不存在的条款 id 执行确认或驳回
- **THEN** 返回 404，MUST NOT 静默成功

