# factor-research-monitor Specification

## Purpose
外部因子研发（RD-Agent 持续循环看护）运行状态的可观测面：distro 侧采集器周期写 JSON 快照（经宿主
`data/` 桥），后端只读端点透传并派生陈旧标记，供 `/factor-research` 监控页呈现研发在不在跑、
跑到哪一步、产出了什么。created by archiving change add-factor-research-monitor.
## Requirements
### Requirement: 状态快照采集契约

外部因子研发（RD-Agent 持续循环看护）的运行状态 SHALL 由 distro 侧常驻**研发代理**周期写入宿主
可见的 JSON 快照（默认路径 `data/factor_research/status.json`，经环境变量可覆盖）：采集周期
SHALL ≤ 60 秒；写入 SHALL 原子（临时文件 + 重命名，读取方 MUST NOT 观察到半写文件）；采集路径
SHALL 只读观测（MUST NOT 修改研发进程状态、MUST NOT 干预会话生命周期）。快照 SHALL 含：
`collected_at`（ISO8601 带时区）与 `collected_at_epoch`（Unix 秒）、`supervisor`（`running` /
`pid` / `started_at`）、`current_session`（`log_file` / `started_at` / `duration_s` / `alive` /
`loop_index` / `step_index` / `step_name` / `steps_total` / `step_elapsed_s` / `step_eta_s` /
`factors` / `log_tail` / `log_age_s`）、`sessions`（历史列表，各含 `log_file` / `started_at` /
`ended_at` / `duration_s`）、`totals`（`sessions` / `candidates` / `workspace_tasks`）与 `agent`
（`last_action` / `last_action_at` / `last_error`）。`agent.last_action` SHALL 为代理最近一次
期望态调和动作，取值 `no_intent` / `noop` / `started` / `stopped` / `start-failed` /
`stop-failed`（控制文件缺失/损坏 → `no_intent`；期望与实态一致 → `noop`）；`last_action_at` SHALL 为该动作的 ISO8601 时刻（尚无动作 → `null`）；
`last_error` SHALL 在 `start-failed` / `stop-failed` 时为可判读的失败摘要（异常类型或首行，
MUST NOT 编造），成功动作后 SHALL 清空为 `null`。代理之外的独立采集路径（无调和动作可报）SHALL
写 `agent: null`，MUST NOT 伪造动作。代理 SHALL 独立于看护生命周期存活：看护停止（经控制通道或
手动脚本）后代理 MUST NOT 随之退出，SHALL 继续周期写快照（如实反映 `supervisor.running=false`）
直至操作者显式停止代理本身。日志中无可解析标记时（如会话刚启动），对应字段 SHALL 如实置空
（`null` / 空数组），MUST NOT 编造进度数字。

`current_session.log_age_s` SHALL 为当前会话日志文件 mtime 距采集时刻的秒数（非负整数）；
无当前会话日志文件或 stat 失败 → `null`。该字段 SHALL 只报**原始读数**——采集器 MUST NOT 自行
判定「僵死」（判定与阈值归只读查询端点单源），MUST NOT 以 0 或猜测值冒充已知新鲜度。

(Previously: `current_session` 不含 `log_age_s`；进度新鲜度不可观测——`alive` 只反映进程存在，
`step_elapsed_s` / `step_eta_s` 取自日志末行，日志冻结后读数定格，僵死会话与健康会话不可区分。)

#### Scenario: 周期快照原子落盘

- **GIVEN** 代理在研发运行期间工作
- **WHEN** 任一采集周期结束
- **THEN** 快照文件含完整 JSON（含 `collected_at`、`collected_at_epoch` 与全部顶层字段）
- **AND** 读取方任一时刻读到的都是完整快照（无半写内容）

#### Scenario: 停止写末次快照

- **GIVEN** 看护进程被显式停止（控制通道调和或手动脚本）
- **WHEN** 停止流程收尾
- **THEN** 快照 `supervisor.running=false` 且 `collected_at_epoch` 为停止时刻
  （其后由代理续写推进——「末次」语义由代理接管，不再以退出收尾）
- **AND** 代理进程仍在运行（MUST NOT 因看护停止而退出）

#### Scenario: 未知字段如实置空

- **GIVEN** 当前会话日志中尚无进度标记
- **WHEN** 代理写快照
- **THEN** `loop_index` / `step_index` / `step_name` 为 null、`factors` 为空数组
- **AND** MUST NOT 以 0 或猜测值冒充已解析的进度

#### Scenario: 调和动作留痕

- **GIVEN** 控制文件 `desired_running=true` 且看护未运行
- **WHEN** 代理完成一次调和并写快照
- **THEN** `agent.last_action="started"`、`agent.last_action_at` 为该次动作时刻、`agent.last_error=null`
- **AND** 下一次期望与实态一致时 `last_action` 回落到 `"noop"`

#### Scenario: 启动失败可读

- **GIVEN** 控制文件 `desired_running=true` 但启动流程失败（脚本缺失/权限/环境异常）
- **WHEN** 代理写快照
- **THEN** `agent.last_action="start-failed"` 且 `agent.last_error` 为非空可判读摘要
- **AND** MUST NOT 把失败写成 `noop` 或以猜测原因冒充

#### Scenario: 独立采集器如实置空

- **GIVEN** 独立调用采集器（无代理调和动作可报）
- **WHEN** 采集器写快照
- **THEN** 快照 `agent=null`
- **AND** MUST NOT 伪造 `last_action` 或动作时刻

#### Scenario: 日志新鲜度原始读数

- **GIVEN** 当前会话日志文件在 42 秒前被写入
- **WHEN** 采集器写快照
- **THEN** `current_session.log_age_s=42`（±1 秒）
- **AND** 采集器 MUST NOT 因该读数大而改写任何进程状态或跳过采集

- **GIVEN** 无当前会话日志文件（看护从未启动或日志目录为空）
- **WHEN** 采集器写快照
- **THEN** `current_session` 为 `null`（既有语义不变）且无 `log_age_s` 可报
- **AND** MUST NOT 以 0 冒充「刚刚写入」

### Requirement: 只读查询端点

系统 SHALL 提供 `GET /api/factor-research/status` 只读端点：快照路径 SHALL 在调用期读取
`FACTOR_RESEARCH_STATUS_PATH`（默认 `data/factor_research/status.json`）；快照存在且可解析时 SHALL
返回快照全量字段并附加派生字段 `available=true`、`age_seconds`（自 `collected_at_epoch` 至请求
时刻的秒数）与 `stale`（`age_seconds > 180` 为真）；快照文件缺失或不可解析时 SHALL 返回 200 +
`available=false` + 原因码（`missing` / `corrupt`），MUST NOT 以 500 或伪快照冒充。响应 SHALL 另
含控制块 `control`：`desired_running`（布尔；控制文件缺失、不可解析或字段非法 → `null`）、
`requested_at`（ISO8601 或 `null`）、`pending`（期望态非空、快照 available 且非陈旧、且期望态与
快照实态不一致时为真，其余情形为假）、`honor`（枚举 `live` / `launcher` / `unavailable`）与
`honor_eta_seconds`（`0` / `180` / `null`）。`honor` 判定：快照 available 且非陈旧 → `live`
（`honor_eta_seconds=0`）；否则启动器心跳 available、`fresh` 且 `agent_ok=true` → `launcher`
（`honor_eta_seconds=180`）；其余 → `unavailable`（`honor_eta_seconds=null`）。控制块 SHALL 在
快照不可用/陈旧时同样返回（`pending` 为假——实态不可证）。响应 SHALL 另含启动器块 `launcher`：
路径 SHALL 在调用期读取 `FACTOR_RESEARCH_LAUNCHER_PATH`（默认
`data/factor_research/launcher.json`）；文件存在且可解析 → `available=true` 并透传 `checked_at` /
`checked_at_epoch` / `agent_pid` / `agent_ok` / `snapshot_age_s` / `detail`，附加派生 `age_seconds`
（自 `checked_at_epoch` 至请求时刻的秒数）与 `fresh`（`age_seconds ≤ 300` 为真）；文件缺失或
不可解析 → `available=false` + 原因码（`missing` / `corrupt`），MUST NOT 以 500 冒充。端点 MUST NOT
写入或修改任何状态。

(Previously: 响应无 `launcher` 块；`control` 块只有 desired_running / requested_at / pending。)

#### Scenario: 快照存在时透传与派生

- **GIVEN** 快照文件合法且 `collected_at_epoch` 距请求时刻 40 秒（`supervisor.running=true`）
- **WHEN** 请求端点
- **THEN** 返回 200、`available=true`、`age_seconds≈40`、`stale=false`
- **AND** 快照原始字段（supervisor / current_session / sessions / totals / agent）逐字透传
- **AND** `control` 块与控制文件一致（缺失 → `desired_running=null`），实态与期望不一致时 `pending=true`
- **AND** `control.honor="live"`、`honor_eta_seconds=0`

#### Scenario: 陈旧快照如实标记

- **GIVEN** 快照文件合法但 `collected_at_epoch` 距请求时刻 400 秒
- **WHEN** 请求端点
- **THEN** 返回 `stale=true` 且 `age_seconds≈400`
- **AND** MUST NOT 因陈旧而拒绝返回快照内容
- **AND** `control.pending` 为假（实态不可证，MUST NOT 据陈旧数据判「切换中」）

#### Scenario: 缺失与损坏如实区分

- **GIVEN** 快照文件不存在
- **WHEN** 请求端点
- **THEN** 返回 200 + `available=false` + `reason=missing`

- **GIVEN** 快照文件存在但内容非合法 JSON
- **WHEN** 请求端点
- **THEN** 返回 200 + `available=false` + `reason=corrupt`
- **AND** 前端可据此展示与缺失不同的空态文案

#### Scenario: 启动器心跳透传与派生

- **GIVEN** 快照陈旧（400 秒）且 `launcher.json` 合法、`checked_at_epoch` 距请求 60 秒、`agent_ok=true`
- **WHEN** 请求端点
- **THEN** `launcher.available=true`、`launcher.age_seconds≈60`、`launcher.fresh=true`
- **AND** `control.honor="launcher"`、`honor_eta_seconds=180`、`pending=false`

#### Scenario: 双无证据 honor 不可用

- **GIVEN** 快照缺失/损坏/陈旧，且 `launcher.json` 缺失/损坏/不新鲜或 `agent_ok=false`
- **WHEN** 请求端点
- **THEN** `control.honor="unavailable"`、`honor_eta_seconds=null`
- **AND** MUST NOT 以 `launcher` 冒充可受理（无启动器存活证据即无受理能力）

### Requirement: 看护控制通道

distro 侧研发代理 SHALL 提供看护启停控制通道：周期（≤ 15 秒）轮询宿主可见控制文件（默认
`data/factor_research/control.json`，经环境变量可覆盖），按**期望状态调和**语义执行——
`desired_running=true` 且看护未运行 → 复用既有启动流程拉起（会话循环与退避策略不变）；
`desired_running=false` 且看护运行中 → 复用既有停止语义停止（终止当前会话，与手动停止同一实现）；
期望与实态一致 → MUST NOT 动作（幂等）。控制文件 SHALL 含 `desired_running`（布尔）、
`requested_at`（ISO8601 带时区）、`requested_at_epoch`（Unix 秒）与 `request_id`（请求标识，供
留痕）；写入方 SHALL 原子写。控制文件缺失、不可解析或字段非法时，代理 MUST NOT 启停任何进程
（fail-safe，MUST NOT 猜测期望态），SHALL 留可判读日志。停止动作 MUST NOT 删除研发产物
（workspace 任务 / 候选文件 / 会话日志保持）；启动与停止动作 SHALL 由代理与手动脚本共享同一启停语义（代理为
唯一主实现；脚本仅在代理不可用时保留逐字兜底硬停）。代理 SHALL 以进程锁串行化控制与采集动作（守护轮询与手动触发不并发）。

#### Scenario: 期望开启拉起看护

- **GIVEN** 控制文件 `desired_running=true` 且看护未运行（pidfile 不存在/进程已死）
- **WHEN** 代理轮询（或手动触发调和）
- **THEN** 看护被拉起（与手动启动同一实现）
- **AND** 快照在 ≤ 60 秒内反映 `supervisor.running=true`

#### Scenario: 期望停止停看护

- **GIVEN** 控制文件 `desired_running=false` 且看护运行中（含进行中的会话）
- **WHEN** 代理轮询（或手动触发调和）
- **THEN** 看护与当前会话被停止（与手动停止同一语义）
- **AND** 快照反映 `supervisor.running=false`，代理本身仍在运行（继续周期快照与控制通道）

#### Scenario: 期望与实态一致不动作

- **GIVEN** 控制文件 `desired_running=false` 且看护已停止（或 `true` 且运行中）
- **WHEN** 代理轮询
- **THEN** 零启停动作（幂等，MUST NOT 重复拉起/重复杀进程）

#### Scenario: 无控制文件不干预

- **GIVEN** 控制文件不存在（首次部署 / 从未下过控制指令）
- **WHEN** 代理轮询
- **THEN** MUST NOT 启停看护（保持现状）

#### Scenario: 控制文件损坏不干预

- **GIVEN** 控制文件非合法 JSON 或 `desired_running` 字段缺失/非布尔
- **WHEN** 代理轮询
- **THEN** MUST NOT 启停看护
- **AND** 日志留可判读记录（MUST NOT 静默猜测期望态）

#### Scenario: 停止不清数据

- **GIVEN** 看护运行中且工作区已有候选实现与候选文件
- **WHEN** 期望停止的调和动作执行
- **THEN** workspace 任务目录 / 候选文件 / 会话日志保持原样（MUST NOT 删除）
- **AND** 重新开启后研发从既有工作区继续（不重置）

### Requirement: 控制请求端点

系统 SHALL 提供 `PUT /api/factor-research/control` 端点（body：`desired_running` 必填布尔），
原子写入控制文件（默认 `data/factor_research/control.json`；路径 SHALL 在调用期读取
`FACTOR_RESEARCH_CONTROL_PATH`），返回 `{"control": {...}}`（与状态端点 control 块同形状，含
`honor` / `honor_eta_seconds`）。请求体 MUST 仅接受布尔期望态（MUST NOT 引入任意命令/脚本/参数
字段——命令注入面为零）。受理规则：快照新鲜（`available=true` 且 `stale=false`）→ SHALL 受理
（`honor="live"`、`honor_eta_seconds=0`）；快照缺失、损坏或陈旧时，若启动器心跳 available、
`fresh` 且 `agent_ok=true` → SHALL 仍受理（`honor="launcher"`、`honor_eta_seconds=180`——请求
将在下一启动器节拍被拉起并按期望态调和）；两者皆不成立（启动器缺失/损坏/不新鲜或
`agent_ok=false`）→ SHALL 拒绝（409 + `control_unavailable`），MUST NOT 写入控制文件。期望态与
已存值相同时 SHALL 幂等成功（可更新 `requested_at` / `request_id` 留痕）。端点 MUST NOT 调用 LLM、
MUST NOT 直接触碰研发进程。

(Previously: 快照缺失/损坏/陈旧即 409 拒绝——无启动器存活证据维度。)

#### Scenario: 控制写入成功

- **GIVEN** 快照新鲜且 `supervisor.running=true`
- **WHEN** `PUT` `desired_running=false`
- **THEN** 控制文件原子落地（含 `desired_running` / `requested_at` / `requested_at_epoch` / `request_id`）
- **AND** 响应 200 且 `control.desired_running=false`、`control.pending=true`（期望与实态不一致）
- **AND** `control.honor="live"`

#### Scenario: 启动器可用时离线受理

- **GIVEN** 快照缺失/损坏/陈旧，但 `launcher.json` 新鲜（≤300 秒）且 `agent_ok=true`
- **WHEN** `PUT` `desired_running=true`
- **THEN** 返回 200，控制文件原子落地
- **AND** 响应 `control.honor="launcher"`、`honor_eta_seconds=180`
- **AND** 下一启动器节拍（≤2 分钟）拉起研究侧后，代理按该期望态调和并在 ≤3 分钟内写出首个快照

#### Scenario: 通道无证据拒绝

- **GIVEN** 快照缺失/损坏/陈旧，且启动器心跳缺失/损坏/不新鲜或 `agent_ok=false`
- **WHEN** `PUT` `desired_running=true`
- **THEN** 返回 409 + `control_unavailable`
- **AND** 控制文件未被写入（MUST NOT 留下无人受理的请求）

#### Scenario: 幂等重复

- **GIVEN** 控制文件已含 `desired_running=false`
- **WHEN** 再次 `PUT` `desired_running=false`
- **THEN** 返回 200（`requested_at` 可更新留痕）且不产生额外副作用

### Requirement: 研究侧自愈启动

研究侧 SHALL 具备宿主级自愈启动器：在用户登录时并以 ≤ 2 分钟的固定节拍，幂等调用 distro 入口
`ensure-factor-research.sh`（→ 研发代理 `--launcher-tick`）。该入口 SHALL：

1. 确保研发代理守护存活——已在运行 MUST NOT 重复拉起（复用既有单例锁；重复实例 SHALL 被安全
   拒绝且不终止既有实例）；
2. 原子写启动器心跳（默认 `data/factor_research/launcher.json`，路径 SHALL 在调用期读取
   `FACTOR_RESEARCH_LAUNCHER_PATH`），含 `checked_at`（ISO8601 带时区）、`checked_at_epoch`
   （Unix 秒）、`agent_pid`（整数或 `null`）、`agent_ok`（布尔）、`snapshot_age_s`（整数或
   `null`：最近状态快照距检查时刻的秒数）与 `detail`（可判读说明或 `null`）；
3. MUST NOT 写入或修改控制文件——用户意图的唯一写入面不变，启动器 MUST NOT 覆盖期望态；
4. MUST NOT 直接启停看护——看护启停只由代理按期望态调和（单一实现）；
5. MUST NOT 删除或修改任何研发产物（workspace / 候选文件 / 会话日志）。

心跳新鲜阈值 SHALL 为 300 秒（2× 节拍 + 余量），由后端派生 `fresh`。心跳写失败 MUST NOT 影响
代理存活保证；心跳缺失时后端 MUST NOT 声称启动器可用（fail-safe）。

#### Scenario: 代理被杀后自愈

- **GIVEN** 代理守护进程被外部终止（distro 仍在运行）
- **WHEN** 下一启动器节拍（≤2 分钟）
- **THEN** 代理守护被重新拉起，心跳 `agent_ok=true`
- **AND** 状态快照在 ≤3 分钟内恢复周期推进（不重置研发工作区）

#### Scenario: distro 停止后自愈

- **GIVEN** distro 被终止（虚拟机仍可用）
- **WHEN** 下一启动器节拍
- **THEN** distro 被启动、代理守护被拉起、心跳落地
- **AND** 若控制文件 `desired_running=true`，代理按期望态拉起看护并在 ≤3 分钟内写出首个快照

#### Scenario: 意图不被改写

- **GIVEN** 控制文件 `desired_running=false` 且看护已停止
- **WHEN** 启动器执行任意次节拍
- **THEN** 控制文件内容保持语义不变（MUST NOT 被启动器写入或覆盖）
- **AND** 看护保持停止（MUST NOT 被启动器直接拉起）

#### Scenario: 单例不重复拉起

- **GIVEN** 代理守护已在运行
- **WHEN** 启动器执行节拍
- **THEN** MUST NOT 启动第二个代理实例（单例锁安全拒绝）
- **AND** 心跳 `agent_pid` 与既有实例一致

#### Scenario: 心跳不可写不阻断

- **GIVEN** 宿主桥（`/mnt/d`）暂时不可写或读取缓慢
- **WHEN** 启动器执行节拍
- **THEN** 代理存活保证不受影响（MUST NOT 因心跳写失败而退出）
- **AND** 心跳缺失/过期时后端按 `launcher.available=false` / `fresh=false` 处理（fail-safe）

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

### Requirement: 会话进度僵死派生

`GET /api/factor-research/status` 响应 SHALL 附加派生块 `progress`：`log_age_seconds`
（整数或 `null`，取自快照 `current_session.log_age_s`；快照不可用或字段缺失 → `null`）与
`stalled`（布尔）。`stalled` 判定 SHALL 为：快照 `available=true`、`supervisor.running=true`、
`current_session.alive=true` 且 `log_age_seconds > FACTOR_RESEARCH_STALL_AFTER_S` → `true`；
其余情形（快照不可用/陈旧、看护未运行、会话非存活、`log_age_seconds` 为 `null`）→ `false`。

阈值 SHALL 单源为后端常量 `FACTOR_RESEARCH_STALL_AFTER_S = 900`（秒）。依据：历史 14 个会话日志
实测最大静默 602 秒（4 次，疑为上游 600 秒超时）、中位 88 秒 ⇒ 取观测上界 1.5 倍，既覆盖正常
长操作又不误报，同时把本次 4h07m 级停摆在 15 分钟内暴露。该阈值 MUST NOT 由采集器或前端另行
定义（MUST NOT 出现第二份阈值）。

`progress` 块 SHALL 在快照不可用/陈旧时同样返回（`log_age_seconds=null`、`stalled=false`）——
实态不可证时 MUST NOT 报僵死，亦 MUST NOT 声称进度正常。端点 MUST NOT 因 `stalled=true` 而
自动采取任何处置动作（不重启、不写控制文件、不触碰研发进程）——本能力只负责**如实披露**。

#### Scenario: 日志静默超阈值判僵死

- **GIVEN** 快照 `available=true`、`stale=false`、`supervisor.running=true`、
  `current_session.alive=true`、`current_session.log_age_s=3600`
- **WHEN** 请求状态端点
- **THEN** `progress.log_age_seconds=3600`、`progress.stalled=true`
- **AND** 响应其余字段逐字透传（MUST NOT 因僵死而改写 `actual_state`）

#### Scenario: 静默在阈值内不告警

- **GIVEN** 同上，但 `current_session.log_age_s=602`（历史观测上界）
- **WHEN** 请求状态端点
- **THEN** `progress.stalled=false`
- **AND** MUST NOT 无端告警

#### Scenario: 无会话不判僵死

- **GIVEN** 快照新鲜但 `supervisor.running=false`（看护已停止）且 `current_session.log_age_s`
  为 `null` 或很大
- **WHEN** 请求状态端点
- **THEN** `progress.stalled=false`
- **AND** MUST NOT 把「看护没在跑」报成「会话僵死」

#### Scenario: 快照不可用时不判僵死

- **GIVEN** 快照缺失或损坏
- **WHEN** 请求状态端点
- **THEN** `available=false` 且 `progress.log_age_seconds=null`、`progress.stalled=false`

#### Scenario: 旧版采集器未上报新鲜度

- **GIVEN** 快照新鲜且会话存活，但 `current_session` 不含 `log_age_s`（旧版采集器）
- **WHEN** 请求状态端点
- **THEN** `progress.log_age_seconds=null`、`progress.stalled=false`
- **AND** 前端可据此不展示僵死提示（MUST NOT 把「未上报」当成「进度正常」的断言）

#### Scenario: 僵死不触发任何处置

- **GIVEN** `progress.stalled=true`
- **WHEN** 请求状态端点（可重复请求）
- **THEN** 控制文件、研发进程状态、快照内容均不被修改
- **AND** 解除僵死只能经既有控制通道（`PUT /control` / `POST /restart`）由人发起

### Requirement: 研究侧 distro 探活与不可用自愈

宿主级自愈启动器 SHALL 在每次节拍（≤2 分钟）先做一次 **distro 可执行性探活**：
`wsl -d <distro> -e /bin/true`（超时 SHALL ≤20 秒），并把结果原子写入宿主侧探活文件（默认
`data/factor_research/distro_probe.json`，路径 SHALL 在调用期读取
`FACTOR_RESEARCH_DISTRO_PROBE_PATH`），字段 SHALL 含：`probed_at`（ISO8601 带时区）、
`probed_at_epoch`（Unix 秒）、`distro`（distro 名）、`ok`（布尔）、`exit_code`（整数或 `null`）、
`detail`（可判读摘要或 `null`，MUST NOT 编造）、`consecutive_failures`（连续探活失败次数，整数）、
`terminated_at`（ISO8601 或 `null`）、`terminate_count`（整数）。

探活 SHALL 与既有 `ensure-factor-research.sh` 调用相互独立：探活失败 MUST NOT 阻止启动器按既有语义
继续尝试拉起代理；探活路径 MUST NOT 修改研发产物（workspace / 候选文件 / 会话日志）与任何控制文件。
探活文件写失败 MUST NOT 影响代理存活保证（fail-safe）。

**不可用自愈**：下列三条件**必须同时成立**，任一不成立 MUST NOT 执行终止：

1. 探活失败且 `consecutive_failures ≥ 15`（2 分钟节拍下 ≈ 持续失败 ≥30 分钟）；
2. 状态快照 `collected_at_epoch` 距当前 ≥ 900 秒（采集链路同时停摆）；
3. 距上一次自愈动作 ≥ 1800 秒（限流）。

三条件成立时 SHALL 执行一次 `wsl --terminate <distro>`（使下一次节拍重新拉起 distro），并在探活
文件记录 `terminated_at` / `terminate_count`（自增）。

**判据依据（MUST 遵守的理由）**：历史 839 次节拍中 611 次（72.8%）以非零退出（WSL relay 超时），
而同期管线实际在跑 ⇒ 单次探活失败 MUST NOT 被当作「distro 已死」。条件 2 与条件 1 互斥于健康态
（健康时快照必然新鲜），故二者叠加才构成「不可用」证据，避免反复杀掉在跑的研发会话。

#### Scenario: 探活成功照常拉起

- **GIVEN** distro 可执行（`wsl -d rdagent -e /bin/true` 退出 0）
- **WHEN** 启动器节拍
- **THEN** 探活文件 `ok=true`、`exit_code=0`、`consecutive_failures=0`
- **AND** 既有 ensure 流程照常执行（行为不变），控制文件与研发产物未被触碰

#### Scenario: 探活失败但快照新鲜 → 只报告不终止

- **GIVEN** 探活连续失败 30 次，但快照 `collected_at_epoch` 距当前 60 秒（采集链路正常）
- **WHEN** 启动器节拍
- **THEN** 探活文件如实记录 `ok=false` 与 `consecutive_failures`
- **AND** MUST NOT 执行 `wsl --terminate`（健康证据优先，避免杀掉在跑的会话）

#### Scenario: 探活失败且快照同时停摆 → 自愈一次

- **GIVEN** 探活连续失败 ≥15 次、快照距当前 ≥900 秒、距上次自愈 ≥1800 秒
- **WHEN** 启动器节拍
- **THEN** 执行一次 `wsl --terminate <distro>`，探活文件记录 `terminated_at` 与自增的 `terminate_count`
- **AND** MUST NOT 触碰控制文件与研发产物

#### Scenario: 自愈限流

- **GIVEN** 上一次自愈发生在 300 秒前，其余条件仍成立
- **WHEN** 启动器节拍
- **THEN** MUST NOT 再次执行终止（`terminate_count` 不变）

#### Scenario: 探活文件不可写不阻断

- **GIVEN** 宿主 `data/` 桥暂时不可写
- **WHEN** 启动器节拍
- **THEN** 代理存活保证与既有 ensure 流程不受影响（MUST NOT 因探活写失败而中断）
- **AND** 后端按 `distro.available=false` 处理（fail-safe，MUST NOT 声称探活结论）

### Requirement: distro 探活只读派生

`GET /api/factor-research/status` 响应 SHALL 附加派生块 `distro`：探活文件路径 SHALL 在调用期读取
`FACTOR_RESEARCH_DISTRO_PROBE_PATH`（默认 `data/factor_research/distro_probe.json`）；文件存在且可
解析 → `available=true` 并透传 `probed_at` / `probed_at_epoch` / `distro` / `ok` / `exit_code` /
`detail` / `consecutive_failures` / `terminated_at` / `terminate_count`，附加派生 `age_seconds`
（自 `probed_at_epoch` 至请求时刻的秒数）与 `fresh`（`age_seconds ≤ 300`）；文件缺失或不可解析 →
`available=false` + 原因码（`missing` / `corrupt`），MUST NOT 以 500 或伪结论冒充。端点 MUST NOT 因
`distro.ok=false` 改变既有 `actual_state` / `control.honor` 判定，亦 MUST NOT 采取任何处置动作
（只披露，不重启、不终止、不写控制文件）。

#### Scenario: 探活文件透传与派生

- **GIVEN** 探活文件合法、`probed_at_epoch` 距请求 40 秒、`ok=false`、`consecutive_failures=3`
- **WHEN** 请求状态端点
- **THEN** `distro.available=true`、`distro.age_seconds≈40`、`distro.fresh=true`、`distro.ok=false`
- **AND** `actual_state` 与 `control.honor` 的判定不受影响

#### Scenario: 缺失与损坏如实区分

- **GIVEN** 探活文件不存在
- **WHEN** 请求状态端点
- **THEN** `distro.available=false` + `reason=missing`

- **GIVEN** 探活文件存在但内容非合法 JSON
- **WHEN** 请求状态端点
- **THEN** `distro.available=false` + `reason=corrupt`

#### Scenario: 探活陈旧不冒充结论

- **GIVEN** 探活文件合法但 `probed_at_epoch` 距请求 900 秒
- **WHEN** 请求状态端点
- **THEN** `distro.available=true` 且 `distro.fresh=false`
- **AND** 前端 MUST NOT 据陈旧探活展示「distro 不可用」结论

### Requirement: 会话产物保留窗清理
The system MUST 在每次研发会话结束后，将 `RD-Agent_workspace` 下最新文件改动时间早于保留窗（默认 48h，可经 `FACTOR_WORKSPACE_RETENTION_H` 覆盖）的任务目录删除，且删除行为 MUST NOT 影响正在运行或保留窗内的会话目录。

#### Scenario: 会话结束后清理过期目录
- GIVEN workspace 下存在任务目录 A（最新文件 mtime 为 72h 前）与 B（最新文件 mtime 为 10h 前）
- WHEN 一次会话结束、看护进入下一节拍
- THEN 目录 A 被删除、目录 B 保留
- AND 清理结果（删除数 / 失败数）写入看护日志

#### Scenario: 清理失败不致崩
- GIVEN 某过期目录因 I/O 错误无法删除
- WHEN 保留窗清理执行
- THEN 该目录被跳过并记录失败明细
- AND 看护主循环继续正常运行

### Requirement: 磁盘水位采集与只读派生
The collector MUST 在每节拍采集研发 distro 的 guest ext4 空闲字节、宿主工作盘空闲字节与 workspace 目录总占用，并 MUST 在只读端点以 `disk` 块派生只读视图；宿主卷或 guest 任一侧空闲低于阈值（guest 默认 30GB、宿主默认 20GB）时派生 `low_watermark=true`。

#### Scenario: 水位读数入快照
- GIVEN 采集器按节拍运行
- WHEN 快照组装
- THEN 快照含 `disk.guest_free_bytes` / `disk.host_free_bytes` / `disk.workspace_bytes`
- AND 任一读数采集失败时该字段如实置空并记录失败原因，MUST NOT 编造数字

#### Scenario: 低水位判定
- GIVEN guest ext4 空闲为 25GB（< 30GB 阈值）
- WHEN 端点派生 `disk` 块
- THEN `low_watermark=true` 且 `low_watermark_reason` 指明 guest 侧

### Requirement: 低水位停发保护
The supervisor MUST 在低水位成立时暂停拉起新的研发会话，并在状态中如实披露暂停原因；已在运行的会话 MUST NOT 被低水位保护中断。

#### Scenario: 低水位时暂停新会话
- GIVEN `disk.low_watermark=true`
- WHEN 看护到达下一节拍且无会话在运行
- THEN 看护不拉起新会话
- AND 状态记录 `paused_reason=disk_low_watermark`

#### Scenario: 运行中会话不受影响
- GIVEN 低水位成立且当前有会话在运行
- WHEN 看护节拍
- THEN 该会话继续运行至自然结束
- AND 结束后进入停发状态

### Requirement: distro 探活覆盖页缓存假阴性
The distro 探活 MUST 在 exec 探针之外增加对 distro 文件系统的真实读写验证（创建并删除一个临时文件）；exec 成功而读写失败时探活结果 MUST 为不可用。

#### Scenario: 页缓存假阴性被识破
- GIVEN distro 内 `/bin/true` 可执行（页缓存命中）但文件系统大面积 I/O 错误
- WHEN 探活执行
- THEN exec 探针成功、读写探针失败
- AND 探活结果为不可用（`ok=false`），失败原因区分 `fs_rw_failed`
