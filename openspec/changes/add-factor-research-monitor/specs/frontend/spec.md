# Delta: frontend（add-factor-research-monitor）

## ADDED Requirements

### Requirement: 因子研发监控页

系统 SHALL 提供 `/factor-research` 路由页，只读展示外部因子研发（RD-Agent 持续循环）的运行
状态，形态为「看护状态区 + 当前会话进度区 + 会话历史表」：状态区 SHALL 含 看护状态徽章
（运行中 / 已停止，复用 StatusBadge 五态）、看护 pid、启动时刻、已运行时长、累计会话数与候选
实现数；进度区 SHALL 含 当前会话开始时刻与时长、循环序号、当前步骤名、步骤进度（x/5）、
本会话因子名列表与日志尾（等宽字体，最近 N 行）；历史表 SHALL 列出各会话（开始时刻 / 时长 /
状态，新→旧）。页面 SHALL 轮询自动刷新（间隔 ≤ 15 秒）；快照陈旧（端点 `stale=true`）SHALL 在
页面明示「数据已陈旧」（含最近采集时刻）且 MUST NOT 冒充实时；快照缺失或损坏（`available=false`）
SHALL 如实展示「采集器未接入」/「快照损坏」两种空态，MUST NOT 显示骨架屏或错误提示冒充。
页面 MUST NOT 提供启停/控制入口（只读页）。页面入口 SHALL 同源提供于侧边栏与命令面板。

#### Scenario: 运行中状态与进度展示

- **GIVEN** 快照显示看护运行中、当前循环序号 4、步骤 coding、本会话已出现 5 个因子名、日志尾非空
- **WHEN** 打开监控页
- **THEN** 状态徽章为运行中（含 pid 与已运行时长）
- **AND** 进度区展示循环序号、步骤名与步骤进度 x/5，因子列表含 5 个名称，日志尾区块渲染最近日志行

#### Scenario: 陈旧快照披露

- **GIVEN** 端点返回 `stale=true`
- **WHEN** 打开监控页
- **THEN** 页面展示「数据已陈旧」提示（含最近采集时刻）
- **AND** 快照内容仍可见（标注为陈旧，MUST NOT 隐藏）

#### Scenario: 未接入与损坏空态

- **GIVEN** 端点返回 `available=false, reason=missing`
- **WHEN** 打开监控页
- **THEN** 展示「采集器未接入」空态文案，MUST NOT 显示骨架屏或错误提示冒充

- **GIVEN** 端点返回 `available=false, reason=corrupt`
- **WHEN** 打开监控页
- **THEN** 展示「快照损坏」空态文案（与未接入文案区分）
