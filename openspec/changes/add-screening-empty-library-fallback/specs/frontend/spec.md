# Delta: frontend（add-screening-empty-library-fallback）

## MODIFIED Requirements

### Requirement: 选股页

系统 SHALL 提供 `/screening` 路由页，展示最近成功批次的候选池表格，列含：标的、综合分、连续在榜天数、L3 verdict 与 reason、批次日期；综合分列 SHALL 支持 hover 展示因子贡献分解（factor_id 与 contribution）；页面 SHALL 提供「对精选池跑深度分析」按钮，触发后复用现有 SSE 管线时间轴展示各标的分析进度；选择模式为 `fallback` 的批次 SHALL 在页面展示降级披露横幅（明示「因子库无可打分因子、按近 20 日日均成交额排序、非因子信号」），且综合分列 SHALL 切换为「近 20 日日均成交额」语义呈现（标题与数值口径同步切换）。

#### Scenario: 贡献分解 hover

- **GIVEN** 候选池表格中标的 C 综合分 1.823
- **WHEN** 用户 hover 综合分单元格
- **THEN** 浮层展示 C 的 factor_contribs 列表（factor_id + contribution），按 contribution 绝对值降序

#### Scenario: 降级披露横幅

- **GIVEN** 最近成功批次选择模式为 fallback
- **WHEN** 用户打开选股页
- **THEN** 页面展示降级披露横幅，文案含「非因子信号」
- **AND** 综合分列标题切换为「近 20 日日均成交额（亿元）」且数值按成交额口径呈现
