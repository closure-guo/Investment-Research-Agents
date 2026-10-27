# Delta for Frontend

## MODIFIED Requirements

### Requirement: 因子研发监控页

系统 SHALL 提供 `/factor-research` 路由页，展示外部因子研发（RD-Agent 持续循环）的运行状态
（数据面只读）与研发看护控制（启停开关 + 停止按钮 + 重启按钮），形态为「看护状态区 + 当前会话进度区 +
会话历史表」：状态区 SHALL 含 看护状态徽章、看护 pid、启动时刻、已运行时长、累计会话数与
候选实现数；进度区 SHALL 含 当前会话开始时刻与时长、循环序号、当前步骤名、步骤进度（x/5）、
本会话因子名列表与日志尾（等宽字体，最近 N 行）；历史表 SHALL 列出各会话（开始时刻 / 时长 /
状态，新→旧）。页面 SHALL 轮询自动刷新（间隔 ≤ 15 秒）；快照陈旧（端点 `stale=true`）SHALL
在页面明示「数据已陈旧」（含最近采集时刻）且 MUST NOT 冒充实时；快照陈旧且
`control.honor="launcher"` 时，陈旧提示 SHALL 同时说明启动器正在按节拍尝试恢复；快照缺失或
损坏（`available=false`）SHALL 如实展示「采集器未接入」/「快照损坏」两种空态，MUST NOT 显示
骨架屏或错误提示冒充。

看护状态徽章 SHALL 按端点 `actual_state` 三态展示：`running` → 「运行中」；`stopped` →
「已停止」；`unverified` → 「状态未知」（复用 StatusBadge 的 degraded 态），MUST NOT 把陈旧
快照的最后读数直接标成运行中/已停止。

启停开关位置 SHALL 反映**验证过的实际运行状态**：`actual_state="running"` → 开；`stopped` 或
`unverified` → 关。快照陈旧或不可用时 MUST NOT 以 `control.desired_running` 决定开关位置——
持久化意图 MUST NOT 冒充运行中；此时状态文案 SHALL 明示「状态未知（研究侧离线，最近采集
时刻）」，并披露持久化意图（如「已记录保持运行，启动器将在 ≤3 分钟内拉起」或「当前未记录
运行意图」）。陈旧时若 `control.requested_at` 晚于快照 `collected_at`，SHALL 展示「已请求
（发起时刻），等待启动器拉起」。点击「停止」SHALL 经二次确认（明示将终止当前研发会话）后
调用 `PUT /api/factor-research/control`；点击「启动」直接提交。提交成功且 `control.pending=true`
期间 SHALL 展示「切换中」并禁用重复操作，MUST NOT 把切换中冒充为已生效。`control.pending=true`
且快照 `agent.last_action` 为 `start-failed` / `stop-failed` 时，SHALL 展示「启动失败」/
「停止失败」（含动作时刻与 `last_error` 摘要）并注明代理将自动重试，MUST NOT 以「切换中」
无限冒充进展。

控制条 SHALL 提供「停止」按钮（常驻可见，与启停开关并列）：点击 SHALL 经二次确认（与开关关闭
方向同一确认文案，明示将终止当前研发会话）后调用 `PUT /api/factor-research/control`
（`desired_running=false`）；未确认前 MUST NOT 发出请求。停止按钮 SHALL 与启停开关共享禁用条件
（提交中 / `control.pending=true` / `control.restart_pending=true` / 通道不可用 / 409 封禁），
另在无可停止对象（`actual_state="stopped"` 且 `control.desired_running !== true`）时 SHALL 禁用。
停止按钮 MUST NOT 改变开关位置语义（开关位置仍只取 `actual_state`），亦 MUST NOT 引入开关/重启
之外的写路径（同一端点、同一确认）。

控制条 SHALL 提供「重启管线」按钮（常驻可见）：点击 SHALL 经二次确认（明示将终止当前研发
会话并重新拉起）后调用 `POST /api/factor-research/restart`；`control.restart_pending=true`
期间 SHALL 展示「重启中…（发起时刻，等待研发侧受理）」并禁用重复操作；快照
`agent.restart.result="restart-failed"` 时 SHALL 展示重启失败原因（含 `agent.last_error`），
MUST NOT 显示成功。重启请求发出后若 `restart_pending` 持续为真且超过 60 秒（`honor="live"`）
或 180 秒（`honor="launcher"`），页面 SHALL 提示「重启请求未被执行（研究侧代理可能为旧版本，
需部署新版代理）」，MUST NOT 无限显示「重启中」冒充进展。

`control.honor="launcher"`（研究侧离线但启动器可用）时启停与重启 SHALL 保持可操作，并明示
「研究侧离线（最近采集时刻）——将在 ≤3 分钟内自动拉起」。`control.honor="unavailable"` 或
端点返回 409（`control_unavailable`）时启停与重启 SHALL 禁用并明示理由（「控制通道暂不可用
（研究侧与启动器均离线）」/「控制通道未接入」）；请求失败 SHALL 如实提示原因，开关位置保持
上一可信实态（MUST NOT 翻转冒充成功）。快照 `sessions` 最近 3 条（按开始时刻倒序）`duration_s`
均小于 120 秒时，页面 SHALL 展示「连续短命会话」告警（含最近 3 次时长），提示疑似凭证/配额
问题。页面其余部分仍只读（MUST NOT 增设其它控制入口）。页面入口 SHALL 同源提供于侧边栏与
命令面板。

(Previously: 控制条只有启停开关与「重启管线」两个入口——停止只能经开关的关闭方向触发，没有字面
「停止」按钮；开关位置语义、二次确认与禁用条件均同本次更新，其余行为相同。)

#### Scenario: 运行中状态与进度展示

- **GIVEN** 快照显示看护运行中、`actual_state="running"`、当前循环序号 4、步骤 coding、
  本会话已出现 5 个因子名、日志尾非空
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

#### Scenario: 重启后不把持久化意图显示成已开启

- **GIVEN** 快照陈旧/缺失（`actual_state="unverified"`），控制文件 `desired_running=true`
- **WHEN** 打开监控页
- **THEN** 开关位置为关（MUST NOT 依据持久化意图显示为开）
- **AND** 状态文案含「状态未知」并披露「已记录保持运行」
- **AND** 若启动器有存活证据，开关可操作并明示「将在 ≤3 分钟内自动拉起」

#### Scenario: 陈旧时开关位置取意图

- **GIVEN** 快照陈旧且 `control.desired_running=true`
- **WHEN** 打开监控页
- **THEN** 开关位置为**关**（本变更推翻原「取意图=开」口径：持久化意图 MUST NOT 冒充运行中）
- **AND** 状态文案含「状态未知」并披露「已记录保持运行，启动器将在 ≤3 分钟内拉起」
- **AND** 点击停止/启动仍可操作（离线方向不因实态未知而禁用）

#### Scenario: 启动失败如实披露

- **GIVEN** `control.pending=true` 且快照 `agent.last_action="start-failed"`、`last_error` 非空
- **WHEN** 页面刷新
- **THEN** 展示「启动失败（动作时刻）：<last_error 摘要>；代理将自动重试」
- **AND** MUST NOT 继续以「切换中」冒充进展
- **AND** 停止失败（`stop-failed`）按同一口径披露

#### Scenario: 开关两态与切换中

- **GIVEN** 快照 `actual_state="running"` 且 `control.pending=false`
- **WHEN** 打开监控页
- **THEN** 开关为开且可操作
- **AND** 用户关闭并在二次确认中确认后发出控制请求，`pending=true` 期间开关禁用并显示「切换中」
- **AND** 快照刷新为 `actual_state="stopped"` 后开关为关、恢复可操作

#### Scenario: 停止需二次确认

- **GIVEN** 开关处于开（看护运行中）
- **WHEN** 点击停止
- **THEN** 出现二次确认（含「将终止当前研发会话」语义）
- **AND** 未确认前 MUST NOT 发出控制请求

#### Scenario: 重启管线

- **GIVEN** `actual_state="running"` 且控制通道可用
- **WHEN** 点击「重启管线」并在二次确认中确认
- **THEN** 发出 `POST /api/factor-research/restart`（未确认前零请求）
- **AND** `control.restart_pending=true` 期间展示「重启中…」并禁用开关与重启按钮
- **AND** 快照 `agent.restart.result="restarted"` 后恢复显示实态

#### Scenario: 重启失败如实披露

- **GIVEN** `agent.restart.result="restart-failed"`、`agent.last_action="start-failed"` 且
  `last_error` 非空
- **WHEN** 页面刷新
- **THEN** 展示重启失败原因（含动作时刻与 `last_error`）
- **AND** MUST NOT 显示「重启中」或成功态

#### Scenario: 旧代理未执行重启

- **GIVEN** 重启请求已受理，快照持续推进但 `agent.restart` 恒为空，`honor="live"`
- **WHEN** 请求发起超过 60 秒
- **THEN** 页面提示「重启请求未被执行（研究侧代理可能为旧版本，需部署新版代理）」
- **AND** MUST NOT 无限显示「重启中」

#### Scenario: 离线可开与预计拉起

- **GIVEN** 快照陈旧（或缺失）且 `control.honor="launcher"`（启动器心跳新鲜、`agent_ok=true`）
- **WHEN** 打开监控页
- **THEN** 开关为关但可操作，并明示「研究侧离线（最近采集时刻）——将在 ≤3 分钟内自动拉起」
- **AND** 点击开启后请求被受理，页面展示「已请求（发起时刻），等待启动器拉起」
- **AND** 启动器拉起研究侧并写出新快照后，开关按 `actual_state` 恢复显示

#### Scenario: 控制通道未接入禁用

- **GIVEN** 快照缺失/损坏/陈旧，且 `control.honor="unavailable"`（启动器亦无证据）
- **WHEN** 打开监控页
- **THEN** 开关与重启按钮禁用且明示理由（「控制通道暂不可用（研究侧与启动器均离线）」）
- **AND** MUST NOT 可点击发出控制/重启请求

#### Scenario: 请求失败如实提示

- **GIVEN** 发出控制或重启请求但端点返回 409 或网络失败
- **WHEN** 请求返回
- **THEN** 页面提示失败原因
- **AND** 开关位置保持上一可信实态（MUST NOT 翻转冒充成功）

#### Scenario: 连续短命会话告警

- **GIVEN** 快照 `sessions` 最近 3 条 `duration_s` 均小于 120 秒
- **WHEN** 打开监控页
- **THEN** 页面展示「连续短命会话」告警（含最近 3 次时长），提示疑似凭证/配额问题

#### Scenario: 停止按钮与开关共享二次确认与写路径

- **GIVEN** 快照 `actual_state="running"`、`control.pending=false`、控制通道可用
- **WHEN** 点击「停止」
- **THEN** 出现二次确认（含「将终止当前研发会话」语义），未确认前 MUST NOT 发出请求
- **AND** 确认后调用 `PUT /api/factor-research/control`，请求体为 `{"desired_running": false}`
- **AND** `control.pending=true` 期间停止按钮禁用（与开关同禁用条件），开关位置不翻转

#### Scenario: 停止按钮在无可停止对象时禁用

- **GIVEN** 快照 `actual_state="stopped"` 且 `control.desired_running !== true`
- **WHEN** 打开监控页
- **THEN** 「停止」按钮禁用
- **AND** 开关位置为关且开关本身可操作（启动方向不经二次确认）

#### Scenario: 停止按钮在通道不可用时禁用

- **GIVEN** `control.honor="unavailable"`（或最近一次控制请求返回 409 `control_unavailable`）
- **WHEN** 打开监控页
- **THEN** 「停止」按钮与开关、「重启管线」按钮一并禁用并明示理由
- **AND** MUST NOT 可点击发出控制请求

#### Scenario: 离线意图可撤销

- **GIVEN** 快照陈旧（`actual_state="unverified"`）、`control.desired_running=true`、
  `honor="launcher"`
- **WHEN** 点击「停止」并在二次确认中确认
- **THEN** 写入 `desired_running=false`（撤销持久化运行意图），状态文案披露等待启动器受理
- **AND** MUST NOT 声称看护已停止（实态仍不可证）
