# frontend delta: add-broker-console

## ADDED Requirements

### Requirement: 执行台账页

系统 SHALL 提供 `/broker` 路由页，采用 PageShellA（指标条 + 主图表 + 底部 Tab 表）形态：指标条 SHALL 含 总资产 / 可用资金 / 持仓市值 / 浮动盈亏（市值不可得时显式标注而非显示 0）；主图表 SHALL 为持仓市值构成（按标的）；底部 Tab 表 SHALL 含 持仓 / 订单 / 门禁拒绝 三个页签，订单状态 SHALL 使用 StatusBadge（pending→running、filled→active、rejected→failed、expired→retired）；空账户时 SHALL 如实展示空态。页面入口 SHALL 同源提供于侧边栏与命令面板。

#### Scenario: 台账齐备

- **GIVEN** 账户含 2 条持仓与 4 条不同状态订单
- **WHEN** 打开页面
- **THEN** 指标条与持仓构成图渲染；订单 Tab 含四态徽章
- **AND** 门禁拒绝 Tab 含被拒订单与原因码

#### Scenario: 空账户空态

- **GIVEN** 账户无持仓、无订单
- **WHEN** 打开页面
- **THEN** 指标条显示资金，图表与表格显示空态文案
- **AND** MUST NOT 渲染伪持仓或伪订单
