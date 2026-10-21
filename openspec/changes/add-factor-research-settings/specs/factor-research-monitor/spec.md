# Delta for factor-research-monitor（add-factor-research-settings）

## ADDED Requirements

### Requirement: 研发服务实态派生

系统 SHALL 在 `GET /api/factor-research/status` 响应中附加 `actual_state`，取值为 `running` /
`stopped` / `unverified`。判定规则：快照 `available=true` 且 `stale=false` 时，
`supervisor.running=true` → `running`，否则 → `stopped`；快照缺失、损坏或陈旧 → `unverified`。
`actual_state` MUST NOT 由控制文件 `desired_running` 推导——持久化意图不是运行证据。

`control` 块 SHALL 同时返回重启请求字段：`restart_requested_at`（ISO8601 或 `null`）、
`restart_requested_at_epoch`（Unix 秒或 `null`）、`restart_request_id`（字符串或 `null`；
控制文件缺失、不可解析或字段非法 → `null`）与 `restart_pending`（布尔）。`restart_pending`
判定：存在合法的重启请求、快照 `available=true` 且非陈旧、且快照 `agent.restart` 未记录同一
`request_id` 的终态结果（`restarted` / `restart-failed`）时为真；快照陈旧、缺失或损坏时恒为
假（实态不可证，MUST NOT 据陈旧数据判「重启中」）。

响应 SHALL 另含 `config` 块（形状见「研发节奏配置通道」）。既有字段与派生语义不变。

#### Scenario: 快照新鲜且看护运行中

- **GIVEN** 快照 `available=true`、`stale=false`、`supervisor.running=true`，且控制文件
  `desired_running=false`
- **WHEN** 请求状态端点
- **THEN** `actual_state="running"`
- **AND** `control.pending=true`（期望与实态不一致）
- **AND** MUST NOT 因持久化意图为 false 而把实态报成 `stopped`

#### Scenario: 快照新鲜且看护已停止

- **GIVEN** 快照新鲜且 `supervisor.running=false`
- **WHEN** 请求状态端点
- **THEN** `actual_state="stopped"`

#### Scenario: 快照陈旧时实态不可证

- **GIVEN** 快照 `collected_at_epoch` 距请求 400 秒，`supervisor.running=true`，控制文件
  `desired_running=true`
- **WHEN** 请求状态端点
- **THEN** `actual_state="unverified"`
- **AND** `control.restart_pending=false`
- **AND** MUST NOT 以「快照最后一次读数 + 意图」冒充 `running`

#### Scenario: 重启请求待处理

- **GIVEN** 控制文件含 `restart_request_id="r123"` 且快照新鲜，快照 `agent.restart=null`
- **WHEN** 请求状态端点
- **THEN** `control.restart_requested_at` / `restart_requested_at_epoch` /
  `restart_request_id` 与控制文件一致
- **AND** `control.restart_pending=true`

#### Scenario: 重启已处理不判切换中

- **GIVEN** 控制文件 `restart_request_id="r123"`，快照 `agent.restart.request_id="r123"`、
  `result="restarted"`
- **WHEN** 请求状态端点
- **THEN** `control.restart_pending=false`

#### Scenario: 重启失败同样终结 pending

- **GIVEN** 控制文件 `restart_request_id="r123"`，快照 `agent.restart.request_id="r123"`、
  `result="restart-failed"`
- **WHEN** 请求状态端点
- **THEN** `control.restart_pending=false`
- **AND** 失败原因按快照 `agent.last_action` / `last_error` 如实透传

### Requirement: 管线重启控制

系统 SHALL 提供 `POST /api/factor-research/restart` 端点。请求体 MUST 为空对象（MUST NOT 接受
任意命令 / 脚本 / 参数——命令注入面为零）。受理规则与 `PUT /api/factor-research/control` 一致：
快照新鲜 → `honor="live"` 受理；快照缺失/损坏/陈旧但启动器心跳 available、`fresh` 且
`agent_ok=true` → `honor="launcher"` 受理；两者皆不成立 → 409 + `control_unavailable`，且
MUST NOT 写控制文件。

受理时 SHALL 原子写控制文件（沿用唯一临时文件名 + replace），内容含 `desired_running=true`、
`requested_at` / `requested_at_epoch` / `request_id`，以及 `restart_requested_at`（ISO8601）、
`restart_requested_at_epoch`（Unix 秒）与 `restart_request_id`（新的请求标识）。响应 SHALL 返回
`{"control": {...}}`（与状态端点 control 块同形状）。

研发代理 SHALL 在调和周期读取重启请求并按 `restart_request_id` 去重：同一请求只执行一次，
已处理标识 SHALL 持久化在研发工作区（代理重启后 MUST NOT 重复执行）。执行语义：若看护运行中，
先停止（终止当前会话，与既有停止实现同一语义）；停止失败 MUST NOT 继续启动（避免双看护）；
随后复用既有启动实现拉起看护。执行结果 SHALL 写入快照 `agent.restart`：`request_id`、
`requested_at_epoch`、`result`（`restarted` / `restart-failed`）、`handled_at`、`handled_at_epoch`。
快照 `agent.last_action` SHALL 继续取既有六值：重启成功报 `started`；停止失败报 `stop-failed`；
启动失败报 `start-failed`。重启 MUST NOT 删除研发产物（workspace 任务 / 候选文件 / 会话日志保持）。

`PUT /api/factor-research/control` SHALL 写入不含重启字段的控制文件——显式启停意图清除未执行的
重启请求。

#### Scenario: 重启请求落盘

- **GIVEN** 快照新鲜（`honor="live"`）
- **WHEN** `POST /api/factor-research/restart`
- **THEN** 返回 200，控制文件含 `desired_running=true` 与 `restart_request_id` /
  `restart_requested_at_epoch`
- **AND** 响应 `control.restart_pending=true`（快照尚未记录处理结果）

#### Scenario: 通道无证据拒绝重启

- **GIVEN** 快照缺失/损坏/陈旧，且启动器心跳缺失/损坏/不新鲜或 `agent_ok=false`
- **WHEN** `POST /api/factor-research/restart`
- **THEN** 返回 409 + `control_unavailable`
- **AND** 控制文件未被写入

#### Scenario: 代理执行一次

- **GIVEN** 控制文件 `desired_running=true` 且含未处理的 `restart_request_id="r123"`，看护运行中
- **WHEN** 代理调和
- **THEN** 先停止看护（终止当前会话）再启动看护
- **AND** 快照 `agent.restart.request_id="r123"`、`result="restarted"`
- **AND** 下一周期同一请求 MUST NOT 再次停止/启动（幂等）

#### Scenario: 代理重启后不重复执行

- **GIVEN** `r123` 已处理并持久化，代理守护被重建
- **WHEN** 新代理首次调和
- **THEN** MUST NOT 再次执行 `r123`

#### Scenario: 停止失败不启动第二个看护

- **GIVEN** 重启请求待处理，看护运行中且停止动作失败
- **WHEN** 代理调和
- **THEN** MUST NOT 执行启动动作
- **AND** 快照 `agent.restart.result="restart-failed"` 且 `agent.last_action="stop-failed"`

#### Scenario: 显式启停清除重启请求

- **GIVEN** 控制文件含未执行的重启请求
- **WHEN** `PUT /api/factor-research/control`（任意布尔值）
- **THEN** 新控制文件不含 `restart_request_id` / `restart_requested_at*`
- **AND** 后续代理调和 MUST NOT 执行该重启

#### Scenario: 旧代理不假成功

- **GIVEN** 重启请求已落盘，研究侧代理不识别该字段（不写 `agent.restart`）
- **WHEN** 快照持续推进
- **THEN** `control.restart_pending` 保持为真
- **AND** MUST NOT 仅凭快照时间推进判定重启成功

### Requirement: 研发节奏配置通道

系统 SHALL 提供 `PUT /api/factor-research/config` 端点，接受可选字段：
`snapshot_interval_s`（整数，5–120）、`session_gap_s`（整数，0–3600）、
`short_session_backoff_s`（整数，0–7200）、`short_session_threshold_s`（整数，30–3600）。
字段 MUST 为严格整数（MUST NOT 接受字符串 / 布尔 / 浮点强转）；未知字段 MUST 422；
空对象 MUST 422。受理规则与看护控制一致（`honor="unavailable"` → 409 +
`control_unavailable`，且 MUST NOT 写文件）。

受理时 SHALL 将字段与既有配置合并，原子写入 `data/factor_research/pipeline.json`
（路径 SHALL 在调用期读取 `FACTOR_RESEARCH_CONFIG_PATH`），并附 `updated_at`（ISO8601）、
`updated_at_epoch`（Unix 秒）与 `request_id`。响应 SHALL 返回 `{"config": {...}}`。

状态端点 SHALL 返回 `config` 块：`values`（四字段的生效值）、`source`
（`file` / `defaults` / `corrupt`）、`invalid_fields`（非法字段名数组）与 `updated_at`。
判定：文件缺失 → `values` 为默认值（15 / 60 / 600 / 120）、`source="defaults"`；文件不可解析
或非对象 → 默认值、`source="corrupt"`；文件可解析 → 逐字段取合法值，非法或越界字段回退默认值
并列入 `invalid_fields`，`source="file"`。

研发代理 SHALL 每个周期读取该文件并 fail-safe 归一化（缺失 / 损坏 / 字段非法 → 默认值；MUST NOT
崩溃或猜测）：`snapshot_interval_s` 在下一采集周期生效；`session_gap_s` /
`short_session_backoff_s` / `short_session_threshold_s` 在下次启动看护时写入看护脚本并生效。
代理 SHALL 在快照 `agent.config` 回显实际读取的 `values` / `source` / `invalid_fields`。
旧版代理未上报 `agent.config` 时，页面 SHALL 如实标注「研究侧未上报（可能为旧版）」，
MUST NOT 声称配置已生效。

#### Scenario: 配置写入与回读

- **GIVEN** 快照新鲜（`honor="live"`）
- **WHEN** `PUT /api/factor-research/config` 传 `{"snapshot_interval_s":30,"session_gap_s":120}`
- **THEN** 返回 200，文件含四字段（未传字段沿用既有值）与 `updated_at` / `request_id`
- **AND** 后续状态端点 `config.values.snapshot_interval_s=30`、
  `config.values.session_gap_s=120`、`config.source="file"`

#### Scenario: 非法字段回退并披露

- **GIVEN** 配置文件含 `{"session_gap_s": 99999, "short_session_backoff_s": "600"}`
- **WHEN** 请求状态端点
- **THEN** `config.values.session_gap_s=60`、`config.values.short_session_backoff_s=600`
- **AND** `config.invalid_fields` 含两个字段名

#### Scenario: 配置请求校验

- **GIVEN** 快照新鲜
- **WHEN** `PUT /api/factor-research/config` 传越界值 / 字符串 / 未知字段 / 空对象
- **THEN** 返回 422
- **AND** 配置文件未被写入

#### Scenario: 通道无证据拒绝配置

- **GIVEN** 快照缺失/损坏/陈旧，且启动器无存活证据
- **WHEN** `PUT /api/factor-research/config`
- **THEN** 返回 409 + `control_unavailable`
- **AND** 配置文件未被写入

#### Scenario: 采集间隔下一周期生效

- **GIVEN** 代理守护以 15 秒间隔运行，配置文件改为 `snapshot_interval_s=30`
- **WHEN** 当前周期结束
- **THEN** 下一周期按 30 秒调度（快照 `agent.config.values.snapshot_interval_s=30`）

#### Scenario: 会话节奏下次启动生效

- **GIVEN** 看护运行中，配置文件改为 `session_gap_s=120`
- **WHEN** 本次看护不重启
- **THEN** 当前看护脚本保持原间隔（页面明示「下次启动看护生效」）
- **AND** 下次启动看护时脚本使用 `session_gap_s=120`

#### Scenario: 旧代理未上报配置

- **GIVEN** 快照 `agent` 块不含 `config`
- **WHEN** 打开配置页
- **THEN** 页面标注「研究侧未上报（可能为旧版）」
- **AND** MUST NOT 展示「已生效」
