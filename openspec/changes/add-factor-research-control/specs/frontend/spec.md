# Delta for frontend（add-factor-research-control）

## MODIFIED Requirements

### Requirement: 因子研发监控页

系统 SHALL 提供 `/factor-research` 路由页，展示外部因子研发（RD-Agent 持续循环）的运行状态
（数据面只读）与研发看护启停开关（控制面），形态为「看护状态区 + 当前会话进度区 + 会话历史表」：
状态区 SHALL 含 看护状态徽章（运行中 / 已停止，复用 StatusBadge 五态）、看护 pid、启动时刻、
已运行时长、累计会话数与候选实现数；进度区 SHALL 含 当前会话开始时刻与时长、循环序号、当前
步骤名、步骤进度（x/5）、本会话因子名列表与日志尾（等宽字体，最近 N 行）；历史表 SHALL 列出
各会话（开始时刻 / 时长 / 状态，新→旧）。页面 SHALL 轮询自动刷新（间隔 ≤ 15 秒）；快照陈旧
（端点 `stale=true`）SHALL 在页面明示「数据已陈旧」（含最近采集时刻）且 MUST NOT 冒充实时；
快照缺失或损坏（`available=false`）SHALL 如实展示「采集器未接入」/「快照损坏」两种空态，
MUST NOT 显示骨架屏或错误提示冒充。页面 SHALL 提供研发看护启停开关（常驻可见，含状态文案）：
开关位置 SHALL 反映快照实际运行状态（运行中 = 开；MUST NOT 乐观翻转——位置仅在快照刷新后
改变）。点击「停止」SHALL 经二次确认（明示将终止当前研发会话）后调用
`PUT /api/factor-research/control`；点击「启动」直接提交。提交成功且 `control.pending=true`
期间 SHALL 展示「切换中」并禁用重复操作，MUST NOT 把切换中冒充为已生效。快照缺失/损坏/陈旧或
端点返回 409（`control_unavailable`）时开关 SHALL 禁用并明示理由（如「控制通道未接入」）；
请求失败 SHALL 如实提示原因，开关位置保持快照实际状态。页面其余部分仍只读（MUST NOT 增设其它
控制入口）。页面入口 SHALL 同源提供于侧边栏与命令面板。

(Previously: 页面 MUST NOT 提供启停/控制入口（只读页）。)

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

#### Scenario: 开关两态与切换中

- **GIVEN** 快照 `supervisor.running=true` 且 `control.pending=false`
- **WHEN** 打开监控页
- **THEN** 开关为开且可操作
- **AND** 用户关闭并在二次确认中确认后发出控制请求，`pending=true` 期间开关禁用并显示「切换中」
- **AND** 快照刷新为 `running=false` 后开关为关、恢复可操作

#### Scenario: 停止需二次确认

- **GIVEN** 开关处于开（看护运行中）
- **WHEN** 点击停止
- **THEN** 出现二次确认（含「将终止当前研发会话」语义）
- **AND** 未确认前 MUST NOT 发出控制请求

#### Scenario: 控制通道未接入禁用

- **GIVEN** 快照缺失 / 损坏 / 陈旧（任一）
- **WHEN** 打开监控页
- **THEN** 开关禁用且明示理由（如「控制通道未接入」）
- **AND** MUST NOT 可点击发出控制请求

#### Scenario: 请求失败如实提示

- **GIVEN** 发出控制请求但端点返回 409 或网络失败
- **WHEN** 请求返回
- **THEN** 页面提示失败原因
- **AND** 开关位置保持快照实际状态（MUST NOT 翻转冒充成功）
