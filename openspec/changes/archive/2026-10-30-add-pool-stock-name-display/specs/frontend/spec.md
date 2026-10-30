# Delta for frontend

## MODIFIED Requirements

### Requirement: 选股页

系统 SHALL 提供 `/screening` 路由页，展示最近成功批次的候选池表格，列含：标的、股票名称、综合分、连续在榜天数、L3 verdict 与 reason、批次日期；「股票名称」列 SHALL 展示池行 `stock_name`，字段为 `null` 或空串时 SHALL 渲染 `—`（退化为仅代码展示，与迁移前行为一致）；综合分列 SHALL 支持 hover 展示因子贡献分解（factor_id 与 contribution）；页面 SHALL 提供「对精选池跑深度分析」按钮，触发后复用现有 SSE 管线时间轴展示各标的分析进度；选择模式为 `fallback` 的批次 SHALL 在页面展示降级披露横幅（明示「因子库无可打分因子、按近 20 日日均成交额排序、非因子信号」），且综合分列 SHALL 切换为「近 20 日日均成交额」语义呈现（标题与数值口径同步切换）。页面 SHALL 展示「因子信号状态」卡片：逐 active 因子给出 IC 样本进度（`n/10`）、是否达标与预计达标日，并给出批次级 `selection_mode`、降级原因与预计恢复因子打分日期；`factor` 模式且全部因子达标时 SHALL 展示达标态；无 active 因子时 SHALL 如实展示「无 active 因子」空态（MUST NOT 显示伪进度）。卡片数据 SHALL 来自池接口的 `factor_signal` 块，MUST NOT 前端自行估算日期。

(Previously: 池表格列含标的、综合分、连续在榜天数、L3 verdict 与 reason、批次日期，不含股票名称——池行数据源（selection_pool 表与池接口）未携带名称字段。)

#### Scenario: 股票名称列渲染

- **GIVEN** 池接口返回池行 `600519` 的 `stock_name` 为「贵州茅台」
- **WHEN** 打开 `/screening`
- **THEN** 精选池表格该行「股票名称」单元格渲染「贵州茅台」
- **AND** 「标的」列仍渲染 `600519`（代码与名称并存，名称列不替代代码列）

#### Scenario: 名称缺失退化展示

- **GIVEN** 池接口返回某池行 `stock_name` 为 `null`（迁移前历史行）或空串（快照缺名）
- **WHEN** 打开 `/screening`
- **THEN** 该行「股票名称」单元格渲染 `—`
- **AND** 该行其余列正常渲染，页面不报错

#### Scenario: 贡献分解 hover

- **GIVEN** 候选池表格中标的 C 综合分 1.823
- **WHEN** 用户 hover 综合分单元格
- **THEN** 浮层展示 C 的 factor_contribs 列表（factor_id + contribution），按 contribution 绝对值降序

#### Scenario: 降级披露横幅

- **GIVEN** 最近成功批次选择模式为 fallback
- **WHEN** 用户打开选股页
- **THEN** 页面展示降级披露横幅，文案含「非因子信号」
- **AND** 综合分列标题切换为「近 20 日日均成交额（亿元）」且数值按成交额口径呈现

#### Scenario: 精选池置顶

- **GIVEN** 端点返回成功批次与 3 只候选
- **WHEN** 打开 `/screening`
- **THEN** 滚动内容区首个子节点是 `screening-pool-card`，含「精选池」「共 3 只」与批次日期
- **AND** 市场温度计与事件观察区块排在池卡片之后
- **AND** 降级/陈旧横幅位于池卡片内

#### Scenario: 空池与失败态仍在池卡片内

- **GIVEN** 批次无候选或池端点失败
- **WHEN** 打开 `/screening`
- **THEN** 空态/失败态文案渲染在 `screening-pool-card` 内
- **AND** 页面其余区块不因此消失

#### Scenario: 因子信号状态卡片

- **GIVEN** 池接口返回 `factor_signal`：3 个因子分别 4/3/1 条样本、门槛 10、预计达标日 2026-11-02
- **WHEN** 用户打开选股页
- **THEN** 卡片逐因子展示「4/10」「3/10」「1/10」与「未达标」，并展示「预计 2026-11-02 恢复因子打分」
- **AND** 降级原因逐字展示（含「IC 样本不足」）

#### Scenario: 达标态

- **GIVEN** 池接口返回的因子全部 `eligible=true` 且批次 `selection_mode=factor`
- **WHEN** 用户打开选股页
- **THEN** 卡片展示达标态（如「因子信号已恢复」），MUST NOT 展示恢复日期
