# decision-memory delta: add-memory-confirmation

## ADDED Requirements

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
