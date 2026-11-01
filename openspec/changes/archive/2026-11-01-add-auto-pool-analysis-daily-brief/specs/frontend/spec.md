# Delta for frontend

## ADDED Requirements

### Requirement: 日批运维页每日汇报区

`/ops` 日批运维页 SHALL 在页面顶部展示最近一份每日汇报：

- 展示日期、生成状态（ok/failed）、模型标识与 Markdown 正文；
- 汇报尚未生成时 SHALL 显示明确空态（含「20:30 自动生成」提示），MUST NOT 显示空白或伪造内容；
- `status=failed` 时 SHALL 显示失败横幅与错误信息，并照常展示降级摘要；
- 取数失败 SHALL 显式报错，与「暂无汇报」空态严格区分；
- 该区 SHALL 由 `GET /api/v1/ops/daily-brief/latest` 数据驱动，不硬编码日期或内容。

#### Scenario: 汇报存在时渲染

- **GIVEN** 最新汇报 `status=ok` 且含 Markdown
- **WHEN** 打开 `/ops`
- **THEN** 页面展示日期、模型与渲染后的 Markdown

#### Scenario: 汇报未生成时为空态

- **GIVEN** 接口返回 404 `daily_brief_not_found`
- **WHEN** 打开 `/ops`
- **THEN** 展示「今日汇报尚未生成（20:30 自动生成）」
- **AND** 不显示任何伪造的结论或数字

#### Scenario: 生成失败时显式告警

- **GIVEN** 最新汇报 `status=failed` 且 `error` 非空
- **WHEN** 打开 `/ops`
- **THEN** 页面展示失败横幅与错误信息
- **AND** 仍展示确定性降级摘要正文
