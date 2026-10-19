# Delta for frontend（add-factor-research-selfheal）

## MODIFIED Requirements

### Requirement: 因子研发监控页

系统 SHALL 提供 `/factor-research` 路由页，展示外部因子研发（RD-Agent 持续循环）的运行状态
（数据面只读）与研发看护启停开关（控制面），形态为「看护状态区 + 当前会话进度区 + 会话历史表」：
状态区 SHALL 含 看护状态徽章（运行中 / 已停止，复用 StatusBadge 五态）、看护 pid、启动时刻、
已运行时长、累计会话数与候选实现数；进度区 SHALL 含 当前会话开始时刻与时长、循环序号、当前
步骤名、步骤进度（x/5）、本会话因子名列表与日志尾（等宽字体，最近 N 行）；历史表 SHALL 列出
各会话（开始时刻 / 时长 / 状态，新→旧）。页面 SHALL 轮询自动刷新（间隔 ≤ 15 秒）；快照陈旧
（端点 `stale=true`）SHALL 在页面明示「数据已陈旧」（含最近采集时刻）且 MUST NOT 冒充实时；
快照陈旧且 `control.honor="launcher"` 时，陈旧提示 SHALL 同时说明启动器正在按节拍尝试恢复；
快照缺失或损坏（`available=false`）SHALL 如实展示「采集器未接入」/「快照损坏」两种空态，
MUST NOT 显示骨架屏或错误提示冒充。页面 SHALL 提供研发看护启停开关（常驻可见，含状态文案）：
快照 available 且非陈旧时，开关位置 SHALL 反映快照实际运行状态（运行中 = 开；MUST NOT 乐观翻转
——位置仅在快照刷新后改变）；快照陈旧或不可用时，开关位置 SHALL 取控制块
`control.desired_running`（`null` 视为关）——离线时持久化意图是唯一可信状态（与重启后按同一
意图恢复一致）。点击「停止」SHALL 经二次确认（明示将终止当前研发会话）后调用
`PUT /api/factor-research/control`；点击「启动」直接提交。提交成功且 `control.pending=true`
期间 SHALL 展示「切换中」并禁用重复操作，MUST NOT 把切换中冒充为已生效。`control.honor="launcher"`
（研究侧离线但启动器可用）时开关 SHALL 保持可操作，并明示「研究侧离线（最近采集时刻）——将在
≤3 分钟内自动拉起」；若 `control.requested_at` 晚于快照 `collected_at`，SHALL 展示「已请求
（发起时刻），等待启动器拉起」。`control.pending=true` 且快照 `agent.last_action` 为
`start-failed` / `stop-failed` 时，SHALL 展示「启动失败」/「停止失败」（含动作时刻与
`last_error` 摘要）并注明代理将自动重试，MUST NOT 以「切换中」无限冒充进展。快照 `sessions`
最近 3 条（按开始时刻倒序）`duration_s` 均小于 120 秒时，页面 SHALL 展示「连续短命会话」告警
（含最近 3 次时长），提示疑似凭证/配额问题。`control.honor="unavailable"` 或端点返回 409
（`control_unavailable`）时开关 SHALL 禁用并明示理由（「控制通道暂不可用（研究侧与启动器均离线）」/
「控制通道未接入」）；请求失败 SHALL 如实提示原因，开关位置保持上一可信状态（快照实态或持久化
意图）。页面其余部分仍只读（MUST NOT 增设其它控制入口）。页面入口 SHALL 同源提供于侧边栏与
命令面板。

(Previously: 开关位置恒取快照实态；快照缺失/损坏/陈旧或 409 时开关禁用——无启动器维度，也不存在
离线可开。)

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

#### Scenario: 离线可开与预计拉起

- **GIVEN** 快照陈旧（或缺失）且 `control.honor="launcher"`（启动器心跳新鲜、`agent_ok=true`）
- **WHEN** 打开监控页
- **THEN** 开关可操作，并明示「研究侧离线（最近采集时刻）——将在 ≤3 分钟内自动拉起」
- **AND** 点击开启后请求被受理，页面展示「已请求（发起时刻），等待启动器拉起」
- **AND** 启动器拉起研究侧并写出新快照后，开关按新快照实态恢复显示

#### Scenario: 陈旧时开关位置取意图

- **GIVEN** 快照陈旧且 `control.desired_running=true`
- **WHEN** 打开监控页
- **THEN** 开关位置为开（依据持久化意图，MUST NOT 依据陈旧快照的 `supervisor.running`）
- **AND** 点击停止进入二次确认（离线停止方向同样可操作）

#### Scenario: 启动失败如实披露

- **GIVEN** `control.pending=true` 且快照 `agent.last_action="start-failed"`、`last_error` 非空
- **WHEN** 页面刷新
- **THEN** 展示「启动失败（动作时刻）：<last_error 摘要>；代理将自动重试」
- **AND** MUST NOT 继续以「切换中」冒充进展
- **AND** 停止失败（`stop-failed`）按同一口径披露

#### Scenario: 连续短命会话告警

- **GIVEN** 快照 `sessions` 最近 3 条 `duration_s` 均小于 120 秒
- **WHEN** 打开监控页
- **THEN** 页面展示「连续短命会话」告警（含最近 3 次时长），提示疑似凭证/配额问题
- **AND** 告警 MUST NOT 阻断开关与既有展示

#### Scenario: 控制通道未接入禁用

- **GIVEN** 快照缺失/损坏/陈旧，且 `control.honor="unavailable"`（启动器亦无证据）
- **WHEN** 打开监控页
- **THEN** 开关禁用且明示理由（「控制通道暂不可用（研究侧与启动器均离线）」）
- **AND** MUST NOT 可点击发出控制请求

#### Scenario: 请求失败如实提示

- **GIVEN** 发出控制请求但端点返回 409 或网络失败
- **WHEN** 请求返回
- **THEN** 页面提示失败原因
- **AND** 开关位置保持上一可信状态（快照实态或持久化意图，MUST NOT 翻转冒充成功）
