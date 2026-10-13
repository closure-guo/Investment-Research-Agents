# frontend delta: add-screening-pipeline

## ADDED Requirements

### Requirement: 选股页

系统 SHALL 提供 `/screening` 路由页，展示最近成功批次的候选池表格，列含：标的、综合分、连续在榜天数、L3 verdict 与 reason、批次日期；综合分列 SHALL 支持 hover 展示因子贡献分解（factor_id 与 contribution）；页面 SHALL 提供「对精选池跑深度分析」按钮，触发后复用现有 SSE 管线时间轴展示各标的分析进度。

#### Scenario: 贡献分解 hover

- **GIVEN** 候选池表格中标的 C 综合分 1.823
- **WHEN** 用户 hover 综合分单元格
- **THEN** 浮层展示 C 的 factor_contribs 列表（factor_id + contribution），按 contribution 绝对值降序
